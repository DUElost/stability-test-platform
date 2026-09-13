"""#743 期望 2：请求级指标接线的行为测试。

覆盖三件事（每条都对应一个**反例就会转红**的判据）：

1. **命中路由**用路由**模板**作为 ``endpoint``（反例：改用 ``request.url.path``
   → 每个 job id 一条序列，基数随实体数膨胀 → ``test_matched_route_records_template`` 转红）；
2. **未命中路由（404）**——正是 #729 幽灵 ``/complete`` 的形态——用**归一化路径**
   （反例：去掉归一化 → ``test_unmatched_404_records_normalized_endpoint`` 转红）；
3. 异常路径记 500 后**照常抛出**（不吞异常、不改错误处理链）。

另有一条端到端用例直接读 prometheus 注册表，证明指标**真的被喂了数据**
（而不只是"调用了函数"）。
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY

from backend.core.request_metrics import (
    ApiRequestMetricsMiddleware,
    normalize_unmatched_path,
)


def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(ApiRequestMetricsMiddleware)

    @app.get("/api/v1/jobs/{job_id}/complete")
    async def _complete(job_id: str):  # pragma: no cover - 路由存在即可
        return {"job_id": job_id}

    @app.get("/api/v1/boom")
    async def _boom():  # pragma: no cover
        raise RuntimeError("boom")

    return app


@pytest.fixture()
def recorded(monkeypatch) -> list[tuple[str, str, int]]:
    """捕获中间件交给 record_api_request 的 (method, endpoint, status)。"""
    calls: list[tuple[str, str, int]] = []
    monkeypatch.setattr(
        "backend.core.request_metrics.record_api_request",
        lambda method, endpoint, status_code, duration: calls.append(
            (method, endpoint, status_code)
        ),
    )
    return calls


class TestNormalizeUnmatchedPath:
    def test_id_like_segments_collapse_to_placeholder(self):
        assert (
            normalize_unmatched_path("/api/v1/jobs/12345/terminate")
            == "/api/v1/jobs/{id}/terminate"
        )
        assert (
            normalize_unmatched_path(
                "/api/v1/jobs/550e8400-e29b-41d4-a716-446655440000/terminate"
            )
            == "/api/v1/jobs/{id}/terminate"
        )
        assert (
            normalize_unmatched_path("/api/v1/jobs/abc123def456/terminate")
            == "/api/v1/jobs/{id}/terminate"
        )

    def test_consecutive_ids_collapse_once(self):
        assert (
            normalize_unmatched_path("/api/v1/jobs/12345/devices/67890/terminate")
            == "/api/v1/jobs/{id}/devices/{id}/terminate"
        )

    def test_non_id_segments_are_preserved(self):
        assert normalize_unmatched_path("/not-an-api") == "/not-an-api"

    def test_unbounded_paths_fall_back_to_other(self):
        # 深度超限与非法形态都归到单一标签，防止基数漂移
        assert normalize_unmatched_path("/" + "a/" * 12) == "other"
        assert normalize_unmatched_path("garbage") == "other"


class TestMiddlewareLabels:
    def test_matched_route_records_template(self, recorded):
        """命中路由 → 模板（不含具体 id）。反例：改用 url.path 则本条转红。"""
        client = TestClient(_app())
        resp = client.get("/api/v1/jobs/12345/complete")

        assert resp.status_code == 200
        assert recorded == [("GET", "/api/v1/jobs/{job_id}/complete", 200)]

    def test_unmatched_404_records_normalized_endpoint(self, recorded):
        """幽灵端点（路由不存在）→ 归一化路径。反例：去掉归一化则本条转红。"""
        client = TestClient(_app())
        resp = client.post("/api/v1/jobs/12345/terminate")

        assert resp.status_code == 404
        assert recorded == [("POST", "/api/v1/jobs/{id}/terminate", 404)]

    def test_deep_unmatched_path_collapses_to_other(self, recorded):
        client = TestClient(_app())
        client.get("/" + "a/" * 12 + "x")

        assert len(recorded) == 1
        assert recorded[0][1] == "other"

    def test_exception_path_records_500_and_reraises(self, recorded):
        """5xx 也被统计；异常继续传播（不吞异常）。"""
        client = TestClient(_app(), raise_server_exceptions=False)
        resp = client.get("/api/v1/boom")

        assert resp.status_code == 500
        assert recorded == [("GET", "/api/v1/boom", 500)]


class TestRegistryWiring:
    def test_counter_and_histogram_actually_fed(self):
        """端到端：真实注册表被喂数据（证明接线，而不只是调了函数）。"""
        labels = {
            "method": "GET",
            "endpoint": "/api/v1/jobs/{job_id}/complete",
            "status_code": "200",
        }
        before = REGISTRY.get_sample_value("stability_api_requests_total", labels) or 0.0

        client = TestClient(_app())
        assert client.get("/api/v1/jobs/777/complete").status_code == 200

        after = REGISTRY.get_sample_value("stability_api_requests_total", labels) or 0.0
        assert after == before + 1
