#!/usr/bin/env python3
"""零引用脚本版本退役：出计划（只读）→ 执行（走平台 API，带写前校验与审计）。

为什么分两步：退役判据是**只读**计算（`backend/services/script_retirement.py` 是唯一
事实源），而退役动作必须经控制面 API——那里有引用守卫（409 `SCRIPT_STILL_REFERENCED`）、
`audit_logs` 与脚本目录版本缓存失效。本工具**不直连数据库写**，也不碰版本目录文件
（ADR-0020/0039：退役 ≠ 删除）。

用法（从仓根）::

    # 1) 只读出计划：打印判据化候选，落盘 manifest
    python tools/dev/retire_script_versions.py plan --out /tmp/retire.json

    # 2) 复核 manifest 后执行（必须显式 --yes，否则只 dry-run）
    python tools/dev/retire_script_versions.py execute --manifest /tmp/retire.json --yes

    # 3) 回滚（重新激活，无守卫——仅用于误退役处置）
    python tools/dev/retire_script_versions.py reactivate --manifest /tmp/retire.json --yes

`plan` 需要可解析的 `DATABASE_URL`（ambient 或仓根 `.env.backend`）；`execute` 只需要
控制面 API 与管理员凭据（默认取 `--env-file` 的 `STP_ADMIN_USER`/`STP_ADMIN_PASSWORD`），
凭据只读不打印。默认只允许打本机回环控制面，`--allow-remote` 才放行远端。
"""

from __future__ import annotations

import argparse
import json
import re
import sys

import requests
from datetime import date, datetime, timezone
from pathlib import Path

# 以 `python tools/dev/retire_script_versions.py …` 直接运行时 sys.path[0] 是 tools/dev，
# 仓库根不在 path 上 ⇒ `_plan()` 里的 `from backend…` 抛 ModuleNotFoundError（与 #1659
# 的 queue_head_telemetry.py 同一形态；`docs/development/script-versioning.md` §判据与巡检
# 给的正是这种直接调用形式）。用 __file__ 推导 REPO_ROOT 做 bootstrap，与 cwd 无关。
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DEFAULT_BASE_URL = "http://127.0.0.1:8000/api/v1"
DEFAULT_ENV_FILE = Path(__file__).resolve().parents[2] / ".env.backend"

# 只读 KEY=value 解析（与 tools/dev/check_alembic_at_head.py 同族）：
# 不 import backend.core.*——它在 import 期就解析 DATABASE_URL 并建 engine，
# 会让「只需 API」的 execute 子命令无端要求数据库配置。
_KEY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


def read_env_key(env_file: Path, key: str) -> str:
    if not env_file.is_file():
        return ""
    for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
        m = _KEY_RE.match(line.strip())
        if m and m.group(1) == key:
            return m.group(2).strip().strip('"').strip("'")
    return ""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def build_manifest(facts, *, verdicts, script_ids, today: date, cooldown_days: int) -> dict:
    """判据结果 + `(name,version)→script_id` 映射 → 可执行 manifest（纯函数，便于测试）。

    无 script_id 的行（理论上不该有：库内行必有 id）直接跳过并在 `skipped` 里留痕，
    不静默丢。
    """
    items, skipped = [], []
    for fact in facts:
        verdict = verdicts[(fact.name, fact.version)]
        if not verdict.retire:
            continue
        sid = script_ids.get((fact.name, fact.version))
        if sid is None:
            skipped.append({"name": fact.name, "version": fact.version, "why": "无 script_id"})
            continue
        items.append({
            "script_id": int(sid),
            "name": fact.name,
            "version": fact.version,
            "reason": verdict.reason,
            "last_used_on": fact.last_used_on.isoformat() if fact.last_used_on else None,
            "refs_at_plan": fact.refs,
        })
    return {
        "generated_at": _now(),
        "today": today.isoformat(),
        "cooldown_days": cooldown_days,
        "count": len(items),
        "items": items,
        "skipped": skipped,
    }


def _plan(args) -> int:
    from sqlalchemy import create_engine, text

    from backend.core.database import normalize_sync_database_url
    from backend.core.env_source import resolve_database_url
    from backend.scripts.check_unreferenced_script_versions import (
        UsageFactsUnavailable,
        build_facts,
        compute_reference_counts,
        compute_usage_facts,
    )
    from backend.services.script_retirement import (
        STALE_COOLDOWN_DAYS,
        classify,
        retirement_candidates,
    )

    url, source = resolve_database_url()
    engine = create_engine(normalize_sync_database_url(url))
    try:
        with engine.connect() as conn:
            rows = compute_reference_counts(conn)
            id_map = {
                (r[0], r[1]): r[2]
                for r in conn.execute(text("SELECT name, version, id FROM script"))
            }
            try:
                usage = compute_usage_facts(conn)
            except UsageFactsUnavailable as exc:
                print(f"执行事实维度不可得，无法出计划：{exc}", file=sys.stderr)
                return 2
    finally:
        engine.dispose()

    if args.name:
        rows = [r for r in rows if r["name"] == args.name]
        id_map = {k: v for k, v in id_map.items() if k[0] == args.name}

    today = date.fromisoformat(args.today) if args.today else date.today()
    cooldown = args.cooldown_days if args.cooldown_days is not None else STALE_COOLDOWN_DAYS
    facts = build_facts(rows, usage)
    verdicts = classify(facts, today=today, cooldown_days=cooldown)
    manifest = build_manifest(facts, verdicts=verdicts, script_ids=id_map,
                              today=today, cooldown_days=cooldown)
    candidates = retirement_candidates(facts, today=today, cooldown_days=cooldown)

    print(f"# 退役计划（env 源：{source}，判据冷却 {cooldown} 天，基准日 {today}）")
    print(f"工具口径 active∧零引用："
          f"{sum(1 for r in rows if r['refs'] == 0 and r['is_active'])}，"
          f"判据可退役：{len(candidates)}")
    for item in manifest["items"]:
        name, version = item["name"], item["version"]
        print(f"  {name}@{version} (id={item['script_id']}) ← {item['reason']}")
    if not manifest["items"]:
        print("  （无可退役项）")
    if manifest["skipped"]:
        print(f"  跳过 {len(manifest['skipped'])} 条：{manifest['skipped']}")

    if args.out:
        Path(args.out).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
        )
        print(f"manifest → {args.out}")
    return 0


_LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")


def ensure_base_url_allowed(base_url: str, *, allow_remote: bool) -> None:
    """写操作默认只允许本机回环控制面。

    放在 `_apply`（CLI 路径）而不是客户端构造函数里：护栏要能被替换客户端的测试
    覆盖，也不该随未来换个 HTTP 封装就丢失。
    """
    match = re.match(r"^[a-z]+://([^/:]+)", base_url)
    host = match.group(1) if match else ""
    if not allow_remote and host not in _LOOPBACK_HOSTS:
        raise SystemExit(f"拒绝向非本机控制面写：{base_url}（确认后用 --allow-remote）")


class ControlPlane:
    """控制面 API 客户端：token 只在内存，不落日志。"""

    def __init__(self, base_url: str, env_file: Path):
        self.base_url = base_url.rstrip("/")
        self._session = requests.Session()
        token = self._login(env_file)
        self._session.headers.update({"Authorization": f"Bearer {token}"})

    def _login(self, env_file: Path) -> str:
        resp = self._session.post(
            f"{self.base_url}/auth/token",
            data={
                "username": read_env_key(env_file, "STP_ADMIN_USER"),
                "password": read_env_key(env_file, "STP_ADMIN_PASSWORD"),
            },
            headers={"X-Agent-Secret": read_env_key(env_file, "AGENT_SECRET")},
            timeout=30,
        )
        resp.raise_for_status()
        token = resp.json()["access_token"]
        me = self._session.get(f"{self.base_url}/auth/me", timeout=30)
        me.raise_for_status()
        if me.json().get("role") != "admin":
            raise SystemExit("凭据对应身份非 admin，拒绝执行退役")
        return token

    def get_script(self, script_id: int) -> dict | None:
        resp = self._session.get(f"{self.base_url}/scripts/{script_id}", timeout=30)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()["data"]

    def deactivate(self, script_id: int) -> tuple[int, str]:
        resp = self._session.delete(f"{self.base_url}/scripts/{script_id}", timeout=30)
        return resp.status_code, resp.text[:200]

    def reactivate(self, script_id: int) -> tuple[int, str]:
        resp = self._session.put(
            f"{self.base_url}/scripts/{script_id}", json={"is_active": True}, timeout=30
        )
        return resp.status_code, resp.text[:200]


def load_manifest(path: Path) -> dict:
    """读取并校验 `plan` 子命令产出的 manifest 形状。

    形状错（含把旧的裸数组清单直接喂进来）必须给出可执行的提示，而不是 AttributeError
    回溯——运维在真实批次里对着回溯猜格式的成本远高于一次显式拒绝。
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("items"), list):
        raise SystemExit(
            f"manifest 形状不符：{path} 需要含 items 列表的对象（由 "
            "`retire_script_versions.py plan --out <path>` 生成；"
            "每行须含 script_id/name/version）"
        )
    for index, item in enumerate(raw["items"]):
        missing = [k for k in ("script_id", "name", "version") if k not in item]
        if missing:
            raise SystemExit(f"manifest items[{index}] 缺字段 {missing}：{path}")
    return raw


def _apply(args, *, deactivate: bool) -> int:
    manifest = load_manifest(Path(args.manifest))
    items = manifest["items"]
    if args.limit:
        items = items[: args.limit]
    action = "退役" if deactivate else "重新激活"
    verb = "deactivate" if deactivate else "reactivate"

    print(f"# {action} {len(items)} 条（manifest {args.manifest}，"
          f"生成于 {manifest.get('generated_at', '?')}）")
    if not args.yes:
        print("DRY-RUN：未加 --yes，不写。逐条预览：")
        for item in items:
            name, version = item["name"], item["version"]
            print(f"  {verb} {name}@{version} (id={item['script_id']})")
        return 0

    if not items:
        print("manifest 无待执行项，跳过（不建连接、不取 token）")
        return 0
    ensure_base_url_allowed(args.base_url, allow_remote=args.allow_remote)
    env_file = Path(args.env_file).expanduser()
    if not (read_env_key(env_file, "STP_ADMIN_USER") and read_env_key(env_file, "STP_ADMIN_PASSWORD")):
        raise SystemExit(
            f"未在 {env_file} 找到 STP_ADMIN_USER/STP_ADMIN_PASSWORD——拒绝在无凭据下盲试 API"
            "（worktree 内没有 .env.backend 属正常，用 --env-file 指向真正的凭据源）"
        )
    client = ControlPlane(args.base_url, env_file)
    counts: dict[str, int] = {}
    for index, item in enumerate(items, 1):
        sid = int(item["script_id"])
        name, version = item["name"], item["version"]
        tag = f"[{index:02d}/{len(items)}] id={sid} {name}@{version}"
        current = client.get_script(sid)
        if current is None:
            counts["SKIPPED"] = counts.get("SKIPPED", 0) + 1
            print(f"{tag} SKIP 库内无此行")
            continue
        if (current["name"], current["version"]) != (item["name"], item["version"]):
            counts["SKIPPED"] = counts.get("SKIPPED", 0) + 1
            print(f"{tag} SKIP 行已漂移（实为 {current['name']}@{current['version']}）")
            continue
        if deactivate and not current["is_active"]:
            counts["ALREADY"] = counts.get("ALREADY", 0) + 1
            print(f"{tag} SKIP 已是 inactive")
            continue
        if not deactivate and current["is_active"]:
            counts["ALREADY"] = counts.get("ALREADY", 0) + 1
            print(f"{tag} SKIP 已是 active")
            continue

        status, body = (client.deactivate if deactivate else client.reactivate)(sid)
        if status != 200:
            key = "REFERENCED" if "SCRIPT_STILL_REFERENCED" in body else "FAILED"
            counts[key] = counts.get(key, 0) + 1
            print(f"{tag} {key} HTTP {status} {body[:150]}")
            if status in (401, 403, 500, 502, 503):
                print("系统性失败（鉴权/服务端）——中止剩余批次", file=sys.stderr)
                return 1
            continue
        after = client.get_script(sid)
        expect_active = not deactivate
        if after is None or after["is_active"] is not expect_active:
            counts["VERIFY_FAIL"] = counts.get("VERIFY_FAIL", 0) + 1
            print(f"{tag} 写后复核失败", file=sys.stderr)
            return 1
        counts[verb.upper()] = counts.get(verb.upper(), 0) + 1
        print(f"{tag} → is_active={after['is_active']} ✅")

    print(f"\n结果：{counts}")
    print("复核：python -m backend.scripts.check_unreferenced_script_versions --guard")
    # ALREADY 是幂等重跑的良性结果；其余（漂移、无行、409、写失败、复核失败）
    # 都说明 manifest 与库已不一致，自动化必须拿到非零退出码。
    bad = ("FAILED", "VERIFY_FAIL", "REFERENCED", "SKIPPED")
    return 1 if any(k in counts for k in bad) else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="零引用脚本版本退役（plan 只读 / execute 走控制面 API）"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_plan = sub.add_parser("plan", help="只读：按判据出退役计划")
    p_plan.add_argument("--name", help="只看指定脚本名")
    p_plan.add_argument("--out", help="manifest 落盘路径")
    p_plan.add_argument("--today", help="判定基准日 YYYY-MM-DD")
    p_plan.add_argument("--cooldown-days", type=int, default=None)
    p_plan.set_defaults(func=_plan)

    for name, help_text in (
        ("execute", "按 manifest 退役（DELETE /scripts/{id}）"),
        ("reactivate", "按 manifest 重新激活（PUT is_active=true，误退役处置）"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--manifest", required=True)
        p.add_argument("--yes", action="store_true", help="实际写库；缺省只 dry-run")
        p.add_argument("--limit", type=int, help="只处理前 N 条（分批推进用）")
        p.add_argument("--base-url", default=DEFAULT_BASE_URL)
        p.add_argument("--env-file", default=str(DEFAULT_ENV_FILE))
        p.add_argument("--allow-remote", action="store_true")
        p.set_defaults(func=lambda a, d=(name == "execute"): _apply(a, deactivate=d))

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
