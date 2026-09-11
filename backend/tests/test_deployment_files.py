from __future__ import annotations

import json
import re
import subprocess
import sys
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
    assert "CORS_ORIGINS: http://127.0.0.1:${DEV_FRONTEND_PORT:-15173},http://localhost:${DEV_FRONTEND_PORT:-15173}" in compose
    assert "STP_ADMIN_PASSWORD: ${STP_ADMIN_PASSWORD:-admin123}" in compose
    assert "python /app/backend/scripts/init_dev_db.py" in compose


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


def test_control_plane_nginx_templates_proxy_health_endpoint():
    nginx_dir = ROOT / "deploy" / "control-plane" / "nginx"

    for filename in ("stability-platform.conf", "stability-platform-https.conf"):
        conf = (nginx_dir / filename).read_text(encoding="utf-8")
        assert "location /health" in conf
        assert "proxy_pass http://127.0.0.1:8000/health;" in conf


def test_nginx_templates_cache_hashed_assets_without_spa_fallback():
    templates = (
        ROOT / "deploy" / "control-plane" / "nginx" / "stability-platform.conf",
        ROOT / "deploy" / "control-plane" / "nginx" / "stability-platform-https.conf",
        ROOT / "deploy" / "nginx" / "frontend-docker.conf",
    )

    for template in templates:
        conf = template.read_text(encoding="utf-8")
        assert "location = /index.html" in conf
        assert 'Cache-Control "no-cache, no-store, must-revalidate"' in conf
        assert "location /assets/" in conf
        assert 'Cache-Control "public, max-age=31536000, immutable"' in conf
        assert 'Cache-Control "public, max-age=31536000, immutable" always' not in conf
        assert "try_files $uri =404;" in conf


def test_frontend_docker_nginx_targets_server_service():
    nginx_conf = (
        ROOT / "deploy" / "nginx" / "frontend-docker.conf"
    ).read_text(encoding="utf-8")

    assert "http://server:8000" in nginx_conf
    assert "http://backend:8000" not in nginx_conf


def test_control_plane_template_verifier_passes():
    """部署根占位符等模板语义不变量由 verify_control_plane_templates.py 单一维护（#1256）。"""
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "verify_control_plane_templates.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_frontend_build_scripts_match_nginx_roots():
    """Nginx root 指向的 frontend/dist-* 必须由某个构建脚本产出（#1256）。"""
    scripts = json.loads(
        (ROOT / "frontend" / "package.json").read_text(encoding="utf-8")
    )["scripts"]
    nginx_dir = ROOT / "deploy" / "control-plane" / "nginx"

    roots: set[str] = set()
    for conf_path in sorted(nginx_dir.glob("*.conf")):
        conf = conf_path.read_text(encoding="utf-8")
        roots.update(
            re.findall(
                r"^\s*root\s+<deploy-root>/frontend/([\w.-]+)\s*;", conf, re.MULTILINE
            )
        )

    assert roots == {"dist-prod", "dist-preview"}
    for out_dir in sorted(roots):
        assert any(f"--outDir {out_dir}" in cmd for cmd in scripts.values()), (
            f"Nginx root frontend/{out_dir} 没有对应构建脚本——"
            "干净 checkout 会构建出 Nginx 不托管的目录（#1256）"
        )


def test_deploy_docs_render_templates_instead_of_copying_them():
    """清单/演练 runbook 必须渲染模板，禁止原样拷贝（否则 <deploy-root> 会落进 /etc，#1256）。"""
    docs = (
        ROOT / "docs" / "production-minimum-deployment-checklist.md",
        ROOT / "docs" / "preprod-drill-runbook.md",
    )

    for doc_path in docs:
        doc = doc_path.read_text(encoding="utf-8")
        assert "$STP_DEPLOY_ROOT" in doc or "$CONTROL_DIR" in doc
        for verbatim in (
            "cp deploy/control-plane/systemd/",
            "cp deploy/control-plane/nginx/",
            "cp deploy/control-plane/logrotate/",
            'cp "$CONTROL_DIR/deploy/control-plane/',
        ):
            assert verbatim not in doc, f"{doc_path.name} 原样拷贝模板：{verbatim}"


def test_nginx_body_limit_covers_suite_upload_budget():
    """#1260：反代 body 上限必须覆盖「2 × Suite 单文件上限 + multipart 余量」。

    上传端点最多收到 file + global 两个文件（suites.py），单文件上限
    _MAX_UPLOAD_BYTES；任一参数变化时本测试联动报警。
    """
    suites_src = (ROOT / "backend" / "api" / "routes" / "suites.py").read_text(
        encoding="utf-8"
    )
    match = re.search(r"_MAX_UPLOAD_BYTES = (\d+) \* 1024 \* 1024", suites_src)
    assert match is not None, "suites.py 的 _MAX_UPLOAD_BYTES 定义形态变化，请同步本测试"
    per_file = int(match.group(1)) * 1024 * 1024

    for name in (
        "stability-platform.conf",
        "stability-platform-https.conf",
        "stability-platform-preview.conf",
    ):
        conf = (ROOT / "deploy" / "control-plane" / "nginx" / name).read_text(
            encoding="utf-8"
        )
        limit_match = re.search(r"client_max_body_size\s+(\d+)m;", conf)
        assert limit_match is not None, f"{name} 缺 client_max_body_size"
        body_limit = int(limit_match.group(1)) * 1024 * 1024
        assert body_limit >= 2 * per_file, (
            f"{name} body 上限 {body_limit} 小于 2 × 单文件上限 {2 * per_file}（#1260）"
        )
