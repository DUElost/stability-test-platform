from backend.core.dedup_platform import (
    DEDUP_PLATFORM_MTK,
    DEDUP_PLATFORM_UNISOC,
    artifact_uri_matches_platform,
    scan_artifact_uri_platform,
)


def test_legacy_flat_scan_path_counts_as_mtk():
    uri = "/mnt/nfs/dedup/42/host-a_Result_foo_org.xls"
    assert scan_artifact_uri_platform(uri) == DEDUP_PLATFORM_MTK
    assert artifact_uri_matches_platform(uri, DEDUP_PLATFORM_MTK)


def test_partitioned_unisoc_path():
    uri = "/mnt/nfs/dedup/42/unisoc/host-a_Result_foo_org.xls"
    assert scan_artifact_uri_platform(uri) == DEDUP_PLATFORM_UNISOC
    assert artifact_uri_matches_platform(uri, DEDUP_PLATFORM_UNISOC)
    assert not artifact_uri_matches_platform(uri, DEDUP_PLATFORM_MTK)


def test_partitioned_mtk_path():
    uri = "/mnt/nfs/dedup/42/mtk/host-a_Result_foo_org.xls"
    assert scan_artifact_uri_platform(uri) == DEDUP_PLATFORM_MTK
