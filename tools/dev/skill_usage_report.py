#!/usr/bin/env python3
"""skill 用量报告：防「建而不用」的空洞。

用户裁决的前提是「建 skill 必须被用，不许造完就闲置」。本工具把这条变成
可观测事实：

  数据源 = 本机 Claude Code 会话转录（~/.claude/projects/<项目>/*.jsonl），
  流式匹配 Skill 工具调用。判定启发式（确定性、无需解析全量 JSON）：
  一行同时命中 `"Skill"` + `"tool_use"` + skill slug → 记一次调用；
  同行多次出现按出现次数计。

  洞态定义（HOLLOW）：skill 存在 ≥ HOLLOW_DAYS 天且历史总调用为 0。
  触发时建议二选一：删除，或改写 description 让触发词真实可命中——
  对应 design/2026-08-governance-surface-protection.md §8.1 观察台账。

用法:
    python tools/dev/skill_usage_report.py             # 报表
    python tools/dev/skill_usage_report.py --strict    # 有洞 → exit 1
"""
from __future__ import annotations

import argparse
import glob
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SKILLS_DIR = os.path.join(ROOT, ".claude", "skills")
TRANSCRIPT_DIR = os.environ.get(
    "STP_SKILL_TRANSCRIPT_DIR",
    os.path.expanduser(
        "~/.claude/projects/-home-debian13-stability-test-platform"
    ),
)
HOLLOW_DAYS = 14


def inventory() -> list[dict]:
    """盘点仓内 skills：slug / description / 出生时间（首次入库时间）。"""
    out = []
    for d in sorted(os.listdir(SKILLS_DIR)) if os.path.isdir(SKILLS_DIR) else []:
        sk = os.path.join(SKILLS_DIR, d, "SKILL.md")
        if not os.path.isfile(sk):
            continue
        text = open(sk, encoding="utf-8").read()
        desc = ""
        nm = d
        m = re.match(r"^---\n(.*?)\n---", text, re.S)
        if m:
            block = m.group(1)
            fm = dict(re.findall(r"^([\w-]+):\s*(.*)$", block, re.M))
            desc = fm.get("description", "").strip()
            nm = fm.get("name", d).strip()
        birth = None
        try:
            ts = subprocess.run(
                ["git", "log", "--diff-filter=A", "--format=%at", "-1", "--",
                 os.path.relpath(sk, ROOT)],
                capture_output=True, text=True, cwd=ROOT,
            ).stdout.strip()
            birth = int(ts) if ts else None
        except Exception:  # noqa: BLE001 —— 观测工具宽容降级
            pass
        out.append({"dir": d, "name": nm, "desc": desc[:60], "birth": birth})
    return out


def count_usage(slug: str) -> tuple[int, int | None]:
    """返回 (总调用数, 最近一次调用的 unix 时间或 None)。见模块头启发式说明。"""
    total = 0
    last: int | None = None
    ts_re = re.compile(r'"timestamp":"([^"]+)"')
    for path in glob.glob(os.path.join(TRANSCRIPT_DIR, "*.jsonl")):
        try:
            with open(path, encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    if '"Skill"' not in line or "tool_use" not in line:
                        continue
                    hits = line.count(f'"{slug}"')
                    if not hits:
                        continue
                    total += hits
                    m = ts_re.search(line)
                    if m:
                        t = m.group(1).rstrip("Z")
                        for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
                            try:
                                epoch = datetime.strptime(
                                    t, fmt).replace(tzinfo=timezone.utc).timestamp()
                                last = max(last or 0, int(epoch))
                                break
                            except ValueError:
                                continue
        except OSError:
            continue
    return total, last


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--strict", action="store_true",
                    help="存在洞态 skill 时 exit 1（供门禁化预留）")
    args = ap.parse_args()

    items = inventory()
    if not items:
        print("[info] .claude/skills/ 下没有已注册 skill")
        return 0

    now = time.time()
    hollow = 0
    width = max(len(i["dir"]) for i in items)
    print(f"# skill 用量报告（转录源: {TRANSCRIPT_DIR}）")
    print("# 计数为启发式上界（同行提及亦计入）；判洞只依赖「是否为零」"
          "——零值可靠，正数仅代表有人知道它\n")
    for it in items:
        total, last = count_usage(it["name"])
        age_days = (int((now - it["birth"]) / 86400)
                    if it["birth"] else None)
        age_s = f"{age_days}d" if age_days is not None else "?"
        last_s = (time.strftime("%Y-%m-%d %H:%M", time.localtime(last))
                  if last else "从未")
        flag = ""
        if age_days is not None and age_days >= HOLLOW_DAYS and total == 0:
            flag = "  ⚠️ HOLLOW"
            hollow += 1
        print(f"[{it['dir']:<{width}}] 出生 {age_s:>4} | 调用 {total:>3} 次 "
              f"| 最近 {last_s}{flag}")
        if not it["desc"]:
            print(f"{'':<{width+4}}⚠️ description 为空（S7 应已拦；此处兜底提示）")

    if hollow:
        print(f"\n[HOLLOW] {hollow} 个 skill 存在 ≥{HOLLOW_DAYS} 天零调用——"
              f"删除或修 description 触发词，勿留空壳", file=sys.stderr)
        return 1 if args.strict else 0
    print("\n[OK] 无洞态 skill")
    return 0


if __name__ == "__main__":
    sys.exit(main())
