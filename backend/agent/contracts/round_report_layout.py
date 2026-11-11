"""B8 v1.9 影子报表与清单的共享路径契约（纯路径函数）。"""

from pathlib import Path


def _round_id(value: int) -> str:
    if type(value) is not int or value <= 0:
        raise ValueError("fact_round_id must be a positive int")
    return str(value)


def _component(value: str) -> str:
    if not value or value in {".", ".."} or "/" in value or "\\" in value:
        raise ValueError("invalid round report path component")
    return value


def round_report_dir(
    nfs_root: str, plan_run_id: int, fact_round_id: int, platform: str,
) -> Path:
    return (
        Path(nfs_root) / "dedup" / _round_id(plan_run_id) / "_rounds"
        / _round_id(fact_round_id) / _component(platform)
    )


def round_manifest_path(
    nfs_root: str, plan_run_id: int, fact_round_id: int, host_id: str,
) -> Path:
    return (
        Path(nfs_root) / "_meta" / _round_id(plan_run_id) / "_rounds"
        / _round_id(fact_round_id) / f"{_component(host_id)}.json"
    )


def delivery_export_path(
    nfs_root: str, plan_run_id: int, platform: str, version_no: int,
) -> Path:
    """B8 G5 交付物统计导出：``_meta/{run}/_delivery/{platform}/v{n}.json``。"""
    if type(version_no) is not int or version_no <= 0:
        raise ValueError("version_no must be a positive int")
    return (
        Path(nfs_root) / "_meta" / _round_id(plan_run_id) / "_delivery"
        / _component(platform) / f"v{version_no}.json"
    )
