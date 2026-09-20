#!/usr/bin/env python3
"""node-exporter textfile 指标的两个公共原语：渲染与原子落盘。

为什么抽出来：`tools/dev/script_guard_probe.py`（#735 退役守卫）与
`tools/dev/pg_error_guard.py`（#2632 PG 日志猜 schema）各自实现了一份**逐行相同**的
渲染/落盘，第三个生产者（`tools/dev/skill_usage_probe.py`，#2881）出现时按仓内惯例
收敛到本模块。两个旧调用点保留同名薄包装（`render_metrics` / `write_metrics`），
它们的契约测试因此不需要跟着改。

两条来自现场的口径（不是风格偏好）：

- **渲染**：每行 `# HELP` / `# TYPE gauge` / `<name> <value>`，名字与 HELP 必须一一对应
  ——`tests/metrics_registry.py` 从生产者的 `_METRIC_HELP` 静态提取名字，两边漂移会让
  「未知指标即红」的判据先红。
- **落盘**：同目录临时文件 + `os.replace` 原子替换。node_exporter 可能正在读这个目录，
  半截文件会被解析成脏数据（旧值留在旧文件里，比没有更坏——它看起来像「刚跑过且干净」）。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping, Sequence, Union

MetricValue = Union[int, float, str]


def render_gauges(
    help_map: Mapping[str, str],
    values: Mapping[str, MetricValue],
    *,
    order: Sequence[str] | None = None,
) -> str:
    """按 `order`（缺省= `help_map` 的键序）渲染无标签 gauge 的 textfile 文本。

    取值一律 `str()`——**格式化由调用方负责**（例如时间戳必须 `int()`：`%g` 会把
    1.7896e+09 渲染成指数形式，node_exporter 的 textfile 解析器对它并不友好）。
    """
    names = list(order) if order is not None else list(help_map)
    lines: list[str] = []
    for name in names:
        lines.append(f"# HELP {name} {help_map[name]}")
        lines.append(f"# TYPE {name} gauge")
        lines.append(f"{name} {values[name]}")
    return "\n".join(lines) + "\n"


def write_atomic(path: Path, text: str) -> None:
    """原子替换（同目录临时文件 + `os.replace`）：node_exporter 可能正在读。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
