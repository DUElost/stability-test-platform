#!/usr/bin/env python3
"""过渡登记簿门禁（ADR-0051 D8）：`docs/governance/transitions.json` 的 lint + 到期执法。

AGENTS.md 要求「临时止血必须标注为过渡并写明终态出口——不标注的止血会沉淀为技术债」，
但标注本身此前**没有到期机制**：448 处「过渡/止血」措辞里，真正在途的机制性过渡
（env 注入、双轨回退、逃生阀窗口……）与历史陈述混在一起，无人被迫回收。
本门禁把台账变成可机检的承诺：

- **schema**：条目字段恰好 {id, what, exit, due, status, evidence?}；id 唯一 kebab-case；
  `status ∈ {active, done, dropped}`；due 为 `YYYY-MM-DD`；
- **到期执法（active）**：`due` 已过而未撤 → 红。续期是合法动作，但必须走 PR
  （改 due + 在 what 里写明为什么续）——让"忘了"变成"当着评审改日期"；
- **exit 锚点可解析（active）**：`adr:ADR-NNNN#条款` → 文件存在且条款标题在场；
  `issue:#N` → 格式合法（门禁不连 GitHub）；禁止 "以后再说" 式空出口；
- **撤销留证（done/dropped）**：`evidence` 非空（PR 号 / commit / 文档行）。
  机器不验证「对象确已删除」——那由 PR 评审 + code 面自证（删除 PR 让在场判据变红）。

台账只收**在途机制性过渡**；docs 里对历史的"曾为过渡"陈述不入账（历史面不追改）。

用法::

    python tools/dev/check_transitions.py --self-test
    python tools/dev/check_transitions.py [--today 2026-09-24]  # 测试注入
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
LEDGER_PATH = REPO_ROOT / "docs" / "governance" / "transitions.json"
FIELDS = frozenset({"id", "what", "exit", "due", "status", "evidence"})
STATUSES = frozenset({"active", "done", "dropped"})
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_DUE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _parse_date(s: str) -> dt.date | None:
    if not _DUE_RE.match(s):
        return None
    try:
        return dt.date.fromisoformat(s)
    except ValueError:
        return None


def lint_ledger(doc: object) -> list[str]:
    """纯函数：schema lint（空 = 绿）。"""
    errs: list[str] = []
    if not isinstance(doc, dict) or doc.get("schema_version") != 1 or not isinstance(doc.get("transitions"), list):
        return ["顶层必须为 {schema_version: 1, transitions: [...]}"]
    seen: set[str] = set()
    for i, t in enumerate(doc["transitions"]):
        tag = f"transitions[{i}]"
        if not isinstance(t, dict):
            errs.append(f"{tag}: 条目必须是对象")
            continue
        fields = set(t)
        if fields - FIELDS:
            errs.append(f"{tag}: 多余字段 {sorted(fields - FIELDS)}")
        missing = {"id", "what", "exit", "due", "status"} - fields
        if missing:
            errs.append(f"{tag}: 缺字段 {sorted(missing)}")
            continue
        tid = t["id"]
        if not isinstance(tid, str) or not _ID_RE.match(tid):
            errs.append(f"{tag}: id 非法（kebab/dotted）：{tid!r}")
        elif tid in seen:
            errs.append(f"{tag}: id 重复：{tid}")
        else:
            seen.add(tid)
        if not (isinstance(t["what"], str) and t["what"].strip()):
            errs.append(f"{tag}: what 必填非空（过渡物是什么、为何存在）")
        due = _parse_date(str(t["due"]))
        if due is None:
            errs.append(f"{tag}: due 必须为 YYYY-MM-DD，实际 {t['due']!r}")
        if t["status"] not in STATUSES:
            errs.append(f"{tag}: status 必须 ∈ {sorted(STATUSES)}，实际 {t['status']!r}")
            continue
        if t["status"] in ("done", "dropped") and not str(t.get("evidence", "")).strip():
            errs.append(f"{tag}: {t['status']} 必须带 evidence（PR/commit/文档行）")
        if t["status"] == "active" and "evidence" in t:
            errs.append(f"{tag}: active 不应带 evidence（撤账时才填）")
    return errs


def resolve_exit(exit_ref: str, repo_root: Path) -> str | None:
    """纯函数：active 条目的 exit 锚点可解析性。返回错误说明或 None。"""
    if exit_ref.startswith("adr:"):
        m = re.match(r"^adr:(ADR-\d{4})(?:#(.+))?$", exit_ref)
        if not m:
            return f"adr 锚点格式非法：{exit_ref!r}（adr:ADR-NNNN#条款）"
        fn, clause = m.group(1), m.group(2)
        hits = sorted((repo_root / "docs" / "adr").glob(f"{fn}-*.md"))
        if not hits:
            return f"{fn} 文件不存在"
        if not clause:
            return f"{fn} 缺条款锚（#D7 / #Phase-3）——整篇 ADR 作出口不可解析"
        body = hits[0].read_text(encoding="utf-8")
        needle = clause.strip()
        # 条款锚优先按标题精确匹配（#D7 / ### D7），否则退化为词边界全文匹配（#Phase-3 等）
        if re.search(rf"^#{{2,4}}\s*{re.escape(needle)}\b", body, re.M):
            return None
        if re.search(rf"\b{re.escape(needle)}\b", body):
            return None
        return f"{fn} 内找不到条款锚 {needle!r}"
    if exit_ref.startswith("issue:#"):
        return None if re.fullmatch(r"issue:#\d+", exit_ref) else f"issue 锚点格式非法：{exit_ref!r}"
    return f"exit 前缀必须是 adr: 或 issue:#，实际 {exit_ref!r}（禁止『以后再说』）"


def check_ledger(doc: dict, today: dt.date, repo_root: Path) -> list[str]:
    """lint + 到期 + exit 锚点的合并判据。"""
    errs = lint_ledger(doc)
    if errs:
        return errs
    for t in doc["transitions"]:
        if t["status"] != "active":
            continue
        due = _parse_date(str(t["due"]))
        assert due is not None
        if due < today:
            errs.append(
                f"{t['id']}: 过渡已到期（due={t['due']} < {today.isoformat()}）且仍 active——"
                "撤账（done + evidence）或走 PR 续期（改 due 并在 what 写明理由）"
            )
        err = resolve_exit(str(t["exit"]), repo_root)
        if err:
            errs.append(f"{t['id']}: exit 锚不可解析：{err}")
    return errs


def run_self_test() -> int:
    failures: list[str] = []

    def entry(**over):
        base = {"id": "t-1", "what": "某 env 注入过渡", "exit": "issue:#3075",
                "due": "2099-01-01", "status": "active"}
        base.update(over)
        return base

    good = {"schema_version": 1, "transitions": [
        entry(),
        entry(id="t-done", status="done", evidence="PR #1234"),
        entry(id="t-drop", status="dropped", evidence="commit abc123"),
    ]}
    today = dt.date(2026, 9, 24)
    if errs := check_ledger(good, today, REPO_ROOT):
        failures.append(f"合法台账应绿：{errs}")
    for bad, why in [
        ({"schema_version": 2, "transitions": []}, "schema 版本"),
        ({"schema_version": 1, "transitions": [entry(bonus=1)]}, "多余字段"),
        ({"schema_version": 1, "transitions": [entry(id="Bad ID")]}, "id 格式"),
        ({"schema_version": 1, "transitions": [entry(due="后年") ]}, "due 格式"),
        ({"schema_version": 1, "transitions": [entry(status="pending")]}, "status 词表"),
        ({"schema_version": 1, "transitions": [entry(status="done")]}, "done 缺 evidence"),
    ]:
        if not lint_ledger(bad):
            failures.append(f"{why} 应红")
    overdue = {"schema_version": 1, "transitions": [entry(due="2026-01-01")]}
    if not any("已到期" in e for e in check_ledger(overdue, today, REPO_ROOT)):
        failures.append("active 过期应红")
    future = {"schema_version": 1, "transitions": [entry(due="2027-01-01")]}
    if check_ledger(future, today, REPO_ROOT):
        failures.append("未到期应绿")
    badadr = {"schema_version": 1, "transitions": [entry(exit="adr:ADR-9999#D1")]}
    if not any("exit 锚" in e for e in check_ledger(badadr, today, REPO_ROOT)):
        failures.append("不存在的 ADR 锚应红")
    goodadr = {"schema_version": 1, "transitions": [entry(exit="adr:ADR-0051#D8")]}
    if check_ledger(goodadr, today, REPO_ROOT):
        failures.append("ADR-0051 D8 锚应绿")
    vague = {"schema_version": 1, "transitions": [entry(exit="以后看情况")]}
    if not any("exit 锚" in e for e in check_ledger(vague, today, REPO_ROOT)):
        failures.append("模糊 exit 应红")
    if failures:
        for f in failures:
            print(f"[SELFTEST-FAIL] {f}", file=sys.stderr)
        return 1
    print("[OK] check_transitions self-test 红绿双向（schema/词表/到期/续期绿/exit 锚三类/evidence）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ledger", type=Path, default=LEDGER_PATH)
    ap.add_argument("--today", default=None, help="YYYY-MM-DD 注入（测试用）")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        return run_self_test()
    today = dt.date.fromisoformat(args.today) if args.today else dt.date.today()
    try:
        doc = json.loads(args.ledger.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"[FAIL] 台账不可读：{exc}", file=sys.stderr)
        return 1
    errs = check_ledger(doc, today, REPO_ROOT)
    if errs:
        for e in errs:
            print(f"[FAIL] {e}", file=sys.stderr)
        print(f"[FAIL] 过渡登记簿：{len(errs)} 项违例", file=sys.stderr)
        return 1
    active = [t for t in doc["transitions"] if t["status"] == "active"]
    print(f"[OK] 过渡登记簿一致（{len(doc['transitions'])} 条 / {len(active)} 在途）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
