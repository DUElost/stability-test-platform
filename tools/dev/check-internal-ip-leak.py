#!/usr/bin/env python3
"""阻塞式门禁：拦截 public 仓库中的真实内网主机地址（#538/#550/#557 收尾）。

背景
----
仓库保持 **public**（2026-08-29 决策），但历史文档里曾长期残留真实内网资产
地址：agent host（`172.21.15.66` 等）、生产控制面（`172.21.8.202`）、设备
序列号。人工清理过两轮（#550 全泛化 `172.21.x.x`、#557 补 `172.16` 网段），
但**没有门禁就会重新累积**——本脚本即把该检查固化下来。

判定的两难与取舍
----------------
「禁止一切 RFC1918 地址」看似安全，实则会误伤三类合法写法，第一天就满屏红：

- **CIDR 网段常量**：`172.16.0.0/12`（`backend/core/limiter.py` 注释、
  `docker-compose.yml` 的 trusted proxies）。这是**公开标准网段**，不是资产。
- **标准基础设施地址**：`172.17.0.1` 是 Docker 默认 bridge 网关。
- **测试夹具**：`backend/tests/test_rate_limiter.py` 用 `172.20.0.4` 验证
  IP 解析逻辑——删掉它测试就没得测了。

所以本门禁只拦**四段齐全的具体主机地址**（排除 CIDR 与标准地址），并对
「因 ADR-0020 / 已锁定迁移而物理不可改」的目录开白名单。

为什么白名单而不是清理
----------------------
- `backend/agent/scripts/**`：ADR-0020 规定**已发布的脚本版本目录内容不可变**
  （改动会让 DB sha 与磁盘永久不一致，2026-07-31 生产事故即源于此）。
- `backend/alembic/versions/**`：已锁定的迁移不可改写（#550 同口径保留）。
这两处的 `172.21.15.66` 属于「改不动」，只能白名单并在本文档留痕。

用法
----
    python tools/dev/check-internal-ip-leak.py                # 全仓扫描（tracked 文件）
    python tools/dev/check-internal-ip-leak.py --check        # 同上，CI 语义（等价）
    python tools/dev/check-internal-ip-leak.py -q             # 静默：只在命中时输出
    python tools/dev/check-internal-ip-leak.py --self-test    # 正反样例自证
    python tools/dev/check-internal-ip-leak.py <path> [...]   # 指定路径（调试用）

退出码：0 = 无命中；1 = 有命中（CI 阻塞）；2 = 用法/环境错误。
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent

# ── 主机地址（四段全数字；已脱敏写法含 x，天然不匹配）──────────────────
DOT = re.compile(
    r"(?<![\w.])("  # 左侧不得是单词字符或点（避开 1.2.3.4.5 / 版本号）
    r"10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"
    r"|192\.168\.\d{1,3}\.\d{1,3}"
    r")(?![\w.])"
)
# HOST_ID 的横杠写法（历史约定：IPv4 点转横杠），同样只认四段全数字
DASH = re.compile(
    r"(?<![\w-])("
    r"10-\d{1,3}-\d{1,3}-\d{1,3}"
    r"|172-(?:1[6-9]|2\d|3[01])-\d{1,3}-\d{1,3}"
    r"|192-168-\d{1,3}-\d{1,3}"
    r")(?![\w-])"
)
# CIDR / 掩码写法：网段常量，不是主机资产
CIDR = re.compile(r"/\d{1,2}(?![\w.])")

# 与具体部署无关的标准地址，放行
SAFE_LITERALS = {
    "0.0.0.0",        # 通配监听
    "127.0.0.1",      # 回环
    "255.255.255.255",  # 广播
    "172.17.0.1",     # Docker 默认 bridge 网关（非本组织资产）
}

# ── 路径白名单（前缀；理由见文件抬头「为什么白名单而不是清理」）────────
ALLOWLIST_PREFIXES = (
    "backend/agent/scripts/",    # ADR-0020：已发布脚本版本不可变
    "backend/alembic/versions/",  # 已锁定迁移不可改
    "backend/tests/",            # 测试夹具（构造 IP 是被测逻辑的一部分）
    "tests/",                    # 仓库级测试，同上
    "tools/dev/check-internal-ip-leak.py",  # 自身：文档与样例含地址文本
    "tools/dev/testdata/",       # 自检与样例数据
)

# 前端测试文件（按后缀放行，路径不定）
ALLOWLIST_SUFFIXES = (
    ".test.ts", ".test.tsx", ".test.js", ".test.jsx",
    ".spec.ts", ".spec.tsx", ".spec.js", ".spec.jsx",
)

# 本地开发栈：写的是标准网段常量而非资产
ALLOWLIST_FILES = {
    "docker-compose.yml",
    "docker-compose.override.yml",
}

SCAN_SUFFIXES = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
    ".md", ".yml", ".yaml", ".json", ".toml", ".ini", ".cfg",
    ".conf", ".sh", ".env", ".example", ".service", ".sql", ".tf",
}


def _tracked_files(root: Path) -> list[str]:
    proc = subprocess.run(
        ["git", "ls-files"], cwd=root, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        print(f"[ip-leak] git ls-files 失败: {proc.stderr.strip()}", file=sys.stderr)
        raise SystemExit(2)
    return [ln for ln in proc.stdout.splitlines() if ln.strip()]


def _is_allowlisted(rel: str) -> bool:
    if rel in ALLOWLIST_FILES:
        return True
    if rel.startswith(ALLOWLIST_PREFIXES):
        return True
    return rel.endswith(ALLOWLIST_SUFFIXES)


def _masked(text: str, start: int, end: int) -> bool:
    """命中位置紧跟 /掩码 → 是网段常量，放行。"""
    tail = text[end:end + 4]
    return bool(CIDR.match(tail))


def scan_text(text: str, rel: str) -> list[tuple[int, str, str]]:
    """返回 [(行号, 命中文本, 规则名)]。"""
    hits: list[tuple[int, str, str]] = []
    if _is_allowlisted(rel):
        return hits
    for rx, rule in ((DOT, "ipv4-dot"), (DASH, "host-id-dash")):
        for m in rx.finditer(text):
            value = m.group(1)
            if value in SAFE_LITERALS:
                continue
            if _masked(text, m.end(1), m.end(1)):
                continue
            line = text.count("\n", 0, m.start(1)) + 1
            hits.append((line, value, rule))
    hits.sort(key=lambda h: (h[0], h[1]))
    return hits


def scan_path(root: Path, rel: str) -> list[tuple[int, str, str]]:
    if Path(rel).suffix not in SCAN_SUFFIXES:
        return []
    full = root / rel
    try:
        text = full.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return []  # 二进制 / 不可读：跳过
    return scan_text(text, rel)


def _self_test() -> int:
    """正反样例自证：拦截该拦的，放行该放的。"""
    cases = [
        # (相对路径, 文本, 期望命中数)
        ("docs/tmp/x.md", "生产控制面（172.21.8.202）不可用。", 1),
        ("docs/tmp/x.md", "真机 172.21.15.66 复现。", 1),
        ("docs/tmp/x.md", "host id 为 172-21-15-80。", 1),
        ("docs/tmp/x.md", "设备 10.20.30.40 离线。", 1),
        # 放行：已脱敏 / CIDR / 标准地址 / 白名单路径
        ("docs/tmp/x.md", "控制面 172.21.x.x 已脱敏。", 0),
        ("docs/tmp/x.md", "host id 172-21-x-x。", 0),
        ("backend/core/limiter.py", "不把 172.16.0.0/12 塞进全局默认。", 0),
        ("docs/tmp/x.md", "网关 tcp:172.17.0.1:5037。", 0),
        ("docs/tmp/x.md", "监听 0.0.0.0 与 127.0.0.1。", 0),
        ("backend/agent/scripts/flash_firmware/v1.3.6/f.py", "实测 172.21.15.66。", 0),
        ("backend/alembic/versions/abc_x.py", "evidence on 172.21.15.66", 0),
        ("backend/tests/test_rate_limiter.py", 'resolve_client_ip("172.20.0.4")', 0),
        ("frontend/src/a/b.test.tsx", "const ip = '10.0.0.50';", 0),
        # 放行：版本号 / 更长的点分串不该被误判
        ("docs/tmp/x.md", "升级到 1.2.3.4.5 版本。", 0),
        ("docs/tmp/x.md", "sha 10.0.31558 无关。", 0),
    ]
    bad = 0
    for rel, text, want in cases:
        got = len(scan_text(text, rel))
        flag = "ok " if got == want else "FAIL"
        if got != want:
            bad += 1
        print(f"  [{flag}] {rel:<48} 期望={want} 实际={got}  {text[:34]}")
    print(f"\nself-test: {len(cases) - bad}/{len(cases)} 通过")
    return 1 if bad else 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        description="阻塞式内网主机地址扫描（public 仓库，#538 收尾）",
    )
    ap.add_argument("paths", nargs="*", help="限定扫描路径（默认 git ls-files 全仓）")
    ap.add_argument("--check", action="store_true",
                    help="CI 语义：只报告不改写（本脚本从不改写）")
    ap.add_argument("-q", "--quiet", action="store_true", help="无命中时保持静默")
    ap.add_argument("--self-test", action="store_true", help="正反样例自证后退出")
    args = ap.parse_args(argv)

    if args.self_test:
        return _self_test()

    root = ROOT
    if args.paths:
        targets: list[str] = []
        for p in args.paths:
            pp = Path(p)
            if pp.is_dir():
                for f in pp.rglob("*"):
                    if f.is_file():
                        targets.append(str(f.relative_to(root)))
            else:
                targets.append(p if not pp.is_absolute() else str(pp.relative_to(root)))
    else:
        targets = _tracked_files(root)

    total = 0
    files_hit = 0
    for rel in targets:
        hits = scan_path(root, rel)
        if not hits:
            continue
        files_hit += 1
        total += len(hits)
        print(f"{rel}:")
        for line, value, rule in hits:
            print(f"  {line}:{value}  [{rule}]")

    if total:
        print(
            f"\n[ip-leak] 命中 {total} 处 / {files_hit} 个文件。\n"
            "处理：① 真实资产 → 泛化（如 172.21.15.66 → 172.21.x.x）；\n"
            "      ② 网段常量 → 写成 CIDR（172.16.0.0/12）；\n"
            "      ③ 确属不可改（ADR-0020 脚本 / 已锁定迁移 / 测试夹具）→ 加入白名单并注明理由。",
            file=sys.stderr,
        )
        return 1
    if not args.quiet:
        print(f"[ip-leak] 通过：{len(targets)} 个文件无真实内网主机地址")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
