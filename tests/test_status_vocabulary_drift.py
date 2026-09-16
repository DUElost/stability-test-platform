"""#2418：Job 状态词表三面同源——后端映射值域 ⊆ 前端徽标键集。

形状与 #786 相同：**后端把库内 `JobStatus` 映射成对外词表**（`PENDING→QUEUED`、
`COMPLETED→FINISHED`、`ABORTED→CANCELED`），前端 `StatusBadge` 按 kind 查表，**缺键就落到
FALLBACK「未知」**。Job 报告页当年用了 `kind="job"`（库内词表那张表），于是每一个非失败
job 的报告页状态都是「未知」——dev 与生产同时命中（2026-09-16 实测）。

本文件把「后端新增/改动一个对外状态值 → 前端徽标未跟上」从人眼发现变成 PR 侧红灯：

- 后端两处 `_JOB_STATUS_TO_RUN_STATUS` 的**值域**必须落在前端 `JOB_RESULT` 键集内；
- 后端 `JobStatus` 枚举全集必须落在前端 `JOB`（库内词表）键集内；
- 两处后端映射的**键集必须相同**，值除豁免表外必须一致（豁免理由见下）。

纯文本解析、离线、秒级——与 `tests/test_frontend_api_types_sync.py` 同一范式，
并同样带「解析器自守」断言（解析失效会让上面三条恒真）。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
BADGE_TS = REPO_ROOT / "frontend/src/components/ui/status-badge.tsx"
ENUMS_PY = REPO_ROOT / "backend/models/enums.py"

# 后端有两份同名映射（results 列表侧 / report_service 报告侧），都要查
BACKEND_MAPS = [
    "backend/api/routes/results.py",
    "backend/services/report_service.py",
]

# 已知且**有意暂不裁决**的值分歧：report_service 把 UNKNOWN 归到终态 FAILED，
# results 归到非终态 RUNNING。谁对属于「风险/状态口径收敛」的问题（#2419 建议词表
# 收敛到一处），不在本单裁决范围。本门禁的作用是钉死分歧**不再扩大**。
_KNOWN_VALUE_DIVERGENCE = {"UNKNOWN"}


def _backend_status_map(rel: str) -> dict[str, str]:
    text = (REPO_ROOT / rel).read_text(encoding="utf-8")
    match = re.search(
        r"^_JOB_STATUS_TO_RUN_STATUS = \{(.*?)^\}", text, re.S | re.M
    )
    assert match, f"未解析到 {rel} 的 _JOB_STATUS_TO_RUN_STATUS（解析器需随实现更新）"
    pairs = dict(re.findall(r'"([A-Z_]+)"\s*:\s*"([A-Z_]+)"', match.group(1)))
    assert "COMPLETED" in pairs and pairs["COMPLETED"] == "FINISHED", (
        f"{rel} 映射解析异常（缺 COMPLETED→FINISHED 哨兵项）"
    )
    return pairs


def _frontend_keys(table: str) -> set[str]:
    text = BADGE_TS.read_text(encoding="utf-8")
    match = re.search(rf"^const {table}: Record<string, StatusEntry> = \{{(.*?)^\}}",
                      text, re.S | re.M)
    assert match, f"未解析到前端 {table} 表（status-badge.tsx 结构变了？）"
    keys = set(re.findall(r'^\s{2}"?([A-Z_]+)"?:', match.group(1), re.M))
    assert keys, f"前端 {table} 表解析为空——恒真断言，判据失效"
    return keys


def _job_status_enum() -> set[str]:
    text = ENUMS_PY.read_text(encoding="utf-8")
    match = re.search(r"^class JobStatus\(.*?\):\n((?:\s+[A-Z_]+\s*=\s*\"[A-Z_]+\"\n)+)",
                      text, re.S | re.M)
    assert match, "未解析到 JobStatus 枚举"
    values = set(re.findall(r'=\s*"([A-Z_]+)"', match.group(1)))
    assert "COMPLETED" in values, "JobStatus 解析异常"
    return values


@pytest.mark.parametrize("rel", BACKEND_MAPS)
def test_backend_outward_vocabulary_has_a_badge_key(rel):
    """后端映射产出的每一个对外状态值，前端 `JOB_RESULT` 都必须有键。

    缺一个键 = 那种状态的报告页/列表页恒显示「未知」（#2418 本体）。
    """
    produced = set(_backend_status_map(rel).values())
    missing = produced - _frontend_keys("JOB_RESULT")
    assert not missing, (
        f"{rel} 会产出前端无键的对外状态 {sorted(missing)}：这些 job 的状态徽标落到"
        "FALLBACK「未知」。补 `JOB_RESULT` 键，或在消费方开 fallbackToRaw。"
    )


def test_inward_job_status_enum_has_a_badge_key():
    """直接读库内字段的消费方走 `JOB`（kind="job"），其键集必须覆盖 `JobStatus` 全集。"""
    missing = _job_status_enum() - _frontend_keys("JOB")
    assert not missing, f"库内 JobStatus {sorted(missing)} 在前端 JOB 表无键"


def test_backend_maps_stay_key_identical_and_value_converged():
    """两份映射不得各长一套键；值分歧只允许豁免表里那一条（且不得被删空）。"""
    maps = {rel: _backend_status_map(rel) for rel in BACKEND_MAPS}
    key_sets = {rel: set(m.keys()) for rel, m in maps.items()}
    assert len({frozenset(s) for s in key_sets.values()}) == 1, (
        f"两处后端状态映射键集已分叉：{key_sets}"
    )

    base_rel, first = next(iter(maps.items()))
    for rel, mapping in maps.items():
        if rel == base_rel:
            continue
        diffs = {k for k in mapping if mapping[k] != first.get(k)}
        unexpected = diffs - _KNOWN_VALUE_DIVERGENCE
        assert not unexpected, (
            f"{rel} 与 {base_rel} 在 {sorted(unexpected)} 上给出不同的对外值："
            "同一 job 在列表与报告页会显示两种状态。确需分歧请把它写进"
            " _KNOWN_VALUE_DIVERGENCE 并注明归属 issue。"
        )

    still_diverging = {
        k for k in first
        if any(maps[rel].get(k) != first[k] for rel in maps)
    }
    assert still_diverging == _KNOWN_VALUE_DIVERGENCE, (
        f"实际值分歧 {sorted(still_diverging)} 与豁免表 {sorted(_KNOWN_VALUE_DIVERGENCE)} "
        "不符：分歧已收敛就请删掉豁免项（豁免表不回收就是下一个 #2419）。"
    )
