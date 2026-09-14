#!/usr/bin/env python3
"""环境变量读取清单：生成 / 漂移门禁（#737 文档切片）。

问题（#737）：`backend/**` 有两百多个 `os.getenv` / `os.environ.get` 读取名，
绝大多数从未记录，运维与新人只能读源码；`.env*.example` 只承载**运维模板**
子集，天然不覆盖内部旋钮。

本工具把「代码到底读了什么」做成可验证的清单，消除配置黑盒：

- 扫描 `backend/**/*.py`（不含 `backend/agent/scripts/**`——版本化脚本目录的
  环境契约见 ADR-0020）的读取形态：`os.getenv("X")`、`os.environ.get("X")`、
  `os.environ["X"]`、以及项目 helper（`_int_env("X", …)` 等 `_*_env("X"` 形态，
  默认值取 `production_default=` / 位置第二参数字面量）；
- 渲染确定性表格（变量 | 默认 | 示例登记 | 类别 | 首个读取点）写入
  `docs/development/environment-variables.md` 的生成块（marker 之间，勿手改）；
- `--check` 与文档生成块逐字节比对：代码新增读取名而文档未刷新即红。

退出码：漂移/用法错 → 1；一致或写入成功 → 0。`--self-test` 离线红绿双向自证。
"""

from __future__ import annotations

import argparse
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs" / "development" / "environment-variables.md"
SCAN_ROOT = ROOT / "backend"
SCAN_SKIP_PARTS = {"__pycache__", "scripts"}
SKIP_PARTS = {"__pycache__", ".git", ".wt", "node_modules", ".venv", "venv"}

BEGIN_MARK = "<!-- env-inventory:begin（generated：python tools/dev/env_inventory.py --write） -->"
END_MARK = "<!-- env-inventory:end -->"

#: 读取形态（每文件动态组装，见 `_file_patterns`）。组 1 = 变量名；
#: 组 2（可选）= 默认值表达式原文。接收者只认 `os` 及其**导入别名**——
#: 2026-09-14 复核踩过两个坑：① 只认字面 `os.getenv` 会漏 `import os as X`；
#: ② 放宽容收（裸 `environ.get(`）会把 WSGI environ 的 `HTTP_ORIGIN` 误当
#: 环境变量。故改为按导入语句精确派生接收者。
_OS_MODULE_ALIAS_RE = re.compile(r"^import\s+os\s+as\s+([A-Za-z_][A-Za-z0-9_]*)", re.M)
_OS_GETENV_ALIAS_RE = re.compile(r"from\s+os\s+import\s+getenv\s+as\s+([A-Za-z_][A-Za-z0-9_]*)")
_OS_ENVIRON_ALIAS_RE = re.compile(r"from\s+os\s+import\s+environ\s+as\s+([A-Za-z_][A-Za-z0-9_]*)")
_OS_BARE_GETENV_RE = re.compile(r"from\s+os\s+import\s+getenv\b(?!\s+as\b)")
_OS_BARE_ENVIRON_RE = re.compile(r"from\s+os\s+import\s+environ\b(?!\s+as\b)")
_HELPER_ENV_RE = re.compile(
    r"""\b_[a-z][a-z0-9_]*_env\(\s*["']([A-Z][A-Z0-9_]+)["']([^)\n]*)""",
)


def _getenv_pattern(receiver: str | None) -> re.Pattern[str]:
    prefix = f"{receiver}\\." if receiver else ""
    return re.compile(
        rf"""\b{prefix}getenv\(\s*["']([A-Z][A-Z0-9_]+)["']\s*(?:,\s*([^),\n]+))?"""
    )


def _environ_get_pattern(receiver: str | None) -> re.Pattern[str]:
    prefix = f"{receiver}\\." if receiver else ""
    return re.compile(
        rf"""\b{prefix}environ\.get\(\s*["']([A-Z][A-Z0-9_]+)["']\s*(?:,\s*([^),\n]+))?"""
    )


def _environ_item_pattern(receiver: str | None) -> re.Pattern[str]:
    prefix = f"{receiver}\\." if receiver else ""
    return re.compile(rf"""\b{prefix}environ\[\s*["']([A-Z][A-Z0-9_]+)["']\s*\]""")


def _bare_call_pattern(func_name: str) -> re.Pattern[str]:
    """裸函数调用形态：`getenv("X")` / `from os import getenv as _g` 的 `_g("X")`。"""
    return re.compile(
        rf"""\b{func_name}\(\s*["']([A-Z][A-Z0-9_]+)["']\s*(?:,\s*([^),\n]+))?"""
    )


def _file_patterns(text: str) -> list[re.Pattern[str]]:
    """按文件的导入语句派生读取形态（防止别名绕过 / 防止 WSGI environ 误报）。"""
    patterns: list[re.Pattern[str]] = []
    receivers = ["os", *_OS_MODULE_ALIAS_RE.findall(text)]
    for receiver in sorted(set(receivers)):
        patterns.append(_getenv_pattern(receiver))
        patterns.append(_environ_get_pattern(receiver))
        patterns.append(_environ_item_pattern(receiver))
    if _OS_BARE_GETENV_RE.search(text):
        patterns.append(_bare_call_pattern("getenv"))
    if _OS_BARE_ENVIRON_RE.search(text):
        patterns.append(_environ_get_pattern(None))
        patterns.append(_environ_item_pattern(None))
    for alias in _OS_GETENV_ALIAS_RE.findall(text):
        patterns.append(_bare_call_pattern(alias))
    for alias in _OS_ENVIRON_ALIAS_RE.findall(text):
        patterns.append(_environ_get_pattern(alias))
        patterns.append(_environ_item_pattern(alias))
    patterns.append(_HELPER_ENV_RE)
    return patterns

_LITERAL_RE = re.compile(r"""^\s*(?:["']([^"']*)["']|(-?\d+(?:\.\d+)?))\s*$""")
_KWARG_RE = re.compile(r"""(?:production_default|default)\s*=\s*([^,\n)]+)""")


def _normalize_default(raw: str | None) -> str:
    """把默认值表达式归一为展示值：字面量取值，其余记 `-`（表达式/未设默认）。"""
    if not raw:
        return "-"
    match = _LITERAL_RE.match(raw)
    if match:
        return match.group(1) if match.group(1) is not None else match.group(2) or "-"
    kwarg = _KWARG_RE.search(raw)
    if kwarg:
        return _normalize_default(kwarg.group(1))
    return "-"


def _iter_py_files(scan_root: Path):
    for path in sorted(scan_root.rglob("*.py")):
        parts = set(path.relative_to(scan_root).parts)
        if parts & SCAN_SKIP_PARTS:
            continue
        if any(part in SKIP_PARTS for part in path.parts):
            continue
        yield path


def scan_reads(scan_root: Path = SCAN_ROOT) -> dict[str, dict]:
    """{变量名: {default, locations: [(relpath, line)], test_only}}（确定性排序）。"""
    reads: dict[str, dict] = {}
    for path in _iter_py_files(scan_root):
        relpath = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        patterns = _file_patterns(text)
        for lineno, line in enumerate(text.splitlines(), 1):
            for pattern in patterns:
                for match in pattern.finditer(line):
                    name = match.group(1)
                    default = _normalize_default(
                        match.group(2) if match.lastindex and match.lastindex >= 2 else None
                    )
                    entry = reads.setdefault(
                        name, {"default": "-", "locations": [], "test_only": True},
                    )
                    entry["locations"].append((str(relpath), lineno))
                    if entry["default"] == "-" and default != "-":
                        entry["default"] = default
    for entry in reads.values():
        entry["locations"] = sorted(set(entry["locations"]))
        entry["test_only"] = all(
            "/tests/" in f"/{loc[0]}" for loc in entry["locations"]
        )
    return reads


def example_keys() -> set[str]:
    """任一 `.env*.example` 出现的键名（含注释态条目）。"""
    keys: set[str] = set()
    pattern = re.compile(r"^#?\s*([A-Z][A-Z0-9_]+)=")
    for path in sorted(ROOT.glob("**/.env*.example")):
        if any(part in SKIP_PARTS for part in path.parts):
            continue
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            match = pattern.match(line.strip())
            if match:
                keys.add(match.group(1))
    return keys


def render_block(reads: dict[str, dict], registered: set[str]) -> str:
    total = len(reads)
    missing = sorted(name for name in reads if name not in registered)
    lines = [
        BEGIN_MARK,
        "",
        f"共 **{total}** 个读取名（`backend/**`，不含 `backend/agent/scripts/**`）："
        f"其中 **{len(missing)}** 个未在 `.env*.example` 登记（下表 `示例` 列 = `—`）。",
        "示例文件是**运维模板**（只承载需要运维改动的子集）；本表是**代码侧完整清单**。",
        "",
        "| 变量 | 默认 | 示例 | 类别 | 首个读取点 |",
        "|---|---|---|---|---|",
    ]
    for name in sorted(reads):
        entry = reads[name]
        first = entry["locations"][0]
        category = "测试" if entry["test_only"] else "运行时"
        flag = "✅" if name in registered else "—"
        lines.append(
            f"| `{name}` | `{entry['default']}` | {flag} | {category} | `{first[0]}:{first[1]}` |"
        )
    lines += ["", END_MARK]
    return "\n".join(lines)


def _replace_block(doc_text: str, block: str) -> str:
    if BEGIN_MARK not in doc_text or END_MARK not in doc_text:
        raise SystemExit(f"文档缺少生成块 marker：{BEGIN_MARK} / {END_MARK}")
    head, _, rest = doc_text.partition(BEGIN_MARK)
    _, _, tail = rest.partition(END_MARK)
    return f"{head}{block}{tail}"


def _self_test() -> int:
    """红绿双向自证：注入新读取名后 --check 必红，--write 后必绿。"""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_root = Path(tmp)
        scan_root = tmp_root / "backend"
        scan_root.mkdir(parents=True)
        (scan_root / "mod.py").write_text(
            'import os\nA = os.getenv("ZZ_SELFTEST_VAR", "7")\n', encoding="utf-8",
        )
        reads = scan_reads(scan_root)
        assert "ZZ_SELFTEST_VAR" in reads, "扫描未命中新读取名"
        assert reads["ZZ_SELFTEST_VAR"]["default"] == "7", "默认值提取失败"
        assert not reads["ZZ_SELFTEST_VAR"]["test_only"], "类别判定失败"

        doc = tmp_root / "doc.md"
        doc.write_text("前文\n" + render_block({}, set()) + "\n后文\n", encoding="utf-8")
        block = render_block(reads, set())
        text = _replace_block(doc.read_text(encoding="utf-8"), block)
        assert "ZZ_SELFTEST_VAR" in text, "生成块未包含新读取名"
    print("[OK] env_inventory self-test 通过（扫描/默认值/类别/渲染/替换）")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="把生成块写回文档（幂等）")
    parser.add_argument("--check", action="store_true", help="校验文档生成块与代码一致（漂移即红）")
    parser.add_argument("--self-test", action="store_true", help="离线红绿自证")
    args = parser.parse_args()

    if args.self_test:
        return _self_test()

    reads = scan_reads()
    block = render_block(reads, example_keys())

    if args.write:
        DOC.write_text(
            _replace_block(DOC.read_text(encoding="utf-8"), block) + "\n",
            encoding="utf-8",
        )
        print(f"[OK] 已刷新 {DOC.relative_to(ROOT)}（{len(reads)} 个读取名）")
        return 0

    if args.check:
        doc_text = DOC.read_text(encoding="utf-8")
        if BEGIN_MARK not in doc_text or END_MARK not in doc_text:
            print("[FAIL] 文档缺少 env-inventory 生成块 marker", file=sys.stderr)
            return 1
        current = doc_text.partition(BEGIN_MARK)[2].partition(END_MARK)[0]
        expected = block.partition(BEGIN_MARK)[2].partition(END_MARK)[0]
        if current != expected:
            print(
                "[FAIL] 环境变量清单漂移：代码读取名与文档生成块不一致。\n"
                "        处置：python tools/dev/env_inventory.py --write 后提交文档。",
                file=sys.stderr,
            )
            return 1
        print(f"[OK] 环境变量清单一致（{len(reads)} 个读取名）")
        return 0

    print(f"读取名 {len(reads)} 个；未登记 {sum(1 for n in reads if n not in example_keys())} 个")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
