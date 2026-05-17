from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_docker_compose_does_not_hardcode_postgres_password():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert "POSTGRES_PASSWORD: password" not in compose
    assert "POSTGRES_PASSWORD: ${POSTGRES_PASSWORD" in compose


def test_backend_dockerfile_runs_as_non_root_and_has_healthcheck():
    dockerfile = (ROOT / "Dockerfile.backend").read_text(encoding="utf-8")

    assert "USER appuser" in dockerfile
    assert "HEALTHCHECK" in dockerfile
