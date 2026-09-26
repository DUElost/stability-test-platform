#!/usr/bin/env python3
"""按站点 DB 现态把 ``tool_manifest.json`` 的 script 族条目 flip retired（ADR-0051 Phase 4b）。

退役的**发布级真源是 manifest**（sync 据 retired:true 建行 inactive / 反激活禁走 PUT）；
本工具把「本库当前 active 集」物化成 manifest 的 retired 标记，使新站/灾备的 catalog
与本站一致。判据与安全（#3386 收口：**行缺失 ≠ 已退役**）：

- 只 flip **script 族**（kind=script），且判据改为「库中**存在该行**且 ``is_active=false``
  且未被引用」——旧行为「库中无行也算退役候选」在空库/新站库/指错库时会把整个
  manifest 翻成 retired（``retired`` true→false 被门禁判为不可逆，后果不可撤销）；
- **缺行条目单列 ``missing_rows`` 仅报告、不动作**——缺行可能是尚未 scan 的新站库，
  「没扫到」不构成退役证据；
- **红线拒动**：被 ``plan_step`` 引用的 (name, version) 绝不退役（引用闭合由行存在保证，
  退役 = 从可选面移除，正在被引用的不允许——与 ``SCRIPT_STILL_REFERENCED`` 同一语义）；
- 默认 dry-run 打印 flip 计划；``--apply`` 才写 manifest（写后请由人提交，PR 审 retired 清单）；
- ``--apply`` 健全性下限：script 表为空 / active 集为空 / 拟 flip 超过非退役 script 条目
  半数时拒绝写盘——确要继续需显式 ``--force``（每次越过下限都会在输出里留痕）。

用法::

    DATABASE_URL=... python tools/dev/manifest_retire_from_db.py            # dry-run
    DATABASE_URL=... python tools/dev/manifest_retire_from_db.py --apply
    DATABASE_URL=... python tools/dev/manifest_retire_from_db.py --apply --force   # 越过健全性下限
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))  # backend.* 可导入（本工具从任意 cwd 运行）
MANIFEST = REPO_ROOT / "tool_manifest.json"

#: --apply 健全性下限（#3386）：拟 flip 数占「非退役 script 条目」的比例上限。
#: 一半是「本库与 manifest 大面积不一致」的经验信号——合法的全站收敛批（如 #3262
#: 的 109 条）行都在库里且 inactive，缺行的灾难形态（空库全翻）已被行存在判据拦住，
#: 这个下限拦的是「行存在但大面积 inactive」的指错库形态。
MAX_FLIP_RATIO = 0.5

from sqlalchemy import create_engine, text

from backend.core.database import normalize_sync_database_url


def _sync_url() -> str:
    raw = os.getenv("DATABASE_URL", "").strip()
    if not raw:
        print("DATABASE_URL 未设置", file=sys.stderr)
        sys.exit(2)
    return normalize_sync_database_url(raw)


def classify_script_entries(
    doc: dict,
    all_rows: set[tuple[str, str]],
    active: set[tuple[str, str]],
    refs: set[tuple[str, str]],
) -> tuple[list[str], list[str], list[str]]:
    """把 script 族条目分成 (flips, refused, missing_rows)，纯函数便于测试。

    - flips：行存在、``is_active=false``、未被引用（#3386 判据核心：**行缺失不进这里**）；
    - refused：被 ``plan_step`` 引用（红线拒动）；
    - missing_rows：库中无行——仅报告，缺行 ≠ 已退役。
    """
    flips: list[str] = []
    refused: list[str] = []
    missing_rows: list[str] = []
    for name, tool in doc["tools"].items():
        if tool.get("kind") != "script":
            continue
        for entry in tool["versions"]:
            key = (name, str(entry["version"]))
            if entry.get("retired") or key in active:
                continue
            if key in refs:
                refused.append(f"{name}@{key[1]}")
                continue
            if key not in all_rows:
                missing_rows.append(f"{name}@{key[1]}")
                continue
            flips.append(f"{name}@{key[1]}")
    return flips, refused, missing_rows


def apply_sanity_problems(
    total_script_entries: int,
    all_rows: set[tuple[str, str]],
    active: set[tuple[str, str]],
    flips: list[str],
) -> list[str]:
    """--apply 健全性下限：返回非空问题清单时写盘被拒（--force 显式越过）。"""
    problems: list[str] = []
    if not all_rows:
        problems.append("script 表为空——空库/指错库无法为任何退役提供证据")
    elif not active:
        problems.append("active 集为空——疑似空库或全量反激活的库")
    elif total_script_entries and len(flips) > MAX_FLIP_RATIO * total_script_entries:
        problems.append(
            f"拟 flip {len(flips)}/{total_script_entries} 超过 "
            f"{MAX_FLIP_RATIO:.0%} 阈值——疑似与 manifest 大面积不一致的库"
        )
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--manifest", type=Path, default=MANIFEST)
    ap.add_argument("--force", action="store_true", help="越过 --apply 健全性下限（每项越线都会打印留痕）")
    args = ap.parse_args()

    eng = create_engine(_sync_url())
    doc = json.loads(args.manifest.read_text(encoding="utf-8"))
    with eng.connect() as conn:
        all_rows = {(r[0], r[1]) for r in conn.execute(text("select name, version from script"))}
        active = {(r[0], r[1]) for r in conn.execute(text("select name, version from script where is_active"))}
        refs = {(r[0], r[1]) for r in conn.execute(
            text("select distinct script_name, script_version from plan_step"))}

    flips, refused, missing_rows = classify_script_entries(doc, all_rows, active, refs)

    print(f"拟退役 flip: {len(flips)}；被引用拒动: {len(refused)} {refused[:5]}；"
          f"库中缺行（仅报告）: {len(missing_rows)} {missing_rows[:5]}")
    for f in flips[:8]:
        print("   -", f)
    if not args.apply:
        print("[dry-run] 加 --apply 写入")
        return 0

    problems = apply_sanity_problems(
        sum(
            1
            for t in doc["tools"].values() if t.get("kind") == "script"
            for e in t["versions"] if not e.get("retired")
        ),
        all_rows, active, flips,
    )
    if problems:
        for p in problems:
            print(f"[SANITY] {p}", file=sys.stderr)
        if not args.force:
            print("[REFUSED] --apply 被健全性下限拒绝；确认库指向无误后可用 --force 显式越过", file=sys.stderr)
            return 2
        print("[FORCED] 健全性下限被 --force 越过（本次运行留痕见上）", file=sys.stderr)

    if not flips:
        print("[OK] 无可写变更（没有满足判据的退役候选），manifest 未改")
        return 0
    for name, tool in doc["tools"].items():
        if tool.get("kind") != "script":
            continue
        for entry in tool["versions"]:
            if f"{name}@{str(entry['version'])}" in flips:
                entry["retired"] = True
    args.manifest.write_text(json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"[OK] 已写 {args.manifest}（flip {len(flips)} 条）——PR 里人工核对 retired 清单")
    return 0


if __name__ == "__main__":
    sys.exit(main())
