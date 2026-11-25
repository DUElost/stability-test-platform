"""物理设备库存服务端查询、三级 Facets 聚合与跨页批量选择预览（#496 / #4117 阶段 B2-1）。

设计约束（``docs/design/2026-11-24-list-pagination-and-facets-contract.md`` §3）：
- 单一事实源：复用 ``inventory_status.device_effective_status_expr`` 与 ``device_lifecycle.not_stale_condition``，
  在同一请求内共享统一 ``now`` 时间基准；
- 严格纯只读、单行投影：单次 ``LEFT OUTER JOIN Host`` 保留无主机设备，项目归属采用
  ``EXISTS`` / ``NOT EXISTS``，标签搜索采用数组类型保护的 ``EXISTS``，绝不因展开标签或联表
  导致设备重复计数；
- 排序白名单：首版仅允许 ``serial`` / ``host_id``，固定 ``NULLS LAST`` 与 ``Device.id ASC`` 尾键，
  杜绝心跳遥测列排序引发跨页漂移；
- 跨页写操作安全：通过 ``build_device_selection_preview`` 冻结匹配 ID，超限时返回空列表并置
  ``exceeds_limit=True``，配合写端点上限校验防止静默部分执行。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sqlalchemy import (
    String,
    and_,
    case,
    cast,
    exists,
    func,
    literal,
    literal_column,
    or_,
    select,
    tuple_,
)
from sqlalchemy.dialects.postgresql import JSONB as PG_JSONB
from sqlalchemy.orm import Session, contains_eager

from backend.api.schemas.device import (
    DeviceFacetOptionsOut,
    DeviceFacetStatusCountsOut,
    DeviceFacetsOut,
    DeviceHostFacetOptionOut,
    DeviceListItemOut,
    DeviceOut,
    DeviceSelectionPreviewOut,
)
from backend.core.device_lifecycle import is_retire_suggested, is_stale
from backend.models.host import Device, Host
from backend.models.project import TestProject
from backend.models.project_model import ProjectModel
from backend.services.device_lifecycle import not_stale_condition
from backend.services.inventory_status import (
    device_effective_status_expr,
    effective_device_status,
)
from backend.services.vbus_usb_presence import powered_off_slot_keys, usb_presence_for

# 与 backend/api/routes/projects.py 的库存口径一致（ADR-0029 v2.5）：
# SEED 项目成员不算真实归属映射。
USER_PROJECT_SOURCE = "USER"

UNASSIGNED_HOST_SENTINEL = "unassigned"
UNASSIGNED_HOST_LABEL = "未分配"
UNKNOWN_MODEL_LABEL = "未知设备"
UNREPORTED_PRODUCT_LABEL = "未上报"

STATUS_NEED_ATTENTION = "need_attention"
STATUS_LOWER_ALIASES: Dict[str, str] = {
    "idle": "ONLINE",
    "testing": "BUSY",
    "offline": "OFFLINE",
    "error": "ERROR",
    "online": "ONLINE",
    "busy": "BUSY",
}

ALLOWED_SORT_BY = frozenset({"serial", "host_id"})
ALLOWED_SORT_DIR = frozenset({"asc", "desc"})
ALLOWED_PROJECTIONS = frozenset({"full", "list"})

# 跨页批量写操作的单次确认上限（读类 copy/export 不设写上限）。
DEVICE_BULK_WRITE_MAX_IDS = 500
BULK_ACTION_MAX_LIMITS: Dict[str, Optional[int]] = {
    "tags": DEVICE_BULK_WRITE_MAX_IDS,
    "assign_project": DEVICE_BULK_WRITE_MAX_IDS,
    "swipe_trail": DEVICE_BULK_WRITE_MAX_IDS,
    "skip_oobe": DEVICE_BULK_WRITE_MAX_IDS,
    "retire": DEVICE_BULK_WRITE_MAX_IDS,
    "unretire": DEVICE_BULK_WRITE_MAX_IDS,
    "copy": None,
    "export": None,
}


class DeviceQueryError(Exception):
    """设备库存查询与批量上限校验领域错误（由路由层转为 HTTPException，遵守 C2 分层契约）。"""

    def __init__(self, status_code: int, detail: Any):
        super().__init__(str(detail))
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class DeviceQueryFilters:
    """规范化后的物理设备查询筛选条件。"""

    q: Optional[str] = None
    status: Optional[str] = None
    host_id: Optional[str] = None
    model: Optional[str] = None
    product: Optional[str] = None
    version: Optional[str] = None
    tags: Optional[str] = None
    has_tags: Optional[bool] = None
    project_key: Optional[str] = None
    unassigned: bool = False
    include_stale: bool = False
    include_retired: bool = False


def _is_postgres(db: Session) -> bool:
    bind = db.get_bind()
    return bind is not None and bind.dialect.name == "postgresql"


def _escape_like_literal(value: str) -> str:
    """按字面转义 SQL LIKE 通配符 ``\\`` / ``%`` / ``_``。"""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _parse_tag_list(tags: Optional[str]) -> List[str]:
    if not tags:
        return []
    return [part.strip() for part in tags.split(",") if part.strip()]


def _normalize_status_filter(status: Optional[str]) -> Optional[str]:
    """归一状态筛选值；保留旧未知值原样透传以兼容历史测试（如 ``__NONE__``）。"""
    if status is None:
        return None
    stripped = status.strip()
    if not stripped:
        return None
    lowered = stripped.lower()
    if lowered == "all":
        return None
    if lowered == STATUS_NEED_ATTENTION:
        return STATUS_NEED_ATTENTION
    if lowered in STATUS_LOWER_ALIASES:
        return STATUS_LOWER_ALIASES[lowered]
    return stripped


def validate_sort_params(
    sort_by: Optional[str],
    sort_dir: Optional[str],
) -> Tuple[Optional[str], str]:
    """校验排序白名单，非法字段或方向一律返回 422。"""
    norm_by = sort_by.strip() if isinstance(sort_by, str) and sort_by.strip() else None
    if norm_by is not None and norm_by not in ALLOWED_SORT_BY:
        raise DeviceQueryError(
            status_code=422,
            detail={
                "code": "INVALID_SORT_BY",
                "message": f"sort_by must be one of {sorted(ALLOWED_SORT_BY)}",
                "allowed": sorted(ALLOWED_SORT_BY),
            },
        )
    norm_dir = sort_dir.strip().lower() if isinstance(sort_dir, str) and sort_dir.strip() else "asc"
    if norm_dir not in ALLOWED_SORT_DIR:
        raise DeviceQueryError(
            status_code=422,
            detail={
                "code": "INVALID_SORT_DIR",
                "message": "sort_dir must be 'asc' or 'desc'",
                "allowed": sorted(ALLOWED_SORT_DIR),
            },
        )
    return norm_by, norm_dir


def validate_projection(projection: Optional[str]) -> str:
    norm = projection.strip().lower() if isinstance(projection, str) and projection.strip() else "full"
    if norm not in ALLOWED_PROJECTIONS:
        raise DeviceQueryError(
            status_code=422,
            detail={
                "code": "INVALID_PROJECTION",
                "message": f"projection must be one of {sorted(ALLOWED_PROJECTIONS)}",
                "allowed": sorted(ALLOWED_PROJECTIONS),
            },
        )
    return norm


def enforce_bulk_write_limit(device_ids: Sequence[int], *, action: str) -> None:
    """写接口执行前校验批量上限，超限时拒绝请求，禁止静默部分执行。"""
    unique_count = len(set(device_ids))
    max_allowed = BULK_ACTION_MAX_LIMITS.get(action, DEVICE_BULK_WRITE_MAX_IDS)
    if max_allowed is not None and unique_count > max_allowed:
        raise DeviceQueryError(
            status_code=422,
            detail={
                "code": "BULK_LIMIT_EXCEEDED",
                "message": (
                    f"bulk {action} exceeds max allowed limit "
                    f"({unique_count} > {max_allowed})"
                ),
                "action": action,
                "requested": unique_count,
                "max_allowed": max_allowed,
            },
        )


def _display_model_expr():
    return case(
        (
            or_(Device.model.is_(None), func.trim(Device.model) == ""),
            literal(UNKNOWN_MODEL_LABEL),
        ),
        else_=Device.model,
    )


def _display_product_expr():
    """与前端 ``frontend/src/utils/deviceProduct.ts`` 同源的展示 Product 表达式。"""
    norm_model = func.upper(func.replace(func.coalesce(Device.model, ""), "-", "_"))
    return case(
        (
            or_(Device.product.is_(None), func.trim(Device.product) == ""),
            literal(UNREPORTED_PRODUCT_LABEL),
        ),
        (
            and_(norm_model == "MLD_NX2", Device.product == "MLD-NX2"),
            literal("NX2 · MLD-NX2"),
        ),
        (
            and_(norm_model == "MLD_NX3", Device.product == "MLD-NX3"),
            literal("NX3 · MLD-NX3"),
        ),
        (
            and_(norm_model == "MLD_NX3", Device.product == "sys_mssi_64_cn_armv82"),
            literal("NX3Carrier · sys_mssi_64_cn_armv82"),
        ),
        else_=Device.product,
    )


def _host_display_name_expr():
    """主机展示名（``Host.name`` > ``Host.ip`` > 空串；无主机返回空串）。"""
    return func.coalesce(
        func.nullif(func.trim(Host.name), ""),
        func.nullif(func.trim(Host.ip), ""),
        literal(""),
    )


def _tags_array_length_expr(*, is_pg: bool):
    """受数组类型保护的 tags 长度表达式；NULL 或非数组历史行一律按 0 处理。"""
    if is_pg:
        return case(
            (
                and_(Device.tags.isnot(None), func.jsonb_typeof(Device.tags) == "array"),
                func.jsonb_array_length(
                    case(
                        (func.jsonb_typeof(Device.tags) == "array", Device.tags),
                        else_=cast(literal("[]"), PG_JSONB),
                    )
                ),
            ),
            else_=0,
        )
    return case(
        (
            and_(Device.tags.isnot(None), func.json_type(Device.tags) == "array"),
            func.json_array_length(
                case(
                    (func.json_type(Device.tags) == "array", Device.tags),
                    else_=literal("[]"),
                )
            ),
        ),
        else_=0,
    )


def _has_tags_true_expr(*, is_pg: bool):
    return _tags_array_length_expr(is_pg=is_pg) > 0


def _tag_exact_match_expr(tag: str, *, is_pg: bool):
    if is_pg:
        safe_array = case(
            (
                and_(Device.tags.isnot(None), func.jsonb_typeof(Device.tags) == "array"),
                Device.tags,
            ),
            else_=cast(literal("[]"), PG_JSONB),
        )
        return safe_array.op("@>")(cast(json.dumps([tag]), PG_JSONB))
    safe_array = case(
        (
            and_(Device.tags.isnot(None), func.json_type(Device.tags) == "array"),
            Device.tags,
        ),
        else_=literal("[]"),
    )
    je = func.json_each(safe_array).alias("dev_tag_exact")
    return exists(
        select(literal(1))
        .select_from(je)
        .where(literal_column("dev_tag_exact.value") == tag)
    )


def _tag_substring_match_expr(like_pattern: str, *, is_pg: bool):
    """受数组类型保护的 ``EXISTS`` 标签子串匹配（不展开主查询行，避免重复计数）。"""
    if is_pg:
        safe_array = case(
            (
                and_(Device.tags.isnot(None), func.jsonb_typeof(Device.tags) == "array"),
                Device.tags,
            ),
            else_=cast(literal("[]"), PG_JSONB),
        )
        tag_elem = func.jsonb_array_elements_text(safe_array).table_valued("value").alias("dev_tag_q")
        return exists(
            select(literal(1))
            .select_from(tag_elem)
            .where(func.lower(tag_elem.c.value).like(like_pattern, escape="\\"))
        )
    safe_array = case(
        (
            and_(Device.tags.isnot(None), func.json_type(Device.tags) == "array"),
            Device.tags,
        ),
        else_=literal("[]"),
    )
    je = func.json_each(safe_array).alias("dev_tag_q")
    return exists(
        select(literal(1))
        .select_from(je)
        .where(func.lower(cast(literal_column("dev_tag_q.value"), String)).like(like_pattern, escape="\\"))
    )


def _resolve_project_id(
    db: Session,
    *,
    project_key: Optional[str],
    unassigned: bool,
) -> Optional[int]:
    if unassigned and project_key:
        raise DeviceQueryError(
            status_code=400,
            detail="unassigned and project_key are mutually exclusive",
        )
    if not project_key:
        return None
    project = (
        db.query(TestProject)
        .filter(TestProject.project_key == project_key)
        .first()
    )
    if project is None:
        raise DeviceQueryError(status_code=404, detail="project not found")
    return int(project.id)


def _scope_b_conditions(
    db: Session,
    filters: DeviceQueryFilters,
    *,
    now: datetime,
    eff_status,
    project_id: Optional[int],
) -> List[Any]:
    """作用域 B（基础集）：退役开关 + 陈旧开关（含 VBUS 关机在槽例外）+ 项目归属。"""
    conditions: List[Any] = []

    if not filters.include_retired:
        conditions.append(Host.retired_at.is_(None))
        conditions.append(Device.retired_at.is_(None))

    if not filters.include_stale:
        stale_cond = not_stale_condition(now=now, status_expr=eff_status)
        powered_off = powered_off_slot_keys(
            db.query(Host).filter(Host.retired_at.is_(None)).all(),
            now=now,
        )
        if powered_off:
            stale_cond = or_(
                stale_cond,
                tuple_(Device.host_id, Device.serial).in_(sorted(powered_off)),
            )
        conditions.append(stale_cond)

    if project_id is not None:
        conditions.append(
            exists(
                select(literal(1)).where(
                    ProjectModel.project_id == project_id,
                    ProjectModel.is_active.is_(True),
                    ProjectModel.match_value == Device.model,
                )
            )
        )

    if filters.unassigned:
        mapped_exists = exists(
            select(literal(1))
            .select_from(ProjectModel)
            .join(TestProject, TestProject.id == ProjectModel.project_id)
            .where(
                ProjectModel.is_active.is_(True),
                TestProject.source == USER_PROJECT_SOURCE,
                ProjectModel.match_value == Device.model,
            )
        )
        conditions.append(or_(Device.model.is_(None), ~mapped_exists))

    return conditions


def _scope_h_extra_conditions(
    filters: DeviceQueryFilters,
    *,
    eff_status,
    is_pg: bool,
) -> List[Any]:
    """作用域 H 增量条件：status + tags + has_tags（不含 host_id）。"""
    conditions: List[Any] = []
    norm_status = _normalize_status_filter(filters.status)
    if norm_status == STATUS_NEED_ATTENTION:
        conditions.append(eff_status.in_(("OFFLINE", "ERROR")))
    elif norm_status is not None:
        conditions.append(eff_status == norm_status)

    for tag in _parse_tag_list(filters.tags):
        conditions.append(_tag_exact_match_expr(tag, is_pg=is_pg))

    if filters.has_tags is True:
        conditions.append(_has_tags_true_expr(is_pg=is_pg))
    elif filters.has_tags is False:
        conditions.append(~_has_tags_true_expr(is_pg=is_pg))

    return conditions


def _host_id_condition(host_id: Optional[str]):
    if host_id is None:
        return None
    stripped = host_id.strip()
    if not stripped or stripped.lower() == "all":
        return None
    if stripped == UNASSIGNED_HOST_SENTINEL:
        return Device.host_id.is_(None)
    return Device.host_id == stripped


def _detail_and_search_conditions(
    filters: DeviceQueryFilters,
    *,
    is_pg: bool,
) -> List[Any]:
    """列表全量筛选的剩余条件：host_id + model + product + version + q。"""
    conditions: List[Any] = []

    host_cond = _host_id_condition(filters.host_id)
    if host_cond is not None:
        conditions.append(host_cond)

    if filters.model is not None:
        norm_model = filters.model.strip()
        if norm_model and norm_model.lower() != "all":
            if norm_model == UNKNOWN_MODEL_LABEL:
                conditions.append(or_(Device.model.is_(None), func.trim(Device.model) == ""))
            else:
                conditions.append(Device.model == norm_model)

    if filters.product is not None:
        norm_product = filters.product.strip()
        if norm_product and norm_product.lower() != "all":
            conditions.append(Device.product == norm_product)

    if filters.version is not None:
        norm_version = filters.version.strip()
        if norm_version and norm_version.lower() != "all":
            conditions.append(Device.build_display_id == norm_version)

    if filters.q is not None and filters.q.strip():
        pattern = f"%{_escape_like_literal(filters.q.strip().lower())}%"
        conditions.append(
            or_(
                func.lower(func.coalesce(Device.serial, "")).like(pattern, escape="\\"),
                func.lower(_display_model_expr()).like(pattern, escape="\\"),
                func.lower(_display_product_expr()).like(pattern, escape="\\"),
                func.lower(func.coalesce(Device.build_display_id, "")).like(pattern, escape="\\"),
                func.lower(_host_display_name_expr()).like(pattern, escape="\\"),
                _tag_substring_match_expr(pattern, is_pg=is_pg),
            )
        )

    return conditions


def build_device_order_by(sort_by: Optional[str], sort_dir: str):
    """构造固定 ``NULLS LAST`` + ``Device.id ASC`` 尾键的稳定排序子句。"""
    if sort_by == "serial":
        primary = (
            Device.serial.desc().nullslast()
            if sort_dir == "desc"
            else Device.serial.asc().nullslast()
        )
        return (primary, Device.id.asc())
    primary = (
        Device.host_id.desc().nullslast()
        if sort_dir == "desc"
        else Device.host_id.asc().nullslast()
    )
    return (primary, Device.id.asc())


def build_filtered_device_query(
    db: Session,
    filters: DeviceQueryFilters,
    *,
    now: datetime,
    sort_by: Optional[str] = None,
    sort_dir: str = "asc",
    eager_host: bool = True,
):
    """构建应用全部查询条件的物理设备查询（严格一台设备一行）。"""
    is_pg = _is_postgres(db)
    project_id = _resolve_project_id(
        db,
        project_key=filters.project_key,
        unassigned=filters.unassigned,
    )
    eff_status = device_effective_status_expr(now=now)
    conditions = [
        *_scope_b_conditions(db, filters, now=now, eff_status=eff_status, project_id=project_id),
        *_scope_h_extra_conditions(filters, eff_status=eff_status, is_pg=is_pg),
        *_detail_and_search_conditions(filters, is_pg=is_pg),
    ]
    query = db.query(Device).outerjoin(Host, Device.host_id == Host.id)
    if eager_host:
        query = query.options(contains_eager(Device.host))
    if conditions:
        query = query.filter(*conditions)
    return query.order_by(*build_device_order_by(sort_by, sort_dir))


def _status_counts_for_conditions(
    db: Session,
    conditions: Sequence[Any],
    *,
    eff_status,
    is_pg: bool,
) -> DeviceFacetStatusCountsOut:
    has_tags_expr = _has_tags_true_expr(is_pg=is_pg)
    stmt = (
        select(
            func.count(Device.id).label("all"),
            func.coalesce(func.sum(case((eff_status == "ONLINE", 1), else_=0)), 0).label("ONLINE"),
            func.coalesce(func.sum(case((eff_status == "BUSY", 1), else_=0)), 0).label("BUSY"),
            func.coalesce(func.sum(case((eff_status == "OFFLINE", 1), else_=0)), 0).label("OFFLINE"),
            func.coalesce(func.sum(case((eff_status == "ERROR", 1), else_=0)), 0).label("ERROR"),
            func.coalesce(
                func.sum(case((eff_status.in_(("OFFLINE", "ERROR")), 1), else_=0)),
                0,
            ).label("need_attention"),
            func.coalesce(func.sum(case((has_tags_expr, 1), else_=0)), 0).label("tagged"),
        )
        .select_from(Device)
        .outerjoin(Host, Device.host_id == Host.id)
    )
    if conditions:
        stmt = stmt.where(*conditions)
    row = db.execute(stmt).one()
    return DeviceFacetStatusCountsOut(
        all=int(row.all or 0),
        ONLINE=int(row.ONLINE or 0),
        BUSY=int(row.BUSY or 0),
        OFFLINE=int(row.OFFLINE or 0),
        ERROR=int(row.ERROR or 0),
        need_attention=int(row.need_attention or 0),
        tagged=int(row.tagged or 0),
    )


def build_device_facets(
    db: Session,
    filters: DeviceQueryFilters,
    *,
    now: Optional[datetime] = None,
) -> DeviceFacetsOut:
    """按三级作用域（B / H / D）聚合设备状态计数与下拉候选项（不含 ``options.tags``）。"""
    ref_now = now or datetime.now(timezone.utc)
    is_pg = _is_postgres(db)
    project_id = _resolve_project_id(
        db,
        project_key=filters.project_key,
        unassigned=filters.unassigned,
    )
    eff_status = device_effective_status_expr(now=ref_now)

    # Scope B：项目归属 + 陈旧/退役开关 → status_counts
    scope_b = _scope_b_conditions(
        db,
        filters,
        now=ref_now,
        eff_status=eff_status,
        project_id=project_id,
    )
    status_counts = _status_counts_for_conditions(
        db,
        scope_b,
        eff_status=eff_status,
        is_pg=is_pg,
    )

    # Scope H：Scope B + status + tags + has_tags → options.hosts（不被已选 host_id 锁死）
    scope_h = [*scope_b, *_scope_h_extra_conditions(filters, eff_status=eff_status, is_pg=is_pg)]
    host_rows_stmt = (
        select(Device.host_id, Host.name, Host.ip)
        .select_from(Device)
        .outerjoin(Host, Device.host_id == Host.id)
        .group_by(Device.host_id, Host.name, Host.ip)
    )
    if scope_h:
        host_rows_stmt = host_rows_stmt.where(*scope_h)
    host_rows = db.execute(host_rows_stmt).all()

    host_by_key: Dict[str, str] = {}
    for raw_host_id, host_name, host_ip in host_rows:
        if raw_host_id is None or not str(raw_host_id).strip():
            key = UNASSIGNED_HOST_SENTINEL
            label = UNASSIGNED_HOST_LABEL
        else:
            key = str(raw_host_id).strip()
            label = (
                (host_name or "").strip()
                or (host_ip or "").strip()
                or key
            )
        if key not in host_by_key:
            host_by_key[key] = label
    host_options = [
        DeviceHostFacetOptionOut(value=k, label=v)
        for k, v in sorted(host_by_key.items(), key=lambda pair: (pair[1].lower(), pair[0]))
    ]

    # 仅当已选 host_id 仍落在作用域 H 的有效主机候选内时，才作用于作用域 D
    requested_host = (filters.host_id or "").strip()
    effective_host_id = (
        requested_host
        if requested_host and requested_host.lower() != "all" and requested_host in host_by_key
        else None
    )

    # Scope D：Scope H + 有效 host_id → models / products / versions（不受 q 或同级下拉影响）
    scope_d = list(scope_h)
    if effective_host_id is not None:
        host_cond = _host_id_condition(effective_host_id)
        if host_cond is not None:
            scope_d.append(host_cond)

    display_model = _display_model_expr().label("display_model")
    model_stmt = (
        select(display_model)
        .select_from(Device)
        .outerjoin(Host, Device.host_id == Host.id)
        .group_by(display_model)
    )
    if scope_d:
        model_stmt = model_stmt.where(*scope_d)
    models = sorted(
        {str(val) for (val,) in db.execute(model_stmt).all() if val},
        key=lambda v: v.lower(),
    )

    product_stmt = (
        select(Device.product)
        .select_from(Device)
        .outerjoin(Host, Device.host_id == Host.id)
        .where(Device.product.isnot(None), func.trim(Device.product) != "")
        .group_by(Device.product)
    )
    if scope_d:
        product_stmt = product_stmt.where(*scope_d)
    products = sorted({str(val) for (val,) in db.execute(product_stmt).all() if val})

    version_stmt = (
        select(Device.build_display_id)
        .select_from(Device)
        .outerjoin(Host, Device.host_id == Host.id)
        .where(Device.build_display_id.isnot(None), func.trim(Device.build_display_id) != "")
        .group_by(Device.build_display_id)
    )
    if scope_d:
        version_stmt = version_stmt.where(*scope_d)
    versions = sorted(
        {str(val) for (val,) in db.execute(version_stmt).all() if val},
        key=lambda v: v.lower(),
    )

    return DeviceFacetsOut(
        status_counts=status_counts,
        options=DeviceFacetOptionsOut(
            hosts=host_options,
            models=models,
            products=products,
            versions=versions,
        ),
    )


def build_device_selection_preview(
    db: Session,
    filters: DeviceQueryFilters,
    *,
    action: str,
    now: Optional[datetime] = None,
) -> DeviceSelectionPreviewOut:
    """跨页批量选择预览：冻结匹配 ID 并计算受影响型号、退役与陈旧度摘要。"""
    ref_now = now or datetime.now(timezone.utc)
    is_pg = _is_postgres(db)
    project_id = _resolve_project_id(
        db,
        project_key=filters.project_key,
        unassigned=filters.unassigned,
    )
    eff_status = device_effective_status_expr(now=ref_now)
    conditions = [
        *_scope_b_conditions(db, filters, now=ref_now, eff_status=eff_status, project_id=project_id),
        *_scope_h_extra_conditions(filters, eff_status=eff_status, is_pg=is_pg),
        *_detail_and_search_conditions(filters, is_pg=is_pg),
    ]

    status_counts = _status_counts_for_conditions(
        db,
        conditions,
        eff_status=eff_status,
        is_pg=is_pg,
    )
    total_matched = status_counts.all
    max_allowed = BULK_ACTION_MAX_LIMITS.get(action, DEVICE_BULK_WRITE_MAX_IDS)
    exceeds_limit = max_allowed is not None and total_matched > max_allowed

    # 窄列读取：不加载大 JSON 字段（extra / return_state）即可算出冻结 ID 与影响摘要
    rows_stmt = (
        select(
            Device.id,
            Device.serial,
            Device.model,
            Device.retired_at,
            Device.last_seen,
            eff_status.label("eff_status"),
        )
        .select_from(Device)
        .outerjoin(Host, Device.host_id == Host.id)
        .order_by(*build_device_order_by(None, "asc"))
    )
    if conditions:
        rows_stmt = rows_stmt.where(*conditions)
    rows = db.execute(rows_stmt).all()

    affected_models_set = set()
    without_model_serials: List[str] = []
    retired_count = 0
    stale_count = 0
    matched_ids: List[int] = []

    for row in rows:
        matched_ids.append(int(row.id))
        model_val = (row.model or "").strip() if row.model is not None else ""
        if model_val:
            affected_models_set.add(model_val)
        else:
            without_model_serials.append(str(row.serial))
        if row.retired_at is not None:
            retired_count += 1
        if is_stale(str(row.eff_status), row.last_seen, now=ref_now):
            stale_count += 1

    return DeviceSelectionPreviewOut(
        action=action,
        total_matched=total_matched,
        max_allowed=max_allowed,
        exceeds_limit=exceeds_limit,
        # 超过上限时不得静默截断，显式返回空 ID 列表强制调用方收窄筛选范围。
        device_ids=[] if exceeds_limit else matched_ids,
        status_counts=status_counts,
        affected_models=sorted(affected_models_set),
        devices_without_model_serials=sorted(without_model_serials),
        retired_count=retired_count,
        active_count=total_matched - retired_count,
        stale_count=stale_count,
    )


def serialize_device_list_item(
    device: Device,
    model_to_project: Dict[str, Tuple[int, str]],
    *,
    now: datetime,
) -> DeviceListItemOut:
    """只读构造有界设备列表 DTO（不含无界 ``extra``，附带预关联 ``host_name``）。"""
    eff_status = effective_device_status(device, device.host, now=now)
    out = DeviceListItemOut.model_validate(device)
    out.status = eff_status
    out.is_stale = is_stale(eff_status, device.last_seen, now=now)
    out.retire_suggested = is_retire_suggested(eff_status, device.last_seen, now=now)
    entry = model_to_project.get(device.model) if device.model else None
    out.project_key = entry[1] if entry else None
    out.attribution_source = "mapped" if (device.model and entry is not None) else "unmapped"
    out.usb_presence = usb_presence_for(device.host, device.serial, now=now)
    if device.host is not None:
        out.host_name = (device.host.name or "").strip() or (device.host.ip or "").strip() or None
    else:
        out.host_name = None
    return out


def serialize_device_full_readonly(
    device: Device,
    model_to_project: Dict[str, Tuple[int, str]],
    *,
    now: datetime,
) -> DeviceOut:
    """只读构造完整 ``DeviceOut``（供旧版列表与单机详情共用）。"""
    eff_status = effective_device_status(device, device.host, now=now)
    out = DeviceOut.model_validate(device)
    out.status = eff_status
    out.is_stale = is_stale(eff_status, device.last_seen, now=now)
    out.retire_suggested = is_retire_suggested(eff_status, device.last_seen, now=now)
    entry = model_to_project.get(device.model) if device.model else None
    out.project_key = entry[1] if entry else None
    out.attribution_source = "mapped" if (device.model and entry is not None) else "unmapped"
    out.usb_presence = usb_presence_for(device.host, device.serial, now=now)
    return out
