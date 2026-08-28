"""AI 助手 LLM 客户端单测（httpx.MockTransport，无网络无 PG）。"""

import json

import httpx
import pytest

from backend.services.ai_assistant.llm_client import (
    AiAuthError,
    AiBadResponse,
    AiNotConfigured,
    AiUpstreamTimeout,
    LlmClient,
    normalize_base_url,
)


def _client(handler, **kw):
    return LlmClient(
        base_url=kw.get("base_url", "https://api.example.com/v1"),
        api_key=kw.get("api_key", "sk-test"),
        model=kw.get("model", "test-model"),
        timeout_seconds=kw.get("timeout_seconds", 5.0),
        transport=httpx.MockTransport(handler),
    )


class TestNormalizeBaseUrl:
    def test_with_v1(self):
        assert normalize_base_url("https://api.example.com/v1") == \
            "https://api.example.com/v1/chat/completions"

    def test_without_v1(self):
        assert normalize_base_url("https://api.example.com") == \
            "https://api.example.com/v1/chat/completions"

    def test_trailing_slash_and_full_path(self):
        assert normalize_base_url("https://api.example.com/v1/") == \
            "https://api.example.com/v1/chat/completions"
        assert normalize_base_url("https://api.example.com/v1/chat/completions") == \
            "https://api.example.com/v1/chat/completions"

    def test_empty_raises(self):
        with pytest.raises(AiNotConfigured):
            normalize_base_url("  ")


class TestChat:
    async def test_plain_reply(self):
        def handler(request):
            return httpx.Response(200, json={
                "choices": [{"message": {"role": "assistant", "content": "你好"}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 2},
            })

        reply = await _client(handler).chat([{"role": "user", "content": "hi"}])
        assert reply.content == "你好"
        assert reply.tool_calls == []
        assert reply.usage == {"prompt_tokens": 5, "completion_tokens": 2}

    async def test_tool_calls_with_string_arguments(self):
        def handler(request):
            body = json.loads(request.content.decode())
            assert body["tool_choice"] == "auto"
            return httpx.Response(200, json={
                "choices": [{"message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{"id": "c1", "type": "function", "function": {
                        "name": "query_hosts", "arguments": "{\"status\": \"ONLINE\"}",
                    }}],
                }}],
            })

        reply = await _client(handler).chat(
            [{"role": "user", "content": "x"}], tools=[{"type": "function", "function": {"name": "query_hosts", "parameters": {"type": "object", "properties": {}}}}],
        )
        assert len(reply.tool_calls) == 1
        assert reply.tool_calls[0].name == "query_hosts"
        assert reply.tool_calls[0].arguments == {"status": "ONLINE"}

    async def test_malformed_tool_call_skipped(self):
        def handler(request):
            return httpx.Response(200, json={
                "choices": [{"message": {
                    "role": "assistant", "content": "ok",
                    "tool_calls": [{"bad": "shape"}],
                }}],
            })

        reply = await _client(handler).chat([{"role": "user", "content": "x"}])
        assert reply.tool_calls == []
        assert reply.content == "ok"

    async def test_auth_error(self):
        def handler(request):
            return httpx.Response(401, json={"error": "bad key"})

        with pytest.raises(AiAuthError):
            await _client(handler).chat([{"role": "user", "content": "x"}])

    async def test_timeout(self):
        def handler(request):
            raise httpx.ReadTimeout("timed out")

        with pytest.raises(AiUpstreamTimeout):
            await _client(handler).chat([{"role": "user", "content": "x"}])

    async def test_bad_status(self):
        def handler(request):
            return httpx.Response(500, text="boom")

        with pytest.raises(AiBadResponse):
            await _client(handler).chat([{"role": "user", "content": "x"}])

    async def test_malformed_json(self):
        def handler(request):
            return httpx.Response(200, text="not-json")

        with pytest.raises(AiBadResponse):
            await _client(handler).chat([{"role": "user", "content": "x"}])

    def test_missing_credentials(self):
        with pytest.raises(AiNotConfigured):
            LlmClient(base_url="https://x", api_key="", model="m")
        with pytest.raises(AiNotConfigured):
            LlmClient(base_url="https://x", api_key="k", model="")

    async def test_authorization_header_not_in_errors(self):
        def handler(request):
            return httpx.Response(403, text="forbidden")

        with pytest.raises(AiAuthError) as ei:
            await _client(handler, api_key="sk-super-secret").chat(
                [{"role": "user", "content": "x"}]
            )
        assert "sk-super-secret" not in str(ei.value)
