"""
Metrics API Endpoint

Exposes Prometheus metrics at /metrics endpoint.
"""

from fastapi import APIRouter, Response

from backend.core.metrics import get_metrics_response, is_prometheus_available

router = APIRouter()


@router.get("/metrics")
async def metrics():
    """
    Prometheus metrics endpoint.

    Returns metrics in Prometheus exposition format.
    """
    data, content_type = get_metrics_response()
    return Response(content=data, media_type=content_type)


@router.get("/health")
async def health_check():
    """
    Health check endpoint.

    Returns service health status.
    """
    return {
        "status": "healthy",
        "prometheus_available": is_prometheus_available()
    }
