"""#2802：powercycle_setup **v1.2.2**——install 重试前等「系统就绪」（boot_completed 门）。

r477 取证（首个带 v1.0.2 证据的开关机窗）：残差 59 条里 21 条
``pm install: Error: device is still booting`` + 19 条 ``push: ... device not found``
——v1.2.0/v1.2.1 的 ``wait-for-device`` 只等 adbd 可见（adbd 在 boot 早期即在线），
10s 退避又短于设备侧 ~75s 重启周期 ⇒ 重试仍落在重启窗内。

本文件钉住 v1.2.2 的四条新语义 + v1.2.1 的对照锚点（旧版本不可变，只读断言）。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPTS = REPO_ROOT / "backend" / "agent" / "scripts"


def _load(name: str, rel_path: str):
    path = _SCRIPTS / rel_path
    sys.path.insert(0, str(path.parent))
    try:
        sys.modules.pop("_lib", None)
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec and spec.loader, f"cannot locate {path}"
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.modules.pop("_lib", None)
        sys.path.remove(str(path.parent))


@pytest.fixture(scope="module")
def lib_v122():
    return _load("powercycle_lib_v122", "powercycle_setup/_lib.py")


@pytest.fixture(scope="module")
def lib_v121():
    """对照锚点：v1.2.1 不可变，只读加载以钉住「只等 adbd」的旧形态。"""
    return _load("powercycle_lib_v121_anchor", "powercycle_setup/_lib.py")


class _FakeAdb:
    """按命令子串派发假 adb；队列用尽后复用最后一个响应。"""

    def __init__(self, routes: dict[str, list]):
        self.routes = {key: list(value) for key, value in routes.items()}
        self.calls: list[list] = []

    def __call__(self, *args, timeout=60):
        self.calls.append([str(a) for a in args])
        cmd = " ".join(str(a) for a in args)
        for needle, seq in self.routes.items():
            if needle in cmd:
                if not seq:
                    return (0, "", "")
                return seq.pop(0) if len(seq) > 1 else seq[0]
        return (0, "", "")

    def count(self, needle: str) -> int:
        return sum(1 for call in self.calls if needle in " ".join(call))


def _patch_clock(monkeypatch, mod, *, start: float = 1_000_000.0):
    """假时钟：sleep 推进 time.time()，避免测试真等 10s/90s。"""
    state = {"now": start, "sleeps": []}

    def fake_time() -> float:
        return state["now"]

    def fake_sleep(seconds: float) -> None:
        state["sleeps"].append(float(seconds))
        state["now"] += float(seconds)

    monkeypatch.setattr(mod.time, "time", fake_time)
    monkeypatch.setattr(mod.time, "sleep", fake_sleep)
    return state


def _apk(tmp_path: Path) -> Path:
    path = tmp_path / "AutoTestTool.apk"
    path.write_bytes(b"fake-apk")
    return path


class TestV122FastPath:
    def test_success_first_attempt_never_waits(self, lib_v122, monkeypatch, tmp_path):
        """正常设备：首试 push+install 即成，不额外做就绪探测（耗时不变）。"""
        fake = _FakeAdb({
            "push": [(0, "1 file pushed", "")],
            "pm install": [(0, "Success", "")],
        })
        monkeypatch.setattr(lib_v122, "adb", fake)

        lib_v122.install_apk(_apk(tmp_path))

        assert fake.count("push") == 1
        assert fake.count("get-state") == 0
        assert fake.count("getprop") == 0


class TestV122RebootWindowAbsorbed:
    def test_not_found_then_ready_retry_succeeds(self, lib_v122, monkeypatch, tmp_path):
        """首试撞上重启窗（device not found）→ 等就绪 → 第二次成功。"""
        _patch_clock(monkeypatch, lib_v122)
        fake = _FakeAdb({
            "push": [
                (1, "", "adb: error: failed to get feature set: device 'S1' not found"),
                (0, "1 file pushed", ""),
            ],
            "pm install": [(0, "Success", "")],
            "get-state": [(0, "device", "")],
            "getprop": [(0, "1", "")],
        })
        monkeypatch.setattr(lib_v122, "adb", fake)

        lib_v122.install_apk(_apk(tmp_path))

        assert fake.count("push") == 2
        assert fake.count("get-state") >= 1, "重试前必须先等系统就绪"

    def test_boot_completed_zero_keeps_polling(self, lib_v122, monkeypatch, tmp_path):
        """get-state=device 但 boot_completed 尚未 1 ⇒ 继续轮询，不放行 push。"""
        _patch_clock(monkeypatch, lib_v122)
        fake = _FakeAdb({
            "push": [
                (1, "", "adb: device 'S1' not found"),
                (0, "1 file pushed", ""),
            ],
            "pm install": [(0, "Success", "")],
            "get-state": [(0, "device", "")],
            "getprop": [(0, "0", ""), (0, "1", "")],
        })
        monkeypatch.setattr(lib_v122, "adb", fake)

        lib_v122.install_apk(_apk(tmp_path))

        assert fake.count("getprop") >= 2, "boot_completed=0 时不得放行"
        assert fake.count("push") == 2


class TestV122EvidenceOnExhaustion:
    def test_budget_exhausted_reports_attempts_and_history(
        self, lib_v122, monkeypatch, tmp_path
    ):
        """设备一直不在 adb 视野 ⇒ 预算内等不到就绪 ⇒ 失败报文带 attempts/history + 原文。"""
        monkeypatch.setenv("STP_ATT_INSTALL_WAIT_BUDGET_SECONDS", "20")
        monkeypatch.setenv("STP_ATT_INSTALL_READY_SECONDS", "10")
        _patch_clock(monkeypatch, lib_v122)
        fake = _FakeAdb({
            "push": [(1, "", "adb: error: failed to get feature set: device 'S1' not found")],
            "get-state": [(1, "", "error: device 'S1' not found")],
        })
        monkeypatch.setattr(lib_v122, "adb", fake)

        with pytest.raises(RuntimeError) as exc:
            lib_v122.install_apk(_apk(tmp_path))

        msg = str(exc.value)
        assert "安装失败: AutoTestTool.apk:" in msg
        assert "device 'S1' not found" in msg, "保留 v1.2.0 起的原始证据"
        assert "attempts=" in msg and "history=[" in msg
        assert "not_ready" in msg

    def test_max_attempts_env_and_message_bounded(self, lib_v122, monkeypatch, tmp_path):
        """attempts 上限可由 env 收窄；超长输出经 _adb_diag 截断后报文仍有界。"""
        monkeypatch.setenv("STP_ATT_INSTALL_MAX_ATTEMPTS", "2")
        _patch_clock(monkeypatch, lib_v122)
        long_text = "y" * 5000
        fake = _FakeAdb({
            "push": [(0, "1 file pushed", "")],
            "pm install": [(1, long_text, long_text)],
            "get-state": [(0, "device", "")],
            "getprop": [(0, "1", "")],
        })
        monkeypatch.setattr(lib_v122, "adb", fake)

        with pytest.raises(RuntimeError) as exc:
            lib_v122.install_apk(_apk(tmp_path))

        msg = str(exc.value)
        assert "attempts=2/2" in msg
        assert fake.count("push") == 2
        assert len(msg) < 800

    def test_backoff_env_still_honored(self, lib_v122, monkeypatch, tmp_path):
        """v1.2.0 的退避 knob 保持有效（失败后重试前仍退避）。"""
        monkeypatch.setenv("STP_ATT_INSTALL_RETRY_BACKOFF_SECONDS", "7")
        clock = _patch_clock(monkeypatch, lib_v122)
        fake = _FakeAdb({
            "push": [(1, "", "adb: device 'S1' not found"), (0, "1 file pushed", "")],
            "pm install": [(0, "Success", "")],
            "get-state": [(0, "device", "")],
            "getprop": [(0, "1", "")],
        })
        monkeypatch.setattr(lib_v122, "adb", fake)

        lib_v122.install_apk(_apk(tmp_path))

        assert 7.0 in clock["sleeps"]


