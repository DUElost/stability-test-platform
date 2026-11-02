"""发生身份契约的唯一实现与稳定性（ADR-0062 §3 第 2 条，B8 G1）。

契约模块是 Agent 与控制面共用的**唯一**实现：Agent 在 reconciler 生成 DLE
payload 时计算，控制面只存储上报值。本测试锁定公式、原文语义与字段边界，
防止两侧各自演化出不同 key。
"""

from __future__ import annotations

import hashlib

import pytest

from backend.agent.contracts.occurrence_key import compute_occurrence_key


# 与实现无关的参考公式：长度前缀拼接后求 sha256。
def _reference_key(
    platform: str,
    serial: str,
    db_path: str,
    aee_ts: str,
    raw_event_type: str,
) -> str:
    parts = (platform, serial, db_path, aee_ts, raw_event_type)
    canonical = "".join(f"{len(part)}:{part}" for part in parts)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


_INPUT = dict(
    platform="MTK",
    serial="SN123456",
    db_path="/data/aee_exp/db.01",
    aee_ts="2026-05-28 10:00:00.000",
    raw_event_type="Java (JE)",
)

# 固定值：公式或字段顺序一旦变化，本断言即红。
_EXPECTED = "d47021abd5ffdeb701327a418ce8efe23cd24b2401320f6080965f36d0f3f059"


def test_contract_key_is_stable_and_matches_reference_formula():
    assert compute_occurrence_key(**_INPUT) == _EXPECTED
    assert compute_occurrence_key(**_INPUT) == _reference_key(**_INPUT)


def test_agent_and_control_plane_share_the_same_implementation():
    """两侧导入同一模块，得到同一个 key（契约只有一份实现）。"""
    from backend.agent.contracts import occurrence_key as agent_side

    def _control_plane_side(**kwargs):
        # 控制面按包名加载同一契约模块（ADR-0054 D1 的正门）。
        from backend.agent.contracts.occurrence_key import compute_occurrence_key

        return compute_occurrence_key(**kwargs)

    assert _control_plane_side(**_INPUT) == agent_side.compute_occurrence_key(
        **_INPUT
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("platform", "UNISOC"),
        ("serial", "SN654321"),
        ("db_path", "/data/aee_exp/db.02"),
        ("aee_ts", "2026-05-28 10:00:00.001"),
        ("raw_event_type", "Native (NE)"),
    ],
)
def test_every_identity_field_changes_the_key(field, value):
    changed = dict(_INPUT)
    changed[field] = value
    assert compute_occurrence_key(**changed) != _EXPECTED


def test_aee_ts_uses_raw_text_not_normalized_datetime():
    """同一时刻的两种原文必须得到不同 key——db.NN 回绕靠原文区分代际。"""
    spaced = dict(_INPUT, aee_ts="2026-05-28 10:00:00.000")
    iso = dict(_INPUT, aee_ts="2026-05-28T10:00:00+08:00")
    assert compute_occurrence_key(**spaced) != compute_occurrence_key(**iso)


def test_field_boundaries_are_unambiguous():
    """朴素 ``|`` 拼接会让两组不同输入碰撞；长度前缀不得碰撞。"""
    left = compute_occurrence_key(
        platform="MTK",
        serial="a|b",
        db_path="/data/aee_exp/db.01",
        aee_ts="2026-05-28 10:00:00.000",
        raw_event_type="Java (JE)",
    )
    right = compute_occurrence_key(
        platform="MTK",
        serial="a",
        db_path="b|/data/aee_exp/db.01",
        aee_ts="2026-05-28 10:00:00.000",
        raw_event_type="Java (JE)",
    )
    assert left != right


@pytest.mark.parametrize("missing", ["db_path", "aee_ts", "serial", "platform"])
def test_missing_required_fact_returns_none(missing):
    """身份不可证明时返回 None（DLE 列为空，按 fail-open 处理）。"""
    inputs = dict(_INPUT)
    inputs[missing] = ""
    assert compute_occurrence_key(**inputs) is None


def test_empty_raw_event_type_still_computes():
    """厂商历史行没有 raw_event_type 时，空值仍是稳定的原值。"""
    inputs = dict(_INPUT, raw_event_type="")
    assert compute_occurrence_key(**inputs) == _reference_key(**inputs)
