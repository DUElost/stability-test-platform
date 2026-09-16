"""空库自举后的脚本身份守卫（#2399）。

背景：seed 迁移的「守卫版本 / 写入版本 / sha 所指版本」曾经错位（两条 v1.3.8/v1.3.9
迁移 off-by-one），空库 `alembic upgrade head` 因此产出一行「version=1.3.8、
content_sha256·nfs_path 属于 1.3.9」的幽灵行，并且 v1.3.9 根本没有行。
`script_catalog` 以 `content_sha256` 判 conflict 并**只跳过不修**，所以新环境不会被
重扫自愈（要 `force_rebaseline`，管理员动作）。同族已三次：#751 / #1276 / #2399。

本脚本断言两件事（**只读**，不写库）：

1. **全局性质**：`script` 表里每一行，只要它自己的 `<name>/v<version>/` 目录在仓库
   脚本树里存在，其 `content_sha256` 就必须等于该目录入口文件的 sha——这正是
   `POST /scripts/scan` 判 conflict 用的同一个判据（复用 `script_catalog` 的实现，
   不另立一份真值）。等价于 issue 建议的「upgrade head 后跑一次 scan，conflicts == []」，
   但不需要真的起服务与鉴权。
2. **本单回归面**：`flash_firmware` 1.3.7 / 1.3.8 / 1.3.9 三行都存在，且各自带各自的
   真值——「补出行」这件事无法由性质 1 表达（缺行没有可比对象）。

为什么不校验 `nfs_path` 的绝对前缀：它是**宿主侧路径**，站点按
`STP_SCRIPT_RUNTIME_ROOT` 锚定（见 `script_catalog` 的「路径锚点跟随站点配置」）。
只校验版本目录段——它才是 #2399 里与 sha 错位的那一面。

用法：
    python -m backend.scripts.check_seed_identity

DATABASE_URL 解析与 `check_schema_sync` 同（环境变量最优先，其次 .env.backend）。
CI 里挂在 `pr-migrate-empty-db`（空库 upgrade head 之后），本地由
`tools/dev/check_pr_migrate.py` 同步复刻。
"""
from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.core.env_source import resolve_database_url
from backend.models.script import Script
from backend.services.script_catalog import _iter_script_entries, sha256_file

SCRIPT_ROOT = Path(__file__).resolve().parents[1] / "agent" / "scripts"

#: 本单的回归面：seed 链必须产出的 (name, version) 行。
REQUIRED_ROWS = (("flash_firmware", "1.3.7"), ("flash_firmware", "1.3.8"),
                 ("flash_firmware", "1.3.9"))


def disk_identity() -> dict[tuple[str, str], str]:
    """磁盘上「扫描会登记成什么指纹」的映射，复用扫描自己的入口挑选与哈希。"""
    return {
        (name, version): sha256_file(entry)
        for _cat, name, version, entry, _stype in _iter_script_entries(SCRIPT_ROOT)
    }


def main() -> int:
    # 不捕获「未配置 DATABASE_URL」：本脚本只挂在 pr-migrate-empty-db / check_pr_migrate
    # 上，那里 DATABASE_URL 必在——缺了就是接线断了，静默 SKIP 会把守卫变成摆设。
    # 只打印 source 标签，绝不回显 DATABASE_URL 本身（可能含凭据）。
    url, source = resolve_database_url()
    engine = create_engine(url, future=True)
    truth = disk_identity()
    problems: list[str] = []

    with Session(engine) as session:
        rows = session.query(Script).all()
        by_key = {(row.name, row.version): row for row in rows}

        for row in sorted(rows, key=lambda r: (r.name, r.version)):
            key = (row.name, row.version)
            expected = truth.get(key)
            if expected is None:
                continue  # 目录不在此树里（历史版本/其它挂载源），不归本守卫
            if (row.content_sha256 or "") != expected:
                problems.append(
                    f"{row.name}@{row.version} 行内 sha={(row.content_sha256 or 'EMPTY')[:12]}… "
                    f"!= 磁盘入口 {expected[:12]}… → scan 会判 conflict 且不会自愈；"
                    "若这是 seed 迁移写错身份，追加一条修复 revision 收敛（#2258 不许改历史），"
                    "不要直接 force_rebaseline 掩盖来源"
                )
            marker = f"/v{row.version}/"
            if marker not in (row.nfs_path or ""):
                problems.append(
                    f"{row.name}@{row.version} 的 nfs_path 不含版本目录段 {marker}"
                    f"（实际={row.nfs_path!r}）→ 派发会指向另一个版本的脚本"
                )

        for name, version in REQUIRED_ROWS:
            if (name, version) not in by_key:
                problems.append(
                    f"缺 {name}@{version} 行：seed 链没有登记它（#2399 的空库形态），"
                    "后果是该内容版本永远等不到 scan 之外的显式登记"
                )

    if problems:
        print(f"[FAIL] check-seed-identity：{len(problems)} 处脚本身份与磁盘真值不一致",
              file=sys.stderr)
        for line in problems:
            print(f"  - {line}", file=sys.stderr)
        return 1

    print(f"[OK] check-seed-identity（DATABASE_URL source={source}）："
          f"{len(rows)} 行脚本身份与磁盘真值一致，覆盖 {len(truth)} 个磁盘版本")
    return 0


if __name__ == "__main__":
    sys.exit(main())
