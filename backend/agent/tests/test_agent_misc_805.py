"""#805 Agent 杂项批回归：心跳护栏 / abort 坏 id / 日志双换行。"""

from __future__ import annotations

from pathlib import Path

from backend.agent.main import _parse_abort_job_ids
from backend.agent.pipeline_engine import _append_log_line


class TestParseAbortJobIds:
    def test_list_skips_invalid_and_keeps_valid(self):
        ids = _parse_abort_job_ids({"job_ids": [1, "2", "oops", None, {"x": 1}, 3]})
        assert ids == [1, 2, 3]

    def test_single_job_id(self):
        assert _parse_abort_job_ids({"job_id": "7"}) == [7]

    def test_empty_payload(self):
        assert _parse_abort_job_ids({}) == []

    def test_all_invalid_does_not_raise(self):
        assert _parse_abort_job_ids({"job_ids": ["bad", None]}) == []


class TestAppendLogLine:
    def test_line_already_ending_newline_not_doubled(self, tmp_path):
        p = tmp_path / "s.log"
        _append_log_line(str(p), "hello\n")
        assert p.read_text(encoding="utf-8") == "hello\n"

    def test_line_without_newline_gets_one(self, tmp_path):
        p = tmp_path / "s.log"
        _append_log_line(str(p), "hello")
        assert p.read_text(encoding="utf-8") == "hello\n"

    def test_multiple_lines_append(self, tmp_path):
        p = tmp_path / "s.log"
        _append_log_line(str(p), "a\n")
        _append_log_line(str(p), "b\n")
        assert p.read_text(encoding="utf-8") == "a\nb\n"

    def test_write_failure_swallowed(self, tmp_path):
        # 目录不存在 → OSError 被吞（不抛出）
        _append_log_line(str(tmp_path / "missing" / "s.log"), "x\n")


class TestHeartbeatSafeTick:
    def test_safe_tick_swallows_exception(self):
        from backend.agent.heartbeat_thread import HeartbeatThread

        ht = HeartbeatThread.__new__(HeartbeatThread)

        def boom():
            raise RuntimeError("tick failed")

        ht._tick = boom  # type: ignore[method-assign]
        # 不得抛出——单次 tick 异常不能杀死心跳守护线程
        ht._safe_tick()


def test_artifact_helper_rejects_empty(tmp_path):
    from backend.agent.unisoc_scan_runner import UnisocScanRunner

    f = Path(tmp_path) / "empty.xls"
    f.write_bytes(b"")
    assert UnisocScanRunner._artifact_looks_complete(f) is False
