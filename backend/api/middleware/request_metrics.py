"""控制面请求级指标接线（#743 期望 2）。

背景
----
``backend/core/metrics.py`` 早已注册 ``stability_api_requests_total`` /
``stability_api_request_duration_seconds`` 与 ``record_api_request(...)``，
但**从未被任何调用点接线**（全仓 grep 只有定义），所以：

- #729 的幽灵 ``POST .../jobs/*/complete`` 404 风暴持续约一个月，
  在指标面上**完全不可见**，只能靠人翻日志；
- #743 期望 2（「``POST .../jobs/*/complete`` 404 速率异常」）因此无法落地。

本模块只做**接线**，不改 ``metrics.py``（该文件另有在办会话），也不改任何
请求的语义/响应：只统计。

基数纪律（重要）
----------------
``endpoint`` 标签若直接用 ``request.url.path``，则每个带 ID 的路径都会成为
一条新时间序列 → 指标基数随业务实体数线性膨胀（仓库既有的
``rate_limiter_evicted_total`` 注释即为同类警告）。因此：

1. **命中路由**的请求：用路由**模板**（``request.scope["route"].path``，
   形如 ``/api/v1/jobs/{job_id}/complete``），天然有界；
2. **未命中路由**的请求（404——正是 #743 要观测的幽灵端点形态）：模板不存在，
   退化为**归一化后的路径**：把 ID 形态的段（纯数字 / UUID / 长十六进制）
   折叠为 ``{id}``，并限制段数；超出上限一律归到 ``other``。

即：``/api/v1/jobs/12345/complete`` → ``/api/v1/jobs/{id}/complete``，
既保住 #743 需要的可辨识信号，又不让任意路径撑爆注册表。
"""
from __future__ import annotations

import re
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from backend.core.metrics import record_api_request

# 段数超过此值 → 归为 other（防止长路径／扫描器制造无限序列）
_MAX_SEGMENTS = 8

# 归一化的不确定标签值（非业务路径）：与具体路径合并，避免基数漂移
_OTHER_ENDPOINT = "other"

_UUID_RE = re.compile(
    r"\A[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\Z"
)
# 纯数字、长十六进制（>=8）、长八进制/十进制串
_NUMERIC_RE = re.compile(r"\A\d+\Z")
_HEX_RE = re.compile(r"\A[0-9a-fA-F]{8,}\Z")


def _is_identifier(segment: str) -> bool:
    """判定一段是否像业务 ID（用于折叠为 ``{id}``）。"""
    return bool(
        _UUID_RE.match(segment) or _NUMERIC_RE.match(segment) or _HEX_RE.match(segment)
    )


def normalize_unmatched_path(path: str) -> str:
    """未命中路由时把路径折叠为有界标签（见模块 docstring 的基数纪律）。"""
    if not path or not path.startswith("/"):
        return _OTHER_ENDPOINT
    segments = [seg for seg in path.split("/") if seg]
    if not segments or len(segments) > _MAX_SEGMENTS:
        return _OTHER_ENDPOINT
    collapsed: list[str] = []
    last_is_id = False
    for seg in segments:
        if _is_identifier(seg):
            # 连续 ID 段只保留一个 {id}，进一步收敛基数
            if not last_is_id:
                collapsed.append("{id}")
            last_is_id = True
            continue
        last_is_id = False
        collapsed.append(seg)
    return "/" + "/".join(collapsed)


def endpoint_label(request: Request) -> str:
    """命中路由 → 模板；未命中 → 归一化路径。"""
    route = request.scope.get("route")
    template = getattr(route, "path", None)
    if isinstance(template, str) and template:
        return template
    return normalize_unmatched_path(request.url.path)


class ApiRequestMetricsMiddleware(BaseHTTPMiddleware):
    """把每个请求的 方法/端点/状态码/耗时 交给 record_api_request（#743 期望 2）。

    只统计，不改语义：响应对象原样返回；``call_next`` 抛异常时记 500 后照常抛出
    （不吞异常、不改变错误处理链）。
    """

    async def dispatch(self, request: Request, call_next):
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            # 未捕获异常最终由 global_exception_handler 变成 500；
            # 这里先记一笔再抛出，保证 5xx 也被统计，且不改变异常传播。
            record_api_request(
                request.method,
                endpoint_label(request),
                500,
                time.perf_counter() - started,
            )
            raise
        record_api_request(
            request.method,
            endpoint_label(request),
            response.status_code,
            time.perf_counter() - started,
        )
        return response
