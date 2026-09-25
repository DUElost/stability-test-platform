#!/usr/bin/env python3
"""按站点 DB 现态把 ``tool_manifest.json`` 的 script 族条目 flip retired（ADR-0051 Phase 4b）。

退役的**发布级真源是 manifest**（sync 据 retired:true 建行 inactive / 反激活禁走 PUT）；
本工具把「本库当前 active 集」物化成 manifest 的 retired 标记，使新站/灾备的 catalog
与本站一致。判据与安全：

- 只 flip **script 族**（kind=script）；条目在场、库中无行或行 inactive → retired:true；
- **红线拒动**：被 ``plan_step`` 引用的 (name, version) 绝不退役（引用闭合由行存在保证，
  退役 = 从可选面移除，正在被引用的不允许——与 ``SCRIPT_STILL_REFERENCED`` 同一语义）；
- 默认 dry-run 打印 flip 计划；``--apply`` 才写 manifest（写后请由人提交，PR 审 retired 清单）。

用法::

    DATABASE_URL=... python tools/dev/manifest_retire_from_db.py            # dry-run
    DATABASE_URL=... python tools/dev/manifest_retire_from_db.py --apply
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


from sqlalchemy import create_engine, text

from backend.core.database import normalize_sync_database_url


def _sync_url() -> str:
    raw = os.getenv("DATABASE_URL", "").strip()
    if not raw:
        print("DATABASE_URL 未设置", file=sys.stderr)
        sys.exit(2)
    return normalize_sync_database_url(raw)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--manifest", type=Path, default=MANIFEST)
    args = ap.parse_args()

    eng = create_engine(_sync_url())
    doc = json.loads(args.manifest.read_text(encoding="utf-8"))
    with eng.connect() as conn:
        active = {(r[0], r[1]) for r in conn.execute(text("select name, version from script where is_active"))}
        refs = {(r[0], r[1]) for r in conn.execute(
            text("select distinct script_name, script_version from plan_step"))}

    flips: list[str] = []
    refused: list[str] = []
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
            flips.append(f"{name}@{key[1]}")
            if args.apply:
                entry["retired"] = True

    print(f"拟退役 flip: {len(flips)}；被引用拒动: {len(refused)} {refused[:5]}")
    for f in flips[:8]:
        print("   -", f)
    if args.apply:
        args.manifest.write_text(json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"[OK] 已写 {args.manifest}（flip {len(flips)} 条）——PR 里人工核对 retired 清单")
    else:
        print("[dry-run] 加 --apply 写入")
    return 0


if __name__ == "__main__":
    sys.exit(main())
