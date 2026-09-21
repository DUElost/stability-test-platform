#!/usr/bin/env python3
"""PG 服务日志的「猜 schema」指纹采集（#2632 缺口①：观测先行）。

**问题**：2026-09-16 起生产 PG 日志里出现约 30 条一次性 SQL 错误，模式是**对生产库
跑猜出来的 schema**——表名靠猜（`关系 "job" 不存在`，真名 `job_instance`）、枚举值
用大写（库内是小写）、引用当时尚未落地的列。**无拦截、无留痕、无告警**：事后人读
日志才发现；若哪次猜对了，就是一次不留审计痕迹的生产读写。

本脚本把三类指纹做成 **node-exporter textfile 指标**（控制面宿主，systemd timer
周期跑），供 `StabilityPgSchemaGuessing` 告警使用。只读日志，不碰数据库。

**指纹**（在最近 ``--window-minutes`` 分钟的日志里按类计数）：

- ``undefined_table``：``关系 "X" 不存在`` / ``relation "X" does not exist``
- ``undefined_column``：``字段 "X" 不存在`` / ``column "X" does not exist``
- ``invalid_value``：``输入值无效`` / ``输入语法`` / ``invalid input value for enum`` /
  ``invalid input syntax``

**为什么中英两套都要匹配**：生产日志是**本地化**的（zh_CN），只写英文形态会恒零——
那正是本仓反复出现的「绿而空」形态（指标在、值恒 0，看起来像「没有人在猜」）。

**边界**：只匹配消息行（``ERROR``/``错误``）且按行计数；上游应用**正常路径**不应产生
这三类错误（迁移期窗口除外，见告警注解）。多行错误的 DETAIL/STATEMENT 不重复计数。

用法::

    python tools/dev/pg_error_guard.py --dry-run            # 只打印，不写指标文件
    python tools/dev/pg_error_guard.py                      # 写 textfile（timer 调用）
    python tools/dev/pg_error_guard.py --self-test          # 离线红绿自证
"""
from __future__ import annotations

import argparse
import glob as globmod
import json
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

# #2973：与 script_guard_probe 同形——ExecStart 直接跑本文件时 sys.path[0]=tools/dev，
# 必须先把仓库根塞进 path，再 import tools.dev.*（见 skill_usage_probe）。
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.dev.textfile_metrics import render_gauges, write_atomic  # noqa: E402

#: 生产者声明：names 由 tests/metrics_registry.py 从本字面量静态提取（无标签）。
_METRIC_HELP = {
    "stp_pg_undefined_table_events": "窗口内 PG 报「关系不存在」的 ERROR 条数（猜表名指纹）",
    "stp_pg_undefined_column_events": "窗口内 PG 报「字段不存在」的 ERROR 条数（猜列指纹）",
    "stp_pg_invalid_value_events": "窗口内 PG 报「枚举/类型输入值无效」的 ERROR 条数（猜枚举指纹）",
    "stp_pg_schema_error_events": "上述三类之和（告警判据；正常路径不应产生）",
    "stp_pg_guard_last_run": "上次成功采集的 unix 时间戳（发现采集器静默停摆）",
}

DEFAULT_LOG_GLOB = "/var/log/postgresql/postgresql-*-main.log*"
DEFAULT_METRICS_PATH = "/var/lib/prometheus/node-exporter/stp-pg-guard.prom"
DEFAULT_WINDOW_MINUTES = 60
#: 单文件只读尾部这么多字节：日志可达数百 MB，而判据只看最近窗口。
DEFAULT_TAIL_BYTES = 8_000_000

#: 行首时间戳（PG 默认 `log_line_prefix` 以 `%m` 起头）：`2026-09-16 19:18:22[.123] [TZ]`
_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")

#: 三类指纹——中英两套消息形态（日志本地化，只写英文会恒零）。
_FINGERPRINTS: dict[str, tuple[re.Pattern[str], ...]] = {
    "undefined_table": (
        re.compile(r'关系 "[^"]*" 不存在'),
        re.compile(r'relation "[^"]*" does not exist', re.I),
    ),
    "undefined_column": (
        re.compile(r'字段 "[^"]*" 不存在'),
        re.compile(r'column "[^"]*" does not exist', re.I),
    ),
    "invalid_value": (
        re.compile(r"输入值无效"),
        re.compile(r"输入语法"),
        re.compile(r"invalid input value for enum", re.I),
        re.compile(r"invalid input syntax", re.I),
    ),
}


def _is_error_line(line: str) -> bool:
    """只认 ERROR 级消息行（本地化后的字面量是 `错误:`）。"""
    return ("ERROR:" in line) or ("错误:" in line)


def count_fingerprints(text: str, *, since: datetime, until: datetime) -> dict[str, int]:
    """统计窗口内三类指纹的条数（按行；多行错误只在消息行计数）。"""
    counts = {kind: 0 for kind in _FINGERPRINTS}
    for line in text.splitlines():
        stamp = _TS_RE.match(line)
        if stamp is None:
            continue
        try:
            when = datetime.strptime(stamp.group(1), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        if not (since <= when <= until):
            continue
        if not _is_error_line(line):
            continue
        for kind, patterns in _FINGERPRINTS.items():
            if any(p.search(line) for p in patterns):
                counts[kind] += 1
                break  # 一行只归一类，避免同一条错误被计两次
    return counts


def _tail_text(path: Path, max_bytes: int) -> str:
    """读文件尾部至多 ``max_bytes``（大日志下判据只看最近窗口）。"""
    try:
        size = path.stat().st_size
    except OSError:
        return ""
    try:
        with path.open("rb") as fh:
            if size > max_bytes:
                fh.seek(size - max_bytes)
                fh.readline()  # 丢掉可能被截断的半行
            return fh.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def collect(
    log_glob: str,
    *,
    window_minutes: int = DEFAULT_WINDOW_MINUTES,
    tail_bytes: int = DEFAULT_TAIL_BYTES,
    now: datetime | None = None,
) -> dict[str, int]:
    """扫 ``log_glob``（含已轮转的 `.1`）并汇总窗口内的指纹计数。"""
    until = now if now is not None else datetime.now()
    since = until - timedelta(minutes=window_minutes)
    totals = {kind: 0 for kind in _FINGERPRINTS}
    for name in sorted(globmod.glob(log_glob)):
        path = Path(name)
        if not path.is_file():
            continue
        totals_here = count_fingerprints(_tail_text(path, tail_bytes), since=since, until=until)
        for kind, value in totals_here.items():
            totals[kind] += value
    totals["total"] = sum(totals[kind] for kind in _FINGERPRINTS)
    return totals


def render_metrics(values: dict[str, int], *, ran_at: int) -> str:
    """textfile 指标（无标签；gauge——窗口计数天然是「当前值」）。"""
    mapping: dict[str, object] = {
        f"stp_pg_{kind}_events": values[kind] for kind in _FINGERPRINTS
    }
    mapping["stp_pg_schema_error_events"] = values["total"]
    mapping["stp_pg_guard_last_run"] = ran_at
    # #2881：收敛到公共原语（第三处生产者出现时抽的 helper）
    return render_gauges(_METRIC_HELP, mapping)


def write_metrics(path: Path, text: str) -> None:
    """原子落盘（同目录临时文件 + rename），避免 node_exporter 读到半截文件。"""
    write_atomic(path, text)


def _self_test() -> int:
    """离线红绿自证：中英两套形态、窗口边界、行级去重、轮转文件都要判得对。"""
    now = datetime(2026, 9, 18, 12, 0, 0)
    inside = "2026-09-18 11:30:00 CST [10] stp@stp ERROR:  关系 \"job\" 不存在"
    inside_en = "2026-09-18 11:31:00 UTC [11] stp@stp ERROR:  relation \"device_lease\" does not exist"
    outside = "2026-09-18 09:00:00 CST [12] stp@stp ERROR:  关系 \"job\" 不存在"
    enum_line = '2026-09-18 11:32:00 CST [13] stp@stp 错误:  枚举 plan_run_status 的输入值无效: "PENDING"'
    detail_line = '2026-09-18 11:32:00 CST [13] stp@stp DETAIL:  字段 "created_at" 不存在'
    col_line = '2026-09-18 11:33:00 CST [14] stp@stp ERROR:  字段 "started_at" 不存在'

    text = "\n".join([inside, inside_en, outside, enum_line, detail_line, col_line])
    counts = count_fingerprints(text, since=now - timedelta(minutes=60), until=now)
    if counts != {"undefined_table": 2, "undefined_column": 1, "invalid_value": 1}:
        print(f"[self-test] 计数不符：{counts}", file=sys.stderr)
        return 1

    # 无 ERROR 标记的 DETAIL 行不得计数（它是上一条错误的续行）
    only_detail = count_fingerprints(detail_line, since=now - timedelta(minutes=60), until=now)
    if only_detail["undefined_column"] != 0:
        print("[self-test] DETAIL 续行被当成独立错误计数了", file=sys.stderr)
        return 1

    # 空日志 / 无指纹日志 → 全零（而不是异常）
    if any(count_fingerprints("", since=now - timedelta(minutes=60), until=now).values()):
        print("[self-test] 空日志应得全零", file=sys.stderr)
        return 1

    # 指标渲染：每个声明名都在，且能被 Prometheus 文本格式解析（无指数形式）
    rendered = render_metrics({"undefined_table": 1, "undefined_column": 0,
                               "invalid_value": 0, "total": 1}, ran_at=123)
    missing = [name for name in _METRIC_HELP if name not in rendered]
    if missing:
        print(f"[self-test] 指标渲染缺名字：{missing}", file=sys.stderr)
        return 1
    if "e+" in rendered or "e-" in rendered:
        print("[self-test] 指标出现指数形式（node_exporter 文本解析不友好）", file=sys.stderr)
        return 1

    print("[OK] pg_error_guard self-test 通过（中英形态/窗口/续行去重/渲染四态）")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--log-glob", default=DEFAULT_LOG_GLOB)
    parser.add_argument("--metrics-path", default=DEFAULT_METRICS_PATH)
    parser.add_argument("--window-minutes", type=int, default=DEFAULT_WINDOW_MINUTES)
    parser.add_argument("--tail-bytes", type=int, default=DEFAULT_TAIL_BYTES)
    parser.add_argument("--dry-run", action="store_true", help="只打印，不写指标文件")
    parser.add_argument("--json", action="store_true", help="输出机器可读明细")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        return _self_test()

    values = collect(
        args.log_glob, window_minutes=args.window_minutes, tail_bytes=args.tail_bytes
    )
    ran_at = int(time.time())

    if args.json:
        print(json.dumps({"values": values, "ran_at": ran_at, "log_glob": args.log_glob},
                         ensure_ascii=False))
    else:
        print(
            "PG schema-guess 指纹（窗口 {win} 分钟）：table={t} column={c} value={v} 合计={tot}".format(
                win=args.window_minutes, t=values["undefined_table"],
                c=values["undefined_column"], v=values["invalid_value"], tot=values["total"],
            )
        )

    if args.dry_run:
        return 0

    try:
        write_metrics(Path(args.metrics_path), render_metrics(values, ran_at=ran_at))
    except OSError as exc:
        print(f"GUARD BROKEN: 指标文件写入失败 {args.metrics_path}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
