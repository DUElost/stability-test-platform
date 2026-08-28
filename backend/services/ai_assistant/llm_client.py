# -*- coding: utf-8 -*-
"""OpenAI 兼容 LLM 客户端（ADR-0031 D2，手写 httpx 载体）。

单一协议：{base_url}/chat/completions + function calling，v1 非流式。
载体收敛在本模块（tools/orchestrator 不直接碰 HTTP）——切换官方 SDK 的
迁移面即本文件（触发复议条件 #6）。
"""

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class AiNotConfigured(RuntimeError):
    """助手未配置（enabled=false 或三元组缺失）。"""


class AiAuthError(RuntimeError):
    """供应商鉴权失败（401/403）。"""


class AiUpstreamTimeout(RuntimeError):
    """请求超时。"""


class AiBadResponse(RuntimeError):
    """非预期响应形状或非 2xx 状态。"""


@dataclass
class ToolCallRequest:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class AssistantReply:
    content: str
    tool_calls: list[ToolCallRequest] = field(default_factory=list)
    usage: dict[str, int] | None = None
    latency_ms: int | None = None


def normalize_base_url(base_url: str) -> str:
    """URL 归一：兼容「含 /v1」「不含 /v1」「已含全路径」三种配置习惯。

    - 已以 /chat/completions 结尾：原样使用
    - 以 /v1 结尾：追加 /chat/completions
    - 其他：追加 /v1/chat/completions
    """
    url = (base_url or "").strip().rstrip("/")
    if not url:
        raise AiNotConfigured("base_url is empty")
    if url.endswith("/chat/completions"):
        return url
    if url.endswith("/v1"):
        return f"{url}/chat/completions"
    return f"{url}/v1/chat/completions"


class LlmClient:
    """最小 OpenAI 兼容 chat/completions 客户端。

    transport 参数仅供测试注入（httpx.MockTransport）；生产留空走默认。
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float = 120.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._endpoint = normalize_base_url(base_url)
        self._api_key = (api_key or "").strip()
        self._model = model
        if not self._api_key:
            raise AiNotConfigured("api_key is empty")
        if not self._model:
            raise AiNotConfigured("model is empty")
        self._timeout = timeout_seconds
        self._transport = transport

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.2,
    ) -> AssistantReply:
        body: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"

        started = time.monotonic()
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, transport=self._transport
            ) as client:
                resp = await client.post(
                    self._endpoint,
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    json=body,
                )
        except httpx.TimeoutException as exc:
            raise AiUpstreamTimeout(f"llm request timeout: {exc}") from exc
        except (httpx.HTTPError, ImportError) as exc:
            # ImportError：环境代理（如 SOCKS）缺可选依赖时 httpx 在构建请求阶段抛出
            raise AiBadResponse(f"llm transport error: {exc}") from exc

        latency_ms = int((time.monotonic() - started) * 1000)

        if resp.status_code in (401, 403):
            raise AiAuthError(f"llm auth failed ({resp.status_code})")
        if resp.status_code != 200:
            snippet = resp.text[:300]
            raise AiBadResponse(
                f"llm unexpected status {resp.status_code}: {snippet}"
            )

        try:
            payload = resp.json()
            choice = payload["choices"][0]["message"]
        except (ValueError, KeyError, IndexError) as exc:
            raise AiBadResponse(f"llm malformed response: {exc}") from exc

        tool_calls: list[ToolCallRequest] = []
        for raw in choice.get("tool_calls") or []:
            try:
                fn = raw["function"]
                args_raw = fn.get("arguments") or "{}"
                arguments = (
                    args_raw if isinstance(args_raw, dict)
                    else json.loads(args_raw)
                )
            except (KeyError, TypeError, ValueError):
                # 单个 tool_call 形状坏 → 跳过并让模型在下一轮重试
                logger.warning("llm_tool_call_malformed raw=%r", raw)
                continue
            tool_calls.append(
                ToolCallRequest(id=str(raw.get("id", "")), name=str(fn.get("name", "")), arguments=arguments)
            )

        usage_raw = payload.get("usage") or {}
        usage = {
            "prompt_tokens": int(usage_raw.get("prompt_tokens", 0)),
            "completion_tokens": int(usage_raw.get("completion_tokens", 0)),
        }
        return AssistantReply(
            content=str(choice.get("content") or ""),
            tool_calls=tool_calls,
            usage=usage,
            latency_ms=latency_ms,
        )
