#!/usr/bin/env python3
"""计算 requirements.txt 的内容摘要,用于校验 lock 是否过期。

为什么需要摘要而不是比对包名集合:只比包名时,`fastapi>=0.104` 改成
`fastapi==999.0`、或 `uvicorn[standard]` 掉成 `uvicorn`,包名都没变 ——
守卫全绿,而 CI 装的是新约束、Docker 装的是旧 lock,测试与生产的依赖悄悄分叉。

规范化规则(让无关改动不触发误报):
  - 去掉注释与空行
  - 去掉行内尾随注释
  - 排序(条目顺序无语义)
  - 其余**逐字节**保留:版本约束、extras、环境标记的任何变化都会改变摘要

用法:
    python tools/dev/requirements_digest.py backend/requirements.txt
    python tools/dev/requirements_digest.py --check backend/requirements.txt backend/requirements.lock
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path

# 写进 lock 抬头的标记行
DIGEST_MARKER = "# requirements-digest: sha256:"


def normalize(requirements_text: str) -> list[str]:
    entries = []
    for raw in requirements_text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            entries.append(line)
    return sorted(entries)


def compute(requirements_path: Path) -> str:
    entries = normalize(requirements_path.read_text(encoding="utf-8"))
    payload = "\n".join(entries).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def read_recorded(lock_path: Path) -> str | None:
    for line in lock_path.read_text(encoding="utf-8").splitlines():
        if line.startswith(DIGEST_MARKER):
            return line[len(DIGEST_MARKER):].strip()
        # 摘要必须在抬头注释区;遇到第一个包名行就停
        if re.match(r"^[A-Za-z0-9]", line):
            break
    return None


def stamp(lock_path: Path, digest: str) -> None:
    """把摘要写进 lock 抬头(已有则替换)。"""
    lines = lock_path.read_text(encoding="utf-8").splitlines()
    marker_line = f"{DIGEST_MARKER}{digest}"
    for i, line in enumerate(lines):
        if line.startswith(DIGEST_MARKER):
            lines[i] = marker_line
            break
    else:
        # 插在抬头注释区末尾(第一个非注释行之前)
        idx = next(
            (i for i, l in enumerate(lines) if l.strip() and not l.startswith("#")),
            len(lines),
        )
        lines.insert(idx, marker_line)
        lines.insert(idx, "#")
    lock_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("requirements", type=Path)
    ap.add_argument("lock", type=Path, nargs="?")
    ap.add_argument("--check", action="store_true", help="校验 lock 中记录的摘要是否匹配")
    ap.add_argument("--stamp", action="store_true", help="把当前摘要写入 lock")
    args = ap.parse_args()

    digest = compute(args.requirements)

    if args.stamp:
        if not args.lock:
            ap.error("--stamp 需要同时给出 lock 路径")
        stamp(args.lock, digest)
        print(f"已写入 {args.lock}: {digest}")
        return 0

    if args.check:
        if not args.lock:
            ap.error("--check 需要同时给出 lock 路径")
        recorded = read_recorded(args.lock)
        if recorded is None:
            print(f"FAIL: {args.lock} 抬头没有 {DIGEST_MARKER}<hash> 标记", file=sys.stderr)
            return 1
        if recorded != digest:
            print(
                f"FAIL: lock 已过期\n  {args.requirements} 当前摘要: {digest}\n"
                f"  {args.lock} 记录的摘要: {recorded}\n"
                "  请重新生成 lock(命令见 lock 抬头)。",
                file=sys.stderr,
            )
            return 1
        print(f"OK: lock 与 {args.requirements} 一致 ({digest[:16]}…)")
        return 0

    print(digest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
