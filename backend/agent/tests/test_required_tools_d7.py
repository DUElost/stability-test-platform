"""ADR-0051 v1.7 D7：脚本包声明的工具依赖（capabilities.json ``requires_tools``）。

覆盖：声明判据（门禁与运行时同一份）、注入成功 / fail-closed（缺包、坏 sha、退役）、
**不回退主机同名 env 键**、verify 预热、引擎端到端（子进程看到的 env）。
"""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.agent.pipeline_engine import PipelineEngine
from backend.agent.script_packages import inject_required_tools, verify_package
from backend.agent.tool_requirements import (
    RequirementError,
    ToolRequirement,
    load_requirements,
    parse_requirements,
)

_FLASH = {"version": "1.2444.00.100", "env": "STP_FLASH_TOOL_DIR"}


# ── 声明判据（纯函数）─────────────────────────────────────────────────────────


def test_no_declaration_means_no_requirements():
    assert parse_requirements({"capabilities": ["progress_stamps"]}) == []
    assert parse_requirements(["progress_stamps"]) == []          # 旧式 list 形态
    assert parse_requirements(None) == []


def test_valid_declaration_sorted_by_family():
    got = parse_requirements({"requires_tools": {
        "flashtool": _FLASH,
        "aimonkey": {"version": "20260317", "env": "AIMONKEY_RESOURCE_DIR"},
    }})
    assert got == [
        ToolRequirement("aimonkey", "20260317", "AIMONKEY_RESOURCE_DIR"),
        ToolRequirement("flashtool", "1.2444.00.100", "STP_FLASH_TOOL_DIR"),
    ]


@pytest.mark.parametrize("requires, fragment", [
    (["flashtool"], "必须是对象"),
    ({"flashtool": "1.2444.00.100"}, "恰为"),
    ({"flashtool": {"version": "1.0"}}, "恰为"),
    ({"flashtool": {**_FLASH, "optional": True}}, "恰为"),
    ({"flashtool": {"version": "../x", "env": "STP_FLASH_TOOL_DIR"}}, "version"),
    ({"flashtool": {"version": "..", "env": "STP_FLASH_TOOL_DIR"}}, "version"),
    ({"flashtool": {"version": 1, "env": "STP_FLASH_TOOL_DIR"}}, "version"),
    ({"flashtool": {"version": "1.0", "env": "PATH"}}, "_DIR"),
    ({"flashtool": {"version": "1.0", "env": "LD_PRELOAD"}}, "_DIR"),
    ({"flashtool": {"version": "1.0", "env": "stp_flash_tool_dir"}}, "_DIR"),
    ({"flashtool": {"version": "1.0", "env": "STP_LOG_DIR"}}, "引擎自有"),
    ({"flashtool": {"version": "1.0", "env": "STP_AGENT_INSTALL_DIR"}}, "引擎自有"),
    ({"../evil": _FLASH}, "族名"),
    ({"a": _FLASH, "b": _FLASH}, "占用"),
])
def test_invalid_declarations_rejected(requires, fragment):
    with pytest.raises(RequirementError, match=fragment):
        parse_requirements({"requires_tools": requires})


def test_load_missing_file_is_empty_but_corrupt_file_raises(tmp_path):
    """控制面 read_capabilities 把坏文件当「无能力」；依赖声明坏了必须 fail-closed，不能当无依赖放行。"""
    assert load_requirements(tmp_path) == []
    (tmp_path / "capabilities.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(RequirementError, match="不可读"):
        load_requirements(tmp_path)


# ── 站点夹具：manifest 副本 + 确定性 tarball ──────────────────────────────────


def _tarball(path: Path, files: dict[str, bytes]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=buf, mtime=0) as gz:
        with tarfile.open(fileobj=gz, mode="w") as tar:
            for name, data in sorted(files.items()):
                info = tarfile.TarInfo(name)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
    path.write_bytes(buf.getvalue())
    return hashlib.sha256(buf.getvalue()).hexdigest()


class _Site:
    """站点包根：``packages_root/manifest.json``（控制面发布的派生副本形态）+ 各族 tarball。"""

    def __init__(self, root: Path):
        self.packages = root / "packages"
        self.cache = root / "cache"
        self.tools: dict[str, dict] = {}

    def add(self, name: str, version: str, files: dict[str, bytes], *, kind: str = "tool",
            script: str, sha: str | None = None, retired: bool = False) -> str:
        real = _tarball(self.packages / name / f"{version}.tar.gz", files)
        fam = self.tools.setdefault(name, {"kind": kind, "versions": []})
        fam["versions"].append({
            "version": version, "package_sha256": sha or real,
            "artifact": f"packages/{name}/{version}.tar.gz",
            "python": None, "script": script, "retired": retired,
        })
        (self.packages / "manifest.json").write_text(
            json.dumps({"schema_version": 1, "tools": self.tools}), encoding="utf-8")
        return real

    def env(self, **extra: str) -> dict[str, str]:
        return {"STP_PACKAGES_ROOT": str(self.packages), "STP_TOOLS_CACHE_ROOT": str(self.cache), **extra}


def _declare(pkg_root: Path, requires: dict) -> Path:
    pkg_root.mkdir(parents=True, exist_ok=True)
    (pkg_root / "capabilities.json").write_text(
        json.dumps({"capabilities": ["progress_stamps"], "requires_tools": requires}), encoding="utf-8")
    return pkg_root


# ── 注入：成功 / fail-closed / 不回退主机 env ─────────────────────────────────


def test_inject_sets_env_to_verified_tool_root(tmp_path):
    site = _Site(tmp_path)
    site.add("flashtool", "1.2444.00.100", {"flash_tool": b"\x7fELF", "lib/libQt.so": b"x"}, script="flash_tool")
    pkg = _declare(tmp_path / "script_pkg", {"flashtool": _FLASH})
    env = site.env()

    assert inject_required_tools(pkg, env) is None
    root = Path(env["STP_FLASH_TOOL_DIR"])
    assert root == site.cache / "flashtool" / "1.2444.00.100"
    assert (root / "flash_tool").read_bytes() == b"\x7fELF"
    assert (root / ".stp-verified").is_file()


@pytest.mark.parametrize("case", ["missing_entry", "sha_mismatch", "retired"])
def test_inject_fails_closed_and_ignores_host_env_decoy(tmp_path, case):
    """失败不得回退主机同名键：诱饵目录真实存在也不用——否则「包面坏了」被旧主机路径掩盖。"""
    site = _Site(tmp_path)
    if case == "sha_mismatch":
        site.add("flashtool", "1.2444.00.100", {"flash_tool": b"x"}, script="flash_tool", sha="0" * 64)
    elif case == "retired":
        site.add("flashtool", "1.2444.00.100", {"flash_tool": b"x"}, script="flash_tool", retired=True)
    else:
        site.add("other", "1.0", {"a": b"x"}, script="a")
    decoy = tmp_path / "host_resources_flashtool"
    decoy.mkdir()
    pkg = _declare(tmp_path / "script_pkg", {"flashtool": _FLASH})
    env = site.env(STP_FLASH_TOOL_DIR=str(decoy))

    err = inject_required_tools(pkg, env)

    assert err == "required_tool_unavailable: flashtool@1.2444.00.100"
    assert env["STP_FLASH_TOOL_DIR"] == str(decoy)   # 未被改写——但步骤已判失败，不会用到它


def test_inject_invalid_declaration_is_an_error(tmp_path):
    site = _Site(tmp_path)
    pkg = _declare(tmp_path / "script_pkg", {"flashtool": {"version": "1.0", "env": "PATH"}})
    assert inject_required_tools(pkg, site.env()).startswith("required_tools_invalid:")


def test_no_declaration_is_a_noop(tmp_path):
    env = {"STP_FLASH_TOOL_DIR": "/host/path"}
    assert inject_required_tools(tmp_path, env) is None
    assert env == {"STP_FLASH_TOOL_DIR": "/host/path"}


# ── verify 预热：precheck/presence 就把工具包拉进缓存，缺失当场暴露 ────────────


def _script_pkg_with_requirement(site: _Site, requires: dict) -> dict:
    caps = json.dumps({"capabilities": [], "requires_tools": requires}).encode()
    sha = site.add("flash_firmware", "1.3.18", {"flash_firmware.py": b"print(1)", "capabilities.json": caps},
                   kind="script", script="flash_firmware.py")
    return {"name": "flash_firmware", "version": "1.3.18", "package_sha256": sha,
            "nfs_path": "/opt/stability-test-agent/agent/scripts/flash_firmware/v1.3.18/flash_firmware.py"}


def test_verify_prewarms_declared_tools(tmp_path):
    site = _Site(tmp_path)
    site.add("flashtool", "1.2444.00.100", {"flash_tool": b"x"}, script="flash_tool")
    entry = _script_pkg_with_requirement(site, {"flashtool": _FLASH})

    assert verify_package(entry, site.env(STP_SCRIPT_PACKAGES="strict")) == (True, None)
    assert (site.cache / "flashtool" / "1.2444.00.100" / ".stp-verified").is_file()


def test_verify_reports_missing_declared_tool(tmp_path):
    site = _Site(tmp_path)
    entry = _script_pkg_with_requirement(site, {"flashtool": _FLASH})

    ok, err = verify_package(entry, site.env(STP_SCRIPT_PACKAGES="strict"))
    assert (ok, err) == (False, "required_tool_unavailable: flashtool@1.2444.00.100")


# ── 引擎端到端：子进程拿到注入的包根；缺失 = exit 2 ───────────────────────────


class _Registry:
    def __init__(self, path: str):
        self.path = path

    def resolve(self, name: str, version: str):
        return SimpleNamespace(script_id=1, name=name, version=version, script_type="python",
                               nfs_path=self.path, content_sha256="c" * 64)


@pytest.fixture
def _stub_resolution(monkeypatch):
    """同 test_pipeline_engine_script_action 的流程用例口径：包解析 stub 为直映射（cwd = 脚本所在目录）。"""
    import backend.agent.script_packages as sp

    monkeypatch.setattr(
        "backend.agent.pipeline_engine.resolve_script_path",
        lambda entry: sp.ResolvedScript(path=entry.nfs_path, cwd=str(Path(entry.nfs_path).parent)),
    )


def _run_step(script_path: Path):
    engine = PipelineEngine(adb=SimpleNamespace(adb_path="adb"), serial="SERIAL001", run_id=7,
                            script_registry=_Registry(str(script_path)))
    result = engine.execute({"lifecycle": {"init": [
        {"step_id": "flash", "action": "script:flash_firmware", "version": "1.3.18", "timeout_seconds": 30},
    ], "teardown": []}})
    return engine, result


def test_engine_injects_tool_root_into_step_env(tmp_path, monkeypatch, _stub_resolution):
    site = _Site(tmp_path)
    site.add("flashtool", "1.2444.00.100", {"flash_tool": b"x"}, script="flash_tool")
    pkg = _declare(tmp_path / "script_pkg", {"flashtool": _FLASH})
    script = pkg / "flash_firmware.py"
    script.write_text(
        "import json, os\n"
        "print(json.dumps({'metrics': {'tool_dir': os.environ['STP_FLASH_TOOL_DIR']}}))\n",
        encoding="utf-8",
    )
    for k, v in site.env().items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("STP_FLASH_TOOL_DIR", "/host/injected/decoy")   # 主机旧注入：必须被包根覆盖

    engine, result = _run_step(script)

    assert result.success is True, result.error_message
    assert engine._shared["flash"]["tool_dir"] == str(site.cache / "flashtool" / "1.2444.00.100")


def test_engine_missing_required_tool_fails_step_with_exit2(tmp_path, monkeypatch, _stub_resolution):
    site = _Site(tmp_path)
    site.add("other", "1.0", {"a": b"x"}, script="a")
    pkg = _declare(tmp_path / "script_pkg", {"flashtool": _FLASH})
    marker = tmp_path / "ran"
    script = pkg / "flash_firmware.py"
    script.write_text(f"open({str(marker)!r}, 'w').write('x')\n", encoding="utf-8")
    for k, v in site.env().items():
        monkeypatch.setenv(k, v)

    engine, result = _run_step(script)

    assert result.success is False
    assert not marker.exists(), "工具不可用时脚本子进程不得启动"
    assert "required_tool_unavailable: flashtool@1.2444.00.100" in (result.error_message or "")
