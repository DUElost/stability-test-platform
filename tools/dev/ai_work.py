#!/usr/bin/env python3
"""ai_work.py — Execution Registry CLI（ADR-0034 P1）。

权威规范：docs/development/ai/execution-contract.md（Living v1.1，唯一权威源）。
本文件是其 §2（Registry 协议）/§3（状态模型）/§5（scope 与 overlap）的 MVP 实现：
选择权原则（ADR §2.1）——本工具是执行侧自声明的 visibility-only 登记簿，不是调度器。

命令：
    ai_work.py declare --requirement R --harness H --worktree W --scope P [--scope ...]
                       [--role ROLE] [--test-impact none|direct|indirect]
    ai_work.py status [--id ID]                 # 严格只读（不刷任何 last_seen）
    ai_work.py update --id ID [--scope P ...] [--pr N]   # 刷 last_seen + GitHub reconcile
    ai_work.py finish --id ID [--pr N | --abandon]
    ai_work.py --self-test                      # 纯函数红绿自证（离线，无 git/gh 调用）

设计约束（契约即约束）：
- Registry root = $(git rev-parse --path-format=absolute --git-common-dir)/ai-work/，
  registry.yaml + registry.lock 同目录，位于 .git 内天然不被跟踪；
- 写入九步全序（flock → read → validate → modify → tmp → fsync → rename → 父目录 fsync → unlock）；
- liveness 查询时派生不持久化；integration 由 GitHub（gh）派生刷新，不可用时保持旧值+observed_at；
- overlap 真值表：开放 PR 恒在风险窗口；effective scope = declared ∪ derived(diff)。

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
    "requirement", "harness", "role", "worktree", "branch", "scope",
    "pr_number", "lifecycle", "test_impact", "last_seen", "created_at",
    "updated_at", "integration_cache", "observed_at",
}
LIFECYCLE = ("CODING", "FINISHED", "ABANDONED")
TEST_IMPACT = ("none", "direct", "indirect")


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
    return "\n".join(lines) + "\n"


def _quote(v) -> str:
    s = str(v)
    if s == "":
        return '""'
    if not s.startswith("#") and re.fullmatch(r"[A-Za-z0-9_./+=:@-]+", s):
        return s  # 含 # 一律引号——行首裸 # 会被当注释（#880）
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _unquote(s: str) -> str:
    s = s.strip()
    if not s.startswith('"'):
        return s
    # 受限转义：仅 \\ 与 \"（与 _quote 对称）
    out, i = [], 1
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s) and s[i + 1] in('\\"'):
            out.append(s[i + 1]); i += 2
        else:
            out.append(c); i += 1
    return "".join(out).removesuffix('"')


def yaml_load(text: str) -> dict:
    data: dict = {}
    current = None
    field = None
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        if line.startswith("  scope:"):
            if current is None:
                raise ValueError(f"registry.yaml:{lineno}: scope 出现在记录外")
            data[current]["scope"] = []
            field = "scope"
            continue
        if line.startswith("    - "):
            if field != "scope" or current is None:
                raise ValueError(f"registry.yaml:{lineno}: 列表项只允许出现在 scope 内")
            data[current]["scope"].append(_unquote(line[6:]))
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
    proc = subprocess.run(["git", *args], capture_output=True, text=True, cwd=cwd)
    if proc.returncode != 0:
        return []
    return [ln for ln in proc.stdout.splitlines() if ln.strip()]


def derived_paths(rec: dict, repo_root: str) -> list[str]:
    wt = rec.get("worktree")
    if wt and os.path.isdir(wt):
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
    """requirement id 校验（#880 修复 1，语义层）：拒绝 '#' 开头与含 ':' 的 id。

    id 是 registry.yaml 的 record key（YAML 行首位）——'#' 开头会被任何 YAML
    解析当注释、': ' 会破坏行结构。codec 已对称转义（防御深度），本校验是
    语义层守门：id 保持简洁标识形态（本仓库自然形态=issue 语义短语或 slug）。"""
    rid = rid.strip()
    if not rid:
        raise ValueError("requirement id 不能为空")
    if rid.startswith("#"):
        raise ValueError(f"requirement id 不得以 '#' 开头（会被 YAML 当注释）: {rid!r}"
                         f"——引用 issue 号请写作 'issue-878' 式 slug 或 'fix #878 描述' 剥离前导 #")
    if ":" in rid:
        raise ValueError(f"requirement id 不得含 ':'（破坏 record 行结构）: {rid!r}")
    return rid


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
        if rec_id in ctx.records and in_risk(ctx.records[rec_id].get("lifecycle", "CODING"),
                                             ctx.records[rec_id].get("integration_cache", "NO_PR")):
            print(f"[REFUSED] 已存在同 Requirement 的在窗记录 {rec_id!r}（先 finish --abandon 收口）",
                  file=sys.stderr)
            return 2
        branch = (_git(["rev-parse", "--abbrev-ref", "HEAD"], args.worktree) or [None])[0]
        now = _now()
        ctx.records[rec_id] = {
            "requirement": args.requirement,
            "harness": args.harness,
            "role": args.role or "",
            "worktree": os.path.abspath(args.worktree),
            "branch": branch or "",
            "scope": scopes,
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
    print(f"[OK] declare {rec_id} scope={scopes} test_impact={args.test_impact or 'indirect(缺省)'}")
    return 0


def _report(rec_id: str, rec: dict, repo_root: str, refresh: bool) -> None:
    now = _now()
    integration = rec.get("integration_cache")
    if refresh and rec.get("pr_number"):
        integration, ok = derive_integration(rec["pr_number"], integration, repo_root)
        if not ok:
            print(f"  (integration 观测于 {rec.get('observed_at', '?')}，GitHub 暂不可达)")
    integration = integration or "NO_PR"
    liveness = derive_liveness(float(rec["last_seen"]) if rec.get("last_seen") else None, now)
    effective = sorted(set(rec.get("scope", [])) | set(derived_paths(rec, repo_root)))
    risk = in_risk(rec["lifecycle"], integration)
    print(f"{rec_id}: {rec['harness']} lifecycle={rec['lifecycle']} liveness={liveness} "
          f"integration={integration} risk={'YES' if risk else 'no'}")
    print(f"  effective_scope={effective}")
    if risk:
        declared = set(rec.get("scope", []))
        derived = set(derived_paths(rec, repo_root))
        if declared and derived:
            unlanded, undeclared = declaration_drift(declared, derived)
            parts = ([f"声明未落地: {unlanded}"] if unlanded
                     else []) + ([f"diff 未声明: {undeclared}"] if undeclared else [])
            if parts:
                print("  [declaration-drift] " + "; ".join(parts))
        # #962：旧判据 `not effective` 在并集语义下不可达（declare --scope
        # required + update 只替换 → declared 恒非空）；僵尸本义是
        # 「声明了、没干、还失联」→ STALE 且 derived 为空
        if liveness == "STALE" and not derived:
            print("  [zombie-candidate] STALE 且 diff 为空（声明未落地）——人工经 finish --abandon 收口")


def cmd_status(args) -> int:
    path, _ = registry_paths()
    records = read_registry(path)  # 严格只读：不取锁写、不刷 last_seen（§2.5）
    if not records:
        print("(registry 为空——过渡条款生效：使用派生视图，见 repository-workflow.md)")
        return 0
    repo_root = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True,
                               text=True, check=True).stdout.strip()
    ids = [args.id] if args.id else sorted(records)
    # overlap 提示（risk 集合内两两组件边界）
    risk_recs = {k: v for k, v in records.items()
                 if in_risk(v.get("lifecycle", "CODING"), v.get("integration_cache") or "NO_PR")}
    for rec_id in ids:
        if rec_id not in records:
            print(f"[NOT-FOUND] {rec_id}", file=sys.stderr)
            return 2
        _report(rec_id, records[rec_id], repo_root, refresh=False)
    if len(risk_recs) >= 2:
        keys = sorted(risk_recs)
        for i, a in enumerate(keys):
            for b in keys[i + 1:]:
                ea = set(risk_recs[a].get("scope", [])) | set(derived_paths(risk_recs[a], repo_root))
                eb = set(risk_recs[b].get("scope", [])) | set(derived_paths(risk_recs[b], repo_root))
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
    """纯函数：drift/freshness/coverage-mismatch/overlap 四类 advisory（不输出、不退出码）。"""
    advisories: list[str] = []
    risk = {k: v for k, v in records.items()
            if in_risk(v.get("lifecycle", "CODING"), v.get("integration_cache") or "NO_PR")}
    effective: dict[str, set] = {}
    for rec_id, rec in sorted(risk.items()):
        declared = set(rec.get("scope", []))
        derived = set(derived_paths(rec, repo_root))
        effective[rec_id] = declared | derived
        seen = float(rec["last_seen"]) if rec.get("last_seen") else None
        if derive_liveness(seen, now) == "STALE":
            advisories.append(f"freshness: {rec_id} STALE（>24h 无心跳）——人工裁决（非死、不剔除）")
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
    for rec_id in sorted(mine):
        _report(rec_id, mine[rec_id], repo_root, refresh=False)
    # 谁的 effective scope 压到了本 worktree 的 scope（入向 overlap）
    my_scope = set()
    for v in mine.values():
        my_scope |= set(v.get("scope", [])) | set(derived_paths(v, repo_root))
    for rec_id, v in sorted(records.items()):
        if rec_id in mine:
            continue
        if not in_risk(v.get("lifecycle", "CODING"), v.get("integration_cache") or "NO_PR"):
            continue
        their = set(v.get("scope", [])) | set(derived_paths(v, repo_root))
        hits = {x for x in my_scope for y in their if scope_overlap(x, y)}
        if hits:
            print(f"[overlap-in] {rec_id}（{v.get('harness', '?')}，{v.get('lifecycle', '?')}）"
                  f" 的集成窗口覆盖本 worktree scope: {sorted(hits)}（hint，从不禁止修改）")
    return 0


def cmd_update(args) -> int:
    path, lock = registry_paths()
    repo_root = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True,
                               text=True, check=True).stdout.strip()
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

    assert derive_liveness(time.time() - 10, time.time()) == "LIVE"
    assert derive_liveness(time.time() - TTL_SECONDS - 1, time.time()) == "STALE"

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
    print("[OK] ai_work self-test 通过（scope/overlap/真值表/liveness/codec/原子写 红绿双向）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Execution Registry CLI（execution-contract.md）")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("declare")
    p.add_argument("--requirement", required=True)
    p.add_argument("--harness", required=True)
    p.add_argument("--worktree", required=True)
    p.add_argument("--role")
    p.add_argument("--scope", action="append", required=True)
    p.add_argument("--test-impact", choices=TEST_IMPACT)
    p.set_defaults(fn=cmd_declare)

    p = sub.add_parser("status")
    p.add_argument("--id")
    p.set_defaults(fn=cmd_status)

    p = sub.add_parser("whoami", help="按 worktree 定位自身 Execution + 入向 overlap（只读）")
    p.add_argument("--worktree", help="默认当前 worktree（git rev-parse --show-toplevel）")
    p.set_defaults(fn=cmd_whoami)

    # update 即 heartbeat：无 --scope/--pr 的 update = 纯心跳（刷 last_seen）+ reconcile
    # （契约 §2.5 的 identity 写命令语义；P2 wrapper 按此定时调用）
    p = sub.add_parser("update", aliases=["heartbeat"])
    p.add_argument("--id", required=True)
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
