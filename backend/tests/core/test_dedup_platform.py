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


def test_device_platform_to_dedup_partition():
    """MTK/UNKNOWN/NULL → mtk（与 Agent 路由一致）；UNISOC → unisoc；QCOM → None。"""
    from backend.core.dedup_platform import dedup_platform_for_device_platform

    assert dedup_platform_for_device_platform("MTK") == DEDUP_PLATFORM_MTK
    assert dedup_platform_for_device_platform(" mtk ") == DEDUP_PLATFORM_MTK
    assert dedup_platform_for_device_platform("UNISOC") == DEDUP_PLATFORM_UNISOC
    assert dedup_platform_for_device_platform("UNKNOWN") == DEDUP_PLATFORM_MTK
    assert dedup_platform_for_device_platform(None) == DEDUP_PLATFORM_MTK
    assert dedup_platform_for_device_platform("") == DEDUP_PLATFORM_MTK
    assert dedup_platform_for_device_platform("QCOM") is None


def test_has_collection_impl_matches_agent_side_routing():
    """R4-b b1：无采集实现的平台（QCOM）必须为 False，其余口径与 Agent 路由一致。"""
    from backend.core.dedup_platform import has_collection_impl

    assert has_collection_impl("MTK") is True
    assert has_collection_impl(" mtk ") is True
    assert has_collection_impl("UNISOC") is True
    assert has_collection_impl("UNKNOWN") is True
    assert has_collection_impl(None) is True
    assert has_collection_impl("") is True
    assert has_collection_impl("QCOM") is False


def test_collection_support_is_equivalent_to_dedup_partition_today():
    """「有无采集实现」与「有无归档分区」**今天等价**，但语义不同。

    本用例把这层等价性钉住：若将来二者分叉（例如先落采集、后补归档），实现者会被
    迫显式选择，而不是让判定静默改义（见 core/dedup_platform.has_collection_impl 文档串）。
    """
    from backend.core.dedup_platform import (
        dedup_platform_for_device_platform,
        has_collection_impl,
    )

    for platform in ("MTK", "UNISOC", "UNKNOWN", "QCOM", "", None):
        assert has_collection_impl(platform) is (
            dedup_platform_for_device_platform(platform) is not None
        ), platform
