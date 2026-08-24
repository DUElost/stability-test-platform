#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ADR-0030 P1c — MTBF 用例集 CLI（外部 agent 的命令行入口，走同一 REST）。

REST 为主通道（D4），本脚本是便捷层：与平台页面共用同一组
``/api/v1/test-suites`` 端点、同一套权限与审计——CLI 做的每个写操作在
控制面 ``audit_logs`` 里与页面操作无异。

用法（套件以对外键 ``name`` 引用）：
    python tools/dev/mtbf-cases.py list [--project MTBF-MLD] [--include-inactive]
    python tools/dev/mtbf-cases.py show --suite MTBF-legacy [--case NAME]
    python tools/dev/mtbf-cases.py import --suite MTBF-legacy --file runtask.xml [--global UiAutomatorTestData.xml]
    python tools/dev/mtbf-cases.py export --suite MTBF-legacy --out runtask.xml [--times 100]
    python tools/dev/mtbf-cases.py validate --suite MTBF-legacy
    python tools/dev/mtbf-cases.py export-to-tool-dir --suite MTBF-legacy [--force]

凭据（明文不进 log / 不进输出）：
- ``--token``：直接用 bearer token（Swagger / 手工签发）；
- 否则取 ``--username`` / ``--password`` 或环境变量 ``STP_ADMIN_USER`` /
  ``STP_ADMIN_PASSWORD``，再否则解析仓库根 ``.env.backend``（admin 约定源），
  经 ``POST /api/v1/auth/token`` 换 token。
``--base-url`` 默认 ``STP_BASE_URL`` env 或 ``http://127.0.0.1:8000``。

退出码：0 成功；2 本地错误（参数 / 套件或用例找不到）；3 远端拒绝
（401/403/404/409…，消息原样透出，不吞成空输出）。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any, Optional

import requests

REPO_ROOT = Path(__file__).resolve().parents[2]
_BACKEND_ENV = REPO_ROOT / ".env.backend"

_SESSION: Optional[requests.Session] = None


# ── HTTP 层（测试经 monkeypatch 注入替身）────────────────────────────────────


def _http(method: str, url: str, **kwargs: Any):
    sess = _SESSION or requests.Session()
    return sess.request(method, url, timeout=kwargs.pop("timeout", 120), **kwargs)


def _load_backend_env() -> dict[str, str]:
    """解析仓库根 .env.backend 的 KEY=VALUE（admin 凭据约定源；缺失返回空）。"""
    if not _BACKEND_ENV.exists():
        return {}
    out: dict[str, str] = {}
    for line in _BACKEND_ENV.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        out[key.strip()] = val.strip().strip('"').strip("'")
    return out


def _obtain_token(args: argparse.Namespace) -> str:
    """优先 --token；否则用户名/密码（args > ambient env > .env.backend）。"""
    if args.token:
        return args.token
    env_file = _load_backend_env()
    username = (
        getattr(args, "username", None)
        or os.getenv("STP_ADMIN_USER")
        or env_file.get("STP_ADMIN_USER")
    )
    password = (
        getattr(args, "password", None)
        or os.getenv("STP_ADMIN_PASSWORD")
        or env_file.get("STP_ADMIN_PASSWORD")
    )
    if not username or not password:
        print(
            "[auth] 未提供凭据：给 --token，或设 STP_ADMIN_USER/STP_ADMIN_PASSWORD"
            "（ambient env 或仓库根 .env.backend）",
            file=sys.stderr,
        )
        sys.exit(2)
    headers: dict[str, str] = {}
    # 控制面 CSRF 中间件对非浏览器请求放行的通道：X-Agent-Secret
    # （AGENTS.md「Production access」口径；缺省不带，纯浏览器同源部署不受影响）。
    agent_secret = os.getenv("AGENT_SECRET") or env_file.get("AGENT_SECRET")
    if agent_secret:
        headers["X-Agent-Secret"] = agent_secret
    resp = _http(
        "POST",
        f"{_base_url(args).rstrip('/')}/api/v1/auth/token",
        data={"username": username, "password": password},
        headers=headers,
    )
    if resp.status_code != 200:
        _die_remote(resp)
    return resp.json()["access_token"]


def _base_url(args: argparse.Namespace) -> str:
    return (
        getattr(args, "base_url", None)
        or os.getenv("STP_BASE_URL")
        or "http://127.0.0.1:8000"
    ).rstrip("/")


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _die_remote(resp) -> None:
    """远端非 2xx：优先 ApiResponse.error，其次 HTTPException detail，兜底原文。"""
    try:
        body = resp.json()
    except ValueError:
        body = None
    message = None
    if isinstance(body, dict):
        err = body.get("error") or {}
        if isinstance(err, dict) and err.get("message"):
            message = f"{err.get('code', 'ERROR')}: {err['message']}"
        else:
            detail = body.get("detail")
            if isinstance(detail, dict) and detail.get("message"):
                extra = {
                    k: v
                    for k, v in detail.items()
                    if k in ("code", "plan_run_ids", "active_run_count", "step")
                }
                message = f"{detail.get('message')} ({extra})" if extra else detail.get("message")
            elif detail is not None:
                message = str(detail)
    print(f"[remote] HTTP {resp.status_code}: {message or resp.text[:300]}", file=sys.stderr)
    sys.exit(3)


def _api_json(method: str, base: str, path: str, token: str, **kwargs: Any):
    resp = _http(method, f"{base}{path}", headers=_headers(token), **kwargs)
    if resp.status_code >= 400:
        _die_remote(resp)
    return resp.json()


def _resolve_suite(args: argparse.Namespace, token: str, name: str) -> dict:
    data = _api_json(
        "GET",
        _base_url(args),
        "/api/v1/test-suites",
        token,
        params={"q": name},
    )["data"]
    exact = [s for s in data if s["name"] == name]
    if not exact:
        print(f"[local] 套件不存在: {name}（name 为对外唯一键，区分大小写）", file=sys.stderr)
        sys.exit(2)
    return exact[0]


def _require_suite_id(args: argparse.Namespace, token: str, name: str) -> int:
    return _resolve_suite(args, token, name)["id"]


# ── 子命令 ────────────────────────────────────────────────────────────────────


def cmd_list(args: argparse.Namespace) -> None:
    token = _obtain_token(args)
    params: dict[str, Any] = {}
    if args.project:
        params["project_key"] = args.project
    if not args.include_inactive:
        params["is_active"] = "true"
    data = _api_json(
        "GET", _base_url(args), "/api/v1/test-suites", token, params=params
    )["data"]
    if not data:
        print("(no suites)")
        return
    header = f"{'ID':>6}  {'NAME':<32} {'PROJECT':<14} {'DIR':<14} {'CASES':>5} {'ENABLED':>7}  STALE"
    print(header)
    for s in data:
        print(
            f"{s['id']:>6}  {s['name']:<32} {(s.get('project_key') or '-'):<14} "
            f"{(s.get('export_dir') or '-'):<14} {s['case_count']:>5} "
            f"{s['enabled_case_count']:>7}  {'YES' if s['export_stale'] else '-'}"
        )


def cmd_show(args: argparse.Namespace) -> None:
    token = _obtain_token(args)
    suite = _resolve_suite(args, token, args.suite)
    detail = _api_json(
        "GET", _base_url(args), f"/api/v1/test-suites/{suite['id']}", token
    )["data"]
    interesting = (
        "id", "name", "display_name", "project_key", "export_dir",
        "apk_binding", "case_count", "enabled_case_count", "is_active",
        "export_stale", "exported_sha256", "content_sha256",
        "exported_content_sha256", "source_sha256",
    )
    for key in interesting:
        if key in detail:
            print(f"{key:>26}: {detail[key]}")

    cases = _api_json(
        "GET", _base_url(args), f"/api/v1/test-suites/{suite['id']}/cases", token
    )["data"]
    if args.case:
        hits = [c for c in cases if c["name"] == args.case]
        if not hits:
            print(f"[local] 用例不存在: {args.case}", file=sys.stderr)
            sys.exit(2)
        cases = hits
    print(f"{'cases':>26}: {len(cases)}")
    for c in cases:
        flag = "" if c["enabled"] else "  (disabled)"
        print(f"    #{c['ordinal']:>3} {c['name']}  times={c['times']}{flag}")


def cmd_import(args: argparse.Namespace) -> None:
    token = _obtain_token(args)
    sid = _require_suite_id(args, token, args.suite)
    files: dict[str, tuple] = {"file": (Path(args.file).name, Path(args.file).read_bytes())}
    if args.global_file:
        files["global"] = (Path(args.global_file).name, Path(args.global_file).read_bytes())
    data = _api_json(
        "POST",
        _base_url(args),
        f"/api/v1/test-suites/{sid}/import",
        token,
        files=files,
    )["data"]
    print(
        f"[import] {data['name']}: testpoints={data['case_count']} "
        f"source_sha256={data.get('source_sha256', '')[:12]}…"
    )


def cmd_export(args: argparse.Namespace) -> None:
    token = _obtain_token(args)
    sid = _require_suite_id(args, token, args.suite)
    resp = _http(
        "GET",
        f"{_base_url(args)}/api/v1/test-suites/{sid}/export",
        headers=_headers(token),
        params={"times": args.times} if args.times else None,
    )
    if resp.status_code >= 400:
        _die_remote(resp)
    Path(args.out).write_bytes(resp.content)
    stale = resp.headers.get("X-Export-Stale") == "1"
    print(f"[export] {args.out} ({len(resp.content)} bytes)")
    if stale:
        # 库已漂离最近导出基线——门禁第 3 步会拦绑定派发，提示重导。
        print("[warn] X-Export-Stale=1：库内容已改未重导，export-to-tool-dir 后才会清基线",
              file=sys.stderr)


def cmd_validate(args: argparse.Namespace) -> None:
    token = _obtain_token(args)
    sid = _require_suite_id(args, token, args.suite)
    data = _api_json(
        "POST", _base_url(args), f"/api/v1/test-suites/{sid}/validate", token
    )["data"]
    print(f"[validate] valid={data['valid']} issues={len(data['issues'])}")
    for issue in data["issues"]:
        print(f"    [{issue['severity']}] {issue['code']}: {issue['message']}")
    if not data["valid"]:
        sys.exit(3)


def cmd_export_to_tool_dir(args: argparse.Namespace) -> None:
    token = _obtain_token(args)
    sid = _require_suite_id(args, token, args.suite)
    params: dict[str, Any] = {"force": "true"} if args.force else None
    data = _api_json(
        "POST",
        _base_url(args),
        f"/api/v1/test-suites/{sid}/export-to-tool-dir",
        token,
        params=params,
    )["data"]
    print(f"[export-to-tool-dir] dir={data['export_dir']}")
    print(f"    runtask: {data['runtask_path']}")
    print(f"    global : {data['global_path']}")
    print(f"    exported_sha256={data['exported_sha256'][:12]}…")


def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--base-url", default=None,
                        help="控制面地址（默认 STP_BASE_URL 或 http://127.0.0.1:8000）")
    parser.add_argument("--token", default=None, help="bearer token（默认走账号密码换发）")
    parser.add_argument("--username", default=None)
    parser.add_argument("--password", default=None)

    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="套件列表")
    p_list.add_argument("--project", default=None, help="按 project_key 过滤")
    p_list.add_argument("--include-inactive", action="store_true")
    p_list.set_defaults(func=cmd_list)

    p_show = sub.add_parser("show", help="套件详情 / 用例清单")
    p_show.add_argument("--suite", required=True)
    p_show.add_argument("--case", default=None, help="只看一条用例")
    p_show.set_defaults(func=cmd_show)

    p_imp = sub.add_parser("import", help="导入 runtask.xml（整体替换入库）")
    p_imp.add_argument("--suite", required=True)
    p_imp.add_argument("--file", required=True, help="runtask.xml 路径")
    p_imp.add_argument("--global-file", dest="global_file", default=None,
                       help="可选 UiAutomatorTestData.xml")
    p_imp.set_defaults(func=cmd_import)

    p_exp = sub.add_parser("export", help="渲染 runtask.xml 到本地文件")
    p_exp.add_argument("--suite", required=True)
    p_exp.add_argument("--out", required=True)
    p_exp.add_argument("--times", type=int, default=0)
    p_exp.set_defaults(func=cmd_export)

    p_val = sub.add_parser("validate", help="校验库内数据")
    p_val.add_argument("--suite", required=True)
    p_val.set_defaults(func=cmd_validate)

    p_tool = sub.add_parser("export-to-tool-dir", help="导出到中心存储消费路径")
    p_tool.add_argument("--suite", required=True)
    p_tool.add_argument("--force", action="store_true",
                        help="越过无绑定 MTBF 长跑守卫（同套件硬阻断不豁免）")
    p_tool.set_defaults(func=cmd_export_to_tool_dir)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
