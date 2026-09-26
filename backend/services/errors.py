"""Services 层领域异常（#3295，C2 棘轮出口）。

services 不感知 HTTP：业务路径抛本模块的语义异常，由 `backend/api/error_handlers.py`
注册的统一 handler 翻译为 HTTP 状态码与响应信封。重构前后对外响应逐字一致——
`detail` 即原 ``HTTPException.detail`` 载荷（纯字符串，或端点约定的结构化 dict）。

`status` 是异常应翻译成的状态码（语义归属，纯 int，不引入任何 web 框架）。
新增语义异常请在本文件补子类，并在 handler 的映射保持不变量下由基类自动承接。
"""

from __future__ import annotations


class ServiceError(Exception):
    """领域异常基类。detail 原样进入响应信封 {"detail": ...}。"""

    status: int = 400

    def __init__(self, detail=None):
        self.detail = detail
        super().__init__(detail)


class BadRequest(ServiceError):
    """调用方输入不满足业务前置（原 400）。"""

    status = 400


class Forbidden(ServiceError):
    """请求本身合法，但跨身份/跨归属被拒（原 403，如事件身份字段不一致）。"""

    status = 403


class NotFound(ServiceError):
    """目标资源不存在（原 404）。"""

    status = 404


class UnprocessableEntity(ServiceError):
    """语义层面不可受理的输入（原 422：格式合法但违反业务规则，如保留名/SEED 限制）。"""

    status = 422


class Conflict(ServiceError):
    """与当前持久状态冲突（原 409：fencing 失效、幂等竞争、状态机表外迁移等）。"""

    status = 409


class BatchTooLarge(ServiceError):
    """批量请求条数超上界（原 413）。"""

    status = 413


class Timeout(ServiceError):
    """操作超出等待上界仍未达成（原 504）。"""

    status = 504


class UpgradeRequired(ServiceError):
    """调用方版本过旧，必须升级后才能继续使用（原 426）。"""

    status = 426
