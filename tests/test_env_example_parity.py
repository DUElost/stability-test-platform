"""环境变量示例对齐门禁（#737）：示例里的每个键都要有真实读取点。

背景：`#737` 复盘发现 9 个「幽灵配置」——`.env.example` 里列出、但代码从未读取
的名字（旧名/已删特性），会误导运维以为调整它们能改变行为。清理一次不够，
本测试把它固化成机械门禁：**示例文件里未注释的键，必须在仓库中出现过**
（字符串字面量、`import.meta.env.X`、compose/systemd 模板等任意形态）。

判据说明：

- 语料 = 仓库文本文件（排除 `.git`/`.wt`/`node_modules`/`__pycache__` + **示例文件自身**），
  在语料里做 token 出现性判断——不限定 `os.getenv("X")` 形态，因为消费方可能是
  前端（`import.meta.env.X`）、compose 插值（`${X}`）、systemd unit 或 postgres 镜像；
- 只看**未注释**键（`^[A-Z][A-Z0-9_]*=`）：注释行是「文档化但当前不启用」的
  形态（如 `# AGENT_LOCK_RENEWAL_INTERVAL=60`），不参与本门禁；
- 失败信息要求二选一：给键补真实读取点，或从示例删除——不允许静默挂着。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

_EXAMPLE_GLOBS = ("**/.env*.example",)

_KEY_RE = re.compile(r"^([A-Z][A-Z0-9_]*)=(.*)$")
_TOKEN_RE = re.compile(r"[A-Z][A-Z0-9_]{2,}")

_SKIP_DIRS = {".git", ".wt", "node_modules", "__pycache__", ".venv", "venv", "dist", "build"}
_SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".woff", ".woff2", ".pdf", ".zip", ".gz", ".db", ".sqlite", ".pyc"}


def _example_files() -> list[Path]:
    files: set[Path] = set()
    for pattern in _EXAMPLE_GLOBS:
        for path in ROOT.glob(pattern):
            if path.is_file() and not any(part in _SKIP_DIRS for part in path.parts):
                files.add(path)
    return sorted(files)


def _example_keys(path: Path) -> list[str]:
    keys: list[str] = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        match = _KEY_RE.match(line.strip())
        if match:
            keys.append(match.group(1))
    return keys


def _repo_tokens(exclude: set[Path]) -> set[str]:
    tokens: set[str] = set()
    for path in ROOT.rglob("*"):
        if not path.is_file() or path in exclude:
            continue
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        if path.suffix.lower() in _SKIP_SUFFIXES:
            continue
        try:
            if path.stat().st_size > 2_000_000:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        tokens.update(_TOKEN_RE.findall(text))
    return tokens


def test_env_example_keys_have_a_reader():
    examples = _example_files()
    assert examples, "未找到任何 .env*.example —— 门禁语料失效，检查本测试的 glob"
    corpus = _repo_tokens(exclude=set(examples))

    ghosts: list[str] = []
    for path in examples:
        for key in _example_keys(path):
            if key not in corpus:
                ghosts.append(f"{path.relative_to(ROOT)}::{key}")

    assert not ghosts, (
        "以下示例键在仓库中没有任何出现（幽灵配置）：\n  "
        + "\n  ".join(sorted(ghosts))
        + "\n处置二选一：为键补真实读取点，或从示例删除（#737 门禁）。"
    )
