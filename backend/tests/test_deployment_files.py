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


def test_docker_compose_uses_isolated_dev_ports_and_localhost_bindings():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert '"127.0.0.1:${DEV_POSTGRES_PORT:-15432}:5432"' in compose
    assert '"127.0.0.1:${DEV_REDIS_PORT:-16379}:6379"' in compose
    assert '"127.0.0.1:${DEV_BACKEND_PORT:-18000}:8000"' in compose
    assert '"127.0.0.1:${DEV_FRONTEND_PORT:-15173}:80"' in compose


def test_docker_compose_mounts_repo_root_and_dev_storage_only():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert "- ./:/app" in compose
    assert "- ./.docker/dev-nfs:/var/lib/stp-dev/nfs" in compose
    assert "- ./.docker/dev-aee-nfs:/var/lib/stp-dev/aee-nfs" in compose
    assert "- ./.docker/dev-aee-local:/var/lib/stp-dev/aee-local" in compose
    assert "STP_RUN_CONSOLE_LOG_ROOT: /tmp/stp-dev/console" in compose


def test_frontend_dockerfile_accepts_vite_build_args():
    dockerfile = (ROOT / "Dockerfile.frontend").read_text(encoding="utf-8")

    assert "ARG VITE_API_BASE_URL=" in dockerfile
    assert "ARG VITE_WS_BASE_URL=" in dockerfile
    assert "ENV VITE_API_BASE_URL=$VITE_API_BASE_URL" in dockerfile
    assert "ENV VITE_WS_BASE_URL=$VITE_WS_BASE_URL" in dockerfile


def test_backend_systemd_service_runs_migrations_before_start():
    service = (
        ROOT / "deploy" / "control-plane" / "systemd" / "stability-backend.service"
    ).read_text(encoding="utf-8")

    assert "ExecStartPre=" in service
    assert "python -m alembic upgrade head" in service


def test_https_nginx_template_exists_for_production_tls():
    https_conf = ROOT / "deploy" / "control-plane" / "nginx" / "stability-platform-https.conf"

    assert https_conf.exists()


def test_frontend_docker_nginx_targets_server_service():
    nginx_conf = (
        ROOT / "deploy" / "nginx" / "frontend-docker.conf"
    ).read_text(encoding="utf-8")

    assert "http://server:8000" in nginx_conf
    assert "http://backend:8000" not in nginx_conf
