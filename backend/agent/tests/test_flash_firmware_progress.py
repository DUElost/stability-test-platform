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
        """无输出的 flash_tool：正常运行结束，返回 (输出, rc=0)。"""
        out, rc = _run(fake_flash_tool)
        assert out == ""
        assert rc == 0

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


class TestHostLock:
    def test_lock_wait_emits_progress_ticks(self, monkeypatch):
        """锁被占用时轮询等待并打 tick——等待本身是可见进度(#142 review)。

        阻塞 flock 期间无戳,permit cap=5 下等待中的设备会被停滞钟误杀。
        """
        import fcntl

        import time as _time

        holder = open(ff._LOCK_PATH, "w")
        fcntl.flock(holder.fileno(), fcntl.LOCK_EX)
        ticks: list[int] = []

        def _tick(waited: int) -> None:
            ticks.append(waited)
            if len(ticks) >= 3:
                # 第 3 次 tick 时释放锁,让等待方拿到
                fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
                holder.close()

        orig_sleep = _time.sleep
        _time.sleep = lambda s: 0.001
        try:
            fd = ff._acquire_host_lock(on_wait_tick=_tick)
            assert len(ticks) >= 3, "锁等待期间必须打多个 tick"
            assert ticks[-1] > 0, "tick 要带已等待秒数"
        finally:
            _time.sleep = orig_sleep
            import os
            try:
                os.close(fd)
            except Exception:
                pass
            try:
                holder.close()
            except Exception:
                pass


class TestLockWaitRealWiring:
    def test_lock_wait_emits_stamps_through_main_wiring(self, fake_flash_tool, monkeypatch, capsys):
        """真实接线回归(#142 review)：锁占用时 tick 走 main 的 lambda,
        seq 必须先于锁等待定义,否则 NameError 被吞、不打戳。

        之前测试用自定义 _tick,没走 main 的 lambda,拦不住这个。
        """
        import fcntl
        from unittest.mock import patch

        # 占住锁
        holder = open(ff._LOCK_PATH, "w")
        fcntl.flock(holder.fileno(), fcntl.LOCK_EX)
        tick_count: list[int] = []

        def _wrap_main_tick(on_wait_tick):
            """包装 main 的 tick：计数释放锁 + 转发给 main 的 lambda 打戳。"""

            def _wrapped(waited: int) -> None:
                tick_count.append(waited)
                if len(tick_count) >= 3:
                    fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
                    holder.close()
                on_wait_tick(waited)  # 转发 → main 的 _emit_progress

            return _wrapped

        fw_dir = fake_flash_tool.parent
        da = fw_dir / "da.bin"
        scatter = fw_dir / "scatter.txt"
        da.write_bytes(b"DA")
        scatter.write_text("scatter", encoding="utf-8")

        def _step_params():
            return {
                "firmware_dir": str(fw_dir), "da_file": "da.bin",
                "scatter_file": "scatter.txt", "command": "firmware-upgrade",
                "boot_mode": "auto", "reboot_to_flash": False, "timeout_seconds": 30,
            }

        import time as _time
        orig_acquire = ff._acquire_host_lock  # patch 前保存,否则 side_effect 递归
        orig_sleep = _time.sleep
        _time.sleep = lambda s: 0.001
        try:
            with patch.object(ff, "_step_params", _step_params), \
                 patch.object(ff, "_locate_flash_tool_dir", return_value=str(fw_dir)), \
                 patch.object(ff, "_pick_flash_tool_exe", return_value=str(fake_flash_tool)), \
                 patch.object(ff, "_resolve_firmware_dir", return_value=str(fw_dir)), \
                 patch.object(ff, "_resolve_under",
                              side_effect=lambda root, name: str(fw_dir / name)), \
                 patch.object(ff, "_acquire_host_lock",
                              side_effect=lambda on_wait_tick=None: orig_acquire(
                                  on_wait_tick=(
                                      _wrap_main_tick(on_wait_tick)
                                      if on_wait_tick is not None else None
                                  )
                              )), \
                 patch.object(ff, "_release_host_lock", return_value=None), \
                 patch.object(ff, "_wait_device_back", return_value=True):
                monkeypatch.setenv("STP_DEVICE_SERIAL", "FAKESERIAL")
                monkeypatch.setenv("STP_ADB_PATH", "/bin/true")
                ff.main()
        finally:
            _time.sleep = orig_sleep
            try:
                holder.close()
            except Exception:
                pass

        err = capsys.readouterr().err
        stamps = [
            json.loads(line[len("PROGRESS "):])
            for line in err.splitlines() if line.startswith("PROGRESS ")
        ]
        lock_stamps = [s for s in stamps if s.get("stage") == "lock-wait"]
        assert lock_stamps, "锁等待期间必须出 stage=lock-wait 戳"
        assert lock_stamps[0]["seq"] == 1, "seq 必须从 1 开始(锁等待最先打)"
