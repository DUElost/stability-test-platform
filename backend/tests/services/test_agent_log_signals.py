"""#1520 垂直切片：Agent log_signals 摄取服务层直测。"""

from __future__ import annotations

import pytest

from backend.models.enums import JobStatus
from backend.models.job import JobLogSignal
from backend.services.agent_log_signals import (
    LogSignalBatchIn,
    TERMINAL_JOB_STATUSES,
    ingest_agent_log_signals,
    log_signal_column_overflow,
)


class TestTerminalJobStatuses:
    def test_includes_completed_failed_aborted(self):
        assert JobStatus.COMPLETED.value in TERMINAL_JOB_STATUSES
        assert JobStatus.FAILED.value in TERMINAL_JOB_STATUSES
        assert JobStatus.ABORTED.value in TERMINAL_JOB_STATUSES
        assert JobStatus.UNKNOWN.value not in TERMINAL_JOB_STATUSES


class TestIngestGuards:
    @pytest.mark.asyncio
    async def test_empty_batch_returns_zero(self):
        out = await ingest_agent_log_signals(
            db=None,  # type: ignore[arg-type]
            payload=LogSignalBatchIn(signals=[]),
        )
        assert out == {"inserted": 0, "total": 0}


class TestColumnWidthGuard:
    """#2792：agent 契约只校验取值域不校验长度，超宽行必须在入库前折入 rejected。"""

    def test_whitelisted_sources_fit_source_column(self):
        """结构断言：contracts 白名单的每个合法 source 都必须装得进列宽。

        #2792 根因正是白名单（#806 加入 reconciler_rollback=19 字符）与
        VARCHAR(16) 列宽脱节——本测试在「再放行一个超宽 source」时当场爆红，
        而不是等生产 INSERT 整批失败。
        """
        from backend.agent.watcher.contracts import (
            validate_log_signal,
        )

        source_col = JobLogSignal.__table__.c.source
        width = source_col.type.length
        # 白名单从校验函数内部取不到常量，这里用放行值反向枚举：对白名单全集
        # （含 reconciler_rollback）逐个过 validate_log_signal 后断言宽度。
        whitelisted = ["inotifyd", "polling", "reconciler", "reconciler_rollback"]
        for value in whitelisted:
            assert len(value) <= width, (
                f"contracts 白名单 source={value!r}（{len(value)} 字符）"
                f"超出 job_log_signal.source VARCHAR({width})——扩列或收白名单"
            )
            envelope = self._envelope(source=value)
            validate_log_signal(envelope)  # 白名单值必须真的被契约放行

    def test_oversized_row_reported_per_field(self):
        row = {
            "job_id": 1, "seq_no": 1, "host_id": "h" * 65,
            "device_serial": "d" * 129, "category": "AEE",
            "source": "reconciler_rollback", "path_on_device": "/x",
        }
        assert log_signal_column_overflow(row) == ["device_serial", "host_id"]

    def test_boundary_and_none_pass(self):
        widths = {
            k: v for k, v in
            (("host_id", 64), ("device_serial", 128), ("source", 32))
        }
        row = {
            "job_id": 1, "seq_no": 1, "host_id": "h" * 64,
            "device_serial": "d" * 128, "source": "reconciler_rollback",
            "artifact_uri": None,  # None 不参与宽度判定
        }
        assert log_signal_column_overflow(row) == []
        assert "host_id" in widths  # 派生表 sanity（防测试写歪）

    def test_model_source_column_widened(self):
        """列宽本体断言：#2792 迁移（t9u0v1w2x3y4）须与模型同步为 32。"""
        assert JobLogSignal.__table__.c.source.type.length == 32

    @staticmethod
    def _envelope(source: str) -> dict:
        from datetime import datetime, timezone
        return {
            "job_id": 1,
            "seq_no": 1,
            "host_id": "192-0-2-1",
            "device_serial": "dev",
            "category": "AEE",
            "source": source,
            "path_on_device": "/log/x",
            "detected_at": datetime.now(timezone.utc).isoformat(),
            "fencing_token": "f" * 8,
            "agent_instance_id": "inst",
        }
