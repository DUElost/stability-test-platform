#!/usr/bin/env python3
"""ai_work.py — Execution Registry CLI（ADR-0034 P1）。

权威规范：docs/development/ai/execution-contract.md（Living v1.10，唯一权威源）。
本文件是其 §2（Registry 协议）/§3（状态模型）/§5（scope 与 overlap）的 MVP 实现：
选择权原则（ADR §2.1）——本工具是执行侧自声明的 visibility-only 登记簿，不是调度器。

命令：
    ai_work.py declare --requirement R --harness H --worktree W --scope P [--scope ...]
                       [--issue N ...] [--force] [--role ROLE]
                       [--test-impact none|direct|indirect]
    ai_work.py status [--id ID]                 # 严格只读（不刷任何 last_seen）
    ai_work.py update --id ID [--scope P ...] [--pr N]   # 刷 last_seen + GitHub reconcile
    ai_work.py finish --id ID [--pr N | --abandon]
    ai_work.py resume --id ID                   # T9：FINISHED→CODING 返工回退（#946）
    ai_work.py --self-test                      # 纯函数红绿自证（离线；含临时 git 仓库 fixture）

设计约束（契约即约束）：
- Registry root = $(git rev-parse --path-format=absolute --git-common-dir)/ai-work/，
  registry.yaml + registry.lock 同目录，位于 .git 内天然不被跟踪；
- 写入九步全序（flock → read → validate → modify → tmp → fsync → rename → 父目录 fsync → unlock）；
- liveness 查询时派生不持久化；integration 由 GitHub（gh）派生刷新，不可用时保持旧值+observed_at；
- overlap 真值表：开放 PR 恒在风险窗口；effective scope = declared ∪ derived(diff)；
- **缓存失效（契约 §3.3 v1.10，#1232）**：integration_cache 只由 update 刷新、可以陈旧。
  Git 能证明本记录分支已含于 origin/main（trunk containment）时，该缓存值对 risk 判定失效——
  `risk = §3.2 真值表 ∧ ¬landed`。**不写 integration 字段**（MERGED 仍只能由 T6/GitHub 写入）；
- **derived 归属（契约 §5.2 v1.10）**：worktree 的 HEAD 必须等于记录 branch，且不得被多条
  在窗记录共享，否则退回 branch diff 档——避免把别人的工作记到本记录名下。

自包含：不依赖 PyYAML（registry.yaml 使用本工具自写的受限 YAML 子集：仅扁平
mapping + 标量/字符串列表，解析器对任何超集语法 fail-fast——同时充当九步协议
中 validate 步骤的一部分）。
"""
from __future__ import annotations

import argparse
import fcntl
import os
import re
import subprocess
import sys
import tempfile
import time

TTL_SECONDS = 24 * 3600  # §4：24h 量级，仅 advisory
RECORD_FIELDS = {
    "requirement", "harness", "role", "worktree", "branch", "scope", "issues",
    "pr_number", "lifecycle", "test_impact", "last_seen", "created_at",
    "updated_at", "integration_cache", "observed_at",
}
LIST_FIELDS = ("scope", "issues")  # codec 列表形态字段（受限 YAML 子集）
LIFECYCLE = ("CODING", "FINISHED", "ABANDONED")
TEST_IMPACT = ("none", "direct", "indirect")
TRUNK_REFS = ("main", "master")  # §3.3 v1.10：主干自身不参与 trunk containment（trivially 祖先）
ADR_FILE_RE = re.compile(r"^docs/adr/ADR-\d{3,4}(?:[-.]|$)")  # §3.5 v1.10：决策类判据
MERGE_PR_RE = re.compile(r"^Merge pull request #(\d+) from ")  # §3.3 v1.10：GitHub merge 主题


# ── 受限 YAML 子集 codec（自写自读 schema；超集语法 fail-fast）──

def yaml_dump(data: dict) -> str:
    lines = ["# ai-work registry (managed by tools/dev/ai_work.py; do not edit by hand)"]
    for key in sorted(data):
        rec = data[key]
        lines.append(f"{_quote(key)}:")  # key 与值同规则引用——#880：'#'/'::' 开头的 id 不得裸写
        for field in ("requirement", "harness", "role", "worktree", "branch",
                      "lifecycle", "test_impact", "pr_number", "integration_cache",
                      "last_seen", "created_at", "updated_at", "observed_at"):
            if field in rec and rec[field] is not None:
                lines.append(f"  {field}: {_quote(rec[field])}")
        scope = rec.get("scope") or []
        lines.append("  scope:")
        if scope:
            lines.extend(f"    - {_quote(s)}" for s in scope)
        else:
            lines.append("    []")
        if "issues" in rec:  # v1.2（#978）：仅在有声明时落盘，legacy 记录输出不变
            issues = rec.get("issues") or []
            lines.append("  issues:")
            if issues:
                lines.extend(f"    - {_quote(str(i))}" for i in issues)
            else:
                lines.append("    []")
    return "\n".join(lines) + "\n"


def _quote(v) -> str:
    s = str(v)
    if s == "":
        return '""'
    if not s.startswith("#") and re.fullmatch(r"[A-Za-z0-9_./+=:@-]+", s):
        return s  # 含 # 一律引号——行首裸 # 会被当注释（#880）
    # 受限转义与 _unquote 对称；\n/\r 必转义——codec 行结构以物理换行为界，
    # 值内裸换行会把一条记录拆成多行、读回即锁死（#1059）
    return ('"' + s.replace("\\", "\\\\").replace('"', '\\"')
            .replace("\n", "\\n").replace("\r", "\\r") + '"')


_UNESCAPES = {"\\": "\\", '"': '"', "n": "\n", "r": "\r"}


def _unquote(s: str) -> str:
    s = s.strip()
    if not s.startswith('"'):
        return s
    # 受限转义：\\ \" \n \r（与 _quote 对称）
    out, i = [], 1
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s) and s[i + 1] in _UNESCAPES:
            out.append(_UNESCAPES[s[i + 1]])
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out).removesuffix('"')


def yaml_load(text: str) -> dict:
    data: dict = {}
    current = None
    field = None
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        m = re.match(r"^  ([a-z_]+):$", line)
        if m:
            if current is None:
                raise ValueError(f"registry.yaml:{lineno}: 列表字段出现在记录外")
            field = m.group(1)
            if field not in LIST_FIELDS:
                raise ValueError(f"registry.yaml:{lineno}: 字段 {field!r} 不支持列表形态")
            data[current][field] = []
            continue
        if line.startswith("    - "):
            if field not in LIST_FIELDS or current is None:
                raise ValueError(
                    f"registry.yaml:{lineno}: 列表项只允许出现在 {sorted(LIST_FIELDS)} 内")
            data[current][field].append(_unquote(line[6:]))
            continue
        if line.startswith("    []"):
            field = None
            continue
        m = re.match(r'^(?:"((?:[^"\\]|\\.)*)"|(\S[^:]*)):$', line)
        if m:
            current = _unquote(m.group(1)) if m.group(1) is not None else m.group(2)
            # codec 层只管正确往返（含引号 '#'/'::' key 的防御深度）；'#' 开头与
            # 含 ':' 的 id 由 declare 语义层拒绝（normalize_requirement_id）
            if current in data:
                raise ValueError(f"registry.yaml:{lineno}: 记录重复 {current!r}")
            data[current] = {}
            field = None
            continue
        m = re.match(r"^  ([a-z_]+): (.*)$", line)
        if m and current is not None:
            field = m.group(1)
            if field not in RECORD_FIELDS:
                raise ValueError(f"registry.yaml:{lineno}: 未知字段 {field!r}")
            data[current][field] = _unquote(m.group(2))
            continue
        raise ValueError(f"registry.yaml:{lineno}: 非法语法 {line!r}")
    return data


# ── scope：归一化、拒绝规则、组件边界 overlap 谓词（§5.3/§5.4）──

def normalize_scope(path: str, repo_root: str | None = None) -> str:
    """归一化并执行拒绝规则；返回归一化路径。非法即 ValueError。"""
    p = path.strip()
    if not p:
        raise ValueError("scope 不能为空")
    if p.startswith("/") or p.startswith("\\") or os.path.isabs(p):
        raise ValueError(f"scope 必须是 repo-relative 路径（拒绝绝对路径）: {path!r}")
    p = p.rstrip("/")
    parts = [seg for seg in p.split("/") if seg not in ("", ".")]
    if ".." in parts:
        raise ValueError(f"scope 拒绝含 '..' 的路径: {path!r}")
    if not parts:
        raise ValueError(f"scope 归一化后为空: {path!r}")
    norm = "/".join(parts)
    # symlink 逃逸：路径已存在时验证 realpath 不出仓库
    if repo_root and os.path.exists(os.path.join(repo_root, norm)):
        real = os.path.realpath(os.path.join(repo_root, norm))
        if not real.startswith(os.path.realpath(repo_root) + os.sep):
            raise ValueError(f"scope 经 symlink 逃逸出仓库: {path!r}")
    return norm


def scope_overlap(a: str, b: str) -> bool:
    """组件边界前缀谓词：backend 与 backend_new 不重叠（§5.4）。"""
    pa, pb = a.split("/"), b.split("/")
    return pa == pb[: len(pa)] or pb == pa[: len(pb)]


def declaration_drift(declared: set[str], derived: set[str]) -> tuple[list[str], list[str]]:
    """§5.4 组件边界语义的 declaration-drift 对（#928）。

    声明粒度与 diff 粒度解耦：目录声明覆盖其组件边界内的全部子路径
    （scope_overlap 判定），只有真正未被任何声明覆盖的 diff、与没有任何
    diff 落地的声明才构成 drift。文件级声明对文件级 diff 行为不变
    （精确匹配是组件边界的特例）。"""
    unlanded = sorted(
        s for s in declared if not any(scope_overlap(d, s) for d in derived)
    )
    undeclared = sorted(
        d for d in derived if not any(scope_overlap(d, s) for s in declared)
    )
    return unlanded, undeclared


# ── 状态模型：真值表与派生（§3）──

def in_risk(lifecycle: str, integration: str) -> bool:
    """overlap 真值表：开放 PR 恒在窗口；CLOSED 不单独出局；MERGED 出局。"""
    if integration in ("PR_OPEN", "READY"):
        return True
    if integration == "NO_PR":
        return lifecycle in ("CODING", "FINISHED")
    if integration == "CLOSED":
        return lifecycle != "ABANDONED"
    return False  # MERGED


def derive_liveness(last_seen: float | None, now: float) -> str:
    if last_seen is None:
        return "UNKNOWN"
    return "LIVE" if now - last_seen < TTL_SECONDS else "STALE"


def can_resume(lifecycle: str, integration: str) -> tuple[bool, str]:
    """T9 守卫（#946，§3.3）：仅 FINISHED 且未 MERGED 可恢复编码；拒绝须给理由。"""
    if lifecycle == "CODING":
        return False, "已在 CODING——无需 resume"
    if lifecycle == "ABANDONED":
        return False, "ABANDONED 仅显式人工动作——恢复 = 新 Execution 重新 declare"
    if integration == "MERGED":
        return False, "PR 已 MERGED，风险窗口真实关闭——返工/新工作走 T1 重新 declare"
    return True, ""


def closed_unmerged_candidate(lifecycle: str, integration: str, liveness: str,
                              derived: set) -> bool:
    """僵尸候选第二类（#906，契约 §3.2）：PR CLOSED 未合且未放弃、失联、diff 为空。

    与既有 [zombie-candidate]（无 PR 分支）同义，但触发条件不同：CLOSED 是终态
    integration，按 §3.2 第三行**仍在风险窗口**（PR 被关 ≠ 工作停止），因此仍占
    issue 槽位与 scope——而唯一合法出口 `finish --abandon` 只有人工知道要调。
    本判据把这类记录显式列出，避免「已关闭 PR 的记录静默占用 issue 直到有人想起」。
    """
    return (integration == "CLOSED" and lifecycle in ("CODING", "FINISHED")
            and liveness == "STALE" and not derived)


def zombie_candidate(lifecycle: str, integration: str, liveness: str, derived: set) -> bool:
    """僵尸候选第一类（契约 §3.2 v1.10 对齐实现，#1232）：**无 PR** + 未放弃 + 失联 + diff 为空。

    契约原文写「effective scope 为空」，而 §5.1 并集语义下 declared 恒非空（declare --scope
    必填）⇒ 旧判据不可达；#962 起实现改用「derived 为空」，本版把契约文案与之对齐并显式
    限定 `integration == NO_PR`：

    - 有开放 PR 的记录**不是**僵尸——它的工作在 PR 里（derived 为空只是 worktree 已移除）；
      对它提示 `finish --abandon` 会与 T4「开放 PR 的 abandon 必须警告并留窗」自相矛盾；
    - CLOSED 分支由 `closed_unmerged_candidate` 覆盖（判据不同、提示不同）。
    """
    return (integration == "NO_PR" and lifecycle in ("CODING", "FINISHED")
            and liveness == "STALE" and not derived)


# ── Registry 定位与九步原子写（§2.1/§2.2）──

def registry_paths(cwd: str | None = None) -> tuple[str, str]:
    out = subprocess.run(
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
        capture_output=True, text=True, check=True, cwd=cwd,
    ).stdout.strip()
    root = os.path.join(out, "ai-work")
    return os.path.join(root, "registry.yaml"), os.path.join(root, "registry.lock")


def read_registry(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    try:
        return yaml_load(text)
    except ValueError:
        # 契约 §2.2 损坏恢复：隔离留证 + 人类可读报错（#880：任何一次损坏都
        # 不能只剩裸 traceback——registry 可重 declare，静默清空会伪造「无人工作」）
        stamp = time.strftime("%Y%m%d-%H%M%S")
        corrupt = f"{path}.corrupt-{stamp}"
        try:
            os.replace(path, corrupt)
        except OSError:
            corrupt = "(隔离失败，原文件未动)"
        raise ValueError(
            f"registry.yaml 无法解析——已隔离至 {corrupt} 留证。\n"
            f"恢复：修复/删除隔离文件后重新 declare（Registry 是声明面，可重建；"
            f"不要静默清空——那会伪造「无人在工作」）。原始内容见隔离文件。"
        ) from None


def validate(records: dict) -> None:
    for key, rec in records.items():
        missing = {"requirement", "harness", "worktree", "lifecycle"} - set(rec)
        if missing:
            raise ValueError(f"记录 {key!r} 缺必填字段: {sorted(missing)}")
        if rec["lifecycle"] not in LIFECYCLE:
            raise ValueError(f"记录 {key!r} lifecycle 非法: {rec['lifecycle']!r}")
        if "test_impact" in rec and rec["test_impact"] not in TEST_IMPACT:
            raise ValueError(f"记录 {key!r} test_impact 非法: {rec['test_impact']!r}")
        if "issues" in rec:
            issues = rec.get("issues") or []
            if not isinstance(issues, list) or not all(str(x).isdigit() for x in issues):
                raise ValueError(f"记录 {key!r} issues 必须为正整数字符串列表: {issues!r}")


def atomic_write(path: str, lock_path: str, records: dict) -> None:
    """九步全序：flock → read → validate → modify(由调用方完成) → tmp → fsync →
    rename → 父目录 fsync → unlock。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    validate(records)
    with open(lock_path, "w") as lock_fh:
        fcntl.flock(lock_fh, fcntl.LOCK_EX)
        try:
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(yaml_dump(records))
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
            dir_fd = os.open(os.path.dirname(path), os.O_DIRECTORY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        finally:
            fcntl.flock(lock_fh, fcntl.LOCK_UN)


def load_locked(path: str, lock_path: str):
    """读+锁上下文：返回 (records, writer)。modify 后调 writer(records) 走九步。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lock_fh = open(lock_path, "w")
    fcntl.flock(lock_fh, fcntl.LOCK_EX)
    # 契约 §2.2：持锁后清理无主残留 tmp（前次崩溃遗留；mtime 早于本次持锁即清）
    tmp = path + ".tmp"
    if os.path.exists(tmp):
        try:
            os.remove(tmp)
        except OSError:
            pass

    class _Ctx:
        def __init__(self):
            self.records = read_registry(path)

        def commit(self) -> None:
            validate(self.records)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(yaml_dump(self.records))
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
            dir_fd = os.open(os.path.dirname(path), os.O_DIRECTORY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)

        def close(self) -> None:
            fcntl.flock(lock_fh, fcntl.LOCK_UN)
            lock_fh.close()

    return _Ctx()


# ── derived(diff) 三分档（§5.2）──

def _git(args: list[str], cwd: str) -> list[str]:
    try:
        proc = subprocess.run(["git", *args], capture_output=True, text=True, cwd=cwd)
    except OSError:
        return []
    if proc.returncode != 0:
        return []
    return [ln for ln in proc.stdout.splitlines() if ln.strip()]


def derived_paths(rec: dict, repo_root: str, shared: frozenset | set = frozenset()) -> list[str]:
    """derived(diff) 三分档（§5.2）+ 归属校验（v1.10，#1232）。

    档 1（worktree 在场）额外要求两条归属条件，否则退回档 2：
    - worktree 的 HEAD 提交必须等于记录 `branch`（相等 = 同一提交）；不等说明该 worktree
      当前 checkout 的是别的分支，其 diff 不属于本记录；
    - worktree 未被多条在窗记录共享（`shared`）：主检出被多个 Execution 共用时，它的
      diff 无法归属任何单条记录——宁可返回空（effective = declared），也不记错账。
    """
    wt = rec.get("worktree")
    if wt and os.path.isdir(wt) and os.path.realpath(wt) not in set(shared or ()):
        branch = rec.get("branch")
        head = (_git(["rev-parse", "HEAD"], wt) or [""])[0]
        branch_head = (_git(["rev-parse", branch], repo_root) or [""])[0] if branch else ""
        if not branch or (head and head == branch_head):
            merge_base = _git(["merge-base", "origin/main", "HEAD"], wt)
            paths: list[str] = []
            if merge_base:
                paths += _git(["diff", "--name-only", merge_base[0], "HEAD"], wt)
                paths += _git(["diff", "--name-only", merge_base[0]], wt)  # 未提交（tracked）
            paths += _git(["ls-files", "--others", "--exclude-standard"], wt)  # untracked
            return sorted({p for p in paths if p})
    branch = rec.get("branch")
    if branch:
        merge_base = _git(["merge-base", "origin/main", branch], repo_root)
        if merge_base:
            return sorted(set(_git(["diff", "--name-only", merge_base[0], branch], repo_root)))
    return []


# ── 缓存失效：trunk containment（§3.3 v1.10，#1232）──

def _git_rc(args: list[str], cwd: str) -> int:
    """返回 git 退出码；环境不可用（cwd 不存在/git 缺失）一律视为失败。"""
    try:
        return subprocess.run(["git", *args], capture_output=True, cwd=cwd).returncode
    except OSError:
        return 1


def landed_in_trunk(rec: dict, repo_root: str, merged: set | None = None) -> bool:
    """Git 证明「本记录分支已含于 origin/main」⇒ integration 缓存对 risk 判定失效。

    仅对**已登记 PR 且缓存称开放**（`PR_OPEN/READY`）的记录判定——无 PR 的记录不存在
    「已合入」语义，而缓存已为终态的记录本就不在窗口。命中即等价 MERGED 的风险理由
    （§3.2「变更已进主干，风险真实关闭」），但**不写 integration 字段**：MERGED 的写入
    路径仍唯一为 T6/GitHub（§3.3 事实来源分层不变）。

    两条本地证据通道（任一命中即 landed，均零网络）：
    1. **ancestry**：`branch` 或 `origin/<branch>` 是 `origin/main` 的祖先（要求 branch
       非空且非主干自身——主干 trivially 是自身祖先，不构成证据）；
    2. **merge 主题**：`origin/main` 的 GitHub merge commit 主题含 `#<pr_number>`
       ——与 branch 无关，覆盖「分支 ref 已被删除 / 提交被改写 / 记录 branch 就是 main」
       的形态（#1232 实测：`docs-adr0035-merge` 本地分支 2 ahead/121 behind，
       `adr-0036-*` 记录 branch=main，两者仅 ancestry 都判不出已合入）。

    fail-safe（任一不成立即返回 False，记录留在窗口）：
    - 无 pr_number / 缓存非开放态；
    - 两条通道都取不到证据（ref 缺失且 merge 主题无该 PR 号）；
    - `origin/main` 落后于实际主干 → 判不出，只会漏判（随后由 `update` 核销），不会误判。
    """
    if not rec.get("pr_number") or (rec.get("integration_cache") or "NO_PR") not in ("PR_OPEN", "READY"):
        return False
    branch = rec.get("branch") or ""
    if branch and branch not in TRUNK_REFS:
        for ref in (branch, f"origin/{branch}"):
            if _git_rc(["rev-parse", "--verify", "--quiet", ref], repo_root) != 0:
                continue
            if _git_rc(["merge-base", "--is-ancestor", ref, "origin/main"], repo_root) == 0:
                return True
    if merged is None:
        merged = merged_pr_numbers(repo_root)
    return str(rec["pr_number"]) in merged


def merged_pr_numbers(repo_root: str) -> set:
    """`origin/main` 上 GitHub merge commit 主题里的 PR 号集合（本地证据，零网络）。

    GitHub 的 merge commit 主题固定为 `Merge pull request #N from <owner>/<branch>`；
    只认这一形态（不做 `(#N)` 泛匹配，避免把普通提交里的 issue 引用误判为已合入）。
    """
    return {m.group(1) for line in _git(["log", "--merges", "--format=%s", "origin/main"], repo_root)
            if (m := MERGE_PR_RE.match(line))}


def landed_ids(records: dict, repo_root: str) -> set:
    """对一批记录预计算 landed 集合（各命令调用一次；merge 主题集合只取一次）。"""
    merged = merged_pr_numbers(repo_root)
    return {rid for rid, rec in records.items() if landed_in_trunk(rec, repo_root, merged)}


def effective_risk(rec: dict, repo_root: str) -> bool:
    """契约 §3.2/§3.3 v1.10：`risk = 真值表 ∧ ¬landed`（单记录便捷入口）。"""
    if not in_risk(rec.get("lifecycle", "CODING"), rec.get("integration_cache") or "NO_PR"):
        return False
    return not landed_in_trunk(rec, repo_root)


def risk_ids(records: dict, repo_root: str) -> set:
    """在窗集合（已剔除 landed 的陈旧缓存记录）；`landed_ids` 的同批入口。"""
    landed = landed_ids(records, repo_root)
    return {rid for rid, rec in records.items()
            if rid not in landed
            and in_risk(rec.get("lifecycle", "CODING"), rec.get("integration_cache") or "NO_PR")}


def shared_worktrees(records: dict, risk: set) -> set:
    """被 ≥2 条在窗记录共用的 worktree 绝对路径（§5.2 v1.10）——其 diff 不可归属单条记录。"""
    seen: dict = {}
    for rid in risk:
        wt = os.path.realpath(records[rid].get("worktree") or "")
        if wt:
            seen[wt] = seen.get(wt, 0) + 1
    return {wt for wt, n in seen.items() if n > 1}


def adr_claims(scopes) -> list[str]:
    """§3.5 v1.10 决策类判据：scope 显式声明了**具体 ADR 文件**（非 `docs/adr` 目录）。"""
    return sorted(s for s in scopes or [] if ADR_FILE_RE.match(s))


def adr_collisions(records: dict, risk: set) -> dict:
    """在窗记录之间的 ADR 文件占用图：{ADR 文件: [占用它的在窗记录]}（§3.5 可见性）。"""
    claims: dict = {}
    for rid in risk:
        for path in adr_claims(records[rid].get("scope", [])):
            claims.setdefault(path, []).append(rid)
    return {path: sorted(ids) for path, ids in claims.items() if len(ids) > 1}


# ── integration 派生（GitHub 权威，§3.3；不可用降级）──

def derive_integration(pr_number: str | None, cached: str | None, cwd: str) -> tuple[str, bool]:
    """返回 (integration, refreshed)。pr 未登记 → NO_PR；GitHub 不可用 → 旧值+False。"""
    if not pr_number:
        return "NO_PR", True
    try:
        proc = subprocess.run(
            ["gh", "pr", "view", pr_number, "--json", "state,statusCheckRollup"],
            capture_output=True, text=True, timeout=30, cwd=cwd,
        )
    except (subprocess.TimeoutExpired, OSError):
        return cached or "PR_OPEN", False
    if proc.returncode != 0:
        return cached or "PR_OPEN", False
    import json
    data = json.loads(proc.stdout)
    state = data.get("state")
    if state == "MERGED":
        return "MERGED", True
    if state == "CLOSED":
        return "CLOSED", True
    rollup = data.get("statusCheckRollup") or []
    conclusions = {c.get("conclusion") for c in rollup if c.get("status") == "COMPLETED"}
    ok = conclusions <= {"SUCCESS", "SKIPPED", "NEUTRAL"} and conclusions
    return ("READY" if ok else "PR_OPEN"), True


# ── 命令实现 ──

def _now() -> float:
    return time.time()


def normalize_requirement_id(rid: str) -> str:
    """requirement id 校验（#880 修复 1，语义层）：拒绝 '#' 开头与含 ':'/换行的 id。

    id 是 registry.yaml 的 record key（YAML 行首位）——'#' 开头会被任何 YAML
    解析当注释、': ' 与换行会破坏行结构（#1059：换行值写入成功读回即锁死）。
    codec 已对称转义（防御深度），本校验是语义层守门：id 保持简洁标识形态
    （本仓库自然形态=issue 语义短语或 slug）。"""
    rid = rid.strip()
    if not rid:
        raise ValueError("requirement id 不能为空")
    if rid.startswith("#"):
        raise ValueError(f"requirement id 不得以 '#' 开头（会被 YAML 当注释）: {rid!r}"
                         f"——引用 issue 号请写作 'issue-878' 式 slug 或 'fix #878 描述' 剥离前导 #")
    if ":" in rid:
        raise ValueError(f"requirement id 不得含 ':'（破坏 record 行结构）: {rid!r}")
    if "\n" in rid or "\r" in rid:
        raise ValueError(f"requirement id 不得含换行（破坏 record 行结构，#1059）: {rid!r}")
    return rid


# ── issue 号：slug 提取与在窗查重（#978，契约 §3.4）──

ISSUE_MARKER_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:issue|fix)(?:[-/_\s]|#)+(\d{1,6})(?![0-9])", re.IGNORECASE)


def extract_issue_numbers(*texts) -> set:
    """从 requirement/branch slug 兜底提取 issue 号（启发式；显式 --issue 为主）。

    标记 = issue|fix + 至少一个分隔符（-/ _ 空格 #）+ 1-6 位数字：
    'fix-978-x' / 'fix/900-x' / 'issue-878' / 'fix #878 x' 命中；
    日期串（'sync-2026-09-07'）、无标记数字、标记粘连（'myfix-123'）与
    ≥7 位数字不误报。"""
    nums: set = set()
    for t in texts:
        if not t:
            continue
        for m in ISSUE_MARKER_RE.finditer(str(t)):
            nums.add(int(m.group(1)))
    return nums


def record_issue_numbers(rec: dict) -> set:
    """单条记录的 issue 集：issues 字段（权威）∪ requirement/branch slug 提取。"""
    nums = {int(x) for x in (rec.get("issues") or []) if str(x).isdigit()}
    nums |= extract_issue_numbers(rec.get("requirement", ""), rec.get("branch", ""))
    return nums


def issue_conflicts(new_issues: set, records: dict, skip_id: str,
                    landed: set | None = None) -> list:
    """在窗 issue 撞车检测（纯函数）：返回 [(record_id, 命中 issue 号列表)]。

    `landed` = 已知「分支已含于 origin/main」的记录 id 集合（§3.3 v1.10 缓存失效）：
    这些记录的 integration 缓存称开放但变更已进主干，不再占用 issue 槽位（#1232——
    提交后 86% 的在窗记录属此类，会把已合入的 issue 号误判为在窗占用）。
    缺省 None = 不掌握缓存失效信息（纯 §3.4 语义，供离线自测使用）。
    """
    landed = landed or set()
    conflicts: list = []
    for rid, rec in records.items():
        if rid == skip_id or rid in landed:
            continue
        if not in_risk(rec.get("lifecycle", "CODING"),
                       rec.get("integration_cache") or "NO_PR"):
            continue
        hit = sorted(new_issues & record_issue_numbers(rec))
        if hit:
            conflicts.append((rid, hit))
    return conflicts


# ── role 缺省归一化（契约 §1.2 v1.6，ADR-0034 v1.7 Revisit ①）──

DEFAULT_ROLE = "implementation"


def default_role(role_arg: str | None) -> str:
    """declare 的 role 缺省归一化：缺省/空串一律写 DEFAULT_ROLE。

    契约「默认且唯一实际运行角色=implementation」落到存储层；历史记录的
    空串同义读取（不迁移）。显式 --role 透传（自由文本，无枚举校验）。"""
    return role_arg or DEFAULT_ROLE


def cmd_declare(args) -> int:
    try:
        args.requirement = normalize_requirement_id(args.requirement)
    except ValueError as exc:
        print(f"[REFUSED] {exc}", file=sys.stderr)
        return 2
    path, lock = registry_paths()
    repo_root = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True,
                               text=True, check=True).stdout.strip()
    scopes = []
    for s in args.scope:
        try:
            scopes.append(normalize_scope(s, repo_root))
        except ValueError as exc:
            print(f"[REFUSED] {exc}", file=sys.stderr)
            return 2
    rec_id = args.requirement
    ctx = load_locked(path, lock)
    try:
        existing = ctx.records.get(rec_id)
        if existing and in_risk(existing.get("lifecycle", "CODING"),
                                existing.get("integration_cache", "NO_PR")):
            stale = landed_in_trunk(existing, repo_root)
            extra = ("该记录 integration 缓存陈旧（分支已含于 origin/main）——先 "
                     "update --id " + rec_id + " 核销后再 declare，或换 requirement 名"
                     if stale else "先 finish --abandon 收口")
            print(f"[REFUSED] 已存在同 Requirement 的在窗记录 {rec_id!r}（{extra}）", file=sys.stderr)
            return 2
        if args.issue and any(n < 1 or n > 999999 for n in args.issue):
            print(f"[REFUSED] --issue 必须为 1-6 位正整数: {sorted(args.issue)}", file=sys.stderr)
            return 2
        branch = (_git(["rev-parse", "--abbrev-ref", "HEAD"], args.worktree) or [None])[0]
        new_issues = set(args.issue or []) | extract_issue_numbers(args.requirement, branch or "")
        # §3.5 v1.10 决策类纪律机械化（#1232）：产物落到具体 ADR 文件时必须显式 --issue，
        # 否则 §3.4 工作项查重没有输入，同主题两个 Execution 完全互不感知（#906 事故形态）。
        claims = adr_claims(scopes)
        if claims and not new_issues and not args.force:
            print(f"[REFUSED] scope 声明了具体 ADR 文件 {claims}——决策类 Execution 必须显式 "
                  f"--issue <n>（契约 §3.5）：issue 集是 §3.4 在窗查重的唯一数据源，"
                  f"缺它会让同一主题的第二个 Execution 无法被拦下。确认要无 issue 登记时用 --force",
                  file=sys.stderr)
            return 2
        if claims and not new_issues:
            print(f"[WARN] --force：决策类 Execution {claims} 未声明 --issue——§3.4 查重无输入（§3.5）")
        landed = landed_ids(ctx.records, repo_root)
        conflicts = issue_conflicts(new_issues, ctx.records, rec_id, landed)
        if conflicts and not args.force:
            for rid, hit in conflicts:
                print(f"[REFUSED] issue {'/'.join('#' + str(n) for n in hit)} 已被在窗 Execution "
                      f"{rid!r}（{ctx.records[rid].get('harness', '?')}，"
                      f"{ctx.records[rid].get('lifecycle', '?')}）引用——"
                      f"转手先 finish --abandon，或 --force 显式覆盖（§3.4）", file=sys.stderr)
            return 2
        if conflicts:  # --force：留痕后放行
            for rid, hit in conflicts:
                print(f"[WARN] --force：issue {'/'.join('#' + str(n) for n in hit)} "
                      f"与在窗 Execution {rid!r} 重叠——人工确认转手/并行边界")
        now = _now()
        role_value = default_role(args.role)
        ctx.records[rec_id] = {
            "requirement": args.requirement,
            "harness": args.harness,
            "role": role_value,
            "worktree": os.path.abspath(args.worktree),
            "branch": branch or "",
            "scope": scopes,
            "issues": sorted((str(n) for n in new_issues), key=int),
            "pr_number": None,
            "lifecycle": "CODING",
            "test_impact": args.test_impact or "indirect",  # 缺省=indirect（§6）
            "last_seen": now,
            "created_at": now,
            "updated_at": now,
        }
        ctx.commit()
    finally:
        ctx.close()
    issue_note = f" issues={sorted((str(n) for n in new_issues), key=int)}" if new_issues else ""
    hint = "" if new_issues else \
        "（hint：requirement/branch 未含 issue 号且未带 --issue——在窗查重无输入，建议 --issue N）"
    print(f"[OK] declare {rec_id} role={role_value} scope={scopes} "
          f"test_impact={args.test_impact or 'indirect(缺省)'}"
          f"{issue_note}{hint}")
    return 0


def _report(rec_id: str, rec: dict, repo_root: str, refresh: bool, *,
            landed: bool = False, shared: frozenset | set = frozenset(),
            peers: list | None = None, derived: set | None = None) -> None:
    """`derived` 可由调用方注入（已 memoize 的集合，见 cmd_status）——不传则现算。"""
    now = _now()
    integration = rec.get("integration_cache")
    if refresh and rec.get("pr_number"):
        integration, ok = derive_integration(rec["pr_number"], integration, repo_root)
        if not ok:
            print(f"  (integration 观测于 {rec.get('observed_at', '?')}，GitHub 暂不可达)")
    integration = integration or "NO_PR"
    liveness = derive_liveness(float(rec["last_seen"]) if rec.get("last_seen") else None, now)
    derived = set(derived) if derived is not None else set(derived_paths(rec, repo_root, shared))
    effective = sorted(set(rec.get("scope", [])) | derived)
    # §3.3 v1.10 缓存失效：缓存称开放但分支已含于 origin/main ⇒ 不按风险窗口处理（不写字段）
    stale_cache = landed
    risk = in_risk(rec["lifecycle"], integration) and not stale_cache
    issue_note = f" issues={rec.get('issues') or []}" if rec.get("issues") else ""
    print(f"{rec_id}: {rec['harness']} lifecycle={rec['lifecycle']} liveness={liveness} "
          f"integration={integration} risk={'YES' if risk else 'no'}{issue_note}")
    print(f"  effective_scope={effective}")
    if stale_cache:
        print(f"  [stale-cache] integration={integration} 但分支已含于 origin/main——"
              f"该缓存值对 risk 判定失效（已按出窗处理）；建议 update --id {rec_id} 核销")
    if os.path.realpath(rec.get("worktree") or "") in set(shared or ()):
        print("  [shared-worktree] 本 worktree 被多条在窗记录共用——其 diff 不可归属单条记录，"
              "已退回 branch diff 档（建议各 Execution 使用专属 worktree，§5.2）")
    if peers:
        print(f"  [adr-collision] ADR 文件被多个在窗记录声明: {list(peers)}——"
              f"§3.5：一个架构主题同一时刻只能有一个权威 Decision Artifact，先汇聚再落笔")
    if risk:
        declared = set(rec.get("scope", []))
        if declared and derived:
            unlanded, undeclared = declaration_drift(declared, derived)
            parts = ([f"声明未落地: {unlanded}"] if unlanded
                     else []) + ([f"diff 未声明: {undeclared}"] if undeclared else [])
            if parts:
                print("  [declaration-drift] " + "; ".join(parts))
        # 僵尸本义「声明了、没干、还失联」= STALE + derived 空（#962 口径）；
        # v1.10（#1232）把契约文案与之对齐，并限定 integration=NO_PR——有开放 PR 的记录
        # 其工作在 PR 里，对它提示 finish --abandon 会与 T4 自相矛盾。
        if liveness == "STALE" and not derived:
            if closed_unmerged_candidate(rec["lifecycle"], integration, liveness, derived):
                # #906 第二类：PR 已关闭未合，仍在风险窗口并占用 issue 槽位
                print("  [closed-unmerged] PR 已 CLOSED 未合且记录未放弃（仍在风险窗口）"
                      "——请 finish --abandon 出窗，或 reopen PR / 转手重新 declare")
            elif zombie_candidate(rec["lifecycle"], integration, liveness, derived):
                print("  [zombie-candidate] 无 PR 且 STALE 且 diff 为空（声明未落地）——"
                      "人工经 finish --abandon 收口")


def cmd_status(args) -> int:
    path, _ = registry_paths()
    records = read_registry(path)  # 严格只读：不取锁写、不刷 last_seen（§2.5）
    if not records:
        print("(registry 为空——过渡条款生效：使用派生视图，见 repository-workflow.md)")
        return 0
    repo_root = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True,
                               text=True, check=True).stdout.strip()
    # 在窗集合（§3.2 真值表 ∧ ¬landed，§3.3 v1.10）：landed/shared/ADR 占用各算一次
    risk = risk_ids(records, repo_root)
    landed = landed_ids(records, repo_root)
    shared = shared_worktrees(records, risk)
    claims = adr_collisions(records, risk)
    risk_recs = {k: v for k, v in records.items() if k in risk}
    ids = ([args.id] if args.id
           else sorted(risk) if getattr(args, "risk", False)
           else sorted(records))
    # derived 按记录 memoize（#1234）：成对 overlap 循环曾对每条记录反复重算
    # derived_paths（O(n²) 次 git 子进程），是前检命令的主要时延来源
    derived_cache: dict = {}

    def dp(rid: str) -> set:
        if rid not in derived_cache:
            derived_cache[rid] = set(derived_paths(records[rid], repo_root, shared))
        return derived_cache[rid]

    if getattr(args, "risk", False) and not args.id:
        print(f"（--risk：仅列在窗记录 {len(risk)}/{len(records)} 条；全量用 status（不带 --risk））")
    for rec_id in ids:
        if rec_id not in records:
            print(f"[NOT-FOUND] {rec_id}", file=sys.stderr)
            return 2
        mine = {p for p in adr_claims(records[rec_id].get("scope", [])) if p in claims}
        peers = sorted({x for p in mine for x in claims[p] if x != rec_id})
        _report(rec_id, records[rec_id], repo_root, refresh=False,
                landed=rec_id in landed, shared=shared, peers=peers, derived=dp(rec_id))
    if len(risk_recs) >= 2:
        keys = sorted(risk_recs)
        for i, a in enumerate(keys):
            for b in keys[i + 1:]:
                ea = set(risk_recs[a].get("scope", [])) | dp(a)
                eb = set(risk_recs[b].get("scope", [])) | dp(b)
                hits = {x for x in ea for y in eb if scope_overlap(x, y)}
                if hits:
                    print(f"[overlap-hint] {a} ↔ {b}: {sorted(hits)}（hint，从不禁止修改）")
    return 0


# ── P3 drift gate（advisory；转 required 须独立裁决，ADR §2.7 P3）──

TEST_PATH_PREFIXES = ("backend/tests/", "backend/agent/tests/", "tests/")
TEST_PATH_NAMES = ("conftest.py", "pytest.ini", "vitest.config.ts", "vitest.config.js",
                   "pyproject.toml")


def is_test_path(path: str) -> bool:
    """coverage-mismatch 的「测试相关路径」判定（MVP：目录前缀 + 名称/后缀）。"""
    if path.startswith(TEST_PATH_PREFIXES):
        return True
    base = path.rsplit("/", 1)[-1]
    return base in TEST_PATH_NAMES or ".test." in base or ".spec." in base


def top_dirs(paths) -> set:
    """overlap 顶层目录聚合（P3 用顶层粒度作 hint，比组件级更低噪）。"""
    return {p.split("/", 1)[0] for p in paths if p}


def collect_drift_advisories(records: dict, repo_root: str, now: float) -> list[str]:
    """纯函数：drift/freshness/coverage-mismatch/overlap 四类 advisory（不输出、不退出码）。

    在窗集合按 §3.3 v1.10 计算（`risk_ids`：真值表 ∧ ¬landed）——已进主干的陈旧缓存
    记录不再产生 freshness/overlap 噪声（#1232）；landed 记录另有一条提示（提醒核销）。
    """
    advisories: list[str] = []
    risk_set = risk_ids(records, repo_root)
    landed = landed_ids(records, repo_root)
    shared = shared_worktrees(records, risk_set)
    risk = {k: v for k, v in records.items() if k in risk_set}
    effective: dict[str, set] = {}
    for rec_id, rec in sorted(risk.items()):
        declared = set(rec.get("scope", []))
        derived = set(derived_paths(rec, repo_root, shared))
        effective[rec_id] = declared | derived
        seen = float(rec["last_seen"]) if rec.get("last_seen") else None
        live = derive_liveness(seen, now)
        if live == "STALE":
            advisories.append(f"freshness: {rec_id} STALE（>24h 无心跳）——人工裁决（非死、不剔除）")
        if closed_unmerged_candidate(rec.get("lifecycle", "CODING"),
                                     rec.get("integration_cache") or "NO_PR", live, derived):
            pr_note = f"PR #{rec['pr_number']} " if rec.get("pr_number") else "PR "
            advisories.append(
                f"closed-unmerged: {rec_id} {pr_note}已 CLOSED 未合且未放弃（仍在风险窗口）"
                "——finish --abandon 出窗，或 reopen/转手")
        if declared and derived:
            unlanded, undeclared = declaration_drift(declared, derived)
            if unlanded:
                advisories.append(f"declaration-drift: {rec_id} 声明未落地 {unlanded}")
            if undeclared:
                advisories.append(f"declaration-drift: {rec_id} diff 未声明 {undeclared}")
        if rec.get("test_impact") == "none":
            hits = sorted(p for p in derived if is_test_path(p))
            if hits:
                advisories.append(
                    f"coverage-mismatch: {rec_id} 声明 test_impact=none 但 diff 触及测试路径 {hits[:5]}")
    for rec_id in sorted(landed):
        advisories.append(
            f"stale-cache: {rec_id} integration={records[rec_id].get('integration_cache')} "
            f"但分支已含于 origin/main——缓存失效（已按出窗处理）；update --id {rec_id} 核销")
    for path, ids in sorted(adr_collisions(records, risk_set).items()):
        advisories.append(f"adr-collision: {path} 被在窗记录 {ids} 同时声明——§3.5 决策实体唯一性")
    keys = sorted(effective)
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            shared = top_dirs(effective[a]) & top_dirs(effective[b])
            if shared:
                advisories.append(f"overlap-hint: {a} ↔ {b} 顶层目录 {sorted(shared)}（hint，从不禁止修改）")
    return advisories


def cmd_drift(args) -> int:
    path, _ = registry_paths()
    records = read_registry(path)
    if not records:
        print("[advisory] registry 无记录——drift gate no-op（过渡条款：派生视图）")
        return 0
    repo_root = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True,
                               text=True, check=True).stdout.strip()
    advisories = collect_drift_advisories(records, repo_root, _now())
    for line in advisories:
        print(f"[advisory] {line}")
    if advisories:
        print(f"drift gate（advisory）：{len(advisories)} 项提示——不阻塞；转 required 须独立裁决")
        return 1 if args.strict else 0
    print("[OK] drift gate（advisory）：无 drift/freshness/coverage/overlap 提示")
    return 0


def cmd_whoami(args) -> int:
    """P2 Adapter 基元：按 worktree 定位自身 Execution（上下文供给，非路由）。

    各 Harness 会话启动时由 agent 执行（薄适配层指引见 harness-adapters.md）：
    输出自身状态 + 其他在窗 Execution 对本 worktree scope 的 overlap 提示。
    严格只读（同 status）。"""
    path, _ = registry_paths()
    records = read_registry(path)
    repo_root = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True,
                               text=True, check=True).stdout.strip()
    if args.worktree:
        wt = os.path.realpath(os.path.abspath(args.worktree))
    else:
        wt = os.path.realpath(repo_root)
    mine = {k: v for k, v in records.items()
            if os.path.realpath(v.get("worktree", "")) == wt}
    if not mine:
        print(f"(worktree {wt} 无 Registry 记录——若本会话属于某个 Requirement，"
              f"请先 declare；过渡条款见 repository-workflow.md)")
        return 0
    risk = risk_ids(records, repo_root)
    landed = landed_ids(records, repo_root)
    shared = shared_worktrees(records, risk)
    claims = adr_collisions(records, risk)
    for rec_id in sorted(mine):
        my = {p for p in adr_claims(mine[rec_id].get("scope", [])) if p in claims}
        peers = sorted({x for p in my for x in claims[p] if x != rec_id})
        _report(rec_id, mine[rec_id], repo_root, refresh=False,
                landed=rec_id in landed, shared=shared, peers=peers)
    # 谁的 effective scope 压到了本 worktree 的 scope（入向 overlap）
    my_scope = set()
    for v in mine.values():
        my_scope |= set(v.get("scope", [])) | set(derived_paths(v, repo_root, shared))
    for rec_id, v in sorted(records.items()):
        if rec_id in mine or rec_id not in risk:
            continue
        their = set(v.get("scope", [])) | set(derived_paths(v, repo_root, shared))
        hits = {x for x in my_scope for y in their if scope_overlap(x, y)}
        if hits:
            print(f"[overlap-in] {rec_id}（{v.get('harness', '?')}，{v.get('lifecycle', '?')}）"
                  f" 的集成窗口覆盖本 worktree scope: {sorted(hits)}（hint，从不禁止修改）")
    return 0


# ── 批量 reconcile（契约 §3.3 v1.11，#1234）──

def fetch_pr_states(cwd: str, limit: int = 400) -> dict | None:
    """**一次** gh 调用取全部 PR 的 state：{pr_number: "OPEN|MERGED|CLOSED"}。

    只取 `state`——批量 reconcile 的职责是**终态**（实测陈旧记录的主体：25/29 为
    已合入），READY 派生仍归单记录 `update`（`gh pr checks --required` 逐 PR 调用，
    #1211 的领地），批量侧不重算 READY，避免同一判据出现两套实现。
    GitHub 不可用（gh 失败/超时/cwd 不可用）→ None（调用方降级，不猜测、不推进终态）。
    """
    try:
        proc = subprocess.run(
            ["gh", "pr", "list", "--state", "all", "--limit", str(limit),
             "--json", "number,state"],
            capture_output=True, text=True, timeout=60, cwd=cwd,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode != 0:
        return None
    try:
        import json
        return {str(item["number"]): item.get("state") for item in json.loads(proc.stdout)}
    except (ValueError, KeyError, TypeError):
        return None


def batch_integration_updates(records: dict, states: dict) -> dict:
    """纯函数：由 PR state 映射算出需要改写的 integration——{record_id: 新值}。

    只改**终态**（MERGED/CLOSED）且仅在取值确有不同的记录；PR 仍 OPEN 的记录保持既有
    `integration_cache` 不动（READY 不在此重算，见 `fetch_pr_states`）。无 `pr_number`
    或未出现在映射中的记录跳过——纯函数、可离线红绿自证。
    """
    updates: dict = {}
    for rid, rec in records.items():
        pr = rec.get("pr_number")
        if not pr:
            continue
        state = states.get(str(pr))
        cached = rec.get("integration_cache") or "NO_PR"
        if state == "MERGED" and cached != "MERGED":
            updates[rid] = "MERGED"
        elif state == "CLOSED" and cached != "CLOSED":
            updates[rid] = "CLOSED"
    return updates


def reconcile_all(path: str, lock: str, repo_root: str) -> int:
    """`update --all`：单次调用 + 单次九步写刷新全部已登记 PR 的终态。

    **不刷任何 `last_seen`**：批量命令没有 execution identity，不冒充心跳（§3.3 的
    identity 语义）；只写 `integration_cache`/`observed_at`/`updated_at`。
    """
    states = fetch_pr_states(repo_root)
    if states is None:
        print("[WARN] GitHub 不可达——批量 reconcile 跳过（不猜测、不推进终态，§3.3）",
              file=sys.stderr)
        return 1
    updates: dict = {}
    ctx = load_locked(path, lock)
    try:
        updates = batch_integration_updates(ctx.records, states)
        if updates:
            now = _now()
            for rid, value in updates.items():
                ctx.records[rid]["integration_cache"] = value
                ctx.records[rid]["observed_at"] = now
                ctx.records[rid]["updated_at"] = now
            ctx.commit()
    finally:
        ctx.close()
    for rid in sorted(updates):
        print(f"[OK] reconcile {rid} → {updates[rid]}")
    print(f"[OK] update --all：刷新 {len(updates)} 条终态（GitHub 侧共 {len(states)} 个 PR；"
          f"未刷任何 last_seen；PR 仍 OPEN 的 READY 派生归单记录 update）")
    return 0


def cmd_update(args) -> int:
    path, lock = registry_paths()
    repo_root = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True,
                               text=True, check=True).stdout.strip()
    if getattr(args, "all", False):
        if args.id:
            print("[REFUSED] --all 与 --id 互斥（批量 reconcile 无 execution identity）", file=sys.stderr)
            return 2
        return reconcile_all(path, lock, repo_root)
    if not args.id:
        print("[REFUSED] update 需要 --id <ID>，或 --all 做批量 reconcile（#1234）", file=sys.stderr)
        return 2
    ctx = load_locked(path, lock)
    try:
        rec = ctx.records.get(args.id)
        if not rec:
            print(f"[NOT-FOUND] {args.id}", file=sys.stderr)
            return 2
        if args.scope:
            try:
                rec["scope"] = [normalize_scope(s, repo_root) for s in args.scope]
            except ValueError as exc:
                print(f"[REFUSED] {exc}", file=sys.stderr)
                return 2
        if args.pr:
            rec["pr_number"] = str(args.pr)
        integration, ok = derive_integration(rec.get("pr_number"),
                                             rec.get("integration_cache"), repo_root)
        rec["integration_cache"] = integration
        rec["observed_at"] = _now()
        rec["last_seen"] = _now()
        rec["updated_at"] = _now()
        ctx.commit()
    finally:
        ctx.close()
    print(f"[OK] update {args.id} integration={integration}"
          + ("" if ok else "（GitHub 不可达，保持旧观测值）"))
    return 0


def cmd_finish(args) -> int:
    path, lock = registry_paths()
    repo_root = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True,
                               text=True, check=True).stdout.strip()
    ctx = load_locked(path, lock)
    try:
        rec = ctx.records.get(args.id)
        if not rec:
            print(f"[NOT-FOUND] {args.id}", file=sys.stderr)
            return 2
        now = _now()
        if args.abandon:
            integration, _ = derive_integration(rec.get("pr_number"),
                                                rec.get("integration_cache"), repo_root)
            rec["lifecycle"] = "ABANDONED"
            if integration in ("PR_OPEN", "READY"):
                print(f"[WARN] PR #{rec.get('pr_number')} 仍在集成窗口——记录留窗至 GitHub 侧终态；"
                      f"请先关闭/转交 PR（转手 = 新 Execution 重新 declare）", file=sys.stderr)
            rec["integration_cache"] = integration
            rec["observed_at"] = now
        else:
            rec["lifecycle"] = "FINISHED"
            if args.pr:
                rec["pr_number"] = str(args.pr)
            integration, _ = derive_integration(rec.get("pr_number"),
                                                rec.get("integration_cache"), repo_root)
            rec["integration_cache"] = integration
            rec["observed_at"] = now
        rec["last_seen"] = now
        rec["updated_at"] = now
        ctx.commit()
    finally:
        ctx.close()
    print(f"[OK] finish {args.id} lifecycle={rec['lifecycle']}")
    return 0


def cmd_resume(args) -> int:
    """T9（#946，契约 §3.3）：FINISHED→CODING 返工回退——评审意见要求继续编码时
    恢复执行生命周期，保审计连续性（替代「abandon+重 declare」的自指 overlap 出路）。
    MERGED/ABANDONED 拒绝。放行判定前先向 GitHub reconcile integration
    （#1056）：缓存可能陈旧（PR 已 MERGED 而 cache 仍 PR_OPEN/READY），
    基于陈旧值放行会复活已出窗 execution 并重新阻塞同 issue 领取；
    GitHub 不可达时拒绝并提示先 update，不基于已知可能陈旧的缓存放行。"""
    path, lock = registry_paths()
    ctx = load_locked(path, lock)
    try:
        rec = ctx.records.get(args.id)
        if not rec:
            print(f"[NOT-FOUND] {args.id}", file=sys.stderr)
            return 2
        cached = rec.get("integration_cache") or "NO_PR"
        integration = cached
        if rec.get("pr_number"):
            repo_root = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"], capture_output=True,
                text=True, cwd=os.getcwd()).stdout.strip() or os.getcwd()
            integration, refreshed = derive_integration(rec["pr_number"], cached, repo_root)
            if not refreshed:
                print(f"[REFUSED] resume {args.id}: GitHub 不可达，integration 无法刷新"
                      "（cache=" + cached + "）——先 update 再 resume", file=sys.stderr)
                return 2
        ok, reason = can_resume(rec["lifecycle"], integration)
        if not ok:
            print(f"[REFUSED] resume {args.id}: {reason}", file=sys.stderr)
            return 2
        now = _now()
        rec["lifecycle"] = "CODING"
        rec["last_seen"] = now
        rec["updated_at"] = now
        ctx.commit()
    finally:
        ctx.close()
    print(f"[OK] resume {args.id} lifecycle=CODING（integration={integration}，"
          "registry 不回写，随下次 update reconcile）")
    return 0


# ── 自测：纯函数红绿双向（离线）──

def run_self_test() -> int:
    failures: list[str] = []

    def expect(name, fn, should_raise=False):
        try:
            fn()
            if should_raise:
                failures.append(f"{name}: 预期红，实际绿")
        except ValueError:
            if not should_raise:
                failures.append(f"{name}: 预期绿，实际红")

    expect("scope 合法", lambda: normalize_scope("backend/agent/aee.py"), False)
    expect("scope 绝对路径拒绝", lambda: normalize_scope("/etc/passwd"), True)
    expect("scope .. 拒绝", lambda: normalize_scope("backend/../outside"), True)
    expect("scope trailing slash 归一", lambda: (
        (lambda n: n == "backend/agent")(normalize_scope("backend/agent/"))), False)
    assert normalize_scope("backend/agent/") == "backend/agent"

    assert scope_overlap("backend", "backend/agent/aee.py") is True
    assert scope_overlap("backend", "backend_new/x.py") is False  # 组件边界（§5.4）
    assert scope_overlap("a.py", "a.py") is True
    assert scope_overlap("a.py", "a.py.bak") is False

    # 真值表（§3.2）：开放 PR 恒在窗口（R23 的 ABANDONED×PR_OPEN 必须在窗）
    assert in_risk("CODING", "NO_PR") and in_risk("FINISHED", "NO_PR")
    assert in_risk("ABANDONED", "PR_OPEN") and in_risk("ABANDONED", "READY")
    assert in_risk("CODING", "CLOSED") and not in_risk("ABANDONED", "CLOSED")
    assert not in_risk("ANY", "MERGED") and not in_risk("ABANDONED", "NO_PR")

    # #946 T9 resume 守卫真值表
    assert can_resume("FINISHED", "PR_OPEN") == (True, "")
    assert can_resume("FINISHED", "READY")[0] and can_resume("FINISHED", "NO_PR")[0]
    assert can_resume("FINISHED", "CLOSED")[0]
    assert not can_resume("FINISHED", "MERGED")[0]  # 风险已关闭，走 T1
    assert not can_resume("CODING", "PR_OPEN")[0]  # 已在编码
    assert not can_resume("ABANDONED", "PR_OPEN")[0]  # 恢复=重新 declare
    assert can_resume("FINISHED", "MERGED")[1]  # 拒绝必附理由

    # #906 僵尸候选第二类：PR CLOSED 未合且未放弃 + 失联 + diff 为空（契约 §3.2）
    assert closed_unmerged_candidate("CODING", "CLOSED", "STALE", set())
    assert closed_unmerged_candidate("FINISHED", "CLOSED", "STALE", set())
    assert not closed_unmerged_candidate("ABANDONED", "CLOSED", "STALE", set())  # 已出窗
    assert not closed_unmerged_candidate("CODING", "CLOSED", "LIVE", set())  # 未失联
    assert not closed_unmerged_candidate("CODING", "CLOSED", "STALE", {"docs/x.md"})  # 有落地
    assert not closed_unmerged_candidate("CODING", "MERGED", "STALE", set())
    assert not closed_unmerged_candidate("CODING", "NO_PR", "STALE", set())

    assert derive_liveness(time.time() - 10, time.time()) == "LIVE"
    assert derive_liveness(time.time() - TTL_SECONDS - 1, time.time()) == "STALE"

    # #1232 僵尸候选第一类（契约 §3.2 v1.10 对齐）：**无 PR** + 未放弃 + 失联 + diff 为空
    assert zombie_candidate("CODING", "NO_PR", "STALE", set())
    assert zombie_candidate("FINISHED", "NO_PR", "STALE", set())
    assert not zombie_candidate("ABANDONED", "NO_PR", "STALE", set())  # 已出窗
    assert not zombie_candidate("CODING", "NO_PR", "LIVE", set())  # 未失联
    assert not zombie_candidate("CODING", "NO_PR", "STALE", {"docs/x.md"})  # 有落地
    # 有开放 PR 的记录不是僵尸（其工作在 PR 里；提示 abandon 会与 T4 自相矛盾）
    assert not zombie_candidate("FINISHED", "PR_OPEN", "STALE", set())
    assert not zombie_candidate("FINISHED", "READY", "STALE", set())
    assert not zombie_candidate("FINISHED", "MERGED", "STALE", set())
    assert not zombie_candidate("CODING", "CLOSED", "STALE", set())  # 归 closed-unmerged 判据

    # #1232 §3.5 决策类判据：只认具体 ADR 文件，不认目录（`docs/adr` 目录是常规 scope）
    assert adr_claims(["docs/adr/ADR-0035-agent-host-identity.md"]) == [
        "docs/adr/ADR-0035-agent-host-identity.md"]
    assert adr_claims(["docs/adr/README.md", "docs/adr", "docs/adr/ADR-0036-x.md"]) == [
        "docs/adr/ADR-0036-x.md"]
    assert adr_claims(["backend/agent", "docs/adr-notes.md"]) == []

    # #1232 trunk containment 红绿（离线临时仓库：不触网、不依赖本仓库状态）
    with tempfile.TemporaryDirectory() as gtd:
        def _g(*a):
            return subprocess.run(["git", *a], cwd=gtd, capture_output=True, text=True)

        def _commit(msg):
            _g("add", "-A")
            _g("commit", "-qm", msg)
            return _g("rev-parse", "HEAD").stdout.strip()

        _g("init", "-q", "-b", "main")
        _g("config", "user.email", "t@example.invalid")
        _g("config", "user.name", "t")
        with open(os.path.join(gtd, "f.txt"), "w", encoding="utf-8") as fh:
            fh.write("1\n")
        trunk = _commit("c1")
        _g("update-ref", "refs/remotes/origin/main", trunk)  # 主干（无 remote，纯本地 ref）
        _g("branch", "fix/merged", trunk)  # 已合入：分支停在主干提交上
        _g("checkout", "-q", "-b", "fix/open", trunk)  # 未合入：有自己的提交
        with open(os.path.join(gtd, "g.txt"), "w", encoding="utf-8") as fh:
            fh.write("2\n")
        _commit("c2")
        _g("branch", "fix/remote-only", trunk)  # 本地 ref 缺失、只有 origin/ 的形态
        _g("update-ref", "refs/remotes/origin/fix/remote-only", trunk)

        def _rec(**kw):
            base = {"requirement": "r", "harness": "h", "worktree": gtd, "lifecycle": "CODING"}
            base.update(kw)
            return base

        # 绿：缓存称开放但分支已含于 origin/main（本地 ref / 远端 ref 两种形态）
        assert landed_in_trunk(_rec(pr_number="1", integration_cache="READY",
                                    branch="fix/merged"), gtd)
        assert landed_in_trunk(_rec(pr_number="6", integration_cache="PR_OPEN",
                                    branch="fix/remote-only"), gtd)
        # 红：仍在窗口的四种 fail-safe（未合入 / 无 PR / 主干自身 / 缓存已终态 / 无分支）
        assert not landed_in_trunk(_rec(pr_number="2", integration_cache="PR_OPEN",
                                        branch="fix/open"), gtd)
        assert not landed_in_trunk(_rec(integration_cache="READY", branch="fix/merged"), gtd)
        assert not landed_in_trunk(_rec(pr_number="3", integration_cache="READY",
                                        branch="main"), gtd)
        assert not landed_in_trunk(_rec(pr_number="4", integration_cache="MERGED",
                                        branch="fix/merged"), gtd)
        assert not landed_in_trunk(_rec(pr_number="5", integration_cache="READY",
                                        branch="no/such"), gtd)
        # 红：仓库不可用（cwd 不存在）不得抛异常，一律留在窗口
        assert not landed_in_trunk(_rec(pr_number="7", integration_cache="READY",
                                        branch="fix/merged"), os.path.join(gtd, "nope"))
        # risk 集合：真值表 ∧ ¬landed
        _recs = {
            "merged-stale": _rec(pr_number="1", integration_cache="READY", branch="fix/merged"),
            "still-open": _rec(pr_number="2", integration_cache="PR_OPEN", branch="fix/open"),
            "no-pr": _rec(integration_cache="NO_PR", branch="fix/merged"),
        }
        assert risk_ids(_recs, gtd) == {"still-open", "no-pr"}
        assert not effective_risk(_recs["merged-stale"], gtd)
        # 查重不再被已合入记录误拒（#1232：86% 假阳性的直接后果）
        _dedup = {"merged-stale": dict(_recs["merged-stale"], issues=["1232"])}
        assert issue_conflicts({1232}, _dedup, "new") == [("merged-stale", [1232])]  # 纯 §3.4 口径
        assert issue_conflicts({1232}, _dedup, "new", landed_ids(_dedup, gtd)) == []  # 缓存失效后

        # #1232 derived 归属（契约 §5.2 v1.10）：HEAD≠记录 branch 或 worktree 被共享 → 退回档 2
        # 当前 checkout 为 fix/open（含 g.txt）
        assert derived_paths(_rec(branch="fix/open"), gtd) == ["g.txt"]  # 档 1：同分支
        assert derived_paths(_rec(branch="main"), gtd) == []  # 分支不符 → 档 2（g.txt 不得被误记）
        assert derived_paths(_rec(branch="main"), gtd,
                             {os.path.realpath(gtd)}) == []  # 共享 worktree → 不归属
        assert derived_paths(_rec(worktree="/nonexistent", branch="fix/open"), gtd) == ["g.txt"]

        # #1232 共享 worktree 与 ADR 占用：纯函数红绿
        _shared = {"a": _rec(worktree=gtd, branch="fix/open"),
                   "b": _rec(worktree=gtd, branch="fix/open")}
        assert shared_worktrees(_shared, {"a", "b"}) == {os.path.realpath(gtd)}
        assert shared_worktrees(_shared, {"a"}) == set()  # 仅一条在窗不算共享
        _adr = {"x": _rec(scope=["docs/adr/ADR-0099-a.md"]),
                "y": _rec(scope=["docs/adr/ADR-0099-a.md", "docs/adr"])}
        assert adr_collisions(_adr, {"x", "y"}) == {"docs/adr/ADR-0099-a.md": ["x", "y"]}
        assert adr_collisions(_adr, {"x"}) == {}

        # #1232 第二条证据通道：origin/main 的 GitHub merge 主题（ancestry 断裂时仍判出）
        _g("checkout", "-q", "main")
        _g("checkout", "-q", "-b", "fix/msg", trunk)
        with open(os.path.join(gtd, "h.txt"), "w", encoding="utf-8") as fh:
            fh.write("3\n")
        _commit("c3")
        _g("checkout", "-q", "main")
        _g("merge", "-q", "--no-ff", "fix/msg", "-m", "Merge pull request #99 from DUElost/fix/msg")
        _g("update-ref", "refs/remotes/origin/main", _g("rev-parse", "HEAD").stdout.strip())
        assert merged_pr_numbers(gtd) == {"99"}
        assert not merged_pr_numbers(os.path.join(gtd, "nope"))  # 环境不可用 → 空集（fail-safe）
        # 分支 ref 已被删除（ancestry 断裂）但 PR 的 merge commit 在主干上 → landed
        assert landed_in_trunk(_rec(pr_number="99", integration_cache="PR_OPEN",
                                    branch="deleted/branch"), gtd)
        assert not landed_in_trunk(_rec(pr_number="98", integration_cache="PR_OPEN",
                                        branch="deleted/branch"), gtd)  # 未合入 → 留在窗口

    # #880 三缺口红绿：declare 校验 / codec 引号 key 往返 / corrupt 隔离
    try:
        normalize_requirement_id("#878")
        failures.append("#880 declare 拒绝 #开头: 预期红，实际绿")
    except ValueError:
        pass
    try:
        normalize_requirement_id("P0: sync")
        failures.append("#880 declare 拒绝含冒号: 预期红，实际绿")
    except ValueError:
        pass
    # #1059 红绿：换行 id 语义层拒绝 + codec 值内换行往返保真（不再拆行锁死）
    try:
        normalize_requirement_id("fix-900\nline2")
        failures.append("#1059 declare 拒绝含换行: 预期红，实际绿")
    except ValueError:
        pass
    _nl_rec = {"requirement": "multi\nline\rvalue", "harness": "zcode",
               "worktree": "/w", "branch": "b", "scope": ["a.py"],
               "lifecycle": "CODING", "test_impact": "indirect",
               "last_seen": 1.0, "created_at": 1.0, "updated_at": 1.0}
    _nl_rt = yaml_load(yaml_dump({"r1": _nl_rec}))["r1"]["requirement"]
    assert _nl_rt == "multi\nline\rvalue", f"#1059 换行往返失真: {_nl_rt!r}"
    # 落盘为转义形态：requirement 值不产生裸换行（记录保持单行结构）
    assert 'requirement: "multi\\nline\\rvalue"' in yaml_dump({"r1": _nl_rec})
    assert normalize_requirement_id("fix #878 login") == "fix #878 login"  # 值位 # 合法
    quoted_key = {"#878": {"requirement": "#878", "harness": "claude",
                            "worktree": "/w", "branch": "", "scope": ["backend"],
                            "lifecycle": "CODING", "test_impact": "indirect",
                            "last_seen": 1.0, "created_at": 1.0, "updated_at": 1.0}}
    rt = yaml_load(yaml_dump(quoted_key))
    assert rt["#878"]["requirement"] == "#878"  # codec 对称：引号 key 往返
    with tempfile.TemporaryDirectory() as td:
        p2, lk2 = os.path.join(td, "registry.yaml"), os.path.join(td, "registry.lock")
        atomic_write(p2, lk2, quoted_key)
        assert read_registry(p2)["#878"]["harness"] == "claude"
        open(p2, "w", encoding="utf-8").write("garbage line not yaml\n")
        try:
            read_registry(p2)
            failures.append("#880 corrupt 隔离: 预期红，实际绿")
        except ValueError as exc:
            assert "已隔离至" in str(exc) and ".corrupt-" in str(exc)
        assert any(".corrupt-" in f for f in os.listdir(td)), "corrupt 隔离文件应留在 td"

    # declaration_drift 红绿双向（#928：目录级 scope 用 §5.4 组件边界谓词）
    assert declaration_drift({"docs/reviews"}, {"docs/reviews/x.md"}) == ([], [])  # 目录覆盖子文件
    assert declaration_drift({"docs"}, {"docs/reviews/x.md"}) == ([], [])  # 父目录覆盖
    assert declaration_drift({"docs/reviews"}, {"docs/reviews2/x.md"}) == (
        ["docs/reviews"], ["docs/reviews2/x.md"])  # 组件边界：reviews2 不被 reviews 覆盖
    assert declaration_drift({"backend/api.py"}, {"backend/api.py"}) == ([], [])  # 文件对文件精确
    assert declaration_drift({"docs"}, {"backend/x.py"}) == (["docs"], ["backend/x.py"])  # 真 drift
    # 空声明：helper 诚实返回全部 derived 为未声明——调用方以 declared and derived 守卫
    assert declaration_drift(set(), {"backend/x.py"}) == ([], ["backend/x.py"])

    # #978 issue 提取启发式红绿
    assert extract_issue_numbers("fix-978-declare-issue-dedup") == {978}
    assert extract_issue_numbers("issue-878") == {878}
    assert extract_issue_numbers("fix/900-jwt-sub") == {900}
    assert extract_issue_numbers("fix #878 login") == {878}
    assert extract_issue_numbers("Fix-881-SID-Renew") == {881}  # 大小写不敏感
    assert extract_issue_numbers("fix-900", "issue-901") == {900, 901}
    assert extract_issue_numbers("drift-sync-2026-09-07") == set()  # 日期串不误报
    assert extract_issue_numbers("docs/947-observation-table-sync") == set()
    assert extract_issue_numbers("myfix-123") == set()  # 标记词边界
    assert extract_issue_numbers("fix-1234567") == set()  # ≥7 位非 issue 号

    # #978 在窗查重纯函数：issues 字段权威、slug 兜底、MERGED 出窗、skip 自身
    recs = {
        "a": {"requirement": "fix-900-jwt", "harness": "h", "worktree": "/w",
              "lifecycle": "CODING", "integration_cache": "NO_PR", "issues": ["900"]},
        "b": {"requirement": "merged-thing", "harness": "h", "worktree": "/w",
              "lifecycle": "CODING", "integration_cache": "MERGED", "issues": ["901"]},
        "c": {"requirement": "legacy-slug", "harness": "h", "worktree": "/w",
              "branch": "fix/902-legacy", "lifecycle": "FINISHED",
              "integration_cache": "NO_PR"},  # 无 issues 字段：branch slug 兜底
    }
    assert issue_conflicts({900}, recs, "new") == [("a", [900])]
    assert issue_conflicts({901}, recs, "new") == []  # MERGED 出窗
    assert issue_conflicts({902}, recs, "new") == [("c", [902])]
    assert issue_conflicts({999}, recs, "new") == []
    assert issue_conflicts({900, 902}, recs, "a") == [("c", [902])]  # 同名在窗已先拒，此处防重扫

    # #978 codec 往返：issues 列表 + 非法元素拒绝；legacy 无字段输出不变
    rec978 = {"requirement": "fix", "harness": "claude", "worktree": "/tmp/w",
              "branch": "b1", "scope": ["backend/agent"], "issues": ["900", "901"],
              "lifecycle": "CODING", "test_impact": "direct", "last_seen": 1.0,
              "created_at": 1.0, "updated_at": 1.0}
    rt2 = yaml_load(yaml_dump({"r1": rec978}))
    assert rt2["r1"]["issues"] == ["900", "901"]
    assert rt2["r1"]["scope"] == ["backend/agent"]
    legacy_out = yaml_dump({"r1": {k: v for k, v in rec978.items() if k != "issues"}})
    assert "issues" not in legacy_out  # legacy 记录（无 issues）输出不变
    try:
        validate({"r1": {"requirement": "x", "harness": "h", "worktree": "/w",
                         "lifecycle": "CODING", "issues": ["abc"]}})
        failures.append("#978 issues 非数字: 预期红，实际绿")
    except ValueError:
        pass
    try:
        yaml_load("r1:\n  pr_number:\n    - a\n")  # 非 LIST_FIELDS 字段的列表形态
        failures.append("#978 列表字段越界: 预期红，实际绿")
    except ValueError:
        pass

    # role 缺省归一化（契约 §1.2 v1.6）：缺省/空串写 implementation，显式值透传
    assert default_role(None) == "implementation"
    assert default_role("") == "implementation"
    assert default_role("docs") == "docs"

    # #1234 批量 reconcile 纯函数：由 PR state 映射算出「需要改的 integration」
    # （本组断言刻意放在此处的独立区块，不追加到 self-test 尾部/状态派生区——
    #  那两处是各 harness 追加断言的天然合并热点，#1211 与 #1232/#1234 曾在此撞车）
    _batch = {
        "stale-merged": {"requirement": "a", "harness": "h", "worktree": "/w",
                         "lifecycle": "FINISHED", "pr_number": "11",
                         "integration_cache": "PR_OPEN"},
        "stale-closed": {"requirement": "b", "harness": "h", "worktree": "/w",
                         "lifecycle": "CODING", "pr_number": "12",
                         "integration_cache": "READY"},
        "still-open": {"requirement": "c", "harness": "h", "worktree": "/w",
                       "lifecycle": "CODING", "pr_number": "13",
                       "integration_cache": "PR_OPEN"},
        "already-merged": {"requirement": "d", "harness": "h", "worktree": "/w",
                           "lifecycle": "FINISHED", "pr_number": "14",
                           "integration_cache": "MERGED"},
        "no-pr": {"requirement": "e", "harness": "h", "worktree": "/w",
                  "lifecycle": "CODING"},
        "unknown-pr": {"requirement": "f", "harness": "h", "worktree": "/w",
                       "lifecycle": "CODING", "pr_number": "99"},
    }
    _states = {"11": "MERGED", "12": "CLOSED", "13": "OPEN", "14": "MERGED"}
    assert batch_integration_updates(_batch, _states) == {
        "stale-merged": "MERGED", "stale-closed": "CLOSED"}
    # PR 仍 OPEN：不在此重算 READY（避免与单记录 update / #1211 双实现）
    assert "still-open" not in batch_integration_updates(_batch, _states)
    # 已终态：幂等（不产生无意义写入）；无 pr_number / 未出现在映射中：跳过
    assert "already-merged" not in batch_integration_updates(_batch, _states)
    assert batch_integration_updates(_batch, {}) == {}
    assert batch_integration_updates({}, _states) == {}
    # GitHub 不可用 → None（降级；此断言用不可用 cwd，不触网）
    assert fetch_pr_states("/nonexistent-cwd-for-test") is None

    # P3 drift gate 纯函数
    assert is_test_path("backend/tests/test_x.py") and is_test_path("tests/y.py")
    assert is_test_path("frontend/src/a.test.ts") and is_test_path("dir/conftest.py")
    assert not is_test_path("backend/api/main.py") and not is_test_path("docs/x.md")
    assert top_dirs(["a/b/c", "a/d", "e"]) == {"a", "e"}
    fake = {
        "r1": {"requirement": "r1", "harness": "h", "worktree": "/nonexistent",
               "branch": "", "scope": ["docs", "backend/api/x.py"], "lifecycle": "CODING",
               "test_impact": "none", "last_seen": time.time(),
               "integration_cache": "NO_PR"},
        "r2": {"requirement": "r2", "harness": "h", "worktree": "/nonexistent2",
               "branch": "", "scope": ["backend/tests/t.py"], "lifecycle": "CODING",
               "test_impact": "direct", "last_seen": time.time() - TTL_SECONDS - 10,
               "integration_cache": "NO_PR"},
    }
    adv = collect_drift_advisories(fake, "/nonexistent-root", time.time())
    text = "\n".join(adv)
    assert "freshness: r2 STALE" in text
    assert "overlap-hint: r1 ↔ r2 顶层目录 ['backend']" in text
    assert not any("coverage-mismatch" in a and "r1" in a for a in adv)  # r1 derived 为空不误报

    # codec 往返 + 破坏检测
    sample = {"r1": {"requirement": "fix", "harness": "claude", "worktree": "/tmp/w",
                     "branch": "b1", "scope": ["backend/agent", "docs/评审 x.md"],
                     "lifecycle": "CODING", "test_impact": "direct", "pr_number": None,
                     "last_seen": 1.0, "created_at": 1.0, "updated_at": 1.0}}
    loaded = yaml_load(yaml_dump(sample))
    assert loaded["r1"]["scope"] == sample["r1"]["scope"]
    assert loaded["r1"]["requirement"] == "fix"
    try:
        yaml_load("r1:\n  requirement: x\n  rogue_field: y\n")
        failures.append("codec 未知字段: 预期红，实际绿")
    except ValueError:
        pass
    try:
        yaml_load("r1: [not, a, mapping]\n")
        failures.append("codec 超集语法: 预期红，实际绿")
    except ValueError:
        pass

    # 九步原子写往返（临时目录）
    with tempfile.TemporaryDirectory() as td:
        p, lk = os.path.join(td, "registry.yaml"), os.path.join(td, "registry.lock")
        atomic_write(p, lk, sample)
        back = read_registry(p)
        assert back["r1"]["scope"] == sample["r1"]["scope"]
        try:
            validate({"r1": {"requirement": "x", "harness": "h", "worktree": "/w",
                             "lifecycle": "BAD"}})
            failures.append("validate 非法 lifecycle: 预期红，实际绿")
        except ValueError:
            pass
        try:
            validate({"r1": {"requirement": "x", "worktree": "/w",
                             "lifecycle": "CODING"}})  # 缺 harness
            failures.append("validate 缺必填: 预期红，实际绿")
        except ValueError:
            pass

    if failures:
        for f in failures:
            print(f"[SELFTEST-FAIL] {f}", file=sys.stderr)
        return 1
    print("[OK] ai_work self-test 通过（scope/overlap/真值表/liveness/codec/原子写/"
          "issue 查重 红绿双向）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Execution Registry CLI（execution-contract.md）")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("declare")
    p.add_argument("--requirement", required=True)
    p.add_argument("--harness", required=True)
    p.add_argument("--worktree", required=True)
    p.add_argument("--role",
                   help="Role Context 标签（自由文本；缺省写入 implementation，"
                        "历史空串记录同义读取——契约 §1.2）")
    p.add_argument("--scope", action="append", required=True)
    p.add_argument("--issue", action="append", type=int, metavar="N",
                   help="关联 issue 号，可重复；与 requirement/branch slug 提取一并作"
                        "在窗查重依据（§3.4，#978）")
    p.add_argument("--force", action="store_true",
                   help="issue 查重命中时强制 declare（转手/并行边界已人工确认）")
    p.add_argument("--test-impact", choices=TEST_IMPACT)
    p.set_defaults(fn=cmd_declare)

    p = sub.add_parser("status")
    p.add_argument("--id")
    p.add_argument("--risk", action="store_true",
                   help="只列在窗记录（附加视图；默认仍列全部，#1234）")
    p.set_defaults(fn=cmd_status)

    p = sub.add_parser("whoami", help="按 worktree 定位自身 Execution + 入向 overlap（只读）")
    p.add_argument("--worktree", help="默认当前 worktree（git rev-parse --show-toplevel）")
    p.set_defaults(fn=cmd_whoami)

    # update 即 heartbeat：无 --scope/--pr 的 update = 纯心跳（刷 last_seen）+ reconcile
    # （契约 §2.5 的 identity 写命令语义；P2 wrapper 按此定时调用）
    p = sub.add_parser("update", aliases=["heartbeat"])
    p.add_argument("--id")
    p.add_argument("--all", action="store_true",
                   help="批量 reconcile 全部已登记 PR 的终态（单次 gh 调用 + 单次九步写；"
                        "不刷任何 last_seen，#1234）")
    p.add_argument("--scope", action="append")
    p.add_argument("--pr", type=int)
    p.set_defaults(fn=cmd_update)

    p = sub.add_parser("drift", help="P3 drift gate（advisory；--strict 转 required 接口）")
    p.add_argument("--strict", action="store_true",
                   help="有提示即 exit 1（转 required 的接口；当前不接 CI）")
    p.set_defaults(fn=cmd_drift)

    p = sub.add_parser("finish")
    p.add_argument("--id", required=True)
    p.add_argument("--pr", type=int)
    p.add_argument("--abandon", action="store_true")
    p.set_defaults(fn=cmd_finish)

    p = sub.add_parser("resume",
                       help="T9：FINISHED→CODING 返工回退（#946；MERGED/ABANDONED 拒绝）")
    p.add_argument("--id", required=True)
    p.set_defaults(fn=cmd_resume)

    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if getattr(args, "self_test", False) or not hasattr(args, "fn"):
        return run_self_test()
    try:
        return args.fn(args)
    except ValueError as exc:
        # 命令层 ValueError（如 registry 损坏的隔离报错）统一走人类可读出口，
        # 不裸 traceback（#880 验收残留；REFUSED 类已在各命令内自捕获）
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
