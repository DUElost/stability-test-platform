"""aee_prepare v1.0.0：荣耀测试前 AEE/日志配置准备。

验证：
- 命令序列（dev-settings → monkey probe → aee before → root →
  setprop → 核验 → logger 广播 ×3 → dev-settings 恢复）；
- mode 设置失败 → 整体失败（error_message 含 setprop）；
- logger 广播失败 → warning 不阻断（success 仍 true）；
- 输出契约（stdout JSON success/metrics）。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

_SCRIPT_DIR = (
    Path(__file__).resolve().parents[2]
    / "agent" / "scripts" / "aee_prepare" / "v1.0.0"
)

# 脚本目录自带 _adb.py——加入 sys.path 供 `from _adb import ...` 解析
sys.path.insert(0, str(_SCRIPT_DIR))

spec = importlib.util.spec_from_file_location(
    "aee_prepare_v100", _SCRIPT_DIR / "aee_prepare.py"
)
ap = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(ap)


class _FakeProc:
    def __init__(self, rc: int, stdout: str) -> None:
        self.returncode = rc
        self.stdout = stdout


def _wire(monkeypatch, *, mode_after: str = "3", setprop_rc: int = 0,
          broadcast_rc: int = 0, broadcast_out: str = "result=0"):
    """stub _shell/_run_adb，记录调用序列。"""
    calls: list[str] = []

    def fake_shell(serial, command, timeout=30):
        calls.append(f"shell:{command}")
        if "getprop" in command and "aee.mode" in command:
            if "before" in calls[-1] or len([c for c in calls if "getprop" in c]) == 1:
                return 0, "4\n"
            return setprop_rc, f"{mode_after}\n"
        if "grep monkey" in command:
            return 0, "root 12345 1 ... com.example.monkey\n"
        if "setprop" in command:
            return setprop_rc, ""
        return 0, ""

    def fake_run_adb(serial, args, timeout=30):
        calls.append(f"adb:{' '.join(args)}")
        if args and args[0] == "root":
            return 0, ""
        if args and args[0] == "shell" and "broadcast" in " ".join(args):
            return broadcast_rc, broadcast_out
        return 0, ""

    monkeypatch.setattr(ap, "_shell", fake_shell)
    monkeypatch.setattr(ap, "_run_adb", fake_run_adb)
    return calls


class TestAeePrepare:
    def test_success_sequence_and_metrics(self, monkeypatch, capsys):
        calls = _wire(monkeypatch)
        monkeypatch.setenv("STP_DEVICE_SERIAL", "S1")
        ap.main()
        out = capsys.readouterr().out.strip()
        payload = json.loads(out)
        assert payload["success"] is True
        m = payload["metrics"]
        assert m["aee_mode_before"] == "4"
        assert m["aee_mode_after"] == "3"
        assert m["aee_mode_set_ok"] is True
        assert m["monkey_processes"] and "monkey" in m["monkey_processes"][0]
        assert m["logger_broadcasts"] == {
            "start": True, "total_log_size": True, "sublog": True}
        assert m["logger_warnings"] == []
        # 序列顺序：dev-settings-on 在 setprop 前、广播在 setprop 后
        joined = " | ".join(calls)
        assert joined.index("settings put global development_settings_enabled 1") < \
               joined.index("setprop persist.vendor.mtk.aee.mode 3")
        assert joined.index("setprop persist.vendor.mtk.aee.mode 3") < \
               joined.index("am broadcast")

    def test_setprop_failure_fails_run(self, monkeypatch, capsys):
        _wire(monkeypatch, setprop_rc=1, mode_after="4")
        monkeypatch.setenv("STP_DEVICE_SERIAL", "S1")
        ap.main()
        out = capsys.readouterr().out.strip()
        payload = json.loads(out)
        assert payload["success"] is False
        assert "setprop" in payload["error_message"]
        assert payload["metrics"]["aee_mode_set_ok"] is False

    def test_broadcast_failure_is_warning_not_fatal(self, monkeypatch, capsys):
        _wire(monkeypatch, broadcast_rc=1, broadcast_out="Error")
        monkeypatch.setenv("STP_DEVICE_SERIAL", "S1")
        ap.main()
        out = capsys.readouterr().out.strip()
        payload = json.loads(out)
        assert payload["success"] is True  # 最佳努力：不阻断
        assert len(payload["metrics"]["logger_warnings"]) == 3
        assert payload["metrics"]["logger_broadcasts"]["start"] is False

    def test_progress_stamps_emitted(self, monkeypatch, capsys):
        _wire(monkeypatch)
        monkeypatch.setenv("STP_DEVICE_SERIAL", "S1")
        ap.main()
        err = capsys.readouterr().err
        stamps = [ln for ln in err.splitlines() if ln.startswith("PROGRESS ")]
        assert len(stamps) >= 6
        assert '"stage": "aee-mode-set"' in err
