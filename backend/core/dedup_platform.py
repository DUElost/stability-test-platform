"""Dedup archive platform partition keys (ADR-0032 B1)."""

from __future__ import annotations

DEDUP_PLATFORM_MTK = "mtk"
DEDUP_PLATFORM_UNISOC = "unisoc"
DEDUP_PLATFORMS: tuple[str, ...] = (DEDUP_PLATFORM_MTK, DEDUP_PLATFORM_UNISOC)


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
