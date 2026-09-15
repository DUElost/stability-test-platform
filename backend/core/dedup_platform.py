"""控制面平台词表：归档分区键（ADR-0032 B1）+ 采集实现支持判定（ADR-0032 R4-b b1）。

本模块是**控制面侧平台词表的唯一来源**：`device.platform` 的两个派生问题在这里回答
——「有没有归档分区」（``dedup_platform_for_device_platform``）与「有没有采集实现」
（``has_collection_impl``）。Agent 侧的对应判定在 ``backend/agent/device_platform.py``，
两者不是同一份代码，改动时需同时检查。
"""

from __future__ import annotations

DEDUP_PLATFORM_MTK = "mtk"
DEDUP_PLATFORM_UNISOC = "unisoc"
DEDUP_PLATFORMS: tuple[str, ...] = (DEDUP_PLATFORM_MTK, DEDUP_PLATFORM_UNISOC)

#: 已有采集/扫描实现的 ``device.platform``。与 Agent 侧路由一一对应：
#: ``job_session._resolve_reconciler_class`` 认 UNISOC / MTK / UNKNOWN；
#: 空值/NULL 与 UNKNOWN 同路（保守放行，避免 adb 抖动漏采）。QCOM 无实现。
_COLLECTION_IMPLEMENTED: frozenset[str] = frozenset({"", "MTK", "UNISOC", "UNKNOWN"})


def has_collection_impl(device_platform: str | None) -> bool:
    """``device.platform`` 是否已有采集/扫描实现（R4-b b1，2026-09-15 裁决）。

    控制面据此在 watcher-summary 的平台分桶上标注「平台未支持」，让"没有异常"与
    "平台未支持"在 UI 上可区分——此前 QCOM 走 ``_resolve_reconciler_class → None``
    静默返回（``job_session.py:311-313,341-342``），页面上看不出差别。

    与 :func:`dedup_platform_for_device_platform` 的**当前等价性**：两者今天对
    MTK / UNKNOWN / NULL / UNISOC / QCOM 判定一致（QCOM 既无归档分区也无采集实现），
    但**语义不同**——前者问"有没有归档分区"，本函数问"有没有采集实现"。二者若将来
    分叉（例如先落采集、后补归档，或反之），以本函数为准表达"未支持"，不要互相代用。
    """
    return (device_platform or "").strip().upper() in _COLLECTION_IMPLEMENTED

#: ``device.platform`` → 归档分区键。
#:
#: ``UNKNOWN`` / 空值兜底到 MTK，与 Agent 侧路由保持一致
#: （``job_session._resolve_reconciler_class`` 把 MTK 与 UNKNOWN 都交给 AEE
#: Reconciler；``aee.collector.get_collector_for_platform`` 同理）。
#: QCOM 等**尚无采集/扫描实现**的平台不在表内 → 返回 ``None``：它们不会产出任何
#: scan 产物，因此不得进入完备性期望集（否则期望永远无法满足）。
_DEVICE_TO_DEDUP_PLATFORM: dict[str, str] = {
    "": DEDUP_PLATFORM_MTK,
    "UNKNOWN": DEDUP_PLATFORM_MTK,
    "MTK": DEDUP_PLATFORM_MTK,
    "UNISOC": DEDUP_PLATFORM_UNISOC,
}


def dedup_platform_for_device_platform(platform: str | None) -> str | None:
    """``device.platform`` → 归档平台分区；无对应分区的平台返回 ``None``。

    仅用于**期望推导**（完备性屏障），不用于产物归类——产物归类以
    中心存储路径为准（``scan_artifact_uri_platform``）。
    """
    return _DEVICE_TO_DEDUP_PLATFORM.get((platform or "").strip().upper())


def scan_artifact_uri_platform(uri: str) -> str:
    """Classify a registered scan artifact path into mtk or unisoc."""
    norm = uri.replace("\\", "/").lower()
    if "/dedup/" not in norm:
        return DEDUP_PLATFORM_MTK
    if f"/{DEDUP_PLATFORM_UNISOC}/" in norm:
        return DEDUP_PLATFORM_UNISOC
    if f"/{DEDUP_PLATFORM_MTK}/" in norm:
        return DEDUP_PLATFORM_MTK
    return DEDUP_PLATFORM_MTK


def artifact_uri_matches_platform(uri: str, platform: str) -> bool:
    return scan_artifact_uri_platform(uri) == platform
