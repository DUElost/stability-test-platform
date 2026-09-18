"""#2632 缺口①：PG「猜 schema」指纹采集的判据层（PR 路径）。

脚本 `tools/dev/pg_error_guard.py` 由控制面宿主的 systemd timer（`stp-pg-guard.timer`，
每 5 分钟）周期运行，只读 PG 日志、把三类指纹（猜表名 / 猜列 / 枚举值大小写）落成
node-exporter textfile 指标，供 `StabilityPgSchemaGuessing` 告警使用。

本文件钉脚本自身的判据行为；**告警规则 ↔ 生产者**的接线由
`tests/test_prometheus_alerts_contract.py`（结构/场景层）与
`tests/test_alert_metric_producers.py`（生产者面）承担——两处都不重复这里。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from tools.dev.pg_error_guard import (
    _METRIC_HELP,
    collect,
    count_fingerprints,
    main,
    render_metrics,
)

NOW = datetime(2026, 9, 18, 12, 0, 0)
WINDOW = timedelta(minutes=60)


def _count(text: str) -> dict[str, int]:
    return count_fingerprints(text, since=NOW - WINDOW, until=NOW)


def test_localized_and_english_forms_are_both_counted():
    """生产日志是本地化的：只写英文形态会恒零——中英两套都要算。"""
    text = "\n".join([
        '2026-09-18 11:10:00 CST [1] stp@stp ERROR:  关系 "job" 不存在',
        '2026-09-18 11:11:00 UTC [2] stp@stp ERROR:  relation "device_lease" does not exist',
        '2026-09-18 11:12:00 CST [3] stp@stp 错误:  枚举 plan_run_status 的输入值无效: "PENDING"',
        '2026-09-18 11:13:00 UTC [4] postgres@stp ERROR:  invalid input syntax for type integer: "check_device"',
    ])
    assert _count(text) == {"undefined_table": 2, "undefined_column": 0, "invalid_value": 2}


def test_window_excludes_older_lines():
    """窗口外的行不得计入（否则「今天没人在猜」会被历史错误一直点亮）。"""
    text = "\n".join([
        '2026-09-18 09:00:00 CST [1] stp@stp ERROR:  关系 "job" 不存在',
        '2026-09-18 11:30:00 CST [2] stp@stp ERROR:  关系 "job" 不存在',
    ])
    counts = _count(text)
    assert counts["undefined_table"] == 1


def test_detail_continuation_line_is_not_double_counted():
    """多行错误的 DETAIL/CONTEXT/STATEMENT 续行不是独立错误。"""
    text = "\n".join([
        '2026-09-18 11:20:00 CST [1] stp@stp ERROR:  关系 "job" 不存在',
        '2026-09-18 11:20:00 CST [1] stp@stp STATEMENT:  SELECT * FROM "job"',
        '2026-09-18 11:20:00 CST [1] stp@stp DETAIL:  字段 "x" 不存在',
    ])
    assert _count(text) == {"undefined_table": 1, "undefined_column": 0, "invalid_value": 0}


def test_rotated_log_is_included(tmp_path):
    """轮转后的 `.1` 也要扫——故障窗口横跨轮转时不能漏。"""
    (tmp_path / "postgresql-17-main.log").write_text(
        '2026-09-18 11:40:00 CST [1] stp@stp ERROR:  关系 "job" 不存在\n', encoding="utf-8")
    (tmp_path / "postgresql-17-main.log.1").write_text(
        '2026-09-18 11:05:00 CST [1] stp@stp ERROR:  关系 "device_lease" 不存在\n', encoding="utf-8")

    totals = collect(str(tmp_path / "postgresql-17-main.log*"), now=NOW)

    assert totals["undefined_table"] == 2, totals
    assert totals["total"] == 2


def test_metrics_render_every_declared_name_without_exponents():
    """每个 `_METRIC_HELP` 声明的名字都要渲染出来，且不得出现指数形式（textfile 解析不友好）。"""
    text = render_metrics(
        {"undefined_table": 1, "undefined_column": 2, "invalid_value": 3, "total": 6},
        ran_at=1234567890,
    )
    for name in _METRIC_HELP:
        assert f"{name} " in text, f"{name} 未渲染"
    assert "e+" not in text and "e-" not in text


def test_no_logs_means_all_zero_not_error(tmp_path):
    """日志缺失（路径不对/尚未生成）→ 全零 + last_run 仍上报，而不是异常。"""
    totals = collect(str(tmp_path / "does-not-exist*.log*"), now=NOW)
    assert totals["total"] == 0


def test_self_test_passes():
    assert main(["--self-test"]) == 0
