"""报告零 plan_step 引用的脚本版本（退役候选）。

背景（评审 P5）：flash_firmware 等脚本在 `script` 表每版本一行，累积了 11
个版本；`plan_step` 以 `script_name + script_version` 冗余引用（非 FK）。
本工具**只读**查询（任意 DATABASE_URL，含生产库），列出每个 script 版本的
plan_step 引用计数，标出「is_active 且零引用」的退役候选——供人工决定是否
置 `is_active=false`（ADR-0020 的 deactivated 语义）。

用法:
    python -m backend.scripts.check_unreferenced_script_versions
    python -m backend.scripts.check_unreferenced_script_versions --json
    python -m backend.scripts.check_unreferenced_script_versions --name flash_firmware

只读 SELECT；不写库、不改状态。退出码恒 0（诊断工具，非门禁）。
"""
from __future__ import annotations

import argparse
import json
import sys

from sqlalchemy import create_engine, text

from backend.core.env_source import resolve_database_url

_QUERY = text(
    """
    SELECT s.name, s.version, s.is_active,
           COUNT(ps.id) AS refs
    FROM script s
    LEFT JOIN plan_step ps
           ON ps.script_name = s.name AND ps.script_version = s.version
    GROUP BY s.id, s.name, s.version, s.is_active
    ORDER BY s.name, s.version
    """
)


def compute_reference_counts(db) -> list[dict]:
    """对给定 SQLAlchemy 连接/会话返回 [{name, version, is_active, refs}]。"""
    return [dict(r) for r in db.execute(_QUERY).mappings()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--name", help="只看指定脚本名（如 flash_firmware）")
    args = parser.parse_args(argv)

    url, source = resolve_database_url()
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            rows = compute_reference_counts(conn)
    finally:
        engine.dispose()

    if args.name:
        rows = [r for r in rows if r["name"] == args.name]

    candidates = [r for r in rows if r["refs"] == 0 and r["is_active"]]
    if args.json:
        print(
            json.dumps(
                {"rows": rows, "retirement_candidates": candidates},
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(f"# script 版本引用（env 源：{source}）")
        print(f"{'name':<24} {'version':<10} {'active':<7} {'refs':>4}")
        for r in rows:
            print(
                f"{r['name']:<24} {r['version']:<10} {str(r['is_active']):<7} {r['refs']:>4}"
            )
        print()
        if candidates:
            print(f"退役候选（active 且零引用，{len(candidates)} 个）：")
            for r in candidates:
                print(f"  {r['name']}@{r['version']}")
        else:
            print("无退役候选（全部 active 版本均有 plan_step 引用）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
