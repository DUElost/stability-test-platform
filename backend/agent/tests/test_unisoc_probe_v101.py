"""unisoc_probe v1.0.1：#73 展锐异常通道只读取证脚本。

验证（对照 backend/agent/tests/test_aee_prepare_v100.py 的设备脚本测试范式）：
- 命令序列为**固定只读集**（不提供任意命令入口）；
- 路径清单逐条 `ls`，缺失路径如实标 exists=False（不猜）；
- prop 扫描与 deep 取证都进输出；
- 缺 STP_DEVICE_SERIAL 或设备不可达 → success=false（不静默）。
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
from pathlib import Path

_SCRIPT_DIR = (
    Path(__file__).resolve().parents[2] / "agent" / "scripts" / "unisoc_probe" / "v1.0.1"
)

spec = importlib.util.spec_from_file_location(
    "unisoc_probe_v101", _SCRIPT_DIR / "unisoc_probe.py"
)
up = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(up)


class _FakeProc:
    def __init__(self, rc: int, stdout: str) -> None:
        self.returncode = rc
        self.stdout = stdout
        self.stderr = ""


def _wire(monkeypatch, *, reachable: bool = True, responses: dict | None = None):
    """stub subprocess.run，记录命令序列；按关键字给出可预期输出。"""
    calls: list[str] = []
    table = responses or {}

    def _run(cmd, capture_output, text, timeout):  # noqa: ANN001
        shell_cmd = cmd[-1]
        calls.append(shell_cmd)
        if not reachable and shell_cmd.strip() == "echo ok":
            return _FakeProc(1, "")
        for key, out in table.items():
            if key in shell_cmd:
                return _FakeProc(0, out)
        if shell_cmd.strip() == "echo ok":
            return _FakeProc(0, "ok")
        if shell_cmd.strip() == "id -u":
            return _FakeProc(0, "0")
        return _FakeProc(0, "")

    monkeypatch.setattr(up.subprocess, "run", _run)
    return calls


def _capture(monkeypatch, argv_serial: str | None):
    """跑 main()，返回 (stdout_json, SystemExit code)。"""
    monkeypatch.setattr(up, "_serial", lambda: argv_serial or "")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        try:
            up.main()
            code = 0
        except SystemExit as exc:  # 脚本用 exit(1) 表达失败
            code = exc.code
    lines = [ln for ln in buf.getvalue().splitlines() if ln.strip()]
    return (json.loads(lines[-1]) if lines else {}), code


class TestUnisocProbeV101:
    def test_missing_serial_fails(self, monkeypatch):
        _wire(monkeypatch)
        payload, code = _capture(monkeypatch, None)
        assert code == 1
        assert payload["success"] is False
        assert "STP_DEVICE_SERIAL" in payload["error_message"]

    def test_unreachable_device_fails(self, monkeypatch):
        _wire(monkeypatch, reachable=False)
        payload, code = _capture(monkeypatch, "SERIAL1")
        assert code == 1
        assert payload["success"] is False
        assert "unreachable" in payload["error_message"]

    def test_lists_paths_and_marks_missing(self, monkeypatch):
        _wire(monkeypatch, responses={
            "ls -la /data/uniview ": "total 14\ndrwxrwxr-x logs",
            "ls -la /data/unisoc_log": "ls: /data/unisoc_log: No such file or directory",
            "wc -l": "1",
        })
        payload, code = _capture(monkeypatch, "SERIAL1")
        assert code == 0
        metrics = payload["metrics"]
        assert payload["success"] is True
        assert metrics["serial"] == "SERIAL1"
        # 真机实测存在的根 → exists True
        assert metrics["paths"]["/data/uniview"]["exists"] is True
        # issue 假设、真机不存在 → exists False（不猜）
        assert metrics["paths"]["/data/unisoc_log"]["exists"] is False

    def test_deep_section_covers_event_source_candidates(self, monkeypatch):
        _wire(monkeypatch, responses={"uniview_tree": "logs\ntmp"})
        payload, _ = _capture(monkeypatch, "SERIAL1")
        deep = payload["metrics"]["deep"]
        # 定位 uniview 真源的四类取证都在（树/进程/prop/init 配置）
        assert "uniview_tree" in deep
        assert "uniview_proc" in deep
        assert "uniview_props" in deep
        assert "uniview_init" in deep

    def test_no_arbitrary_command_interface(self):
        """只读固定命令集：不接受外部传入命令（避免越权面）。"""
        assert not hasattr(up, "run_command")
        assert isinstance(up._DEEP, dict)
        assert all(isinstance(v, str) for v in up._DEEP.values())
