from __future__ import annotations

import argparse
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Verify control-plane template invariants.")
    p.add_argument(
        "--repo-root",
        default=str(Path(__file__).resolve().parents[1]),
        help="Repository root path (default: auto-detected).",
    )
    return p.parse_args()


def _require_contains(text: str, needle: str, where: str) -> None:
    if needle not in text:
        raise AssertionError(f"Missing {needle!r} in {where}")


def main() -> int:
    args = parse_args()
    root = Path(args.repo_root).resolve()

    systemd_service = root / "deploy" / "control-plane" / "systemd" / "stability-backend.service"
    nginx_http = root / "deploy" / "control-plane" / "nginx" / "stability-platform.conf"
    nginx_https = root / "deploy" / "control-plane" / "nginx" / "stability-platform-https.conf"
    nginx_preview = root / "deploy" / "control-plane" / "nginx" / "stability-platform-preview.conf"

    # 清单/演练 runbook 渲染时替换的全部控制面模板（#1256）
    control_plane_templates = (
        systemd_service,
        root / "deploy" / "control-plane" / "systemd" / "stability-backend-nomigrate.service",
        root / "deploy" / "control-plane" / "systemd" / "stability-backend-migrate.service",
        nginx_http,
        nginx_https,
        nginx_preview,
        root / "deploy" / "control-plane" / "logrotate" / "stability-backend",
    )

    try:
        svc = systemd_service.read_text(encoding="utf-8")
        _require_contains(svc, "ExecStartPre=", str(systemd_service))
        _require_contains(svc, "python -m alembic upgrade head", str(systemd_service))
        _require_contains(
            svc,
            "uvicorn backend.main:app --host 127.0.0.1 --port 8000",
            str(systemd_service),
        )

        for conf_path in (nginx_http, nginx_https):
            conf = conf_path.read_text(encoding="utf-8")
            _require_contains(conf, "location /api/", str(conf_path))
            _require_contains(conf, "proxy_pass http://127.0.0.1:8000/api/;", str(conf_path))
            _require_contains(conf, "location /health", str(conf_path))
            _require_contains(conf, "proxy_pass http://127.0.0.1:8000/health;", str(conf_path))
            _require_contains(conf, "location /socket.io/", str(conf_path))
            _require_contains(conf, "proxy_pass http://127.0.0.1:8000/socket.io/;", str(conf_path))
            _require_contains(conf, 'proxy_set_header Connection "upgrade";', str(conf_path))
            _require_contains(conf, "proxy_set_header Upgrade $http_upgrade;", str(conf_path))
            _require_contains(conf, "location = /index.html", str(conf_path))
            _require_contains(
                conf,
                'Cache-Control "no-cache, no-store, must-revalidate"',
                str(conf_path),
            )
            _require_contains(conf, "location /assets/", str(conf_path))
            _require_contains(conf, "try_files $uri =404;", str(conf_path))
            _require_contains(
                conf,
                'Cache-Control "public, max-age=31536000, immutable"',
                str(conf_path),
            )
            if 'Cache-Control "public, max-age=31536000, immutable" always' in conf:
                raise AssertionError(
                    f"Hashed asset 404s must not be cached as immutable in {conf_path}"
                )

        # Suite 上传体上限（#1260）：反代 body 上限必须覆盖后端
        # 「2 × 10 MiB 单文件预算 + multipart 余量」，否则合法上传在 Nginx 413。
        # 后端常量 ↔ 模板值的数值对齐由 backend/tests/test_deployment_files.py 守。
        for conf_path in (nginx_http, nginx_https, nginx_preview):
            conf = conf_path.read_text(encoding="utf-8")
            _require_contains(conf, "client_max_body_size 25m;", str(conf_path))

        # 部署根唯一性（#1256）：模板只能用 <deploy-root> 占位符。任何硬编码的部署根
        # 都会让「清单按 STP_DEPLOY_ROOT 渲染」与模板固定路径再次分叉——只核对字符串
        # 的旧检查看不见这类漂移。
        for template_path in control_plane_templates:
            text = template_path.read_text(encoding="utf-8")
            _require_contains(text, "<deploy-root>", str(template_path))
            for hardcoded in ("/opt/", "/home/"):
                if hardcoded in text:
                    raise AssertionError(
                        f"{template_path} 含硬编码部署根 {hardcoded!r}——"
                        "部署根只能由 <deploy-root> 占位符确定（#1256）"
                    )

    except Exception as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1

    print("OK: control-plane templates look consistent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
