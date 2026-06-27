"""Stage A env audit for preprod drill (#45). Prints checklist; never prints secret values."""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import httpx

BACKEND = os.getenv("STP_AUDIT_BACKEND", "http://172.21.10.25:8000")
ENV_FILE = Path(os.getenv("STP_AUDIT_ENV_FILE", Path(__file__).resolve().parents[1] / ".env"))
PLACEHOLDER_PATTERNS = (
    "change-me",
    "password@localhost",
    "your-",
    "example",
    "<",
)


def load_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def is_placeholder(key: str, val: str) -> bool:
    if not val:
        return True
    low = val.lower()
    if key in ("JWT_SECRET_KEY", "AGENT_SECRET", "WS_TOKEN") and any(p in low for p in PLACEHOLDER_PATTERNS):
        return True
    if key == "DATABASE_URL" and "password@localhost" in low:
        return True
    return False


def prod_guard_ok(env: dict[str, str]) -> tuple[bool, list[str]]:
    issues: list[str] = []
    if env.get("ENV", "development").strip().lower() != "production":
        issues.append("ENV != production (dev 联调模式)")
        return False, issues
    if env.get("AUTH_COOKIE_SECURE", "0") != "1":
        issues.append("AUTH_COOKIE_SECURE != 1")
    ss = env.get("AUTH_COOKIE_SAMESITE", "lax").strip().lower()
    if ss not in ("lax", "strict"):
        issues.append(f"AUTH_COOKIE_SAMESITE={ss!r} invalid for production")
    csrf = env.get("STP_CSRF_ENABLED", "1").strip().lower()
    if csrf in ("0", "false", "no", "off"):
        issues.append("STP_CSRF_ENABLED disabled")
    for key in ("JWT_SECRET_KEY", "AGENT_SECRET"):
        if is_placeholder(key, env.get(key, "")):
            issues.append(f"{key} looks like placeholder")
    return len(issues) == 0, issues


def mask(val: str) -> str:
    if not val:
        return "(unset)"
    if len(val) <= 4:
        return "****"
    return val[:2] + "…" + val[-2:] + f" (len={len(val)})"


def probe_backend(base: str) -> dict:
    out: dict = {}
    try:
        r = httpx.get(f"{base}/health", timeout=10)
        out["health_status"] = r.status_code
        out["health_body"] = r.json() if r.status_code == 200 else r.text[:200]
    except Exception as exc:
        out["health_error"] = str(exc)
        return out

    origin = os.getenv("STP_SMOKE_ORIGIN", "http://localhost:5173")
    h = {"Origin": origin, "Referer": f"{origin}/"}
    pwd = os.getenv("STP_ADMIN_PASSWORD", "")
    if pwd:
        try:
            lr = httpx.post(
                f"{base}/api/v1/auth/login",
                data={"username": os.getenv("STP_ADMIN_USER", "admin"), "password": pwd},
                headers=h,
                timeout=15,
            )
            out["login_status"] = lr.status_code
            cookies = lr.headers.get_list("set-cookie") if hasattr(lr.headers, "get_list") else []
            if not cookies and "set-cookie" in lr.headers:
                cookies = [lr.headers["set-cookie"]]
            out["set_cookie_flags"] = []
            for c in cookies:
                flags = []
                if "Secure" in c:
                    flags.append("Secure")
                if "HttpOnly" in c:
                    flags.append("HttpOnly")
                m = re.search(r"SameSite=(\w+)", c, re.I)
                if m:
                    flags.append(f"SameSite={m.group(1)}")
                out["set_cookie_flags"].append(flags)
            # CSRF: POST without Origin should fail if enabled
            bad = httpx.post(
                f"{base}/api/v1/auth/logout",
                cookies=lr.cookies,
                timeout=10,
            )
            out["csrf_logout_no_origin"] = bad.status_code
        except Exception as exc:
            out["login_error"] = str(exc)
    return out


def main() -> None:
    env = load_env(ENV_FILE)
    print(f"=== Stage A audit ===")
    print(f"env_file: {ENV_FILE} ({'found' if env else 'missing/empty'})")
    print(f"backend:  {BACKEND}\n")

    checks = [
        ("P0-O1 DATABASE_URL", bool(env.get("DATABASE_URL")), env.get("DATABASE_URL", "")[:30] + "…"),
        ("P0-O1 REDIS_URL", bool(env.get("REDIS_URL")), mask(env.get("REDIS_URL", ""))),
        (
            "P0-O1 AGENT_SECRET",
            bool(env.get("AGENT_SECRET")) and not is_placeholder("AGENT_SECRET", env.get("AGENT_SECRET", "")),
            "set" if env.get("AGENT_SECRET") else "unset",
        ),
        (
            "P0-O1 JWT_SECRET_KEY",
            bool(env.get("JWT_SECRET_KEY")) and not is_placeholder("JWT_SECRET_KEY", env.get("JWT_SECRET_KEY", "")),
            "set" if env.get("JWT_SECRET_KEY") else "unset",
        ),
        ("P0-O1 STP_ENABLE_INPROCESS_SAQ", env.get("STP_ENABLE_INPROCESS_SAQ", "1") == "1", env.get("STP_ENABLE_INPROCESS_SAQ", "1")),
        ("ENV mode", True, env.get("ENV", "development")),
        ("AUTH_COOKIE_SECURE", True, env.get("AUTH_COOKIE_SECURE", "0")),
        ("AUTH_COOKIE_SAMESITE", True, env.get("AUTH_COOKIE_SAMESITE", "lax")),
        ("STP_CSRF_ENABLED", True, env.get("STP_CSRF_ENABLED", "1")),
        ("CORS_ORIGINS", bool(env.get("CORS_ORIGINS")), (env.get("CORS_ORIGINS") or "")[:60]),
    ]

    print("| Check | OK | Value |")
    print("|-------|-----|-------|")
    for name, ok, val in checks:
        mark = "PASS" if ok else "FAIL"
        print(f"| {name} | {mark} | {val} |")

    guard_ok, issues = prod_guard_ok(env)
    print(f"\nADR-0024 production guard: {'PASS' if guard_ok else 'NOT production-ready'}")
    for i in issues:
        print(f"  - {i}")

    probe = probe_backend(BACKEND)
    print(f"\n=== Live probe {BACKEND} ===")
    if "health_error" in probe:
        print(f"health: ERROR {probe['health_error']}")
    else:
        body = probe.get("health_body", {})
        data = body.get("data", body) if isinstance(body, dict) else body
        print(f"health: HTTP {probe['health_status']} -> {data}")

    if "login_status" in probe:
        print(f"login: HTTP {probe['login_status']}")
        for i, flags in enumerate(probe.get("set_cookie_flags") or []):
            print(f"  set-cookie[{i}]: {', '.join(flags) or '(no Secure/HttpOnly parsed)'}")
        if "csrf_logout_no_origin" in probe:
            code = probe["csrf_logout_no_origin"]
            print(f"CSRF probe (logout w/o Origin): HTTP {code} -> {'blocked' if code in (403, 401) else 'NOT blocked'}")

    print("\n=== Stage A verdict ===")
    infra_ok = all(
        [
            env.get("DATABASE_URL"),
            env.get("REDIS_URL"),
            env.get("AGENT_SECRET") and not is_placeholder("AGENT_SECRET", env.get("AGENT_SECRET", "")),
        ]
    )
    saq_ok = isinstance(probe.get("health_body"), dict) and probe.get("health_body", {}).get("data", {}).get("saq_ready") is True
    if infra_ok and saq_ok:
        print("Dev/staging infra: OK for drill")
    else:
        print("Dev/staging infra: GAPS (see above)")
    if guard_ok:
        print("Production hardening: ready")
    else:
        print("Production hardening: defer — set ENV=production + checklist SS3 when going live")


if __name__ == "__main__":
    main()
