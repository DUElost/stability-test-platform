# -*- coding: utf-8 -*-
"""#3601 批次 B4 / G1a：#3320 四个 setup 的 APK 资源 authority（C1）。

缺陷形态（#3601 §1.1 F1）：四个 setup 的 ``_lib.py`` 用
``Path(__file__).resolve().parents[3] / "resources" / <family>`` 推导 host-local
APK 根——开发态落到 ``backend/resources/...``、包态落到 tools_cache 的祖先，两者
都不是该资源的实际 authority。修复（C1）：

    非空 param > 非空 family env > ``config.AGENT_DIR/resources/<family>``

本文件按 §4.2 验证：

1. 开发态 / 真实 builder 生成的 immutable package 态解析（真实 helper，两态各自
   ``cwd`` + Agent PYTHONPATH 的引擎契约）；
2. 安装根以外的自定义 ``STP_TOOLS_CACHE_ROOT`` 不改变 authority（不能依赖 cache
   祖先关系）；
3. param/env/无 override 三路、空值进入默认、显式缺目录不切换 authority、override
   分支在缺默认锚模块时仍独立解析（默认锚不可得时显式失败）；
4. 现存业务：MTBF project/三个精确 APK/suite/results、GPU project/variant/包内
   companion、PowerCycle/Sleep 精确 ``AutoTestTool.apk``、run_dir marker 兼容；
5. 负向变异：隔离副本恢复旧 ``parents[3]`` 表达式，开发/包两态分别变红，恢复转绿；
6. 包身份：真实 builder 重建 sha == manifest 最新条目，包成员 == git 跟踪成员。

不使用生产库 / 不写生产资源；设备侧一律 fake adb 或隔离 fixture。
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
AGENT_DIR = REPO_ROOT / "backend" / "agent"
SCRIPTS_DIR = AGENT_DIR / "scripts"
MANIFEST = REPO_ROOT / "tool_manifest.json"
TEMPLATES_DIR = REPO_ROOT / "backend" / "schemas" / "pipeline_templates"

#: family → C1 符号（param 键 / env 键 / resources 子目录）；与 #3601 §1.3 对齐。
FAMILIES: dict[str, dict[str, str]] = {
    "mtbf_setup": {
        "param": "mtbf_resources_dir",
        "env": "STP_MTBF_RESOURCES_DIR",
        "subdir": "mtbf",
    },
    "gpu_setup": {
        "param": "gpu_resources_dir",
        "env": "STP_GPU_RESOURCES_DIR",
        "subdir": "gpu",
    },
    "powercycle_setup": {
        "param": "powercycle_resources_dir",
        "env": "STP_POWER_CYCLE_RESOURCES_DIR",
        "subdir": "power-cycle",
    },
    "sleep_setup": {
        "param": "sleep_resources_dir",
        "env": "STP_SLEEP_RESOURCES_DIR",
        "subdir": "sleep",
    },
}

_RESOLVE_SNIPPET = r"""
import json, sys
lib_dir, cfg_json = sys.argv[1], sys.argv[2]
sys.path.insert(0, lib_dir)
import _lib
print(json.dumps({"rdir": str(_lib.resources_dir(json.loads(cfg_json)))}))
"""


# ---------------------------------------------------------------------------
# 工具与 fixture 助手
# ---------------------------------------------------------------------------

def _load_tool(name: str, relpath: str):
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / relpath)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_family_entry(family: str):
    """加载真实入口模块（family 目录进 sys.path，模拟脚本自身目录 = sys.path[0]）。

    入口 `from _lib import ...`：各 family 的 `_lib` 同名，必须清缓存再载，
    否则第二个 family 会复用第一个的 `_lib`；载完恢复现场，不污染其它测试。
    """
    d = SCRIPTS_DIR / family
    entry = next(p for p in sorted(d.iterdir()) if p.suffix == ".py" and not p.name.startswith("_"))
    saved = sys.modules.pop("_lib", None)
    sys.path.insert(0, str(d))
    try:
        spec = importlib.util.spec_from_file_location(f"b4_{family}_entry", entry)
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        sys.path.remove(str(d))
        sys.modules.pop("_lib", None)
        if saved is not None:
            sys.modules["_lib"] = saved
    return mod


def _load_family_lib(family: str):
    """直接加载族 `_lib.py`（纯 stdlib；用于入口未导入的 helper 断言）。"""
    d = SCRIPTS_DIR / family
    spec = importlib.util.spec_from_file_location(f"b4_{family}_lib", d / "_lib.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _manifest_head(family: str) -> dict:
    doc = json.loads(MANIFEST.read_text(encoding="utf-8"))
    live = [e for e in doc["tools"][family]["versions"] if not e.get("retired")]
    assert live, f"{family}: manifest 无未退役条目"
    return max(live, key=lambda e: tuple(int(p) for p in str(e["version"]).split(".")))


def _fake_agent_root(tmp_path: Path, project: str = "legacy") -> Path:
    """隔离的 Agent 代码根：真实 config.py + 当前族树副本 + 资源目录（放 fixture）。"""
    agent = tmp_path / "agent"
    (agent / "resources").mkdir(parents=True)
    shutil.copy2(AGENT_DIR / "config.py", agent / "config.py")
    for family, spec in FAMILIES.items():
        (agent / "resources" / spec["subdir"] / project).mkdir(parents=True)
        shutil.copytree(
            SCRIPTS_DIR / family,
            agent / "scripts" / family,
            ignore=shutil.ignore_patterns("__pycache__"),
        )
    return agent


def _run_resolver(
    lib_dir: Path,
    cfg: dict | None = None,
    *,
    agent_pythonpath: Path | None,
    cwd: Path | None = None,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """按引擎契约跑真实 resolver：cwd=包/族目录，PYTHONPATH=Agent 目录。"""
    env = {
        "PYTHONPATH": str(agent_pythonpath) if agent_pythonpath else "",
        "STP_DEVICE_SERIAL": "B4-RESOLVER",
    }
    for spec in FAMILIES.values():
        env.pop(spec["env"], None)
    env.update(extra_env or {})
    return subprocess.run(
        [sys.executable, "-c", _RESOLVE_SNIPPET, str(lib_dir), json.dumps(cfg or {})],
        cwd=str(cwd or lib_dir),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def _resolve(lib_dir: Path, cfg: dict | None, *, agent_pythonpath: Path | None, **kw) -> Path:
    proc = _run_resolver(lib_dir, cfg, agent_pythonpath=agent_pythonpath, **kw)
    assert proc.returncode == 0, f"resolver 失败 rc={proc.returncode}: {proc.stderr}"
    out = next((line for line in reversed(proc.stdout.splitlines()) if line.strip().startswith("{")), None)
    assert out is not None, f"resolver 无 JSON 输出: {proc.stdout!r}"
    return Path(json.loads(out)["rdir"])


def _build_family_tar(family: str, dest: Path) -> tuple[Path, dict]:
    """真实 builder：git 跟踪成员 → 确定性 tar（与 manifest 登记同源）。"""
    packer = _load_tool("b4_packer_3320", "tools/dev/package_tool_asset.py")
    checker = _load_tool("b4_checker_3320", "tools/dev/check_script_packages.py")
    out = dest / f"{family}.tar.gz"
    facts = checker.build_family(SCRIPTS_DIR / family, packer, out)
    assert facts is not None, f"{family}: 族树无入口"
    return out, facts


def _extract_tar(tar_path: Path, dest: Path) -> None:
    dest.mkdir(parents=True)
    with tarfile.open(tar_path, "r:gz") as tar:
        for member in tar.getmembers():
            assert member.isfile(), f"包内非文件成员: {member.name}"
            target = dest / member.name
            target.parent.mkdir(parents=True, exist_ok=True)
            with tar.extractfile(member) as src, open(target, "wb") as out:
                assert src is not None
                out.write(src.read())


def _plant_apks(root: Path, names: tuple[str, ...]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for name in names:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"apk-" + name.encode())


# ---------------------------------------------------------------------------
# 1/2. 开发态 + 真实包态解析；自定义 cache root 不移动 authority
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("family", sorted(FAMILIES))
def test_default_authority_dev_and_package_layouts(family: str, tmp_path: Path) -> None:
    """§4.2.1/2：开发态与真实 immutable package 态都落 Agent 代码根 authority。"""
    spec = FAMILIES[family]
    project = "legacy"
    agent = _fake_agent_root(tmp_path, project)
    authority = agent / "resources" / spec["subdir"] / project

    # 开发态：真实族树 helper + Agent PYTHONPATH（cwd = 族目录）。
    dev = _resolve(SCRIPTS_DIR / family, {}, agent_pythonpath=agent, cwd=SCRIPTS_DIR / family)
    assert dev == authority, f"{family} 开发态落点 {dev} != authority {authority}"

    # 包态：真实 builder 打 tar → 隔离 cache 解包 → 引擎 cwd/PYTHONPATH 契约。
    tar_path, facts = _build_family_tar(family, tmp_path / "build")
    head = _manifest_head(family)
    assert facts["package_sha256"] == head["package_sha256"], (
        f"{family} 重建 sha 与 manifest 最新条目不符——包身份漂移"
    )
    pkg_dir = tmp_path / "cache" / family / str(head["version"])
    _extract_tar(tar_path, pkg_dir)
    packed = _resolve(pkg_dir, {}, agent_pythonpath=agent, cwd=pkg_dir)
    assert packed == authority, f"{family} 包态落点 {packed} != authority {authority}"


@pytest.mark.parametrize("family", sorted(FAMILIES))
def test_custom_tools_cache_root_does_not_move_authority(family: str, tmp_path: Path) -> None:
    """§4.2.2：安装根以外的自定义 STP_TOOLS_CACHE_ROOT 不改变 host-local authority。"""
    spec = FAMILIES[family]
    project = "legacy"
    agent = _fake_agent_root(tmp_path, project)
    authority = agent / "resources" / spec["subdir"] / project

    tar_path, _ = _build_family_tar(family, tmp_path / "build")
    custom_cache = tmp_path / "outside-install" / "cache"
    pkg_dir = custom_cache / family / "x.y.z"
    _extract_tar(tar_path, pkg_dir)
    old_style_wrong = custom_cache.parent / "resources" / spec["subdir"] / project
    install = tmp_path / "install-without-these-resources"

    resolved = _resolve(
        pkg_dir,
        {},
        agent_pythonpath=agent,
        cwd=pkg_dir,
        extra_env={
            "STP_TOOLS_CACHE_ROOT": str(custom_cache),
            "STP_AGENT_INSTALL_DIR": str(install),
            "AGENT_INSTALL_DIR": str(install),
        },
    )
    assert resolved == authority, f"{family} 自定义 cache 下 authority 漂移: {resolved}"
    assert resolved != old_style_wrong, "落回旧 parents[3] 形态（cache 祖先）"
    assert str(custom_cache) not in str(resolved) and str(install) not in str(resolved)


# ---------------------------------------------------------------------------
# 3. override 三路 / 空值 / 显式缺目录 / 缺默认锚时的独立性
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("family", sorted(FAMILIES))
def test_param_env_default_precedence_and_empty_fallback(family: str, tmp_path: Path) -> None:
    spec = FAMILIES[family]
    agent = _fake_agent_root(tmp_path)
    authority = agent / "resources" / spec["subdir"] / "legacy"
    lib = SCRIPTS_DIR / family

    param_win = _resolve(
        lib,
        {spec["param"]: "/param/base", "project": "p"},
        agent_pythonpath=agent,
        extra_env={spec["env"]: "/env/base"},
    )
    assert param_win == Path("/param/base/p"), "非空 param 未优先于 env"

    env_only = _resolve(
        lib, {"project": "p"}, agent_pythonpath=agent, extra_env={spec["env"]: "/env/base"}
    )
    assert env_only == Path("/env/base/p"), "非空 env 未优先于默认"

    empty_param = _resolve(
        lib,
        {spec["param"]: "", "project": "p"},
        agent_pythonpath=agent,
        extra_env={spec["env"]: "/env/base"},
    )
    assert empty_param == Path("/env/base/p"), "空 param 未进入 env"

    empty_both = _resolve(
        lib,
        {spec["param"]: "", "project": "legacy"},
        agent_pythonpath=agent,
        extra_env={spec["env"]: ""},
    )
    assert empty_both == authority, "空 param + 空 env 未进入默认 authority"

    # 显式缺目录：原样使用，不偷偷切换 authority。
    missing = _resolve(
        lib,
        {spec["param"]: "/no/such/resources", "project": "legacy"},
        agent_pythonpath=agent,
    )
    assert missing == Path("/no/such/resources/legacy"), "显式缺目录被替换成其它 authority"

    # 中心存储形态按原 Path 语义消费（不强制迁到 agent/resources）。
    central = _resolve(
        lib,
        {spec["param"]: f"/mnt/stp-aee/resources/{spec['subdir']}", "project": "legacy"},
        agent_pythonpath=agent,
    )
    assert central == Path(f"/mnt/stp-aee/resources/{spec['subdir']}/legacy")


@pytest.mark.parametrize("family", sorted(FAMILIES))
def test_override_branch_independent_of_default_authority(family: str, tmp_path: Path) -> None:
    """§4.2.3：缺默认锚模块时 param/env 仍可解析；无 override 时显式失败。"""
    spec = FAMILIES[family]
    lib = SCRIPTS_DIR / family

    # PYTHONPATH 不含 Agent 目录 → 顶层 `import config` 必失败（隔离进程）。
    proc = _run_resolver(
        lib, {spec["param"]: "/param/base", "project": "p"}, agent_pythonpath=None
    )
    assert proc.returncode == 0, f"缺 config 时 param 分支失败: {proc.stderr}"
    assert Path(json.loads(proc.stdout)["rdir"]) == Path("/param/base/p")

    proc = _run_resolver(
        lib, {"project": "p"}, agent_pythonpath=None, extra_env={spec["env"]: "/env/base"}
    )
    assert proc.returncode == 0, f"缺 config 时 env 分支失败: {proc.stderr}"
    assert Path(json.loads(proc.stdout)["rdir"]) == Path("/env/base/p")

    # 无 override 且无 authority：显式失败（不猜路径、不落 cwd 相对目录）。
    proc = _run_resolver(lib, {}, agent_pythonpath=None)
    assert proc.returncode != 0, "默认 authority 不可得时未显式失败"
    assert "config.AGENT_DIR" in proc.stderr, f"失败信息未指明 authority: {proc.stderr!r}"


def test_config_normalization_preserves_resource_key(tmp_path: Path, monkeypatch) -> None:
    """§4.2.3：配置规范化不丢资源键（param / env / 空值三态都透传）。"""
    monkeypatch.delenv("STP_AEE_NFS_ROOT", raising=False)
    for family, expected_key in (
        ("gpu_setup", "gpu_resources_dir"),
        ("powercycle_setup", "powercycle_resources_dir"),
        ("sleep_setup", "sleep_resources_dir"),
    ):
        spec = FAMILIES[family]
        monkeypatch.delenv(spec["env"], raising=False)
        entry = _load_family_entry(family)
        normalize = getattr(entry, f"{family.replace('_setup', '')}_config")
        cfg = normalize({spec["param"]: "/param/base"})
        assert cfg[expected_key] == "/param/base", f"{family} 丢 param 资源键"
        cfg = normalize({})
        assert cfg[expected_key] == "", f"{family} 空值应保留空键（不是丢键）"
        monkeypatch.setenv(spec["env"], "/env/base")
        cfg = normalize({})
        assert cfg[expected_key] == "/env/base", f"{family} 丢 env 资源键"
        monkeypatch.delenv(spec["env"], raising=False)
        # 直接对规范化结果解析 override（不触发默认锚）。
        resolved = _resolve(
            SCRIPTS_DIR / family,
            {**cfg, spec["param"]: "/param/base", "project": "p"},
            agent_pythonpath=None,
        )
        assert resolved == Path("/param/base/p")


# ---------------------------------------------------------------------------
# 5. 负向变异：隔离副本恢复旧 parents[3]，两态变红；恢复后转绿
# ---------------------------------------------------------------------------

def _mutate_to_legacy_parents(src: str, subdir: str) -> str:
    fixed = f'return Path(AGENT_DIR) / "resources" / "{subdir}"'
    legacy = f'return Path(__file__).resolve().parents[3] / "resources" / "{subdir}"'
    assert fixed in src, "未找到修复点 return 行——变异锚点失效"
    return src.replace(fixed, legacy)


@pytest.mark.parametrize(
    ("family", "layout"),
    [(f, layout) for f in sorted(FAMILIES) for layout in ("dev", "package")],
)
def test_legacy_parents_expression_is_red_in_both_layouts(
    family: str, layout: str, tmp_path: Path
) -> None:
    """§4.2.5：旧表达式在开发/包两态都解析到非 authority（修复点变红）。"""
    spec = FAMILIES[family]
    subdir = spec["subdir"]
    agent = _fake_agent_root(tmp_path)
    authority = agent / "resources" / subdir / "legacy"
    src = (SCRIPTS_DIR / family / "_lib.py").read_text(encoding="utf-8")
    mutated = _mutate_to_legacy_parents(src, subdir)

    if layout == "dev":
        # 开发（扁平）布局副本：<agent>/scripts/<family>/_lib.py → parents[3] = tmp_path。
        lib_dir = agent / "scripts" / family
        (lib_dir / "_lib.py").write_text(mutated, encoding="utf-8")
        legacy_root = tmp_path / "resources" / subdir
    else:
        # 包布局副本：<cache>/<family>/<version>/_lib.py → parents[3] = tmp_path。
        lib_dir = tmp_path / "pkg-cache" / family / "x.y.z"
        lib_dir.mkdir(parents=True)
        (lib_dir / "_lib.py").write_text(mutated, encoding="utf-8")
        legacy_root = tmp_path / "resources" / subdir

    resolved = _resolve(lib_dir, {}, agent_pythonpath=agent, cwd=lib_dir)
    assert resolved == legacy_root / "legacy", "旧表达式落点与预期不符（变异未生效）"
    assert resolved != authority, f"{layout} 态旧表达式未变红：{resolved}"

    # 恢复修复点（未变异的真实树）→ 转绿。
    restored = _resolve(SCRIPTS_DIR / family, {}, agent_pythonpath=agent)
    assert restored == authority, "恢复修复后未回到 authority"


# ---------------------------------------------------------------------------
# 4/6. 现存业务：MTBF 端到端、精确 APK、suite/results、marker、GPU companion
# ---------------------------------------------------------------------------

class _FakeTime:
    """可控时钟：sleep 不阻塞；time() 单调递增，保证 wait 循环可终止。"""

    def __init__(self) -> None:
        self._now = 0.0

    def sleep(self, _seconds: float) -> None:
        return None

    def time(self) -> float:
        self._now += 1.0
        return self._now


def test_mtbf_consumer_end_to_end_with_fake_adb(tmp_path: Path, monkeypatch) -> None:
    """§4.2.1/4/6：真实 mtbf_setup._run 消费 project + 三个精确 APK + 精确 suite。"""
    entry = _load_family_entry("mtbf_setup")

    project = "LegacyProject"
    nfs = tmp_path / "nfs"
    suite = nfs / "mtbf" / project
    suite.mkdir(parents=True)
    (suite / "runtask.xml").write_text(
        '<runtask name="x" times="3"><testpoint name="tp"/></runtask>', encoding="utf-8"
    )
    (suite / "UiAutomatorTestData.xml").write_text("<data/>", encoding="utf-8")

    rdir = tmp_path / "resources-roots" / "mtbf" / project
    _plant_apks(
        rdir,
        (
            "ReliabilityUiautomatorTest.apk",
            "ReliabilityUiautomatorTestTest.apk",
            "OfflineScriptManager.apk",
        ),
    )

    serial = f"B4-MTBF-{os.getpid()}"
    marker = Path(tempfile.gettempdir()) / f"mtbf_run_dir_{serial}.json"
    marker.unlink(missing_ok=True)
    monkeypatch.setenv("STP_AEE_NFS_ROOT", str(nfs))
    monkeypatch.setenv("STP_DEVICE_SERIAL", serial)
    monkeypatch.setattr(entry, "time", _FakeTime())

    ls_calls = {"n": 0}

    def fake_adb(*args, timeout=60):
        if args[0] == "root":
            return 0, "", ""
        if args[0] == "push":
            return 0, "1 file pushed", ""
        if args[0] == "shell":
            cmd = args[1]
            if cmd.strip() == "id -u":
                return 0, "0\n", ""
            if cmd.startswith("dumpsys package"):
                return 0, "sharedUser=android.uid.system", ""
            if cmd.startswith("ls /sdcard/results/realresult/"):
                ls_calls["n"] += 1
                if ls_calls["n"] == 1:  # 启动前快照：只有旧目录
                    return 0, "old-dir\n", ""
                return 0, "old-dir\n20260825-120000\n", ""
            if cmd.startswith("stat -c %Y /sdcard/results/realresult/old-dir"):
                return 0, "1000\n", ""
            if cmd.startswith("stat -c %Y"):
                return 0, "2000\n", ""
            return 0, "", ""
        return 0, "", ""

    monkeypatch.setattr(entry, "adb", fake_adb)
    monkeypatch.setattr(
        entry, "adb_shell", lambda cmd, timeout=60: fake_adb("shell", cmd, timeout=timeout)[1]
    )

    try:
        metrics = entry._run(
            {
                "mtbf_resources_dir": str(tmp_path / "resources-roots" / "mtbf"),
                "project": project,
                "install_apks": False,
                "auto_resume": False,
                "task_times": 5,
            }
        )
        marker_payload = json.loads(marker.read_text()) if marker.is_file() else None
    finally:
        marker.unlink(missing_ok=True)

    assert set(metrics["apk_sha256"]) == {
        "ReliabilityUiautomatorTest.apk",
        "ReliabilityUiautomatorTestTest.apk",
        "OfflineScriptManager.apk",
    }, "APK 精确名或 project 消费漂移"
    assert metrics["round_expected"] == 5
    assert metrics["run_dir"] == "20260825-120000", "run_dir 新鲜度判据回归"
    # suite/results authority 不变（旧 finish 1.4.0 依赖同一路径）。
    lib = _load_family_lib("mtbf_setup")
    assert lib.suite_dir(project) == suite
    assert lib.results_dir(project) == suite / "results"
    assert marker_payload == {"project": project, "run_dir": "20260825-120000"}, (
        "setup 未写同形 run_dir marker（旧载入器/check 组合依赖）"
    )


def test_mtbf_marker_legacy_format_readable_and_check_state_untouched(
    tmp_path: Path, monkeypatch
) -> None:
    """§4.2.6：旧 run_dir marker 格式可读；不触碰旧 check@1.2.0 的 per-device state。"""
    lib = _load_family_lib("mtbf_setup")
    serial = f"B4-MARKER-{os.getpid()}"
    monkeypatch.setenv("STP_DEVICE_SERIAL", serial)
    marker = Path(tempfile.gettempdir()) / f"mtbf_run_dir_{serial}.json"
    legacy_state = Path(tempfile.gettempdir()) / f"mtbf_check_{serial}.json"
    marker.unlink(missing_ok=True)
    legacy_state.unlink(missing_ok=True)

    try:
        # 旧 setup v1.4.0/v1.4.1 写出的载荷形态（无版本字段）必须仍可读。
        marker.write_text(json.dumps({"project": "legacy", "run_dir": "20260801-000000"}))
        assert lib.load_run_dir("legacy") == "20260801-000000"
        assert lib.load_run_dir("other-project") == ""
        # 新 setup 覆写后仍是无版本字段的同形载荷；旧 check 自身 state 不受影响。
        legacy_state.write_text(json.dumps({"dead_streak": 0, "seq": 7}))
        lib.save_run_dir("legacy", "20260825-120000")
        assert json.loads(marker.read_text()) == {"project": "legacy", "run_dir": "20260825-120000"}
        assert json.loads(legacy_state.read_text()) == {"dead_streak": 0, "seq": 7}, (
            "新 setup 改写了旧 check 的 per-device state"
        )
    finally:
        marker.unlink(missing_ok=True)
        legacy_state.unlink(missing_ok=True)


def test_gpu_package_companion_pushed_from_package_dir(tmp_path: Path, monkeypatch) -> None:
    """§4.2.4：GPU 包内 companion `_gpu_stress_loop.sh` 以本包根定位（两态同源）。"""
    family = "gpu_setup"
    pkg_dir = tmp_path / "cache" / family / "x.y.z"
    pkg_dir.mkdir(parents=True)
    shutil.copy2(SCRIPTS_DIR / family / "_lib.py", pkg_dir / "_lib.py")
    shutil.copy2(SCRIPTS_DIR / family / "_gpu_stress_loop.sh", pkg_dir / "_gpu_stress_loop.sh")

    spec = importlib.util.spec_from_file_location("b4_gpu_lib_pkg", pkg_dir / "_lib.py")
    assert spec is not None and spec.loader is not None
    lib = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lib)

    pushed: list[tuple] = []

    def fake_adb(*args, timeout=60):
        pushed.append(args)
        return 0, "", ""

    monkeypatch.setattr(lib, "adb", fake_adb)
    monkeypatch.setattr(lib, "adb_shell", lambda cmd, timeout=60: "")
    lib.push_device_script()
    assert pushed and pushed[0][0] == "push"
    assert Path(pushed[0][1]) == pkg_dir / "_gpu_stress_loop.sh", "companion 不再以本包根定位"


def test_powercycle_sleep_consumer_apk_and_suite_authority(tmp_path: Path, monkeypatch) -> None:
    """§4.2.4：PowerCycle/Sleep 精确 APK 名 + suite/results authority 不变。"""
    agent = _fake_agent_root(tmp_path)
    nfs = tmp_path / "nfs"
    monkeypatch.setenv("STP_AEE_NFS_ROOT", str(nfs))
    for family, subdir in (("powercycle_setup", "power-cycle"), ("sleep_setup", "sleep")):
        project = "legacy"
        rdir = agent / "resources" / subdir / project
        _plant_apks(rdir, ("AutoTestTool.apk",))
        entry = _load_family_entry(family)
        assert entry._APK_NAME == "AutoTestTool.apk"
        resolved = _resolve(
            SCRIPTS_DIR / family,
            {},
            agent_pythonpath=agent,
            extra_env={"STP_AEE_NFS_ROOT": str(nfs)},
        )
        assert resolved == rdir
        assert (resolved / entry._APK_NAME).is_file(), "consumer 在 authority 下取不到 APK"
        lib = _load_family_lib(family)
        assert lib.suite_dir(project) == nfs / subdir / project
        assert lib.results_dir(project) == nfs / subdir / project / "results"


def test_gpu_legacy_config_shapes_resolve_as_before(tmp_path: Path) -> None:
    """§4.2.6：生产 Plan 的旧 GPU 配置形态（含/不含 `/mnt/stp-aee/gpu`）不变。"""
    agent = _fake_agent_root(tmp_path)
    lib = SCRIPTS_DIR / "gpu_setup"

    overridden = _resolve(
        lib,
        {"gpu_resources_dir": "/mnt/stp-aee/gpu", "project": "legacy"},
        agent_pythonpath=agent,
    )
    assert overridden == Path("/mnt/stp-aee/gpu/legacy"), "旧 override 路径语义被改写"

    empty = _resolve(lib, {"project": "legacy"}, agent_pythonpath=agent)
    assert empty == agent / "resources" / "gpu" / "legacy", "空配置未落 Agent authority"
    # variant 目录仍挂在 project 之下（consumer 第二段路径不变）。
    assert empty / "Antutu_v10_Lite" == agent / "resources" / "gpu" / "legacy" / "Antutu_v10_Lite"


# ---------------------------------------------------------------------------
# 包身份 / 包成员 / 模板 pin 未动
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("family", sorted(FAMILIES))
def test_package_identity_and_members_match_manifest(family: str, tmp_path: Path) -> None:
    """§4.2.7：真实 builder 重建 sha == manifest 新 head；成员 == git 跟踪成员。"""
    packer = _load_tool("b4_packer_members_3320", "tools/dev/package_tool_asset.py")
    checker = _load_tool("b4_checker_members_3320", "tools/dev/check_script_packages.py")
    tar_path, facts = _build_family_tar(family, tmp_path)
    head = _manifest_head(family)
    assert facts["package_sha256"] == head["package_sha256"]
    assert facts["script"] == head["script"]
    assert head["python"] is None
    with tarfile.open(tar_path, "r:gz") as tar:
        members = {m.name for m in tar.getmembers()}
    tracked = {str(p) for p in checker.tracked_files(SCRIPTS_DIR / family, packer)}
    assert members == tracked, "包成员与 git 跟踪面不一致"
    assert "_lib.py" in members, "共享库未入包"


def _iter_action_versions(obj: object, action: str):
    if isinstance(obj, dict):
        if obj.get("action") == action:
            yield obj.get("version")
        for value in obj.values():
            yield from _iter_action_versions(value, action)
    elif isinstance(obj, list):
        for item in obj:
            yield from _iter_action_versions(item, action)


def test_templates_untouched_by_g1a() -> None:
    """§4.2.8：三个模板 setup pin 仍为保留的旧 active 版本（G1a 不改 pin）。"""
    expected = {
        "gpu.json": ("script:gpu_setup", "1.2.3"),
        "powercycle.json": ("script:powercycle_setup", "1.2.8"),
        "sleep.json": ("script:sleep_setup", "1.0.5"),
    }
    for name, (action, version) in expected.items():
        data = json.loads((TEMPLATES_DIR / name).read_text(encoding="utf-8"))
        assert list(_iter_action_versions(data, action)) == [version], (
            f"{name} 的 {action} pin 被改动"
        )
