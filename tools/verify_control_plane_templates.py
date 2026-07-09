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

    except Exception as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1

    print("OK: control-plane templates look consistent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
