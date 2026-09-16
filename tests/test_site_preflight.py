from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from tools.site_config.ops import CommandResult

REPO_ROOT = Path(__file__).resolve().parents[1]
from tools.site_config.preflight import run_preflight
from tools.site_config.validation import Check


class FakeOps:
    """最小 Ops 替身：只覆盖 preflight 真正读的那几个面。"""

    def __init__(
        self,
        *,
        hostname: str = "control.synthetic.invalid",
        commands: set[str] | None = None,
        addresses: set[str] | None = None,
        responses: dict[str, tuple[int, str]] | None = None,
        distribution: str = "debian",
        machine: str = "x86_64",
    ):
        self.calls: list[tuple[str, ...]] = []
        self._hostname = hostname
        self._commands = set(commands or ("python3", "systemctl", "nginx", "ansible-playbook", "sshpass", "ssh-keyscan"))
        self._addresses = set(addresses or {"127.0.0.1"})
        self._responses = responses or {}
        self._distribution = distribution
        self._machine = machine

    def run(self, argv, *, env=None, input_text=None, cwd=None):
        argv = tuple(str(item) for item in argv)
        self.calls.append(argv)
        for needle, (code, output) in self._responses.items():
            if needle in " ".join(argv):
                return CommandResult(argv, code, output)
        return CommandResult(argv, 0, "")

    def hostname(self) -> str:
        return self._hostname

    def local_addresses(self) -> set[str]:
        return set(self._addresses)

    def route_address(self) -> str:
        return ""

    def os_release(self) -> dict[str, str]:
        return {"ID": self._distribution, "VERSION_ID": "13"}

    def machine(self) -> str:
        return self._machine

    def timezone(self) -> str:
        return "Asia/Shanghai"

    def command_exists(self, name: str) -> bool:
        return name in self._commands


def checks_by_id(report: dict) -> dict[str, dict]:
    return {check["check_id"]: check for check in report["checks"]}


def healthy_ops(**overrides) -> FakeOps:
    defaults = dict(
        responses={
            "timedatectl": (0, "yes\n"),
            "redis-cli": (0, "PONG\n"),
        },
    )
    defaults.update(overrides)
    return FakeOps(**defaults)


def empty_probe(url: str) -> tuple[str, str | None]:
    return "empty", None


def test_preflight_is_read_only_and_reports_every_item():
    ops = healthy_ops()
    report = run_preflight(ops=ops)
    assert report["stage"] == "preflight"
    assert report["deferred_checks"] == []
    # 只读：没有任何写命令（mount/createdb/useradd/tee/install 一律不允许出现）
    for call in ops.calls:
        assert not {"mount", "createdb", "useradd", "tee", "install", "systemctl"} & set(call)
    found = checks_by_id(report)
    assert found["preflight.platform"]["status"] == "PASS"
    assert found["preflight.toolenv"]["status"] == "PASS"
    # 未给输入的两项既不 PASS 也不 FAIL，避免「没验证」被当成通过
    assert found["preflight.database"]["status"] == "BLOCKED"
    assert found["preflight.redis"]["status"] == "BLOCKED"


def test_unsupported_platform_names_the_host_and_fix():
    report = run_preflight(ops=FakeOps(distribution="centos"))
    check = checks_by_id(report)["preflight.platform"]
    assert check["status"] == "FAIL"
    assert "centos" in check["message"]
    assert check["remediation"]


def test_missing_base_commands_are_listed_by_name():
    ops = FakeOps(commands={"python3", "systemctl", "ansible-playbook", "sshpass", "ssh-keyscan"})
    check = checks_by_id(run_preflight(ops=ops))["preflight.dependencies"]
    assert check["status"] == "FAIL"
    assert "nginx" in check["message"]


def test_missing_agent_path_commands_block_agent_onboarding():
    ops = FakeOps(commands={"python3", "systemctl", "nginx"})
    check = checks_by_id(run_preflight(ops=ops))["preflight.agent_commands"]
    assert check["status"] == "BLOCKED"
    assert "ansible-playbook" in check["message"]
    assert "sshpass" in check["message"]


def test_unsynchronized_clock_fails_with_the_observed_value():
    ops = healthy_ops(responses={"timedatectl": (0, "no\n")})
    check = checks_by_id(run_preflight(ops=ops))["preflight.time"]
    assert check["status"] == "FAIL"
    assert "no" in check["message"]


def test_database_probe_states_are_distinguished():
    states = {
        "empty": "PASS",
        "managed": "PASS",
        "unreachable": "FAIL",
        "unmanaged": "FAIL",
        "driver_missing": "FAIL",
    }
    for state, expected in states.items():
        report = run_preflight(db_url="postgresql+psycopg://u:p@127.0.0.1:5432/db", probe=lambda url, s=state: (s, "head"))
        check = checks_by_id(report)["preflight.database"]
        assert check["status"] == expected, state


def test_redis_without_cli_names_the_missing_command(monkeypatch):
    """不依赖宿主是否装了 redis-cli：显式打桩，否则本地绿、CI 红。"""
    monkeypatch.setattr(
        "tools.site_config.preflight.shutil.which",
        lambda name: None if name == "redis-cli" else f"/usr/bin/{name}",
    )
    check = checks_by_id(run_preflight(redis_url="redis://127.0.0.1:6379/1", ops=healthy_ops()))["preflight.redis"]
    assert check["status"] == "FAIL"
    assert "redis-cli" in check["message"]
    assert check["remediation"]


def test_redis_without_pong_shows_the_observed_output(monkeypatch):
    monkeypatch.setattr("tools.site_config.preflight.shutil.which", lambda name: "/usr/bin/redis-cli")
    ops = healthy_ops(responses={"redis-cli": (0, "")})
    check = checks_by_id(run_preflight(redis_url="redis://127.0.0.1:6379/1", ops=ops))["preflight.redis"]
    assert check["status"] == "FAIL"
    assert "PONG" in check["message"]
    assert "db 1" in check["message"]


def test_bindings_directory_permissions_are_enforced(tmp_path):
    good = tmp_path / "bindings"
    good.mkdir(mode=0o700)
    assert checks_by_id(run_preflight(bindings_dir=good, ops=healthy_ops()))["preflight.bindings"]["status"] == "PASS"

    loose = tmp_path / "loose"
    loose.mkdir(mode=0o755)
    check = checks_by_id(run_preflight(bindings_dir=loose, ops=healthy_ops()))["preflight.bindings"]
    assert check["status"] == "FAIL"
    assert "0o755" in check["message"] or "755" in check["message"]

    check = checks_by_id(run_preflight(bindings_dir=tmp_path / "absent", ops=healthy_ops()))["preflight.bindings"]
    assert check["status"] == "FAIL"
    assert "does not exist" in check["message"]


def test_bundle_layout_is_checked_with_the_missing_paths(tmp_path):
    bundle = tmp_path / "bundle"
    (bundle / "backend").mkdir(parents=True)
    check = checks_by_id(run_preflight(bundle=bundle, ops=healthy_ops()))["preflight.bundle"]
    assert check["status"] == "FAIL"
    assert "frontend/dist-prod" in check["message"]
    assert "backend/agent/resources" in check["message"]
    assert "release-manifest.json" in check["message"]

    from tools.site_config.preflight import BUNDLE_REQUIRED

    for name in BUNDLE_REQUIRED:
        target = bundle / name
        if "." in Path(name).name:      # 文件（release-manifest.json 等）
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("", encoding="utf-8")
        else:                            # 目录（backend、backend/agent/resources 等）
            target.mkdir(parents=True, exist_ok=True)
    assert checks_by_id(run_preflight(bundle=bundle, ops=healthy_ops()))["preflight.bundle"]["status"] == "PASS"


def test_existing_deploy_root_is_reported_as_resumable(tmp_path):
    marker_root = tmp_path / "stp-city-b"
    marker_root.mkdir()
    (marker_root / ".stp-site.json").write_text("{}\n", encoding="utf-8")
    report = run_preflight(deploy_root=marker_root, ops=healthy_ops())
    assert checks_by_id(report)["preflight.existing_site"]["code"] == "site_marker_present"

    report = run_preflight(deploy_root=tmp_path / "empty", ops=healthy_ops())
    assert checks_by_id(report)["preflight.existing_site"]["code"] == "deploy_root_empty"


def test_busy_entry_ports_are_reported_from_the_kernel_table(monkeypatch):
    monkeypatch.setattr(
        "tools.site_config.preflight._listening_ports", lambda: {80, 22},
    )
    check = checks_by_id(run_preflight(ops=healthy_ops()))["preflight.ports"]
    assert check["status"] == "FAIL"
    assert "80" in check["message"]


def test_suggested_entry_prefers_the_routed_address():
    """有路由时用路由地址：接口列表里可能有 docker0/网桥这类误导项。"""
    ops = healthy_ops(addresses={"127.0.0.1", "172.17.0.1"})
    ops.route_address = lambda: "192.0.2.5"
    assert run_preflight(ops=ops)["suggested_public_url"] == "http://192.0.2.5"

    # 无路由时才退回接口列表
    assert run_preflight(ops=healthy_ops(addresses={"127.0.0.1", "192.0.2.6"}))["suggested_public_url"] == "http://192.0.2.6"
    assert run_preflight(ops=healthy_ops(addresses={"127.0.0.1"}))["suggested_public_url"] == ""


def test_resource_floor_names_the_observed_numbers(monkeypatch):
    monkeypatch.setattr("tools.site_config.preflight.os.cpu_count", lambda: 1)
    check = checks_by_id(run_preflight(ops=healthy_ops()))["preflight.resources"]
    assert check["status"] == "FAIL"
    assert "1 cores" in check["message"]


def test_report_status_is_fail_only_for_actual_failures(monkeypatch):
    # 端口项读的是真实内核表：测试机（生产控制面）上 80/8000 在用，必须打桩
    monkeypatch.setattr("tools.site_config.preflight._listening_ports", set)
    assert run_preflight(ops=healthy_ops())["status"] == "PASS"
    failing = healthy_ops(responses={"timedatectl": (0, "no\n")})
    assert run_preflight(ops=failing)["status"] == "FAIL"


def test_checks_are_check_instances_with_remediation():
    report = run_preflight(ops=healthy_ops(), db_url="postgresql+psycopg://u:p@127.0.0.1:5432/db", probe=empty_probe)
    for check in report["checks"]:
        assert set(check) == {"check_id", "role", "status", "location", "code", "message", "remediation"}
        assert check["status"] in {"PASS", "FAIL", "BLOCKED"}
        assert check["remediation"]


def test_preflight_module_exposes_no_write_helpers():
    """零写入是契约：模块里不应出现写文件/建目录的调用。"""
    source = (Path(__file__).resolve().parents[1] / "tools/site_config/preflight.py").read_text(encoding="utf-8")
    for forbidden in ("write_text", "os.makedirs", "mkdir(", "open(", "subprocess"):
        assert forbidden not in source
    assert isinstance(run_preflight(ops=healthy_ops())["checks"][0], dict)
    assert Check  # 校验构造器仍由 validation 提供，preflight 只组装


def test_preflight_runs_without_installer_dependencies():
    """裸机（还没装 pydantic/yaml/psycopg）也必须给出逐项报告，而不是回溯。

    `-S` 不加载 site-packages，等价于「刚 clone 下来的机器」。缺依赖本身就是
    preflight 要报的一项（preflight.toolenv），所以它自己不能依赖那些库。
    """
    result = subprocess.run(
        [sys.executable, "-S", "-m", "tools.site_config", "preflight"],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=90, check=False,
    )
    output = result.stdout + result.stderr
    assert "Traceback" not in output, output
    assert "preflight.toolenv" in output
    assert "installer environment is missing" in output
    assert result.returncode == 1


def test_database_probe_degrades_when_dependencies_are_absent():
    result = subprocess.run(
        [sys.executable, "-S", "-m", "tools.site_config", "preflight",
         "--db-url", "postgresql+psycopg://user:pass@127.0.0.1:5432/empty"],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=90, check=False,
    )
    output = result.stdout + result.stderr
    assert "Traceback" not in output, output
    # 探测不了就如实 FAIL（带同一个 toolenv 修复线索），绝不谎报或崩溃
    assert "preflight.database" in output


def test_checks_module_stays_dependency_free():
    source = (REPO_ROOT / "tools/site_config/checks.py").read_text(encoding="utf-8")
    for dependency in (
        "import pydantic", "from pydantic", "import yaml", "from yaml",
        "import psycopg", "from psycopg",
    ):
        assert dependency not in source, dependency


def test_main_module_only_imports_preflight_at_module_level():
    """其余子命令在分支内延迟导入，避免把重依赖拉进 preflight 路径。"""
    text = (REPO_ROOT / "tools/site_config/__main__.py").read_text(encoding="utf-8")
    head = text.split("class RedactedParser", 1)[0]
    imports = [line for line in head.splitlines() if line.startswith("from .")]
    assert imports == ["from .preflight import run_preflight"]
