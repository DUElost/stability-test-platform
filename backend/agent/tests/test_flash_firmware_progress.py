"""flash_firmware v1.1.0 的阶段打戳（#115 阶段 2 / #134）。

单元级测 `_run_flash_tool_with_progress` / `_emit_progress`（不 mock main 全流程，
main 的参数校验与打戳逻辑无关）：
  - 阶段关键字切换 → seq+1 打戳
  - 百分比 → percent 字段戳
  - 超时仍杀进程
"""

import importlib.util
import json
import os
import subprocess
import textwrap
from pathlib import Path

import pytest

_SCRIPT_DIR = Path(__file__).resolve().parents[2] / "agent" / "scripts" / "flash_firmware" / "v1.1.0"

spec = importlib.util.spec_from_file_location("flash_firmware_v110", _SCRIPT_DIR / "flash_firmware.py")
ff = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(ff)


@pytest.fixture
def fake_flash_tool(tmp_path: Path, request) -> Path:
    """假 flash_tool 可执行。param: phases / silent / hang"""
    variant = getattr(request, "param", "phases")
    tool = tmp_path / "flash_tool"
    body = textwrap.dedent(f"""\
        import sys, time
        variant = {variant!r}
        if variant == "hang":
            time.sleep(600)
        if variant == "phases":
            for line in ["DA handshake ok", "Download start", "45%", "88%", "VERIFY ok"]:
                print(line); sys.stdout.flush(); time.sleep(0.2)
        sys.exit(0)
    """)
    tool.write_text("#!/usr/bin/env python3\n" + body, encoding="utf-8")
    tool.chmod(0o755)
    return tool


def _run(tool: Path, *, timeout: int = 60, on_stage=None, on_percent=None) -> str:
    return ff._run_flash_tool_with_progress(
        [str(tool)], cwd=str(tool.parent), env=dict(os.environ),
        timeout=timeout,
        on_stage=on_stage or (lambda tok: None),
        on_percent=on_percent or (lambda pct: None),
    )


def _collect(capsys):
    err = capsys.readouterr().err
    return [
        json.loads(line[len("PROGRESS "):])
        for line in err.splitlines() if line.startswith("PROGRESS ")
    ]


class TestPhaseStamps:
    @pytest.mark.parametrize("fake_flash_tool", ["phases"], indirect=True)
    def test_phase_transitions_emit_stamps(self, fake_flash_tool, capsys):
        seq: list[int] = [0]
        _run(fake_flash_tool,
             on_stage=lambda tok: ff._emit_progress(seq, stage=tok),
             on_percent=lambda pct: ff._emit_progress(seq, percent=pct))
        stamps = _collect(capsys)
        assert stamps, "没有任何 PROGRESS 戳"
        # 阶段识别必须有 stage 字段的戳（percent 戳不算阶段推进）
        stage_stamps = [s for s in stamps if "stage" in s]
        assert stage_stamps, "没有阶段戳"
        assert any("DOWNLOAD" in s["stage"] for s in stage_stamps), "DOWNLOAD 阶段未被识别"
        seqs = [s["seq"] for s in stamps]
        assert seqs == sorted(seqs), "seq 必须递增"
        assert len(seqs) == len(set(seqs)), "seq 必须唯一"

    @pytest.mark.parametrize("fake_flash_tool", ["phases"], indirect=True)
    def test_percent_stamps_carry_percent(self, fake_flash_tool, capsys):
        seq: list[int] = [0]
        _run(fake_flash_tool,
             on_stage=lambda tok: ff._emit_progress(seq, stage=tok),
             on_percent=lambda pct: ff._emit_progress(seq, percent=pct))
        stamps = _collect(capsys)
        pcts = [s.get("percent") for s in stamps if "percent" in s]
        assert pcts, "百分比戳缺失"
        assert any(p == 45 for p in pcts)

    @pytest.mark.parametrize("fake_flash_tool", ["silent"], indirect=True)
    def test_silent_flash_returns_cleanly(self, fake_flash_tool):
        """无输出的 flash_tool：正常运行结束，不抛错。"""
        assert _run(fake_flash_tool) == ""

    @pytest.mark.parametrize("fake_flash_tool", ["hang"], indirect=True)
    def test_timeout_kills_flash_tool(self, fake_flash_tool):
        with pytest.raises(subprocess.TimeoutExpired):
            _run(fake_flash_tool, timeout=2)


class TestStampFormat:
    def test_stamp_is_json_on_stderr(self, capsys):
        seq: list[int] = [0]
        ff._emit_progress(seq, stage="reboot")
        ff._emit_progress(seq, stage="download")
        err = capsys.readouterr().err
        stamps = [l for l in err.splitlines() if l.startswith("PROGRESS ")]
        assert len(stamps) == 2, err
        payloads = [json.loads(l[len("PROGRESS "):]) for l in stamps]
        assert [p["seq"] for p in payloads] == [1, 2]
        assert payloads[0]["stage"] == "reboot"
        assert payloads[1]["step"] == "flash"
