# -*- coding: utf-8 -*-
"""#763：auto_register_host 4xx 日志必须带真实状态码（#729 同款真值脚枪）。

``requests.Response.__bool__`` 是 ``ok`` 别名（4xx/5xx 恒假）——写成
``if exc.response`` 会把 status/body 记成 None，故障定位无法区分「服务端拒绝」
与「网络层异常」。本文件用真实 falsy Response 锁住日志形态（MagicMock 恒真，
无法防此类回归）。
"""

from __future__ import annotations

import logging
from unittest.mock import patch

import pytest
from requests import Response
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import HTTPError

from backend.agent.host_registry import auto_register_host


def _http_error_response(status: int, body: str) -> Response:
    response = Response()
    response.status_code = status
    response._content = body.encode("utf-8")
    response.url = "http://127.0.0.1:8000/api/v1/heartbeat"
    assert not response, "precondition: Response is falsy on 4xx/5xx"
    return response


def test_4xx_logs_real_status_and_body(caplog):
    response = _http_error_response(400, '{"detail":"bad host payload"}')
    with patch(
        "backend.agent.host_registry.requests.post", return_value=response,
    ), caplog.at_level(logging.ERROR, logger="backend.agent.host_registry"):
        with pytest.raises(HTTPError):
            auto_register_host("http://127.0.0.1:8000", {"ip": "10.0.0.9"})

    messages = [record.getMessage() for record in caplog.records]
    assert any("status=400" in m for m in messages), messages
    assert any("bad host payload" in m for m in messages), messages
    assert not any("status=None" in m for m in messages), messages


def test_network_error_logs_via_exception_branch(caplog):
    """无 response 的网络异常走 except Exception 分支（status 语义不适用）。"""
    with patch(
        "backend.agent.host_registry.requests.post",
        side_effect=RequestsConnectionError("boom"),
    ), caplog.at_level(logging.ERROR, logger="backend.agent.host_registry"):
        with pytest.raises(RequestsConnectionError):
            auto_register_host("http://127.0.0.1:8000", {"ip": "10.0.0.9"})

    messages = [record.getMessage() for record in caplog.records]
    assert any("auto_register_host_failed: error=boom" in m for m in messages), messages
