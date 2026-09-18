from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from backend.api.schemas.base import ORMBaseModel


class AuditLogOut(ORMBaseModel):
    id: int
    user_id: Optional[int] = None
    username: Optional[str] = None
    action: str
    resource_type: str
    resource_id: Optional[str] = None
    details: Dict[str, Any] = Field(default_factory=dict)
    ip_address: Optional[str] = None
    timestamp: datetime


class AuditFacetValue(BaseModel):
    """一个**真实写入过**的筛选值 + 它在全表里的条数。"""

    value: str
    count: int


class AuditFacetsOut(BaseModel):
    """审计筛选候选（#2629）——值域由 `audit_logs` 里实际存在的 distinct 值给出。

    为什么要有这个端点：前端原先硬编码「9 个资源 / 6 个操作」，其中 6 个字面量在
    **写入侧根本不存在**（`tool`/`tool_category`/`template`/`dispatch`/`start`/`cancel`），
    管理员选中就看到「共 0 条」——审计面上这比报错危险（会被读成「没人做过这件事」）；
    反过来真实存在的 `session`/`plan_run`/`job_instance` 等 18 种资源类型没有任何入口。
    把「有哪些可筛」交回数据本身之后，**列表里出现的值一定筛得出东西**。

    口径是**全表**，不随时间或其它筛选收窄：否则「选项随选择消失」会让已选值在下拉里
    找不到，比原来的假阴性更难解释。
    """

    resource_types: List[AuditFacetValue] = Field(default_factory=list)
    actions: List[AuditFacetValue] = Field(default_factory=list)
