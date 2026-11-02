"""发生身份 ``occurrence_key`` 契约（ADR-0062 §3 第 2 条，B8 G1）——唯一实现。

发生身份由设备侧可证明的事实构成::

    occurrence_key = sha256(platform | serial | db_path | aee_ts 原文 | raw_event_type)

- ``db_path`` 是设备端 AEE 历史条目路径（例如 ``/data/aee_exp/db.01``）；
- ``aee_ts`` 是设备自报时间的**原文**，不做时区换算或格式化——``db.NN`` 编号会
  回绕，只有原文才能区分不同存储代际；
- ``raw_event_type`` 是厂商历史行中的原始事件类型（不是归一化后的 DLE
  ``event_type``）。

字段按「长度前缀 + 原文字节」拼接后再求 sha256，避免分隔符出现在字段内容里造成
歧义；拼接顺序固定，任何一侧不得自行重排或改写字段。同一组输入在 Agent 与控制面
得到同一个十六进制 key。

``db_path`` 或 ``aee_ts`` 缺失时返回 ``None``：身份不可证明，控制面按 ADR-0053
D7 规则 4 fail-open 处理（DLE 的 ``occurrence_key`` 为空，照常进入后续处理并标注
来源未证实）。``raw_event_type`` 允许为空字符串，此时它仍作为原值参与计算——设备
历史行没有该字段时，同一条目的空值也是稳定的。
"""

from __future__ import annotations

import hashlib
from typing import Optional


def _canonical_payload(
    platform: str,
    serial: str,
    db_path: str,
    aee_ts: str,
    raw_event_type: str,
) -> bytes:
    """长度前缀拼接，保留每个字段的原文边界。"""
    parts = (platform, serial, db_path, aee_ts, raw_event_type)
    return "".join(f"{len(part)}:{part}" for part in parts).encode("utf-8")


def compute_occurrence_key(
    *,
    platform: str,
    serial: str,
    db_path: str,
    aee_ts: str,
    raw_event_type: str,
) -> Optional[str]:
    """返回发生身份的十六进制 sha256；``db_path`` 或 ``aee_ts`` 缺失时返回 None。

    调用方必须传设备侧事实原文。控制面不得从目录名、报表文件名或 xls 文本反推
    本值（ADR-0062 §3 第 2 条）。
    """
    platform = str(platform or "")
    serial = str(serial or "")
    db_path = str(db_path or "")
    aee_ts = str(aee_ts or "")
    raw_event_type = str(raw_event_type or "")

    if not platform or not serial or not db_path or not aee_ts:
        return None

    digest = hashlib.sha256(
        _canonical_payload(platform, serial, db_path, aee_ts, raw_event_type)
    )
    return digest.hexdigest()
