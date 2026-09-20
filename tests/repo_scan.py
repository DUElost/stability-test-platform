"""守卫扫描面的单一来源：**仓库跟踪内容**，不是文件系统（#2870）。

**要解决的问题**：多条仓库级守卫用 `REPO_ROOT.rglob("*.py")` 扫全仓，于是把两类
**与仓库内容无关的东西**读进判定集：

1. `.wt/*` —— 并行 Execution 的专属 worktree（`.gitignore` 有 `.wt/`），里面是**整仓副本**
   （本机 10 个 worktree = 14,645 个 `.py`）。守卫因此在本机恒红、在 CI（干净检出）恒绿
   ⇒ **真回归与噪声不可分**，比没有守卫更糟；
2. 本地未跟踪的 `.env` / 构建产物 / `node_modules` —— 同理，`backend/.env` 里的陈旧键
   会把「已移除键不得被当成生效开关」这条守卫在本机点红。

**正解不是再加一条 `startswith(".wt/")`**：那是把「文件系统 ≠ 仓库内容」这件事
散在每个守卫里各自记一遍，下一个新目录（`.codex/`、下一 kind of worktree、vendored 树）
会再犯一次。本模块把口径收成一处，语义与既有门禁 `tools/dev/check-internal-ip-leak.py`
的 `default_targets()` **完全一致**（已跟踪 ∪ 未跟踪且未被 .gitignore 命中）：

- **含未跟踪新文件**：#2402 的教训——只取 `git ls-files` 会让「`git add` 之前」这个
  最该拦的时点完全不在门禁眼里（本地连绿、push 上去 CI 才红）；
- **不绕过 .gitignore**（`--exclude-standard`）：`.wt/`、`.venv`、`node_modules` 一并出局，
  而 `.env.example` 这类**已跟踪**示例仍在集内（tracked 优先于 ignore）。

git 不可用/失败时**抛错而不是返回空集**——静默零命中正是 #2639 数过的那类失效
（守卫还在跑、还是绿的，只是不再覆盖任何东西）。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def tracked_and_new_files(root: Path = REPO_ROOT) -> list[str]:
    """`{root}` 内**属于仓库内容**的文件：已跟踪 ∪ 未跟踪（不含 .gitignore 命中项）。

    返回相对 posix 路径（升序）。语义与 `tools/dev/check-internal-ip-leak.py` 的
    `default_targets()` 同构——两条门禁必须看同一份「仓库内容」定义，否则又会各漂各的。
    """
    argv = ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"]
    proc = subprocess.run(argv, cwd=root, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(
            "git ls-files 失败 ⇒ 扫描面不可得。拒绝降级为空集："
            "空集会让所有基于它的守卫「全绿」，而那与「全仓无违规」看起来一模一样（#2639）。"
            f" stderr={proc.stderr.strip()[:200]}"
        )
    return sorted(p for p in proc.stdout.split("\0") if p.strip())


def repo_files_with_suffixes(
    suffixes: tuple[str, ...],
    *,
    root: Path = REPO_ROOT,
    include_dotenv_names: bool = False,
) -> list[str]:
    """同上的相对路径集，按后缀过滤（`include_dotenv_names` 保留 `.env*` 形态的仓库示例文件）。"""
    bad = [s for s in suffixes if not (s.startswith(".") and "/" not in s)]
    if bad:
        raise AssertionError(
            f"suffixes 必须是扩展名形态（'.py'），收到 {bad}——写错会让过滤静默失配，"
            "扫描集悄悄变成空集（#2870 要防的正是这种「看着在扫、其实没扫」）"
        )
    out: list[str] = []
    for rel in tracked_and_new_files(root):
        name = Path(rel).name
        if any(rel.endswith(s) for s in suffixes):
            out.append(rel)
            continue
        if include_dotenv_names and (name == ".env" or name.startswith(".env.")):
            out.append(rel)
    return out


def iter_scanned(
    suffixes: tuple[str, ...],
    *,
    root: Path = REPO_ROOT,
    exclude_prefixes: tuple[str, ...] = (),
    include_dotenv_names: bool = False,
) -> list[Path]:
    """给出 `Path` 列表（相对 `root` 解析），并套用调用方自己的前缀排除。

    排除面留在调用方：每条守卫的「哪些目录属测试面/历史面」语义不同，
    收成一处的是**扫描集**，不是各守卫的判据。
    """
    paths: list[Path] = []
    for rel in repo_files_with_suffixes(
        suffixes, root=root, include_dotenv_names=include_dotenv_names
    ):
        if rel.startswith(exclude_prefixes):
            continue
        paths.append(root / rel)
    return paths
