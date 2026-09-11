# -*- coding: utf-8 -*-
"""通知投递结果归一化与失败分类 + 统一重试策略（#1167 P1/P2，ADR-0036 D1/D2/D5/D9）。

契约要点（ADR-0036 Accepted v1.0）：

- **D1**：所有渠道适配器把底层结果归一化为 :class:`DeliveryResult`；
  ``ACCEPTED`` = 渠道明确接受请求，≠ 用户已收到（``DELIVERED`` 是挂起项）；
- **D2**：失败三态 ``REJECTED_PERMANENT`` / ``REJECTED_TRANSIENT`` / ``UNKNOWN``
  ——``UNKNOWN``（超时/断连，请求可能已成功）与瞬时失败必须可区分；
- **D5**：可重试性/退避/上限构成统一策略对象；``UNKNOWN`` 计入退避与上限；
- **D9**：新渠道不得自定义"成功"——一律经本模块构造结果。

协议状态码/异常类型 → 结果类的映射属 **adapter contract**（ADR 正文有意不写），
集中在本模块保证跨渠道一致；具体数值（超时秒数、重试上限）属实现/配置。
"""

from __future__ import annotations

import smtplib
from dataclasses import dataclass
from enum import Enum
from typing import Any

import requests


class DeliveryOutcome(str, Enum):
    """单次投递尝试的结果类（D2 四分类）。"""

    ACCEPTED = "ACCEPTED"
    REJECTED_PERMANENT = "REJECTED_PERMANENT"
    REJECTED_TRANSIENT = "REJECTED_TRANSIENT"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class DeliveryResult:
    """一次投递尝试的归一化结果（D1）。"""

    outcome: DeliveryOutcome
    detail: str = ""
    channel_type: str = ""

    @property
    def accepted(self) -> bool:
        return self.outcome is DeliveryOutcome.ACCEPTED

    @property
    def retryable(self) -> bool:
        """D2/D5：瞬时失败与 UNKNOWN 可重试；永久拒绝不重试。"""
        return self.outcome in (
            DeliveryOutcome.REJECTED_TRANSIENT,
            DeliveryOutcome.UNKNOWN,
        )

    def record(self) -> dict[str, Any]:
        """投递事实记录的字典形态（落 NotificationLog.context.channel_delivery）。"""
        return {
            "status": "ok" if self.accepted else "failed",
            "outcome": self.outcome.value,
            "error": self.detail,
        }


def accepted(channel_type: str = "", detail: str = "") -> DeliveryResult:
    return DeliveryResult(DeliveryOutcome.ACCEPTED, detail, channel_type)


def rejected_permanent(detail: str, *, channel_type: str = "") -> DeliveryResult:
    return DeliveryResult(DeliveryOutcome.REJECTED_PERMANENT, detail, channel_type)


def rejected_transient(detail: str, *, channel_type: str = "") -> DeliveryResult:
    return DeliveryResult(DeliveryOutcome.REJECTED_TRANSIENT, detail, channel_type)


def unknown(detail: str, *, channel_type: str = "") -> DeliveryResult:
    return DeliveryResult(DeliveryOutcome.UNKNOWN, detail, channel_type)


def classify_http_status(status: int, *, channel_type: str = "") -> DeliveryResult:
    """HTTP 状态码分类（adapter contract）。

    - 2xx/3xx（跟随重定向后）：渠道接受了请求 → ACCEPTED；
    - 429 / 5xx：明确瞬时 → REJECTED_TRANSIENT（可重试）；
    - 其余 4xx：配置/鉴权/语义错，再试无意义 → REJECTED_PERMANENT。
    """
    if 200 <= status < 400:
        return accepted(channel_type, f"HTTP {status}")
    if status == 429 or status >= 500:
        return rejected_transient(f"HTTP {status}", channel_type=channel_type)
    return rejected_permanent(f"HTTP {status}", channel_type=channel_type)


def classify_exception(exc: BaseException, *, channel_type: str = "") -> DeliveryResult:
    """异常分类（adapter contract）。

    保守原则：能确定未送达 → TRANSIENT；可能已送达（超时/断连）→ UNKNOWN；
    配置/参数类错误 → PERMANENT；无法识别 → UNKNOWN（计入重试上限）。
    """
    # 认证失败/收件人被拒：配置或地址语义错，重试无意义
    if isinstance(exc, (
        smtplib.SMTPAuthenticationError,
        smtplib.SMTPRecipientsRefused,
        smtplib.SMTPSenderRefused,
    )):
        return rejected_permanent(str(exc), channel_type=channel_type)
    # 超时：请求可能已被对端处理
    if isinstance(exc, requests.Timeout):
        return unknown(f"timeout: {exc}", channel_type=channel_type)
    # 连接中断（可能已在发送后）—— SMTP DATA 后断连同理
    if isinstance(exc, (smtplib.SMTPServerDisconnected, requests.ConnectionError)):
        if isinstance(exc, requests.ConnectionError):
            # requests 不区分"连接未建立"与"发送中断"；保守记 TRANSIENT 的
            # 常见情形是前者（明确未送达），发送中断的重复投递由 at-least-once
            # 语义承接（ADR D7）。
            return rejected_transient(f"connection error: {exc}", channel_type=channel_type)
        return unknown(f"server disconnected: {exc}", channel_type=channel_type)
    # 其余 SMTP 异常/网络 IO 错误：明确未完成，瞬时
    if isinstance(exc, (smtplib.SMTPException, OSError)):
        return rejected_transient(str(exc), channel_type=channel_type)
    # 配置/参数错误（缺 URL/收件人等）→ 永久
    if isinstance(exc, (ValueError, TypeError, KeyError)):
        return rejected_permanent(str(exc), channel_type=channel_type)
    # 未识别异常：保守归 UNKNOWN（计入退避与上限，不形成无上限重试源）
    return unknown(f"{type(exc).__name__}: {exc}", channel_type=channel_type)


@dataclass(frozen=True)
class RetryPolicy:
    """统一重试策略（D5）：可重试性 + 退避 + 上限，逐通道可配。

    ``attempt`` 语义 = 已完成的投递尝试次数（首次投递后为 1）。
    具体数值属实现/配置层；本对象提供缺省值并在测试中锁定语义。
    """

    max_attempts: int = 3
    base_delay_s: float = 5.0
    max_delay_s: float = 120.0

    def should_retry(self, result: DeliveryResult, attempt: int) -> bool:
        return result.retryable and attempt < self.max_attempts

    def delay_for(self, attempt: int) -> float:
        """第 attempt 次尝试失败后的退避时长（指数 + 上限）。"""
        if attempt < 1:
            attempt = 1
        return min(self.max_delay_s, self.base_delay_s * (2 ** (attempt - 1)))


DEFAULT_RETRY_POLICY = RetryPolicy()
