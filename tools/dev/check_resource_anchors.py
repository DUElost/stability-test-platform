#!/usr/bin/env python3
"""B4 / G2（#3321；方案 #3601 v1.1 §1.2 C1、§4.3）——资源 authority 离线守卫。

判据（C1：**authority 由资源所属通道决定，不能由消费者包位置决定**）：

- **host-local 资源根**（四专项 APK）：默认值 = 既有 Agent 代码根 authority
  ``config.AGENT_DIR/resources/<族>``（开发态 ``<repo>/backend/agent``、部署态
  ``<install>/agent``），由 fallback 分支**惰性**取得；不得按 ``__file__`` 深度、
  ``tools_cache``、cwd 或 ``STP_AGENT_INSTALL_DIR`` 的目录深度推导。override
  （param > env）按原 Path 语义消费，空串视为未提供；param/env 分支不依赖默认锚。
- **工具包根**（ADR-0051 D7）：消费族 ``capabilities.json`` 的 ``requires_tools``
  + 引擎整包核验后注入的 env 指向所 pin 工具包根（合法落在 tools_cache）；
  声明缺失 / env 不一致 / 引用未登记或已退役条目 = 红。
- **包内伴随文件**：以本包根（``__file__`` 同级）定位，须真实入包。
- **import / schema / 运行目录 / 部署源**不是本形态资源锚，须按类登记，不许误判。

检查器以最小契约输入 ``tools/dev/resource_anchor_contract.json`` 的
C1 类型、family 的 param/env/project 后缀、Agent module authority、包成员/工具
pin 依据及函数级 legacy 分类为输入；工具版本真源仍是 ``tool_manifest.json``
与 ``backend/agent/tool_requirements.py``（按路径加载，不复制）。

行为：

- **全量 census**（默认，与 --base 无关）：独立发现 ``__file__`` / AGENT_DIR 派生的
  路径锚与 family 资源键消费者，与声明集双向对拍——未声明候选、声明条目无法在
  源码复现、无法解析的被消费表达式、缺失源文件、零候选/零覆盖均非零退出；
- **函数级 legacy**：10 个无消费者 helper 只按函数登记；新增调用（含**别名导入**、
  ``import *``、``getattr`` 动态引用）、导入路径、``__all__`` 导出使其可达即红；
  禁止整文件/族目录豁免；
- **authority 判据**（#3615 复核 P2 + 复审 R1–R3/S1–S3 后加固）：
  - 资源根相对 Agent authority 的**全部片段**必须恰为 ``("resources", <族子目录>)``——
    前/后缀均不允许；root 与 consumer 的 project/variant/bundle 层分开；
  - 资源根必须出现在**返回位置**且每个返回值都符合声明 authority——「保留正确赋值
    却返回错误路径」「增加错误目录返回」不得通过；authority 定位点不得返回字面量根；
  - 显式 override 必须**双通道**（同一消费者同时读取 param 与 env），且**实际被消费**的
    选择链必须证明 param 先行——未被使用的链不作证明；多条候选链无法关联消费时要求
    人工分类（不得按 param-first 放行）；
  - 同族导入索引覆盖**函数体内** Import/ImportFrom 并保留原始符号名；同一别名多来源时
    保留**全部**候选并显式报人工分类（禁止 last-write-wins）；``import *``、``getattr``
    动态引用同样不得让调用从可达性图消失；
- **--base**：增量防新增与例外防扩张（head 的 legacy 集合必须是 base 的子集）；
  不替代全量 census；base ref 不可解析或 base 契约坏 JSON = 不可验证（退出 2），
  不得当空 diff 成功。

用法::

    python tools/dev/check_resource_anchors.py --self-test
    python tools/dev/check_resource_anchors.py
    python tools/dev/check_resource_anchors.py --base origin/main

退出码：0 = 绿；1 = 判据违例；2 = 不可验证（base 不可解析等）。
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field, replace
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_REL = "tools/dev/resource_anchor_contract.json"
SCRIPTS_ROOT_REL = "backend/agent/scripts"
MANIFEST_REL = "tool_manifest.json"
TOOL_REQUIREMENTS_REL = "backend/agent/tool_requirements.py"

#: 未登记但形如资源/工具根的键（新族/新别名不得静默消失）。
RESOURCE_KEY_RE = re.compile(r"(_RESOURCE_DIR|_RESOURCES_DIR|_TOOL_DIR|TOOLS?_DIR)$")

_GIT_ENV = {**os.environ, "LC_ALL": "C"}

KIND_HOST_ROOT = "host_local_resource_root"
KIND_PACKAGE_MEMBER = "package_member"
KIND_TOOL_ROOT = "tool_root"
KIND_AGENT_MODULE = "agent_module_authority"
KIND_AGENT_BASE = "agent_base_dir"
KIND_SCHEMA = "schema_path"
KIND_TOOL_CACHE = "tool_cache_authority"
KIND_INSTALL_LAYOUT = "install_layout"
KIND_DEPLOY_SOURCE = "deploy_source"
KIND_IMPORT_BOOTSTRAP = "import_bootstrap"
KIND_EXPLICIT_OVERRIDE = "explicit_override"

KNOWN_KINDS = frozenset(
    {
        KIND_HOST_ROOT,
        KIND_PACKAGE_MEMBER,
        KIND_TOOL_ROOT,
        KIND_AGENT_MODULE,
        KIND_AGENT_BASE,
        KIND_SCHEMA,
        KIND_TOOL_CACHE,
        KIND_INSTALL_LAYOUT,
        KIND_DEPLOY_SOURCE,
        KIND_IMPORT_BOOTSTRAP,
        KIND_EXPLICIT_OVERRIDE,
    }
)

AUTHORITY_AGENT_DIR = "agent_dir"
AUTHORITY_DEPTH_GUESS = "depth_guess"


class Unverifiable(Exception):
    """base 不可解析 / base 契约坏 JSON——必须显式报不可验证。"""


# ---------------------------------------------------------------------------
# git / JSON
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=False, env=_GIT_ENV
    )


def git_rev_parse(repo: Path, ref: str) -> str | None:
    proc = _git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    return proc.stdout.strip() if proc.returncode == 0 else None


def load_ref_contract(repo: Path, ref: str, rel: str = CONTRACT_REL) -> dict | None:
    """读 ``ref`` 上的契约；文件不存在 → None；ref 不可解析 / JSON 坏 → Unverifiable。"""
    if git_rev_parse(repo, ref) is None:
        raise Unverifiable(f"基线 ref 不可解析：{ref}（CI 浅克隆需 fetch base；fail-closed）")
    exists = _git(repo, "cat-file", "-e", f"{ref}:{rel}")
    if exists.returncode != 0:
        return None
    raw = _git(repo, "show", f"{ref}:{rel}").stdout
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise Unverifiable(f"{ref}:{rel} 不是合法 JSON: {exc}") from None
    if not isinstance(doc, dict):
        raise Unverifiable(f"{ref}:{rel} 顶层必须是对象")
    return doc


def load_contract(repo: Path, rel: str = CONTRACT_REL) -> dict:
    path = repo / rel
    if not path.is_file():
        raise SystemExit(f"[FAIL] 契约缺失：{path}——唯一 census 输入不许消失")
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"[FAIL] 契约不是合法 JSON：{exc}") from None
    if not isinstance(doc, dict):
        raise SystemExit("[FAIL] 契约顶层必须是对象")
    return doc


def load_manifest(repo: Path) -> dict:
    path = repo / MANIFEST_REL
    if not path.is_file():
        raise SystemExit(f"[FAIL] {MANIFEST_REL} 缺失——工具版本真源不许消失")
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"[FAIL] {MANIFEST_REL} 不是合法 JSON：{exc}") from None
    return doc


_TOOL_REQUIREMENTS_CACHE: dict[str, object] = {}


def load_tool_requirements_module(repo: Path):
    """按路径加载 ``backend/agent/tool_requirements.py``（判据单源，不 import backend）。"""
    path = repo / TOOL_REQUIREMENTS_REL
    if not path.is_file():
        raise SystemExit(f"[FAIL] {TOOL_REQUIREMENTS_REL} 缺失——requires_tools 判据单源不许消失")
    key = str(path.resolve())
    cached = _TOOL_REQUIREMENTS_CACHE.get(key)
    if cached is not None:
        return cached
    name = f"stp_resource_anchor_tool_requirements_{abs(hash(key))}"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod  # dataclass 字符串注解需要模块在 sys.modules 中
    spec.loader.exec_module(mod)
    _TOOL_REQUIREMENTS_CACHE[key] = mod
    return mod


def tracked_files(repo: Path, tree_rel: str) -> set[str]:
    """族树下已跟踪文件（相对族树）；非 git 环境退回文件系统枚举。"""
    proc = _git(repo, "ls-files", "-z", "--", tree_rel)
    if proc.returncode == 0 and proc.stdout:
        prefix = tree_rel.rstrip("/") + "/"
        out = set()
        for item in proc.stdout.split("\0"):
            if item.startswith(prefix):
                out.add(item[len(prefix):])
        if out:
            return out
    base = repo / tree_rel
    if not base.is_dir():
        return set()
    return {str(p.relative_to(base)) for p in base.rglob("*") if p.is_file()}


# ---------------------------------------------------------------------------
# 有限路径表达式求值
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PathVal:
    """有限抽象域：一条路径表达式解析出的（可能不完整的）事实。"""

    kind: str  # file | file_derived | agent_dir | literal | param | env | override | unknown
    depth: int | None = None  # 距 __file__ 的 parent 跳数（file/file_derived 有效）
    segments: tuple[str, ...] = ()
    param_key: str | None = None
    env_key: str | None = None

    def has_segment(self, *needles: str) -> bool:
        return all(any(seg == n for seg in self.segments) for n in needles)

    @property
    def file_derived(self) -> bool:
        return self.kind in ("file", "file_derived") and self.depth is not None


def _str_const(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _int_const(node: ast.AST | None) -> int | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, int) and not isinstance(node.value, bool):
        return node.value
    return None


def _append_segments(base: PathVal, segments: tuple[str, ...]) -> PathVal:
    depth = base.depth
    out: list[str] = list(base.segments)
    for seg in segments:
        if seg in ("", "."):
            continue
        if seg == "..":
            if depth is not None:
                depth += 1
                continue
            out.append(seg)
            continue
        out.append(seg)
    return replace(base, segments=tuple(out), depth=depth, kind=("file_derived" if base.file_derived else base.kind))


def _merge_vals(vals: list[PathVal]) -> PathVal | None:
    """并集语义：优先保留最危险（file_derived）的结果，其次 agent_dir，再取首个非空。"""
    if not vals:
        return None
    for val in vals:
        if val.file_derived:
            return val
    for val in vals:
        if val.kind == "agent_dir":
            return val
    return vals[0]


class _Evaluator:
    """有限表达式求值：覆盖 __file__ → variable/parent/join/relative tuple → helper。"""

    def __init__(self, analysis: "FileAnalysis", param_keys: set[str], env_keys: set[str]):
        self.fa = analysis
        self.param_keys = param_keys
        self.env_keys = env_keys

    def _attr_chain(self, node: ast.AST) -> str:
        parts: list[str] = []
        cur = node
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name):
            parts.append(cur.id)
        return ".".join(reversed(parts))

    def _key_val(self, key: str | None) -> PathVal | None:
        if key is None:
            return None
        if key in self.param_keys:
            return PathVal("param", param_key=key)
        if key in self.env_keys:
            return PathVal("env", env_key=key)
        return None

    def eval(self, node: ast.AST | None, scope: dict[str, ast.AST], seen: frozenset[str] = frozenset(), depth: int = 0) -> PathVal | None:
        if node is None or depth > 12:
            return None
        if isinstance(node, ast.Name):
            name = node.id
            if name == "__file__":
                return PathVal("file", depth=0)
            if name in self.fa.agent_dir_names:
                return PathVal("agent_dir")
            if name in scope and name not in seen:
                return self.eval(scope[name], scope, seen | {name}, depth + 1)
            if name in self.fa.module_assigns and name not in seen:
                return self.eval(self.fa.module_assigns[name], scope, seen | {name}, depth + 1)
            return None
        if isinstance(node, ast.Constant):
            if isinstance(node.value, str):
                return PathVal("literal", segments=(node.value,))
            return None
        if isinstance(node, ast.Attribute):
            base = self.eval(node.value, scope, seen, depth + 1)
            if node.attr == "parent" and base is not None and base.file_derived:
                return replace(base, kind="file_derived", depth=(base.depth or 0) + 1)
            if self._attr_chain(node) == "config.AGENT_DIR":
                return PathVal("agent_dir")
            return None
        if isinstance(node, ast.Subscript):
            if isinstance(node.value, ast.Attribute) and node.value.attr == "parents":
                base = self.eval(node.value.value, scope, seen, depth + 1)
                hops = _int_const(node.slice)
                if base is not None and base.file_derived and hops is not None:
                    # parents[0] = 文件所在目录 = 距文件 1 跳
                    return replace(base, kind="file_derived", depth=(base.depth or 0) + hops + 1)
                return None
            key = _str_const(node.slice)
            if key is not None and isinstance(node.value, ast.Attribute):
                chain = self._attr_chain(node.value)
                if chain.endswith(".environ") or chain == "os.environ":
                    return self._key_val(key)
            return self._key_val(key)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            left = self.eval(node.left, scope, seen, depth + 1)
            right = self.eval(node.right, scope, seen, depth + 1)
            if left is None or not left.file_derived and left.kind not in ("agent_dir", "override", "param", "env", "unknown"):
                return left
            segs = right.segments if right is not None and right.kind in ("literal", "tuple") else None
            if segs is None:
                return None
            return _append_segments(left, segs)
        if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
            merged = PathVal("override")
            found = False
            for value in node.values:
                sub = self.eval(value, scope, seen, depth + 1)
                if sub is None:
                    continue
                found = True
                if sub.param_key:
                    merged = replace(merged, param_key=sub.param_key)
                if sub.env_key:
                    merged = replace(merged, env_key=sub.env_key)
                if sub.file_derived:
                    merged = sub
                if sub.kind == "literal" and sub.segments:
                    merged = replace(merged, segments=sub.segments)
            return merged if found else None
        if isinstance(node, ast.Tuple):
            segs: list[str] = []
            for elt in node.elts:
                sub = self.eval(elt, scope, seen, depth + 1)
                if sub is None or sub.kind != "literal":
                    return None
                segs.extend(sub.segments)
            return PathVal("tuple", segments=tuple(segs))
        if isinstance(node, ast.Call):
            return self._eval_call(node, scope, seen, depth)
        return None

    def _call_args_segments(self, args: list[ast.AST], scope: dict[str, ast.AST], seen: frozenset[str], depth: int) -> tuple[str, ...] | None:
        segs: list[str] = []
        for arg in args:
            target = arg.value if isinstance(arg, ast.Starred) else arg
            sub = self.eval(target, scope, seen, depth + 1)
            if sub is None or sub.kind not in ("literal", "tuple"):
                return None
            segs.extend(sub.segments)
        return tuple(segs)

    def _eval_call(self, node: ast.Call, scope: dict[str, ast.AST], seen: frozenset[str], depth: int) -> PathVal | None:
        func = node.func
        # 形如 `value` 的裸调用：包装器透传
        if isinstance(func, ast.Name):
            name = func.id
            if name == "env" and node.args:
                return self._key_val(_str_const(node.args[0]))
            if name == "param_or_env" and len(node.args) >= 3:
                param = self._key_val(_str_const(node.args[1]))
                env = self._key_val(_str_const(node.args[2]))
                if param is not None and env is not None:
                    return replace(param, kind="override", env_key=env.env_key)
                return param or env
            if name in ("Path", "str", "os.path.abspath", "os.path.expanduser", "os.path.normpath", "os.fspath"):
                return self.eval(node.args[0], scope, seen, depth + 1) if node.args else None
            if name == "os.path.dirname" and node.args:
                return self._dirname(node.args[0], scope, seen, depth)
            if name == "os.path.join" and node.args:
                base = self.eval(node.args[0], scope, seen, depth + 1)
                segs = self._call_args_segments(node.args[1:], scope, seen, depth)
                if base is None or segs is None:
                    return None
                return _append_segments(base, segs)
            if name in self.fa.funcs and f"fn:{name}" not in seen:
                # helper → 资源消费者：同文件本地函数调用回溯其**全部**返回值（有限深度，
                # 取并集而非首个——任一分支的深度推导都不得被静默漏过）
                fn = self.fa.funcs[name]
                inner = _scope_of(fn)
                vals: list[PathVal] = []
                for ret in (n for n in ast.walk(fn) if isinstance(n, ast.Return) and n.value is not None):
                    val = self.eval(ret.value, inner, seen | {f"fn:{name}"}, depth + 1)
                    if val is not None:
                        vals.append(val)
                return _merge_vals(vals)
            return None
        if isinstance(func, ast.Attribute):
            attr = func.attr
            if attr in ("resolve", "absolute", "expanduser", "strip", "expandvars"):
                return self.eval(func.value, scope, seen, depth + 1)
            if attr in ("abspath", "fspath", "normpath"):
                return self.eval(node.args[0], scope, seen, depth + 1) if node.args else None
            if attr == "joinpath":
                base = self.eval(func.value, scope, seen, depth + 1)
                segs = self._call_args_segments(node.args, scope, seen, depth)
                if base is None or segs is None:
                    return None
                return _append_segments(base, segs)
            if attr == "get":
                if node.args:
                    return self._key_val(_str_const(node.args[0]))
                return None
            if attr == "dirname" and node.args:
                return self._dirname(node.args[0], scope, seen, depth)
            if attr == "with_name" and node.args:
                base = self.eval(func.value, scope, seen, depth + 1)
                name = _str_const(node.args[0])
                if base is None or name is None:
                    return None
                return _append_segments(replace(base, segments=base.segments[:-1]), (name,))
        return None

    def _dirname(self, arg: ast.AST, scope: dict[str, ast.AST], seen: frozenset[str], depth: int) -> PathVal | None:
        base = self.eval(arg, scope, seen, depth + 1)
        if base is None:
            return None
        if base.file_derived:
            return replace(base, kind="file_derived", depth=(base.depth or 0) + 1)
        if base.segments:
            return replace(base, segments=base.segments[:-1])
        return replace(base, kind="unknown", depth=None)


# ---------------------------------------------------------------------------
# 文件分析 / 发现
# ---------------------------------------------------------------------------


@dataclass
class FileAnalysis:
    rel: str
    tree: ast.Module
    parents: dict[int, ast.AST]
    funcs: dict[str, ast.FunctionDef]
    module_assigns: dict[str, ast.AST]
    agent_dir_names: set[str]
    #: 本地名 → **全部**候选 (本地模块 stem, 原始符号名)——多值映射，禁止 last-write-wins
    #: （两个函数复用同一别名时，覆盖会隐藏真实调用，复审 S2）
    imports_from_local: dict[str, tuple[tuple[str, str], ...]]
    #: 别名 → 本地模块 stem 候选（同样多值）
    module_aliases: dict[str, tuple[str, ...]]
    #: 从本地模块 ``import *`` 的模块 stem——调用面无法静态追踪，出现即报人工分类
    wildcard_local_imports: tuple[str, ...] = ()
    #: 同一本地名映射到多个不同来源的别名（需人工分类；不得静默取最后一个）
    multi_source_aliases: tuple[str, ...] = ()


@dataclass
class LocatorInfo:
    file: str
    locator: str
    in_script_tree: bool = False
    vals: list[PathVal] = field(default_factory=list)
    #: 返回值位置上的路径事实 (行号, 值, 是否在本函数 return 表达式里直接写死路径)
    returns: list[tuple[int, PathVal, bool]] = field(default_factory=list)
    family_keys: set[tuple[str, str]] = field(default_factory=set)  # (family, 资源 param/env 键)
    tool_env_keys: set[str] = field(default_factory=set)
    anchor_keys: set[str] = field(default_factory=set)  # 契约锚显式登记的 param/env 键
    declared_other_keys: set[str] = field(default_factory=set)  # 契约已登记（project/缓存等）的键
    undeclared_like_keys: set[str] = field(default_factory=set)  # 形如资源/工具根但未登记
    file_name_lines: list[int] = field(default_factory=list)

    def file_vals(self) -> list[PathVal]:
        return [v for v in self.vals if v.file_derived]

    def agent_vals(self) -> list[PathVal]:
        return [v for v in self.vals if v.kind == "agent_dir"]

    def is_candidate(self) -> bool:
        if self.file_vals() or self.agent_vals():
            return True
        if self.in_script_tree:
            return bool(self.family_keys or self.tool_env_keys or self.anchor_keys or self.undeclared_like_keys)
        return bool(self.anchor_keys)  # 额外扫描面：只认契约锚显式登记的键读取

    def fmt(self) -> str:
        return f"{self.file}:{self.locator}"


def _build_parents(tree: ast.AST) -> dict[int, ast.AST]:
    parents: dict[int, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[id(child)] = node
    return parents


def _analyze_file(repo: Path, rel: str) -> FileAnalysis:
    path = repo / rel
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
    parents = _build_parents(tree)
    funcs: dict[str, ast.FunctionDef] = {}
    module_assigns: dict[str, ast.AST] = {}
    agent_dir_names: set[str] = set()
    local_stems = {p.stem for p in path.parent.glob("*.py")}
    collect_from: dict[str, set[tuple[str, str]]] = {}
    collect_aliases: dict[str, set[str]] = {}
    wildcard: list[str] = []
    for node in ast.walk(tree):
        # 全树收集（含函数体内）：config.AGENT_DIR 惰性导入，以及同族 Import/ImportFrom。
        # 函数内 `from _lib import x as rd` 若不进索引，调用将整个从可达性图消失（复审 R2）；
        # 同名多来源必须保留**全部**候选（复审 S2）——单值覆盖会把真实调用隐藏。
        if isinstance(node, ast.ImportFrom):
            if node.module == "config":
                for alias in node.names:
                    if alias.name == "AGENT_DIR":
                        agent_dir_names.add(alias.asname or alias.name)
            elif node.module in local_stems:
                for alias in node.names:
                    if alias.name == "*":
                        wildcard.append(node.module)
                    else:
                        # 保留原始符号名：`from _lib import resources_dir as rd` 仍须解析到 resources_dir
                        collect_from.setdefault(alias.asname or alias.name, set()).add((node.module, alias.name))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in local_stems:
                    collect_aliases.setdefault(alias.asname or alias.name.split(".")[0], set()).add(
                        alias.name.split(".")[0]
                    )
    imports_from_local = {name: tuple(sorted(cands)) for name, cands in collect_from.items()}
    module_aliases = {name: tuple(sorted(cands)) for name, cands in collect_aliases.items()}
    multi_source = tuple(sorted(name for name, cands in collect_from.items() if len(cands) > 1))
    for stmt in tree.body:
        if isinstance(stmt, ast.FunctionDef):
            funcs[stmt.name] = stmt
        elif isinstance(stmt, (ast.Assign, ast.AnnAssign)):
            targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
            value = stmt.value
            for target in targets:
                if isinstance(target, ast.Name) and value is not None:
                    module_assigns[target.id] = value
    return FileAnalysis(
        rel=rel,
        tree=tree,
        parents=parents,
        funcs=funcs,
        module_assigns=module_assigns,
        agent_dir_names=agent_dir_names,
        imports_from_local=imports_from_local,
        module_aliases=module_aliases,
        wildcard_local_imports=tuple(wildcard),
        multi_source_aliases=multi_source,
    )


def _enclosing_locator(node: ast.AST, fa: FileAnalysis) -> str:
    cur: ast.AST = node
    while id(cur) in fa.parents:
        parent = fa.parents[id(cur)]
        if isinstance(parent, ast.FunctionDef):
            grand = fa.parents.get(id(parent))
            if isinstance(grand, ast.Module):
                return parent.name
        cur = parent
        if isinstance(cur, ast.Module):
            break
    # 模块级：找最近的赋值目标符号
    cur = node
    while id(cur) in fa.parents:
        parent = fa.parents[id(cur)]
        if isinstance(parent, (ast.Assign, ast.AnnAssign)) and isinstance(fa.parents.get(id(parent)), ast.Module):
            targets = parent.targets if isinstance(parent, ast.Assign) else [parent.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    return target.id
        cur = parent
    return "<module>"


def _scope_of(node: ast.FunctionDef) -> dict[str, ast.AST]:
    scope: dict[str, ast.AST] = {}
    for sub in ast.walk(node):
        if isinstance(sub, ast.Assign) and sub.value is not None:
            for target in sub.targets:
                if isinstance(target, ast.Name):
                    scope[target.id] = sub.value
        elif isinstance(sub, ast.AnnAssign) and sub.value is not None and isinstance(sub.target, ast.Name):
            scope[sub.target.id] = sub.value
    return scope


def _module_scope(fa: FileAnalysis) -> dict[str, ast.AST]:
    return dict(fa.module_assigns)


def _collect_key_reads(fa: FileAnalysis, contract: dict) -> list[tuple[str, str, str]]:
    """(locator, raw_key, channel)——channel ∈ {env, param}；只收 AST 读取（非文本）。"""
    out: list[tuple[str, str, str]] = []
    for node in ast.walk(fa.tree):
        locator = _enclosing_locator(node, fa)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get":
            key = _str_const(node.args[0]) if node.args else None
            if key is None:
                continue
            if isinstance(node.func.value, ast.Attribute) and node.func.value.attr == "environ":
                out.append((locator, key, "env"))
            else:
                out.append((locator, key, "both"))
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "env":
            key = _str_const(node.args[0]) if node.args else None
            if key is not None:
                out.append((locator, key, "env"))
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "param_or_env" and len(node.args) >= 3:
            pkey, ekey = _str_const(node.args[1]), _str_const(node.args[2])
            if pkey is not None:
                out.append((locator, pkey, "param"))
            if ekey is not None:
                out.append((locator, ekey, "env"))
        elif isinstance(node, ast.Subscript):
            key = _str_const(node.slice)
            if key is None:
                continue
            if isinstance(node.value, ast.Attribute) and node.value.attr == "environ":
                out.append((locator, key, "env"))
            else:
                out.append((locator, key, "both"))
    return out


@dataclass
class Discovery:
    files: list[str]
    families: set[str]
    locators: dict[tuple[str, str], LocatorInfo]
    unresolved: list[str]
    #: 文件级静态风险（如同一别名多来源）——出现即人工分类，不得静默放行
    hazards: list[str] = field(default_factory=list)

    @property
    def candidates(self) -> dict[tuple[str, str], LocatorInfo]:
        return {k: v for k, v in self.locators.items() if v.is_candidate()}


def _declared_keys(contract: dict) -> tuple[set[str], set[str]]:
    param_keys: set[str] = set()
    env_keys: set[str] = set()
    for fam in (contract.get("families") or {}).values():
        if fam.get("param_key"):
            param_keys.add(fam["param_key"])
        if fam.get("env_key"):
            env_keys.add(fam["env_key"])
        if fam.get("project_key"):
            param_keys.add(fam["project_key"])
        if fam.get("project_env_key"):
            env_keys.add(fam["project_env_key"])
    for binding in contract.get("tool_bindings") or []:
        if binding.get("env_key"):
            env_keys.add(binding["env_key"])
    for anchor in contract.get("anchors") or []:
        env_keys.update(anchor.get("env_keys") or [])
        param_keys.update(anchor.get("param_keys") or [])
        if anchor.get("env_key"):
            env_keys.add(anchor["env_key"])
        if anchor.get("param_key"):
            param_keys.add(anchor["param_key"])
    authority = contract.get("authority") or {}
    env_keys.update(authority.get("env_keys") or [])
    return param_keys, env_keys


def _analyze_locators(fa: FileAnalysis, contract: dict, param_keys: set[str], env_keys: set[str]) -> list[LocatorInfo]:
    """独立发现：函数级与模块级符号的路径/键读取事实。"""
    infos: dict[str, LocatorInfo] = {}

    def info(name: str) -> LocatorInfo:
        if name not in infos:
            infos[name] = LocatorInfo(file=fa.rel, locator=name, in_script_tree=fa.rel.startswith(SCRIPTS_ROOT_REL + "/"))
        return infos[name]

    evaluator = _Evaluator(fa, param_keys, env_keys)

    def evaluate_into(locator: str, nodes: list[ast.AST], scope: dict[str, ast.AST]) -> None:
        target = info(locator)
        for node in nodes:
            val = evaluator.eval(node, scope)
            if val is not None:
                target.vals.append(val)

    def walk_locator_body(locator: str, scope: dict[str, ast.AST], body: list[ast.stmt]) -> None:
        values: list[ast.AST] = []
        returns: list[ast.Return] = []
        for sub in ast.walk(ast.Module(body=body, type_ignores=[])):
            if isinstance(sub, ast.Assign) and sub.value is not None:
                values.append(sub.value)
            elif isinstance(sub, ast.AnnAssign) and sub.value is not None:
                values.append(sub.value)
            elif isinstance(sub, ast.Return) and sub.value is not None:
                values.append(sub.value)
                returns.append(sub)
        evaluate_into(locator, values, scope)
        target = info(locator)
        for ret in returns:
            val = evaluator.eval(ret.value, scope)
            if val is not None:
                target.returns.append((ret.lineno, val, _return_has_direct_literal_path(ret.value)))

    for name, fn in fa.funcs.items():
        walk_locator_body(name, _scope_of(fn), fn.body)

    for sym, value in fa.module_assigns.items():
        evaluate_into(sym, [value], _module_scope(fa))

    # family / tool 键读取（AST 读取点，非文本）；只有资源 param/env 键参与候选判定，
    # project 键与缓存等键归 declared_other_keys（供 kind 校验，不做候选面扩张）。
    families = contract.get("families") or {}
    key_to_families: dict[str, list[str]] = {}
    for fam_name, fam in families.items():
        for key in ("param_key", "env_key"):
            if fam.get(key):
                key_to_families.setdefault(fam[key], []).append(fam_name)
    tool_env = {b.get("env_key") for b in (contract.get("tool_bindings") or []) if b.get("env_key")}
    anchor_keys: set[str] = set()
    for anchor in contract.get("anchors") or []:
        anchor_keys.update(anchor.get("env_keys") or [])
        anchor_keys.update(anchor.get("param_keys") or [])
        if anchor.get("env_key"):
            anchor_keys.add(anchor["env_key"])
        if anchor.get("param_key"):
            anchor_keys.add(anchor["param_key"])
    project_keys = {
        fam.get(key)
        for fam in families.values()
        for key in ("project_key", "project_env_key")
        if fam.get(key)
    }
    for locator, key, _channel in _collect_key_reads(fa, contract):
        target = info(locator)
        if key in key_to_families:
            for fam_name in key_to_families[key]:
                target.family_keys.add((fam_name, key))
        elif key in tool_env:
            target.tool_env_keys.add(key)
        elif key in anchor_keys:
            target.anchor_keys.add(key)
        elif key in project_keys or key in env_keys or key in param_keys:
            target.declared_other_keys.add(key)
        elif RESOURCE_KEY_RE.search(key):
            target.undeclared_like_keys.add(key)

    # __file__ 出现点（未被求值覆盖者 = 无法解析，需人工分类）
    for node in ast.walk(fa.tree):
        if isinstance(node, ast.Name) and node.id == "__file__":
            info(_enclosing_locator(node, fa)).file_name_lines.append(node.lineno)

    return list(infos.values())


def discover(repo: Path, contract: dict) -> Discovery:
    files: list[str] = []
    for rel in sorted(tracked_files(repo, SCRIPTS_ROOT_REL)):
        if rel.endswith(".py"):
            files.append(f"{SCRIPTS_ROOT_REL}/{rel}")
    families: set[str] = set()
    for rel in files:
        family = _family_of(rel)
        if family:
            families.add(family)
    for entry in contract.get("extra_scan_files") or []:
        path = entry.get("path", "")
        if entry.get("language", "python") != "python":
            continue
        if (repo / path).is_file():
            files.append(path)
    param_keys, env_keys = _declared_keys(contract)
    locators: dict[tuple[str, str], LocatorInfo] = {}
    unresolved: list[str] = []
    hazards: list[str] = []
    for rel in files:
        fa = _analyze_file(repo, rel)
        if fa.multi_source_aliases:
            hazards.append(
                f"{rel}: 同一别名多来源 {list(fa.multi_source_aliases)}——需人工分类"
                "（不得 last-write-wins 静默取最后一个）"
            )
        for info in _analyze_locators(fa, contract, param_keys, env_keys):
            locators[(rel, info.locator)] = info
            if info.file_name_lines and not info.file_vals():
                lines = ",".join(str(n) for n in sorted(set(info.file_name_lines)))
                unresolved.append(
                    f"{info.fmt()}（__file__ @L{lines}）——表达式无法解析为已知形态，需人工分类，"
                    "不得静默判绿或判 dead"
                )
    return Discovery(files=files, families=families, locators=locators, unresolved=unresolved, hazards=hazards)


# ---------------------------------------------------------------------------
# 调用可达性（legacy 判据）
# ---------------------------------------------------------------------------


def _module_level_calls(tree: ast.Module) -> list[ast.Call]:
    calls: list[ast.Call] = []
    for stmt in tree.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Import, ast.ImportFrom)):
            continue
        for node in ast.walk(stmt):
            if isinstance(node, ast.Call):
                calls.append(node)
    return calls


def _callee(node: ast.Call, fa: FileAnalysis, file_set: dict[str, FileAnalysis]) -> list[tuple[str, str]]:
    """把一次调用解析为**全部**可解析的 (file, func) 候选；跨文件仅限同族本地模块。

    - ``from _lib import x as alias`` 解析到原始符号名（别名不得让调用消失）；
    - 同名多来源时返回全部候选（保守超集），不取最后一个（复审 S2）。
    """
    out: list[tuple[str, str]] = []
    func = node.func
    if isinstance(func, ast.Name):
        name = func.id
        if name in fa.funcs:
            out.append((fa.rel, name))
        for stem, original in fa.imports_from_local.get(name, ()):
            mod_rel = f"{Path(fa.rel).parent.as_posix()}/{stem}.py"
            if mod_rel in file_set and original in file_set[mod_rel].funcs:
                out.append((mod_rel, original))
        return out
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        alias = func.value.id
        for stem in fa.module_aliases.get(alias, ()):
            mod_rel = f"{Path(fa.rel).parent.as_posix()}/{stem}.py"
            if mod_rel in file_set and func.attr in file_set[mod_rel].funcs:
                out.append((mod_rel, func.attr))
    return out


def build_reachable(repo: Path, family: str) -> set[tuple[str, str]]:
    """从族入口模块级代码出发的可达函数集合（file 相对仓库根的路径）。"""
    tree_rel = f"{SCRIPTS_ROOT_REL}/{family}"
    py_files = [f"{tree_rel}/{name}" for name in sorted(tracked_files(repo, tree_rel)) if name.endswith(".py")]
    entries = [f for f in py_files if not Path(f).name.startswith("_")]
    if not entries:
        return set()
    file_set: dict[str, FileAnalysis] = {}
    for rel in py_files:
        file_set[rel] = _analyze_file(repo, rel)
    entry = sorted(entries)[0]
    work: list[tuple[str, str]] = []
    reachable: set[tuple[str, str]] = set()
    for call in _module_level_calls(file_set[entry].tree):
        work.extend(_callee(call, file_set[entry], file_set))
    while work:
        item = work.pop()
        if item in reachable:
            continue
        reachable.add(item)
        rel, name = item
        fa = file_set.get(rel)
        if fa is None:
            continue
        fn = fa.funcs.get(name)
        if fn is None:
            continue
        for call in (c for c in ast.walk(fn) if isinstance(c, ast.Call)):
            for resolved in _callee(call, fa, file_set):
                if resolved not in reachable:
                    work.append(resolved)
    return reachable


def _exported_symbols(fa: FileAnalysis) -> set[str]:
    out: set[str] = set()
    for stmt in fa.tree.body:
        if isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                if isinstance(target, ast.Name) and target.id == "__all__" and isinstance(stmt.value, (ast.List, ast.Tuple)):
                    for elt in stmt.value.elts:
                        name = _str_const(elt)
                        if name:
                            out.add(name)
    return out


def _referenced_in_file(fa: FileAnalysis, symbol: str) -> bool:
    for node in ast.walk(fa.tree):
        if isinstance(node, ast.Name) and node.id == symbol and not isinstance(node.ctx, ast.Store):
            return True
        if isinstance(node, ast.Attribute) and node.attr == symbol:
            return True
    return False


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------


@dataclass
class Analysis:
    errors: list[str]
    discovery: Discovery
    anchors: list[dict]
    legacy_count: int
    stats: dict[str, int]


def _lt(items: list[str]) -> str:
    return "；".join(items)


def _find_call_nodes(fn: ast.FunctionDef) -> list[ast.Call]:
    return [node for node in ast.walk(fn) if isinstance(node, ast.Call)]


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _is_lazy_guarded(call: ast.Call, fn: ast.FunctionDef, fa: FileAnalysis, key_read_vars: set[str]) -> bool:
    """默认锚调用是否惰性：位于「override 变量非空」守卫内 / Or 右操作数 / 条件表达式分支。

    嵌在其它调用参数里的默认锚调用（如 ``env(KEY, str(_default_resources_root()))``）
    一律视为急切——override 分支不得依赖默认锚导入（C1 第 3 条）。
    """
    cur: ast.AST = call
    while id(cur) in fa.parents:
        parent = fa.parents[id(cur)]
        if isinstance(parent, ast.Call) and parent is not call:
            return False
        if isinstance(parent, ast.BoolOp) and isinstance(parent.op, ast.Or) and any(cur is v for v in parent.values[1:]):
            return True
        if isinstance(parent, ast.IfExp) and (cur is parent.body or cur is parent.orelse):
            return True
        if isinstance(parent, ast.If):
            test_names = {n.id for n in ast.walk(parent.test) if isinstance(n, ast.Name)}
            if test_names & key_read_vars:
                in_body = any(cur is sub or _is_descendant(cur, sub) for sub in parent.body)
                in_else = any(cur is sub or _is_descendant(cur, sub) for sub in parent.orelse)
                if in_body or in_else:
                    return True
        if isinstance(parent, (ast.FunctionDef, ast.Module)):
            break
        cur = parent
    return False


def _is_descendant(node: ast.AST, candidate: ast.AST) -> bool:
    return any(sub is node for sub in ast.walk(candidate))


def _key_read_vars(fn: ast.FunctionDef, keys: set[str], fa: FileAnalysis, param_keys: set[str], env_keys: set[str]) -> set[str]:
    """被 param/env 键读取赋值的局部变量名（用于惰性守卫识别）。"""
    out: set[str] = set()
    for sub in ast.walk(fn):
        if isinstance(sub, ast.Assign) and sub.value is not None:
            found = {node for node in ast.walk(sub.value)}
            for key in keys | param_keys | env_keys:
                if any(isinstance(n, ast.Constant) and n.value == key for n in found):
                    for target in sub.targets:
                        if isinstance(target, ast.Name):
                            out.add(target.id)
    return out


def _validate_reachability(anchor: dict, repo: Path, discovery: Discovery, errors: list[str]) -> None:
    file_rel = anchor["file"]
    family = _family_of(file_rel)
    if family is None:
        errors.append(f"{anchor['id']}: legacy 条目不在族树内：{file_rel}")
        return
    reachable = build_reachable(repo, family)
    for sym in anchor["locators"]:
        key = (file_rel, sym["name"])
        if key in reachable:
            errors.append(
                f"{anchor['id']}: legacy helper {file_rel}:{sym['name']} 现可从族入口到达——"
                "撤销豁免，按 C1 复核并重分类（#3601 §7.3）"
            )
    fa = None
    try:
        fa = _analyze_file(repo, file_rel)
    except (OSError, SyntaxError):
        pass
    if fa is not None:
        exported = _exported_symbols(fa)
        for sym in anchor["locators"]:
            if sym["name"] in exported:
                errors.append(f"{anchor['id']}: legacy helper {file_rel}:{sym['name']} 被 __all__ 导出——豁免作废")
        entry_rel = _entry_of(file_rel)
        if entry_rel and (repo / entry_rel).is_file():
            entry = _analyze_file(repo, entry_rel)
            for sym in anchor["locators"]:
                if _referenced_in_file(entry, sym["name"]):
                    errors.append(
                        f"{anchor['id']}: 族入口 {entry_rel} 引用 legacy helper {sym['name']}——"
                        "调用路径已存在，豁免作废"
                    )
                # 别名导入路径同样作废豁免（`from _lib import x as y`）
                if _entry_imports_symbol(entry, Path(file_rel).stem, sym["name"]):
                    errors.append(
                        f"{anchor['id']}: 族入口 {entry_rel} 以别名导入 legacy helper {sym['name']}——"
                        "导入路径已存在，豁免作废"
                    )
            if any(stem == Path(file_rel).stem for stem in entry.wildcard_local_imports):
                errors.append(
                    f"{anchor['id']}: 族入口 {entry_rel} 对 {Path(file_rel).stem} 使用 import *——"
                    "调用面无法静态追踪，需人工分类/禁止（不得静默判 green）"
                )
        # getattr 动态引用（跨族文件扫描）
        for other_rel in _family_py_files(repo, family):
            try:
                other = _analyze_file(repo, other_rel)
            except (OSError, SyntaxError):
                continue
            for node in ast.walk(other.tree):
                hit = _getattr_local_symbol(other, node)
                if hit and hit[0] in {s["name"] for s in anchor["locators"]}:
                    errors.append(
                        f"{anchor['id']}: {other_rel}:{hit[0]} 被 getattr 动态引用（L{node.lineno}）——"
                        "调用路径无法静态追踪，豁免作废"
                    )


def _entry_imports_symbol(entry: FileAnalysis, module_stem: str, symbol: str) -> bool:
    """入口是否以任意本地名（含别名）导入了目标符号。"""
    return any(
        stem == module_stem and original == symbol
        for cands in entry.imports_from_local.values()
        for stem, original in cands
    )


def _getattr_local_symbol(fa: FileAnalysis, node: ast.AST) -> tuple[str, str] | None:
    """``getattr(<本地模块别名>, "<符号>")`` → (符号, 模块别名)。"""
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "getattr"):
        return None
    if len(node.args) < 2 or not isinstance(node.args[0], ast.Name):
        return None
    alias = node.args[0].id
    if alias not in fa.module_aliases:
        return None
    literal = _str_const(node.args[1])
    if literal is None:
        return None
    return (literal, alias)


def _family_py_files(repo: Path, family: str) -> list[str]:
    tree_rel = f"{SCRIPTS_ROOT_REL}/{family}"
    return [f"{tree_rel}/{name}" for name in sorted(tracked_files(repo, tree_rel)) if name.endswith(".py")]


def _family_of(file_rel: str) -> str | None:
    prefix = SCRIPTS_ROOT_REL + "/"
    if not file_rel.startswith(prefix):
        return None
    parts = file_rel[len(prefix):].split("/")
    return parts[0] if len(parts) >= 2 else None


def _entry_of(file_rel: str) -> str | None:
    family = _family_of(file_rel)
    if family is None:
        return None
    return f"{SCRIPTS_ROOT_REL}/{family}/{family}.py"


def _validate_locator_exists(anchor: dict, repo: Path, errors: list[str]) -> None:
    file_rel = anchor["file"]
    path = repo / file_rel
    if not path.is_file():
        errors.append(f"{anchor['id']}: 源文件缺失：{file_rel}")
        return
    if anchor.get("kind") == KIND_INSTALL_LAYOUT:
        return
    try:
        fa = _analyze_file(repo, file_rel)
    except SyntaxError as exc:
        errors.append(f"{anchor['id']}: 源文件无法解析：{file_rel}: {exc}")
        return
    for sym in anchor["locators"]:
        name = sym["name"]
        if sym.get("type", "function") == "function" and name not in fa.funcs:
            errors.append(f"{anchor['id']}: 声明函数缺失：{file_rel}:{name}")
        if sym.get("type") == "symbol" and name not in fa.module_assigns:
            errors.append(f"{anchor['id']}: 声明符号缺失：{file_rel}:{name}")


def _locator_info(discovery: Discovery, file_rel: str, name: str) -> LocatorInfo | None:
    return discovery.locators.get((file_rel, name))


def _root_exact(val: PathVal, subdir: str) -> bool:
    """C1 默认根：相对 Agent authority 的**全部**片段恰为 ``("resources", subdir)``。

    前缀（``AGENT_DIR/unexpected/resources/mtbf``）与后缀（``…/mtbf/unexpected``）
    都不允许——只在最后两段上比对会把根位置判错（复审 S1）。
    """
    return val.segments == ("resources", subdir)


def _contains_ordered_pair(val: PathVal, subdir: str) -> bool:
    """``("resources", subdir)`` 以相邻有序对出现（root 可作为更长路径的前缀）。"""
    segments = val.segments
    return any(segments[i] == "resources" and segments[i + 1] == subdir for i in range(len(segments) - 1))


def _host_root_shape_ok(anchor: dict, contract: dict, discovery: Discovery) -> tuple[list[str], list[str]]:
    """返回 (errors, root_locators)：复核默认资源根表达式的形态。"""
    fam_name = anchor.get("family")
    fam = (contract.get("families") or {}).get(fam_name)
    errors: list[str] = []
    if not fam:
        return [f"{anchor['id']}: 契约缺 family 定义：{fam_name!r}"], []
    subdir = fam.get("subdir")
    roots: list[str] = []
    for sym in anchor["locators"]:
        if sym.get("type", "function") != "function":
            continue
        info = _locator_info(discovery, anchor["file"], sym["name"])
        if info is None:
            continue
        shaped = [v for v in info.vals if "resources" in v.segments and subdir in v.segments]
        if not shaped:
            continue
        roots.append(sym["name"])
        file_vals = [v for v in shaped if v.file_derived]
        agent_vals = [v for v in shaped if v.kind == "agent_dir"]
        bad_suffix = sorted({v.segments for v in shaped if not _root_exact(v, subdir)})
        if anchor.get("authority") == AUTHORITY_AGENT_DIR:
            if file_vals:
                errors.append(
                    f"{anchor['id']}: {anchor['file']}:{sym['name']} 仍按 __file__ 深度推导资源根"
                    f"（深度={[(v.depth, v.segments) for v in file_vals]}）——C1 要求 config.AGENT_DIR"
                )
            if not agent_vals:
                errors.append(
                    f"{anchor['id']}: {anchor['file']}:{sym['name']} 未复现 config.AGENT_DIR/resources/{subdir} 形态"
                )
            if bad_suffix:
                errors.append(
                    f"{anchor['id']}: {anchor['file']}:{sym['name']} 资源根片段必须恰为 "
                    f"('resources', {subdir!r})（前/后缀均不允许）：{bad_suffix}"
                )
        elif not file_vals:
            errors.append(
                f"{anchor['id']}: {anchor['file']}:{sym['name']} 声明为 depth_guess，但未复现 __file__ 深度资源根"
            )
    if not roots:
        errors.append(f"{anchor['id']}: 未能复现默认资源根表达式——需人工分类，不得静默判绿")
    return errors, roots


def _validate_host_local_root(anchor: dict, contract: dict, repo: Path, discovery: Discovery, errors: list[str]) -> None:
    shape_errors, roots = _host_root_shape_ok(anchor, contract, discovery)
    errors.extend(shape_errors)
    fam_name = anchor.get("family")
    fam = (contract.get("families") or {}).get(fam_name) or {}
    if anchor.get("authority") == AUTHORITY_DEPTH_GUESS and not anchor.get("legacy"):
        errors.append(f"{anchor['id']}: 可调用的 depth_guess 资源根（非 legacy）——C1 违例")
    if anchor.get("legacy"):
        # legacy 只判可达性/导出；C1 全量校验在修复或重分类时适用（#3601 §1.4 尾部）
        _validate_reachability(anchor, repo, discovery, errors)
        return

    fa = _analyze_file(repo, anchor["file"])
    for root in roots:
        fn = fa.funcs.get(root)
        if fn is None:
            continue
        if _function_uses_agent_dir_symbol(fn, fa) and not _function_imports_agent_dir(fn):
            errors.append(
                f"{anchor['id']}: {anchor['file']}:{root} 使用 AGENT_DIR 但未在 fallback 分支惰性导入 config"
            )
    consumers: list[str] = []
    for sym in anchor["locators"]:
        info = _locator_info(discovery, anchor["file"], sym["name"])
        if info is not None and any(f == fam_name for f, _ in info.family_keys):
            consumers.append(sym["name"])
    if not consumers:
        errors.append(f"{anchor['id']}: 未发现读取 {fam_name} 资源键的消费者函数（override 通道缺失）")
    param_key, env_key = fam.get("param_key"), fam.get("env_key")
    # 双通道 + 优先级：显式 param 与 env 必须成对出现（否则删掉 param 会静默丢 override），
    # 且选择顺序必须可静态证明是 param 先行（含经局部变量转写的形态）；
    # 证明不了就要求人工分类（复审 R3：不得把「未识别出 env-first」当作已证明 param-first）。
    module_scope = _module_scope(fa)
    channel_ok = False
    unproven: list[str] = []
    for consumer in consumers:
        info = _locator_info(discovery, anchor["file"], consumer)
        fam_keys = {k for f, k in (info.family_keys if info else set()) if f == fam_name}
        if param_key not in fam_keys or env_key not in fam_keys:
            continue
        channel_ok = True
        fn = fa.funcs.get(consumer)
        if fn is None:
            continue
        status, detail = _override_order(fn, param_key, env_key, module_scope)
        if status == "env-first":
            errors.append(
                f"{anchor['id']}: {anchor['file']}:{consumer} 的 override 选择链 env 先于 param"
                f"（{detail}）——显式参数优先语义被反转"
            )
        elif status == "param-first":
            continue
        elif _has_param_or_env_call(fn, param_key, env_key):
            continue
        elif status == "ambiguous":
            errors.append(
                f"{anchor['id']}: {anchor['file']}:{consumer} 存在多条候选 override 链（{detail}），"
                "无法关联实际消费值——需人工分类"
            )
        else:
            unproven.append(consumer)
    if not channel_ok:
        errors.append(
            f"{anchor['id']}: 无消费者同时读取 param {param_key!r} 与 env {env_key!r}——"
            "显式参数 override 通道被删除（C1 第 3 条）"
        )
    for consumer in unproven:
        errors.append(
            f"{anchor['id']}: {anchor['file']}:{consumer} 的 override 选择顺序无法静态证明——"
            "需人工分类（不得按 param-first 放行）"
        )
    lazy_root_calls = 0
    project_ok = False
    for consumer in consumers:
        fn = fa.funcs.get(consumer)
        if fn is None:
            continue
        info = _locator_info(discovery, anchor["file"], consumer)
        if info is not None:
            bad_shaped = [v for v in info.vals if v.file_derived and "resources" in v.segments]
            if bad_shaped:
                errors.append(
                    f"{anchor['id']}: 消费者 {anchor['file']}:{consumer} 内出现 __file__ 深度资源路径——C1 违例"
                )
        key_vars = _key_read_vars(fn, {k for k in (param_key, env_key) if k}, fa, set(), set())
        for call in _find_call_nodes(fn):
            name = _call_name(call)
            if name not in roots:
                continue
            if _is_lazy_guarded(call, fn, fa, key_vars):
                lazy_root_calls += 1
                if _calls_project_component(fn, fam):
                    project_ok = True
            else:
                errors.append(
                    f"{anchor['id']}: {anchor['file']}:{consumer} 对默认锚 {name}() 的调用被急切求值——"
                    "override 分支不得依赖默认锚导入"
                )
    if roots and lazy_root_calls == 0:
        errors.append(f"{anchor['id']}: 默认锚 {roots} 未被任何消费者惰性消费")
    elif roots and not project_ok:
        errors.append(f"{anchor['id']}: 资源根消费者缺少 project 后缀读取（{fam_name}）")
    # 返回值一致性（#3615 复核 P2-2）：默认资源根必须出现在**返回位置**且返回值全部符合
    # authority——「保留正确赋值却返回错误路径」「增加错误目录返回」不得静默通过；
    # 非 root 定位点（消费者）也不得返回硬编码路径。
    subdir = fam.get("subdir")
    for sym in anchor["locators"]:
        if sym.get("type", "function") != "function":
            continue
        info = _locator_info(discovery, anchor["file"], sym["name"])
        if info is None:
            continue
        fn = fa.funcs.get(sym["name"])
        constructing = sym["name"] in roots and fn is not None and _function_uses_agent_dir_symbol(fn, fa)
        if constructing:
            returned_shaped = [
                (line, v)
                for line, v, _direct in info.returns
                if v.kind == "agent_dir" and _root_exact(v, subdir)
            ]
            if not returned_shaped:
                contains = [
                    (line, v)
                    for line, v, _direct in info.returns
                    if v.kind == "agent_dir" and "resources" in v.segments and subdir in v.segments
                ]
                if contains:
                    errors.append(
                        f"{anchor['id']}: {anchor['file']}:{sym['name']} 返回位置的资源根不是 "
                        f"('resources', {subdir!r}) 精确形态：{[(line, v.segments) for line, v in contains]}"
                    )
                else:
                    errors.append(
                        f"{anchor['id']}: {anchor['file']}:{sym['name']} 的资源根只出现在赋值、未出现在返回位置——"
                        "authority 必须以返回值提供"
                    )
            for line, val, _direct in info.returns:
                if val.kind == "agent_dir" and _root_exact(val, subdir):
                    continue
                errors.append(
                    f"{anchor['id']}: {anchor['file']}:{sym['name']} L{line} 的返回值不符合 authority"
                    f"（{val.kind}/{val.segments}）——默认资源根不得返回其它路径"
                )
        else:
            for line, val, direct in info.returns:
                if val.kind == "literal" and direct:
                    errors.append(
                        f"{anchor['id']}: {anchor['file']}:{sym['name']} L{line} 返回硬编码路径"
                        f"{val.segments!r}——authority 消费链不得返回字面量根"
                    )


def _return_has_direct_literal_path(node: ast.AST) -> bool:
    """return 表达式里是否直接写死路径字面量（``Path("/tmp/x")`` / ``"/opt/x"``）。

    用于区分「本函数自己返回字面量」（红）与「字面量经 helper 返回值流入」（由 helper
    的返回值一致性负责报出，不重复归因给消费者）。
    """
    for sub in ast.walk(node):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            value = sub.value
            if value.startswith("/") or value.startswith("./") or value.startswith("../"):
                return True
    return False


def _name_value_at(fn: ast.FunctionDef, name: str, lineno: int) -> ast.AST | None:
    """函数内 ``lineno`` 之前对该名字的**最近一次**赋值右值（按行序，非 walk 序）。

    ``base = env(...); base = base or param`` 这类经局部变量反转优先级的写法，
    必须按赋值顺序还原引用点的实际来源，不能用「最后一次赋值」近似（复审 R3）。
    """
    best: tuple[int, ast.AST] | None = None
    for sub in ast.walk(fn):
        if isinstance(sub, ast.Assign) and sub.value is not None:
            for target in sub.targets:
                if isinstance(target, ast.Name) and target.id == name and sub.lineno < lineno:
                    if best is None or sub.lineno > best[0]:
                        best = (sub.lineno, sub.value)
        elif isinstance(sub, ast.AnnAssign) and sub.value is not None and isinstance(sub.target, ast.Name):
            if sub.target.id == name and sub.lineno < lineno and (best is None or sub.lineno > best[0]):
                best = (sub.lineno, sub.value)
    return best[1] if best is not None else None


def _key_markers(
    fn: ast.FunctionDef,
    node: ast.AST,
    module_scope: dict[str, ast.AST],
    seen: frozenset[str] = frozenset(),
) -> set[str]:
    """表达式里出现的键字面量（经局部变量/模块变量有限回溯；不解释任意调用）。"""
    keys: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            keys.add(sub.value)
        elif isinstance(sub, ast.Name) and sub.id not in seen:
            src = _name_value_at(fn, sub.id, sub.lineno) or module_scope.get(sub.id)
            if src is not None:
                keys |= _key_markers(fn, src, module_scope, seen | {sub.id})
    return keys


def _dead_assignment_value_ids(fn: ast.FunctionDef) -> set[int]:
    """被赋给「此后从未被读取」的局部变量的右值节点 id。

    ``unused = cfg.get(P) or env(E, "")`` 这类未使用链不得参与选择顺序证明（复审 S3）。
    """
    assigns: list[tuple[int, str, ast.AST]] = []
    loads: list[tuple[int, str]] = []
    for sub in ast.walk(fn):
        if isinstance(sub, ast.Assign) and sub.value is not None:
            for target in sub.targets:
                if isinstance(target, ast.Name):
                    assigns.append((sub.lineno, target.id, sub.value))
        elif isinstance(sub, ast.AnnAssign) and sub.value is not None and isinstance(sub.target, ast.Name):
            assigns.append((sub.lineno, sub.target.id, sub.value))
        elif isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load):
            loads.append((sub.lineno, sub.id))
    dead: set[int] = set()
    for line, name, value in assigns:
        if not any(other_line > line and other_name == name for other_line, other_name in loads):
            dead.add(id(value))
    return dead


def _override_order(
    fn: ast.FunctionDef,
    param_key: str | None,
    env_key: str | None,
    module_scope: dict[str, ast.AST],
) -> tuple[str, str]:
    """实际被消费的 override 选择链顺序。

    返回 ``(status, detail)``：``param-first`` / ``env-first`` / ``ambiguous``（多条候选
    链无法关联实际消费）/ ``none``（没有含双键的链，或被完全未使用）。未使用的链不得
    作为 param-first 的证明（复审 S3）。
    """
    if not param_key or not env_key:
        return "none", ""
    dead = _dead_assignment_value_ids(fn)
    candidates: list[tuple[int, str]] = []
    for node in ast.walk(fn):
        if not (isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or)):
            continue
        if id(node) in dead:
            continue
        param_idx = env_idx = None
        for index, value in enumerate(node.values):
            keys = _key_markers(fn, value, module_scope)
            if param_key in keys and param_idx is None:
                param_idx = index
            if env_key in keys and env_idx is None:
                env_idx = index
        if param_idx is not None and env_idx is not None:
            candidates.append((node.lineno, "param-first" if param_idx < env_idx else "env-first"))
    if not candidates:
        return "none", ""
    if len(candidates) > 1:
        return "ambiguous", f"L{[line for line, _ in candidates]}"
    return candidates[0][1], f"L{candidates[0][0]}"


def _has_param_or_env_call(fn: ast.FunctionDef, param_key: str | None, env_key: str | None) -> bool:
    """``param_or_env(cfg, param_key, env_key, …)`` 形态——该 helper 语义即 param > env。"""
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "param_or_env":
            if len(node.args) >= 3 and _str_const(node.args[1]) == param_key and _str_const(node.args[2]) == env_key:
                return True
    return False


def _function_uses_agent_dir_symbol(fn: ast.FunctionDef, fa: FileAnalysis) -> bool:
    for node in ast.walk(fn):
        if isinstance(node, ast.Name) and node.id in fa.agent_dir_names:
            return True
        if isinstance(node, ast.Attribute):
            chain: list[str] = []
            cur: ast.AST = node
            while isinstance(cur, ast.Attribute):
                chain.append(cur.attr)
                cur = cur.value
            if isinstance(cur, ast.Name):
                chain.append(cur.id)
            if ".".join(reversed(chain)) == "config.AGENT_DIR":
                return True
    return False


def _function_imports_agent_dir(fn: ast.FunctionDef) -> bool:
    for node in ast.walk(fn):
        if isinstance(node, ast.ImportFrom) and node.module == "config":
            if any(alias.name == "AGENT_DIR" for alias in node.names):
                return True
        if isinstance(node, ast.Import) and any(alias.name == "config" for alias in node.names):
            return True
    return False


def _calls_project_component(fn: ast.FunctionDef, fam: dict) -> bool:
    keys = {fam.get("project_key"), fam.get("project_env_key")} - {None}
    names = {"project_name"}
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in names:
            return True
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in keys:
            return True
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "param_or_env":
            if len(node.args) >= 3 and _str_const(node.args[1]) in keys:
                return True
    return False


def _validate_package_member(anchor: dict, repo: Path, discovery: Discovery, errors: list[str]) -> None:
    member = anchor.get("member")
    file_rel = anchor["file"]
    found_member = False
    for sym in anchor["locators"]:
        info = _locator_info(discovery, file_rel, sym["name"])
        if info is None:
            continue
        for val in info.file_vals():
            if val.segments and val.segments[-1] == member:
                found_member = True
                if val.depth != 1:
                    errors.append(
                        f"{anchor['id']}: {file_rel}:{sym['name']} 成员 {member} 解析深度 {val.depth}"
                        "（同级包成员应为 1）——包布局下会落错位"
                    )
    if not found_member:
        errors.append(f"{anchor['id']}: 未能复现包成员表达式 {member}——需人工分类")
    if anchor.get("legacy"):
        _validate_reachability(anchor, repo, discovery, errors)
        return
    family = _family_of(file_rel)
    if family is None:
        errors.append(f"{anchor['id']}: 包成员锚不在族树内：{file_rel}")
        return
    members = tracked_files(repo, f"{SCRIPTS_ROOT_REL}/{family}")
    if member not in members:
        errors.append(
            f"{anchor['id']}: 包成员缺失：{SCRIPTS_ROOT_REL}/{family}/{member}（真实入包才合法）"
        )


def _validate_tool_root(anchor: dict, contract: dict, repo: Path, discovery: Discovery, errors: list[str]) -> None:
    env_key = anchor.get("env_key")
    tool_family = anchor.get("tool_family")
    fam_name = anchor.get("family")
    bindings = contract.get("tool_bindings") or []
    binding = next(
        (b for b in bindings if b.get("env_key") == env_key and b.get("tool_family") == tool_family), None
    )
    if binding is None:
        errors.append(f"{anchor['id']}: 契约 tool_bindings 缺 {tool_family}@{env_key} 绑定声明")
        return
    if fam_name and fam_name not in (binding.get("consumer_families") or []):
        errors.append(f"{anchor['id']}: 契约未把 {fam_name} 列为 {tool_family} 的消费族")
    # 消费族声明（单源 tool_requirements）
    req_mod = load_tool_requirements_module(repo)
    tree_rel = f"{SCRIPTS_ROOT_REL}/{fam_name}"
    try:
        requirements = req_mod.load_requirements(repo / tree_rel)
    except req_mod.RequirementError as exc:
        errors.append(f"{anchor['id']}: {tree_rel}/capabilities.json requires_tools 声明非法——{exc}")
        requirements = []
    match = [r for r in requirements if r.name == tool_family]
    if not match:
        errors.append(
            f"{anchor['id']}: {tree_rel}/capabilities.json 缺 requires_tools.{tool_family}——"
            "工具根失去 fail-closed 绑定（#3601 §1.2 第 5 条）"
        )
        return
    requirement = match[0]
    if requirement.env != env_key:
        errors.append(
            f"{anchor['id']}: requires_tools.{tool_family}.env={requirement.env!r} ≠ 消费键 {env_key!r}——错误 env"
        )
    manifest = load_manifest(repo)
    tool = (manifest.get("tools") or {}).get(tool_family) or {}
    if tool.get("kind") != "tool":
        errors.append(f"{anchor['id']}: requires_tools.{tool_family} 不是 kind=tool 族")
        return
    entry = next((v for v in tool.get("versions") or [] if v.get("version") == requirement.version), None)
    if entry is None:
        errors.append(f"{anchor['id']}: requires_tools.{tool_family}@{requirement.version} 未登记进 tool_manifest.json")
    elif entry.get("retired"):
        errors.append(f"{anchor['id']}: requires_tools.{tool_family}@{requirement.version} 已 retired——换依赖须发脚本新版本")
    # 表达式中必须真实读取注入键
    for sym in anchor["locators"]:
        info = _locator_info(discovery, anchor["file"], sym["name"])
        if info is None or not info.tool_env_keys:
            errors.append(f"{anchor['id']}: {anchor['file']}:{sym['name']} 未读取工具注入键 {env_key}")
    for sym in anchor["locators"]:
        info = _locator_info(discovery, anchor["file"], sym["name"])
        if info is not None and any(v.file_derived and "resources" in v.segments for v in info.vals):
            errors.append(
                f"{anchor['id']}: {anchor['file']}:{sym['name']} 含 __file__ 深度 resources 路径——"
                "工具根不得伪装成 host-local 资源"
            )


def _validate_agent_module(anchor: dict, contract: dict, repo: Path, discovery: Discovery, errors: list[str]) -> None:
    file_rel = anchor["file"]
    if Path(file_rel).parent.as_posix() != "backend/agent":
        errors.append(f"{anchor['id']}: agent module authority 必须直接位于 backend/agent/：{file_rel}")
    subdir = anchor.get("subdir")
    exact_root_seen = False
    for sym in anchor["locators"]:
        info = _locator_info(discovery, file_rel, sym["name"])
        if info is None:
            continue
        if sym.get("type") == "symbol":
            for val in info.vals:
                if val.file_derived and val.depth == 1 and not val.segments:
                    break
            else:
                errors.append(f"{anchor['id']}: {file_rel}:{sym['name']} 未复现 Path(__file__).resolve().parent（depth=1）")
        else:
            shaped = [v for v in info.vals if v.file_derived and v.depth == 1 and _contains_ordered_pair(v, subdir)]
            if not shaped:
                errors.append(
                    f"{anchor['id']}: {file_rel}:{sym['name']} 未复现 Agent module 资源根（depth=1 + 有序 ('resources', {subdir!r})）"
                )
            if any(v.file_derived and v.depth == 1 and _root_exact(v, subdir) for v in info.vals):
                exact_root_seen = True
            for val in info.vals:
                if val.file_derived and "resources" in val.segments and subdir in val.segments and not _contains_ordered_pair(val, subdir):
                    errors.append(
                        f"{anchor['id']}: {file_rel}:{sym['name']} 资源根片段错序：{val.segments}"
                        f"——须含相邻有序 ('resources', {subdir!r})"
                    )
            deep = [v for v in info.vals if v.file_derived and (v.depth or 0) > 1]
            if deep:
                errors.append(f"{anchor['id']}: {file_rel}:{sym['name']} 出现祖先深度推导——agent module 只允许 .parent")
    if not exact_root_seen:
        errors.append(
            f"{anchor['id']}: {file_rel} 未出现 ('resources', {subdir!r}) 精确形态的资源根构造（前后缀均不允许）"
        )
    env_key = anchor.get("env_key")
    if env_key and not _file_reads_env(repo, file_rel, env_key):
        errors.append(f"{anchor['id']}: {file_rel} 未读取 override 键 {env_key}（显式路径通道缺失）")


def _file_reads_env(repo: Path, file_rel: str, env_key: str) -> bool:
    fa = _analyze_file(repo, file_rel)
    return any(
        isinstance(node, ast.Constant) and node.value == env_key for node in ast.walk(fa.tree)
    )


def _validate_return_authority(anchor: dict, contract: dict, discovery: Discovery, errors: list[str]) -> None:
    """kind 级返回值一致性（#3615 复核 P2-2）：被返回的路径必须符合声明 authority。

    - 任何 authority 定位点不得返回硬编码字面量根；
    - agent module / base-dir / 工具根 / schema 的每个**被返回**的路径值都必须落在
      该 kind 允许的通道（显式 env/param override 或声明的文件/Agent 形态）内。
    """
    kind = anchor.get("kind")
    file_rel = anchor["file"]
    fam = (contract.get("families") or {}).get(anchor.get("family")) or {}
    subdir = anchor.get("subdir") or fam.get("subdir")
    for sym in anchor["locators"]:
        if sym.get("type", "function") != "function":
            continue
        info = _locator_info(discovery, file_rel, sym["name"])
        if info is None:
            continue
        for line, val, _direct in info.returns:
            if val.kind in ("env", "param", "override"):
                continue  # 显式 override 通道
            if val.kind == "literal":
                errors.append(
                    f"{anchor['id']}: {file_rel}:{sym['name']} L{line} 返回硬编码路径 {val.segments!r}——"
                    "authority 不得返回字面量根"
                )
                continue
            if kind == KIND_AGENT_BASE:
                if not val.file_derived:
                    errors.append(
                        f"{anchor['id']}: {file_rel}:{sym['name']} L{line} 返回值不符合 base-dir 形态（{val.kind}）"
                    )
            elif kind == KIND_TOOL_ROOT:
                if not val.file_derived:
                    errors.append(
                        f"{anchor['id']}: {file_rel}:{sym['name']} L{line} 返回值不符合工具根形态（{val.kind}）"
                    )
            elif kind == KIND_AGENT_MODULE:
                if not (val.file_derived and val.depth == 1 and subdir in val.segments):
                    errors.append(
                        f"{anchor['id']}: {file_rel}:{sym['name']} L{line} 返回值不符合 agent module 形态"
                        f"（{val.kind}/{val.depth}/{val.segments}）"
                    )
            elif kind == KIND_SCHEMA:
                if not (val.file_derived and "schemas" in val.segments):
                    errors.append(
                        f"{anchor['id']}: {file_rel}:{sym['name']} L{line} 返回值不符合 schema 布局（{val.kind}/{val.segments}）"
                    )


def _validate_non_resource_shape(anchor: dict, repo: Path, errors: list[str], *, forbid_resources: bool = True) -> None:
    file_rel = anchor["file"]
    fa = _analyze_file(repo, file_rel)
    for sym in anchor["locators"]:
        name = sym["name"]
        if sym.get("type", "function") == "function":
            fn = fa.funcs.get(name)
            if fn is None:
                continue
            vals = []
            for sub in ast.walk(fn):
                if isinstance(sub, ast.Return) and sub.value is not None:
                    vals.append(sub.value)
                if isinstance(sub, ast.Assign) and sub.value is not None:
                    vals.append(sub.value)
        else:
            val = fa.module_assigns.get(name)
            vals = [val] if val is not None else []
        evaluator = _Evaluator(fa, set(), set())
        for node in vals:
            pv = evaluator.eval(node, _module_scope(fa))
            if pv is None:
                continue
            if forbid_resources and "resources" in pv.segments:
                errors.append(
                    f"{anchor['id']}: {file_rel}:{name} 的路径落入 resources/ —— 分类漂移，需重新判定"
                )


def _validate_agent_base(anchor: dict, repo: Path, discovery: Discovery, errors: list[str]) -> None:
    file_rel = anchor["file"]
    if file_rel != "backend/agent/config.py":
        errors.append(f"{anchor['id']}: agent_base_dir 只允许 backend/agent/config.py，实际 {file_rel}")
    for sym in anchor["locators"]:
        if sym.get("type", "function") != "function":
            continue
        info = _locator_info(discovery, file_rel, sym["name"])
        if info is None or not info.file_vals():
            errors.append(f"{anchor['id']}: {file_rel}:{sym['name']} 未复现布局识别形态")
    # 漂移守卫：本锚的 RESOURCE_DIR/base-dir 若被四族资源根引用，host_local 锚的
    # AGENT_DIR 形态校验会同步变红（不得把遗留顶层 resources 当 C1 authority）。


def _validate_tool_cache(anchor: dict, repo: Path, discovery: Discovery, errors: list[str]) -> None:
    file_rel = anchor["file"]
    for sym in anchor["locators"]:
        info = _locator_info(discovery, file_rel, sym["name"])
        if info is None:
            continue
        if info.file_vals():
            errors.append(f"{anchor['id']}: {file_rel}:{sym['name']} 出现 __file__ 深度推导——cache 根须为显式 authority")
        if not (info.anchor_keys or _file_reads_env(repo, file_rel, "STP_TOOLS_CACHE_ROOT")):
            errors.append(f"{anchor['id']}: {file_rel}:{sym['name']} 未读取 cache authority（STP_TOOLS_CACHE_ROOT/AGENT_INSTALL_DIR）")


def _validate_import_bootstrap(anchor: dict, repo: Path, errors: list[str]) -> None:
    file_rel = anchor["file"]
    fa = _analyze_file(repo, file_rel)
    for sym in anchor["locators"]:
        name = sym["name"]
        for node in ast.walk(fa.tree):
            if not (isinstance(node, ast.Name) and node.id == name and isinstance(node.ctx, ast.Load)):
                continue
            if not _used_only_for_sys_path(node, fa):
                errors.append(
                    f"{anchor['id']}: {file_rel}:{name} 在 L{node.lineno} 被非 sys.path 消费——"
                    "import bootstrap 分类作废，需按实际用途重分类"
                )


def _used_only_for_sys_path(node: ast.AST, fa: FileAnalysis) -> bool:
    cur: ast.AST = node
    while id(cur) in fa.parents:
        parent = fa.parents[id(cur)]
        if isinstance(parent, ast.Call):
            fname = _call_name(parent)
            if fname == "str":
                cur = parent
                continue
            target = parent.func
            if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Attribute):
                if target.value.attr == "path" and target.attr in ("insert", "append", "extend"):
                    return True
                if target.value.attr == "path":
                    return False
            return False
        if isinstance(parent, ast.Compare):
            for comp in parent.comparators:
                if isinstance(comp, ast.Attribute) and _attr_tail(comp) == "sys.path":
                    return True
        if isinstance(parent, (ast.FunctionDef, ast.Module)):
            break
        cur = parent
    return False


def _attr_tail(node: ast.Attribute) -> str:
    parts: list[str] = []
    cur: ast.AST = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
    return ".".join(reversed(parts))


def validate_anchor(anchor: dict, contract: dict, repo: Path, discovery: Discovery, errors: list[str]) -> None:
    kind = anchor.get("kind")
    if kind not in KNOWN_KINDS:
        errors.append(f"{anchor['id']}: 未知分类 kind={kind!r}")
        return
    if not anchor.get("locators"):
        errors.append(f"{anchor['id']}: 声明缺 locators——不允许文件级/目录级豁免")
        return
    if anchor.get("legacy"):
        legacy = anchor["legacy"]
        for field_name in ("issue", "reason", "removal_condition"):
            if not legacy.get(field_name):
                errors.append(f"{anchor['id']}: legacy 声明缺 {field_name}")
        if not all(sym.get("type", "function") == "function" for sym in anchor["locators"]):
            errors.append(f"{anchor['id']}: legacy 豁免必须是函数级")
    _validate_locator_exists(anchor, repo, errors)
    if not (repo / anchor["file"]).exists():
        return  # 文件缺失已由 _validate_locator_exists 报出；其余判据无对象可判
    if kind == KIND_HOST_ROOT:
        _validate_host_local_root(anchor, contract, repo, discovery, errors)
    elif kind == KIND_PACKAGE_MEMBER:
        _validate_package_member(anchor, repo, discovery, errors)
    elif kind == KIND_TOOL_ROOT:
        _validate_tool_root(anchor, contract, repo, discovery, errors)
    elif kind == KIND_AGENT_MODULE:
        _validate_agent_module(anchor, contract, repo, discovery, errors)
    elif kind == KIND_AGENT_BASE:
        _validate_agent_base(anchor, repo, discovery, errors)
    elif kind == KIND_SCHEMA:
        _validate_non_resource_shape(anchor, repo, errors)
    elif kind == KIND_DEPLOY_SOURCE:
        _validate_non_resource_shape(anchor, repo, errors)
    elif kind == KIND_TOOL_CACHE:
        _validate_tool_cache(anchor, repo, discovery, errors)
    elif kind == KIND_IMPORT_BOOTSTRAP:
        _validate_import_bootstrap(anchor, repo, errors)
    elif kind == KIND_INSTALL_LAYOUT:
        pass  # 文件存在性已在上方核对（shell 不作 AST 分析）
    elif kind == KIND_EXPLICIT_OVERRIDE:
        override_reads = 0
        for sym in anchor["locators"]:
            info = _locator_info(discovery, anchor["file"], sym["name"])
            if info is not None and (info.family_keys or info.tool_env_keys or info.anchor_keys):
                override_reads += 1
            if info is not None and info.file_vals():
                errors.append(
                    f"{anchor['id']}: 显式 override 锚 {anchor['file']}:{sym['name']} 出现 __file__ 深度路径——"
                    "override 不得依赖默认锚/文件深度"
                )
        if not override_reads:
            errors.append(f"{anchor['id']}: 显式 override 锚未发现参数/env 读取")
    if kind in (
        KIND_TOOL_ROOT,
        KIND_TOOL_CACHE,
        KIND_AGENT_MODULE,
        KIND_AGENT_BASE,
        KIND_SCHEMA,
        KIND_PACKAGE_MEMBER,
    ):
        _validate_return_authority(anchor, contract, discovery, errors)


def _declared_coverage(contract: dict) -> dict[tuple[str, str], str]:
    coverage: dict[tuple[str, str], str] = {}
    authority = contract.get("authority") or {}
    if authority.get("file") and authority.get("symbol"):
        coverage[(authority["file"], authority["symbol"])] = "<authority>"
    for anchor in contract.get("anchors") or []:
        for sym in anchor.get("locators") or []:
            coverage[(anchor["file"], sym["name"])] = anchor["id"]
    return coverage


def _validate_contract_shape(contract: dict, errors: list[str]) -> None:
    if contract.get("contract_version") != 1:
        errors.append(f"契约 contract_version 必须为 1，实际 {contract.get('contract_version')!r}")
    anchors = contract.get("anchors") or []
    if not anchors:
        errors.append("契约 anchors 为空——零候选不得判绿")
    seen_ids: set[str] = set()
    for anchor in anchors:
        aid = anchor.get("id")
        if not aid or aid in seen_ids:
            errors.append(f"锚 id 缺失或重复：{aid!r}")
        seen_ids.add(aid)
        if not anchor.get("file"):
            errors.append(f"{aid}: 缺 file")
    if not (contract.get("families") or {}):
        errors.append("契约 families 为空")
    legacy = [a for a in anchors if a.get("legacy")]
    if legacy and not any(a.get("kind") == KIND_HOST_ROOT for a in legacy):
        errors.append("legacy 集合与 host-local 资源根无关——分类错位")


def _validate_authority(contract: dict, repo: Path, discovery: Discovery, errors: list[str]) -> None:
    authority = contract.get("authority") or {}
    file_rel, symbol = authority.get("file"), authority.get("symbol")
    if not file_rel or not symbol:
        errors.append("契约 authority 缺 file/symbol")
        return
    if not (repo / file_rel).is_file():
        errors.append(f"authority 源文件缺失：{file_rel}")
        return
    info = _locator_info(discovery, file_rel, symbol)
    if info is None:
        errors.append(f"authority 符号无法复现：{file_rel}:{symbol}")
        return
    want = authority.get("expect_depth")
    ok = any(v.file_derived and v.depth == want and not v.segments for v in info.vals)
    if not ok:
        errors.append(
            f"authority {file_rel}:{symbol} 形态漂移：应为 Path(__file__).resolve().parent（depth={want}，无后缀）"
        )


def analyze(repo: Path, contract: dict | None = None) -> Analysis:
    contract = contract or load_contract(repo)
    errors: list[str] = []
    _validate_contract_shape(contract, errors)
    discovery = discover(repo, contract)
    _validate_authority(contract, repo, discovery, errors)

    coverage = _declared_coverage(contract)
    candidates = discovery.candidates
    if not candidates:
        errors.append("零候选——扫描面为空或全未发现（不得判绿）")
    for key, info in sorted(candidates.items()):
        if key not in coverage:
            errors.append(
                f"未声明候选：{info.fmt()}（file-derived/资源键消费者）——新增资源/路径锚必须登记分类"
            )
    for _key, info in sorted(discovery.locators.items()):
        for weird in sorted(info.undeclared_like_keys):
            errors.append(f"未登记的资源/工具形键读取：{info.fmt()} 读取 {weird}——需登记或说明")
    declared_any = set(coverage)
    for anchor in contract.get("anchors") or []:
        anchor_hits = [sym["name"] for sym in anchor.get("locators") or [] if (anchor["file"], sym["name"]) in candidates]
        if not anchor_hits and anchor.get("kind") != KIND_INSTALL_LAYOUT:
            errors.append(f"{anchor['id']}: 声明条目在源码中无法复现——缺失源文件或形态不符")
        validate_anchor(anchor, contract, repo, discovery, errors)
    errors.extend(discovery.unresolved)
    errors.extend(discovery.hazards)

    stats = {
        "files": len(discovery.files),
        "families": len(discovery.families),
        "candidates": len(candidates),
        "declared": len(declared_any),
        "legacy": len([a for a in contract.get("anchors") or [] if a.get("legacy")]),
        "unresolved": len(discovery.unresolved),
    }
    return Analysis(
        errors=errors,
        discovery=discovery,
        anchors=list(contract.get("anchors") or []),
        legacy_count=stats["legacy"],
        stats=stats,
    )


def compare_base(base_contract: dict | None, head_contract: dict) -> tuple[list[str], list[str]]:
    """返回 (errors, notes)：legacy 集合只许收缩（例外防扩张）。"""
    head_legacy = {
        (a["file"], sym["name"])
        for a in head_contract.get("anchors") or []
        if a.get("legacy")
        for sym in a.get("locators") or []
    }
    if base_contract is None:
        return [], ["base 无契约文件（首次引入）：例外面以本 PR 的声明为基线，后续新增将判红"]
    base_legacy = {
        (a["file"], sym["name"])
        for a in base_contract.get("anchors") or []
        if a.get("legacy")
        for sym in a.get("locators") or []
    }
    errors = [
        f"例外扩张：新增 legacy 豁免 {file}:{sym}——不得借更新 baseline 赦免新增错根"
        for file, sym in sorted(head_legacy - base_legacy)
    ]
    notes = []
    removed = base_legacy - head_legacy
    if removed:
        notes.append(f"legacy 豁免收缩 {len(removed)} 项（合法方向）：{sorted(removed)}")
    return errors, notes


# ---------------------------------------------------------------------------
# self-test（离线红绿双向；隔离 fixture，不依赖真实仓库）
# ---------------------------------------------------------------------------


AGENT_SETUP_LIB = '''\
from __future__ import annotations

import os
from pathlib import Path


def env(key, default=""):
    return os.environ.get(key, default)


def _default_resources_root() -> Path:
    from config import AGENT_DIR
    return Path(AGENT_DIR) / "resources" / "{subdir}"


def resources_dir(cfg: dict) -> Path:
    base = cfg.get("{param}") or env("{envkey}", "")
    if not base:
        base = _default_resources_root()
    return Path(base) / (cfg.get("project") or "legacy")


def push_device_script() -> str:
    return str(Path(__file__).resolve().parent / "_alpha_loop.sh")
'''

AGENT_SETUP_ENTRY = '''\
from __future__ import annotations

from _lib import push_device_script, resources_dir


def _run(cfg):
    rdir = resources_dir(cfg)
    push_device_script()
    return str(rdir)


def main():
    _run({{}})


if __name__ == "__main__":
    main()
'''

DEAD_LIB = '''\
from __future__ import annotations

import os
from pathlib import Path


def env(key, default=""):
    return os.environ.get(key, default)


def _default_resources_root() -> Path:
    return Path(__file__).resolve().parents[3] / "resources" / "{subdir}"


def resources_dir(cfg: dict) -> Path:
    base = cfg.get("{param}") or env("{envkey}", str(_default_resources_root()))
    return Path(base) / (cfg.get("project") or "legacy")
'''

DEAD_ENTRY = '''\
from __future__ import annotations


def main():
    return 0


if __name__ == "__main__":
    main()
'''

TOOL_LIB = '''\
from __future__ import annotations

import os
from pathlib import Path

_DEFAULT_REL_TOOL = ("..", "..", "tools", "toolx")


def _locate_tool_dir(params_override) -> str:
    if params_override:
        return params_override
    env_override = os.environ.get("{envkey}", "")
    if env_override:
        return env_override
    script_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(script_dir, *_DEFAULT_REL_TOOL))
'''

TOOL_ENTRY = '''\
from __future__ import annotations

from _lib import _locate_tool_dir


def main():
    return _locate_tool_dir(None)


if __name__ == "__main__":
    main()
'''

CONFIG_PY = '''\
from pathlib import Path


def _resolve_base_dir() -> Path:
    this_dir = Path(__file__).resolve().parent
    if this_dir.parent.name == "backend":
        return this_dir.parent.parent
    return this_dir.parent


BASE_DIR = _resolve_base_dir()
AGENT_DIR = Path(__file__).resolve().parent
RESOURCE_DIR = BASE_DIR / "resources"
'''

AIMONKEY_PY = '''\
import os
from pathlib import Path

AGENT_DIR: Path = Path(__file__).resolve().parent


def get_aimonkey_resource_root() -> Path:
    env_dir = os.environ.get("AIMONKEY_RESOURCE_DIR", "").strip()
    if env_dir:
        return Path(env_dir)
    return AGENT_DIR / "resources" / "aimonkey"
'''

SCHEMA_VALIDATOR = '''\
from pathlib import Path

_SCHEMA_FILENAME = "pipeline_schema.json"


def resolve_pipeline_schema_path() -> Path:
    agent_package_root = Path(__file__).resolve().parents[1]
    return agent_package_root.parent / "schemas" / _SCHEMA_FILENAME
'''

TOOL_CACHE_PY = '''\
import os
from pathlib import Path


def _cache_root(env):
    raw = (env.get("STP_TOOLS_CACHE_ROOT") or "").strip()
    if raw:
        return Path(raw)
    install = (env.get("AGENT_INSTALL_DIR") or "").strip()
    if install:
        return Path(install) / "tools_cache"
    return None
'''

HOST_UPDATER_PY = '''\
from pathlib import Path

_AGENT_SOURCE_DIR = Path(__file__).resolve().parent.parent / "agent"
_PIPELINE_SCHEMA_FILE = Path(__file__).resolve().parent.parent / "schemas" / "pipeline_schema.json"
_INVENTORY_PATH = Path(__file__).resolve().parent.parent.parent / "tools" / "ansible" / "inventory.ini"
'''


def _fixture_contract() -> dict:
    return {
        "contract_version": 1,
        "authority": {
            "file": "backend/agent/config.py",
            "symbol": "AGENT_DIR",
            "expect_depth": 1,
        },
        "families": {
            "alpha": {
                "subdir": "alpha",
                "param_key": "alpha_resources_dir",
                "env_key": "STP_ALPHA_RESOURCES_DIR",
                "project_key": "project",
                "project_env_key": "STP_ALPHA_PROJECT",
                "project_default": "legacy",
            },
            "beta": {
                "subdir": "beta",
                "param_key": "beta_resources_dir",
                "env_key": "STP_BETA_RESOURCES_DIR",
                "project_key": "project",
                "project_env_key": "STP_BETA_PROJECT",
                "project_default": "legacy",
            },
        },
        "tool_bindings": [
            {
                "tool_family": "toolx",
                "env_key": "STP_BETA_TOOL_DIR",
                "consumer_families": ["beta_flash"],
            }
        ],
        "extra_scan_files": [
            {"path": "backend/agent/config.py"},
            {"path": "backend/agent/aimonkey_paths.py"},
            {"path": "backend/agent/contracts/pipeline_validator.py"},
            {"path": "backend/agent/tool_cache.py"},
            {"path": "backend/services/host_updater.py"},
        ],
        "anchors": [
            {
                "id": "A01",
                "file": "backend/agent/scripts/alpha_setup/_lib.py",
                "locators": [
                    {"type": "function", "name": "_default_resources_root"},
                    {"type": "function", "name": "resources_dir"},
                ],
                "kind": KIND_HOST_ROOT,
                "family": "alpha",
                "authority": AUTHORITY_AGENT_DIR,
            },
            {
                "id": "A02",
                "file": "backend/agent/scripts/alpha_setup/_lib.py",
                "locators": [{"type": "function", "name": "push_device_script"}],
                "kind": KIND_PACKAGE_MEMBER,
                "member": "_alpha_loop.sh",
            },
            {
                "id": "A03",
                "file": "backend/agent/scripts/beta_check/_lib.py",
                "locators": [
                    {"type": "function", "name": "_default_resources_root"},
                    {"type": "function", "name": "resources_dir"},
                ],
                "kind": KIND_HOST_ROOT,
                "family": "beta",
                "authority": AUTHORITY_DEPTH_GUESS,
                "legacy": {
                    "issue": "#3320",
                    "reason": "fixture: 无消费者",
                    "removal_condition": "出现消费者即撤销",
                },
            },
            {
                "id": "A04",
                "file": "backend/agent/scripts/beta_flash/_lib.py",
                "locators": [{"type": "function", "name": "_locate_tool_dir"}],
                "kind": KIND_TOOL_ROOT,
                "family": "beta_flash",
                "tool_family": "toolx",
                "env_key": "STP_BETA_TOOL_DIR",
            },
            {
                "id": "A05",
                "file": "backend/agent/scripts/gamma_boot/gamma_boot.py",
                "locators": [{"type": "symbol", "name": "_AGENT_ROOT"}],
                "kind": KIND_IMPORT_BOOTSTRAP,
            },
            {
                "id": "A06",
                "file": "backend/agent/aimonkey_paths.py",
                "locators": [
                    {"type": "symbol", "name": "AGENT_DIR"},
                    {"type": "function", "name": "get_aimonkey_resource_root"},
                ],
                "kind": KIND_AGENT_MODULE,
                "subdir": "aimonkey",
                "env_key": "AIMONKEY_RESOURCE_DIR",
            },
            {
                "id": "A07",
                "file": "backend/agent/contracts/pipeline_validator.py",
                "locators": [{"type": "function", "name": "resolve_pipeline_schema_path"}],
                "kind": KIND_SCHEMA,
            },
            {
                "id": "A08",
                "file": "backend/agent/tool_cache.py",
                "locators": [{"type": "function", "name": "_cache_root"}],
                "kind": KIND_TOOL_CACHE,
                "env_keys": ["STP_TOOLS_CACHE_ROOT", "AGENT_INSTALL_DIR"],
            },
            {
                "id": "A09",
                "file": "backend/services/host_updater.py",
                "locators": [
                    {"type": "symbol", "name": "_AGENT_SOURCE_DIR"},
                    {"type": "symbol", "name": "_PIPELINE_SCHEMA_FILE"},
                    {"type": "symbol", "name": "_INVENTORY_PATH"},
                ],
                "kind": KIND_DEPLOY_SOURCE,
            },
            {
                "id": "A10",
                "file": "backend/agent/config.py",
                "locators": [
                    {"type": "function", "name": "_resolve_base_dir"},
                    {"type": "symbol", "name": "BASE_DIR"},
                    {"type": "symbol", "name": "RESOURCE_DIR"},
                ],
                "kind": KIND_AGENT_BASE,
            },
        ],
    }


def _fixture_manifest() -> dict:
    return {
        "schema_version": 1,
        "tools": {
            "toolx": {
                "kind": "tool",
                "versions": [
                    {
                        "version": "1.0",
                        "package_sha256": "a" * 64,
                        "artifact": "packages/toolx/1.0.tar.gz",
                        "python": None,
                        "script": "run.sh",
                        "retired": False,
                    }
                ],
            }
        },
    }


def _write_fixture(root: Path, *, alpha_lib: str | None = None, beta_caps: dict | None = None,
                   contract: dict | None = None, dead_helper: str = DEAD_LIB,
                   alpha_member: bool = True, gamma: bool = True) -> Path:
    def w(rel: str, text: str) -> None:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    w("backend/agent/config.py", CONFIG_PY)
    w("backend/agent/aimonkey_paths.py", AIMONKEY_PY)
    w("backend/agent/contracts/pipeline_validator.py", SCHEMA_VALIDATOR)
    w("backend/agent/tool_cache.py", TOOL_CACHE_PY)
    w("backend/services/host_updater.py", HOST_UPDATER_PY)
    w("backend/agent/scripts/alpha_setup/_lib.py", alpha_lib or AGENT_SETUP_LIB.format(
        subdir="alpha", param="alpha_resources_dir", envkey="STP_ALPHA_RESOURCES_DIR"))
    w("backend/agent/scripts/alpha_setup/alpha_setup.py", AGENT_SETUP_ENTRY)
    w("backend/agent/scripts/alpha_setup/capabilities.json", '{"capabilities": []}\n')
    if alpha_member:
        w("backend/agent/scripts/alpha_setup/_alpha_loop.sh", "#!/bin/sh\n")
    w("backend/agent/scripts/beta_check/_lib.py", dead_helper.format(
        subdir="beta", param="beta_resources_dir", envkey="STP_BETA_RESOURCES_DIR"))
    w("backend/agent/scripts/beta_check/beta_check.py", DEAD_ENTRY)
    w("backend/agent/scripts/beta_check/capabilities.json", '{"capabilities": []}\n')
    w("backend/agent/scripts/beta_flash/_lib.py", TOOL_LIB.format(envkey="STP_BETA_TOOL_DIR"))
    w("backend/agent/scripts/beta_flash/beta_flash.py", TOOL_ENTRY)
    w(
        "backend/agent/scripts/beta_flash/capabilities.json",
        json.dumps(beta_caps if beta_caps is not None else {
            "capabilities": [],
            "requires_tools": {"toolx": {"version": "1.0", "env": "STP_BETA_TOOL_DIR"}},
        }) + "\n",
    )
    if gamma:
        w("backend/agent/scripts/gamma_boot/gamma_boot.py", (
            "import sys\nfrom pathlib import Path\n\n"
            "_AGENT_ROOT = Path(__file__).resolve().parents[3]\n"
            "if str(_AGENT_ROOT) not in sys.path:\n"
            "    sys.path.insert(0, str(_AGENT_ROOT))\n"
        ))
    w("tool_manifest.json", json.dumps(_fixture_manifest()) + "\n")
    # requires_tools 判据单源：把真实实现拷进 fixture（门禁不 import backend 的既有约定）
    real_req = (REPO_ROOT / TOOL_REQUIREMENTS_REL).read_text(encoding="utf-8")
    w(TOOL_REQUIREMENTS_REL, real_req)
    w(CONTRACT_REL, json.dumps(contract or _fixture_contract(), ensure_ascii=False, indent=2) + "\n")
    return root


def _errors_of(root: Path, contract: dict | None = None) -> list[str]:
    return analyze(root, contract or load_contract(root)).errors


def run_self_test() -> int:
    failures: list[str] = []

    def expect(messages: list[str], needle: str, label: str) -> None:
        if not any(needle in m for m in messages):
            failures.append(f"{label}: 期望命中「{needle}」，实际 {messages}")

    def expect_clean(messages: list[str], label: str) -> None:
        if messages:
            failures.append(f"{label}: 应绿，实际 {messages}")

    with tempfile.TemporaryDirectory(prefix="stp-anchor-self-") as tmp:
        # --- 绿基线：agent-dir 形态 + 包成员 + tool 绑定 + import bootstrap + schema + cache ---
        base = _write_fixture(Path(tmp) / "base")
        expect_clean(_errors_of(base), "绿基线")

        # --- 红 R1：恢复旧深度 fallback（可达） ---
        old = AGENT_SETUP_LIB.format(subdir="alpha", param="alpha_resources_dir", envkey="STP_ALPHA_RESOURCES_DIR")
        old = old.replace(
            '    from config import AGENT_DIR\n    return Path(AGENT_DIR) / "resources" / "alpha"',
            '    return Path(__file__).resolve().parents[3] / "resources" / "alpha"',
        )
        r1 = _write_fixture(Path(tmp) / "r1", alpha_lib=old)
        errs = _errors_of(r1)
        expect(errs, "仍按 __file__ 深度推导", "R1 旧 fallback")

        # --- 红 R2/R3/R4/R5：别名 / join / relative tuple / 自定义 cache 祖先深度 ---
        variants = {
            "R2 别名": (
                '    _p = Path(__file__).resolve()\n'
                '    return _p.parents[3] / "resources" / "alpha"'
            ),
            "R3 join": (
                '    base = os.path.dirname(os.path.abspath(__file__))\n'
                '    return Path(os.path.join(base, "..", "..", "..", "resources", "alpha"))'
            ),
            "R4 tuple": (
                '    _rel = ("resources", "alpha")\n'
                '    return Path(__file__).resolve().parents[3].joinpath(*_rel)'
            ),
            "R5 cache 祖先": (
                '    return Path(__file__).resolve().parents[6] / "resources" / "alpha"'
            ),
        }
        for label, body in variants.items():
            lib = AGENT_SETUP_LIB.format(subdir="alpha", param="alpha_resources_dir", envkey="STP_ALPHA_RESOURCES_DIR")
            lib = lib.replace(
                '    from config import AGENT_DIR\n    return Path(AGENT_DIR) / "resources" / "alpha"',
                body,
            )
            errs = _errors_of(_write_fixture(Path(tmp) / f"v-{label}", alpha_lib=lib))
            if not errs:
                failures.append(f"{label}: 应红，实际全绿")

        # --- 红 R6：dead helper 被接入（入口调用） ---
        root = _write_fixture(Path(tmp) / "r6")
        (root / "backend/agent/scripts/beta_check/beta_check.py").write_text(
            "from _lib import resources_dir\n\n\ndef main():\n    resources_dir({})\n\n\n"
            "if __name__ == \"__main__\":\n    main()\n",
            encoding="utf-8",
        )
        expect(_errors_of(root), "现可从族入口到达", "R6 dead helper 接入")

        # --- 红 R7：dead helper 被导出 ---
        root = _write_fixture(Path(tmp) / "r7")
        lib = (root / "backend/agent/scripts/beta_check/_lib.py").read_text(encoding="utf-8")
        (root / "backend/agent/scripts/beta_check/_lib.py").write_text(
            lib + '\n__all__ = ["_default_resources_root"]\n', encoding="utf-8"
        )
        expect(_errors_of(root), "被 __all__ 导出", "R7 dead helper 导出")

        # --- 红 R8/R9：工具绑定缺失 / 错误 env ---
        r8 = _write_fixture(Path(tmp) / "r8", beta_caps={"capabilities": []})
        expect(_errors_of(r8), "缺 requires_tools", "R8 绑定缺失")
        r9 = _write_fixture(
            Path(tmp) / "r9",
            beta_caps={"capabilities": [], "requires_tools": {"toolx": {"version": "1.0", "env": "STP_OTHER_DIR"}}},
        )
        expect(_errors_of(r9), "错误 env", "R9 错误 env")

        # --- 红 R10：包成员缺失（可达） ---
        r10 = _write_fixture(Path(tmp) / "r10", alpha_member=False)
        expect(_errors_of(r10), "包成员缺失", "R10 成员缺失")

        # --- 红 R11：未声明候选（新族 + 深度资源根，不在契约） ---
        root = _write_fixture(Path(tmp) / "r11")
        w = root / "backend/agent/scripts/delta_new/_lib.py"
        w.parent.mkdir(parents=True)
        w.write_text(
            'from pathlib import Path\n\n\ndef _default_resources_root():\n'
            '    return Path(__file__).resolve().parents[3] / "resources" / "delta"\n',
            encoding="utf-8",
        )
        expect(_errors_of(root), "未声明候选", "R11 未声明候选")

        # --- 红 R12：无法解析的被消费表达式（需人工分类） ---
        lib = AGENT_SETUP_LIB.format(subdir="alpha", param="alpha_resources_dir", envkey="STP_ALPHA_RESOURCES_DIR")
        lib = lib.replace(
            '    from config import AGENT_DIR\n    return Path(AGENT_DIR) / "resources" / "alpha"',
            '    return _compute(Path(__file__)) / "resources" / "alpha"',
        )
        r12 = _write_fixture(Path(tmp) / "r12", alpha_lib=lib)
        expect(_errors_of(r12), "无法解析为已知形态", "R12 未解析表达式")

        # --- 红 R13：零候选（扫描面与声明同时为空） ---
        root = Path(tmp) / "r13"
        empty_contract = _fixture_contract()
        empty_contract["anchors"] = []
        empty_contract["extra_scan_files"] = []
        _write_fixture(root, contract=empty_contract, gamma=False)
        shutil.rmtree(root / "backend/agent/scripts")
        (root / "backend/agent/scripts").mkdir()
        expect(_errors_of(root, empty_contract), "零候选", "R13 零候选")

        # --- 红 R14：consumer 急切求值默认锚 ---
        lib = AGENT_SETUP_LIB.format(subdir="alpha", param="alpha_resources_dir", envkey="STP_ALPHA_RESOURCES_DIR")
        lib = lib.replace(
            '    base = cfg.get("alpha_resources_dir") or env("STP_ALPHA_RESOURCES_DIR", "")\n'
            "    if not base:\n        base = _default_resources_root()\n",
            '    base = cfg.get("alpha_resources_dir") or env("STP_ALPHA_RESOURCES_DIR", str(_default_resources_root()))\n',
        )
        r14 = _write_fixture(Path(tmp) / "r14", alpha_lib=lib)
        expect(_errors_of(r14), "急切求值", "R14 急切默认锚")

        # --- 红 R15：例外扩张（base 对比） ---
        base_contract = _fixture_contract()
        head_contract = json.loads(json.dumps(base_contract))
        head_contract["anchors"].append(
            {
                "id": "A99",
                "file": "backend/agent/scripts/alpha_setup/_lib.py",
                "locators": [{"type": "function", "name": "push_device_script"}],
                "kind": KIND_PACKAGE_MEMBER,
                "member": "_alpha_loop.sh",
                "legacy": {"issue": "#3320", "reason": "x", "removal_condition": "y"},
            }
        )
        errs, _notes = compare_base(base_contract, head_contract)
        expect(errs, "例外扩张", "R15 例外扩张")

        # --- 红 R17：legacy helper 经**别名导入**接入（#3615 复核 P2-1） ---
        root = _write_fixture(Path(tmp) / "r17")
        (root / "backend/agent/scripts/beta_check/beta_check.py").write_text(
            "from _lib import resources_dir as rd\n\n\ndef main() -> None:\n    rd({})\n\n\n"
            'if __name__ == "__main__":\n    main()\n',
            encoding="utf-8",
        )
        expect(_errors_of(root), "现可从族入口到达", "R17 别名导入接入")

        # --- 红 R18：legacy helper 经 import * 接入（调用面不可静态追踪） ---
        root = _write_fixture(Path(tmp) / "r18")
        (root / "backend/agent/scripts/beta_check/beta_check.py").write_text(
            "from _lib import *\n\n\ndef main() -> None:\n    resources_dir({})\n\n\n"
            'if __name__ == "__main__":\n    main()\n',
            encoding="utf-8",
        )
        expect(_errors_of(root), "import *", "R18 通配导入接入")

        # --- 红 R19：legacy helper 经 getattr 动态引用 ---
        root = _write_fixture(Path(tmp) / "r19")
        (root / "backend/agent/scripts/beta_check/beta_check.py").write_text(
            "import _lib\n\n\ndef main() -> None:\n    getattr(_lib, \"resources_dir\")({})\n\n\n"
            'if __name__ == "__main__":\n    main()\n',
            encoding="utf-8",
        )
        expect(_errors_of(root), "getattr 动态引用", "R19 getattr 接入")

        # --- 红 R20：默认资源根「保留正确赋值却返回错误路径」（#3615 复核 P2-2） ---
        lib = AGENT_SETUP_LIB.format(subdir="alpha", param="alpha_resources_dir", envkey="STP_ALPHA_RESOURCES_DIR")
        lib = lib.replace(
            '    from config import AGENT_DIR\n    return Path(AGENT_DIR) / "resources" / "alpha"',
            '    from config import AGENT_DIR\n    _good = Path(AGENT_DIR) / "resources" / "alpha"\n'
            '    return Path("/tmp/incorrect")',
        )
        expect(_errors_of(_write_fixture(Path(tmp) / "r20", alpha_lib=lib)), "未出现在返回位置", "R20 错误返回根")

        # --- 红 R21：额外错误目录返回分支 ---
        lib = AGENT_SETUP_LIB.format(subdir="alpha", param="alpha_resources_dir", envkey="STP_ALPHA_RESOURCES_DIR")
        lib = lib.replace(
            '    return Path(AGENT_DIR) / "resources" / "alpha"',
            '    if os.environ.get("STP_ALPHA_ALT"):\n'
            '        return Path("/opt/other/resources/alpha")\n'
            '    return Path(AGENT_DIR) / "resources" / "alpha"',
        )
        expect(_errors_of(_write_fixture(Path(tmp) / "r21", alpha_lib=lib)), "不符合 authority", "R21 额外错误目录")

        # --- 红 R22：删除 param override（只留 env）（#3615 复核 P2-3） ---
        lib = AGENT_SETUP_LIB.format(subdir="alpha", param="alpha_resources_dir", envkey="STP_ALPHA_RESOURCES_DIR")
        lib = lib.replace(
            '    base = cfg.get("alpha_resources_dir") or env("STP_ALPHA_RESOURCES_DIR", "")',
            '    base = env("STP_ALPHA_RESOURCES_DIR", "")',
        )
        expect(_errors_of(_write_fixture(Path(tmp) / "r22", alpha_lib=lib)), "显式参数 override 通道被删除", "R22 删 param")

        # --- 红 R23：环境链 env 先于 param（显式参数优先被反转） ---
        lib = AGENT_SETUP_LIB.format(subdir="alpha", param="alpha_resources_dir", envkey="STP_ALPHA_RESOURCES_DIR")
        lib = lib.replace(
            '    base = cfg.get("alpha_resources_dir") or env("STP_ALPHA_RESOURCES_DIR", "")',
            '    base = env("STP_ALPHA_RESOURCES_DIR", "") or cfg.get("alpha_resources_dir")',
        )
        expect(_errors_of(_write_fixture(Path(tmp) / "r23", alpha_lib=lib)), "env 先于 param", "R23 env 优先")

        # --- 红 R24：资源根尾部多余片段（复审 R1 原始反例） ---
        lib = AGENT_SETUP_LIB.format(subdir="alpha", param="alpha_resources_dir", envkey="STP_ALPHA_RESOURCES_DIR")
        lib = lib.replace(
            '    return Path(AGENT_DIR) / "resources" / "alpha"',
            '    return Path(AGENT_DIR) / "resources" / "alpha" / "unexpected"',
        )
        expect(_errors_of(_write_fixture(Path(tmp) / "r24", alpha_lib=lib)), "片段必须恰为", "R24 多余后缀")

        # --- 红 R24b：资源根片段错序（resources 与族目录颠倒） ---
        lib = AGENT_SETUP_LIB.format(subdir="alpha", param="alpha_resources_dir", envkey="STP_ALPHA_RESOURCES_DIR")
        lib = lib.replace(
            '    return Path(AGENT_DIR) / "resources" / "alpha"',
            '    return Path(AGENT_DIR) / "alpha" / "resources"',
        )
        expect(_errors_of(_write_fixture(Path(tmp) / "r24b", alpha_lib=lib)), "片段必须恰为", "R24b 片段错序")

        # --- 红 R25：legacy helper 在函数体内别名导入并调用（复审 R2） ---
        root = _write_fixture(Path(tmp) / "r25")
        (root / "backend/agent/scripts/beta_check/beta_check.py").write_text(
            "def main():\n"
            "    from _lib import resources_dir as rd\n"
            "    print(rd({}))\n\n"
            'if __name__ == "__main__":\n'
            "    main()\n",
            encoding="utf-8",
        )
        expect(_errors_of(root), "现可从族入口到达", "R25 函数内别名导入")

        # --- 红 R26：经局部变量反转优先级（复审 R3） ---
        lib = AGENT_SETUP_LIB.format(subdir="alpha", param="alpha_resources_dir", envkey="STP_ALPHA_RESOURCES_DIR")
        lib = lib.replace(
            '    base = cfg.get("alpha_resources_dir") or env("STP_ALPHA_RESOURCES_DIR", "")',
            '    base = env("STP_ALPHA_RESOURCES_DIR", "")\n'
            '    param_value = cfg.get("alpha_resources_dir")\n'
            "    base = base or param_value",
        )
        expect(_errors_of(_write_fixture(Path(tmp) / "r26", alpha_lib=lib)), "env 先于 param", "R26 变量 env 优先")

        # --- 绿 G4：经局部变量的 param 先行（等价写法不得误报） ---
        lib = AGENT_SETUP_LIB.format(subdir="alpha", param="alpha_resources_dir", envkey="STP_ALPHA_RESOURCES_DIR")
        lib = lib.replace(
            '    base = cfg.get("alpha_resources_dir") or env("STP_ALPHA_RESOURCES_DIR", "")',
            '    param_value = cfg.get("alpha_resources_dir")\n'
            '    base = param_value or env("STP_ALPHA_RESOURCES_DIR", "")',
        )
        expect_clean(_errors_of(_write_fixture(Path(tmp) / "g4", alpha_lib=lib)), "G4 变量 param 先行")

        # --- 红 R27：资源根**前置**多余片段（复审 S1） ---
        lib = AGENT_SETUP_LIB.format(subdir="alpha", param="alpha_resources_dir", envkey="STP_ALPHA_RESOURCES_DIR")
        lib = lib.replace(
            '    return Path(AGENT_DIR) / "resources" / "alpha"',
            '    return Path(AGENT_DIR) / "unexpected" / "resources" / "alpha"',
        )
        expect(_errors_of(_write_fixture(Path(tmp) / "r27", alpha_lib=lib)), "片段必须恰为", "R27 前缀多余片段")

        # --- 红 R28：两个函数复用同一别名（后一个导入不得覆盖前一个，复审 S2） ---
        root = _write_fixture(Path(tmp) / "r28")
        (root / "backend/agent/scripts/beta_check/beta_check.py").write_text(
            "def main():\n"
            "    from _lib import resources_dir as rd\n"
            "    print(rd({}))\n\n"
            "def unused():\n"
            "    from _lib import sha256_file as rd\n"
            '    return rd("unused")\n\n'
            'if __name__ == "__main__":\n'
            "    main()\n",
            encoding="utf-8",
        )
        errs = _errors_of(root)
        expect(errs, "现可从族入口到达", "R28 别名复用可达")
        expect(errs, "同一别名多来源", "R28 别名多来源")

        # --- 红 R29：未使用的 param-first 链掩盖实际 env-first（复审 S3） ---
        lib = AGENT_SETUP_LIB.format(subdir="alpha", param="alpha_resources_dir", envkey="STP_ALPHA_RESOURCES_DIR")
        lib = lib.replace(
            '    base = cfg.get("alpha_resources_dir") or env("STP_ALPHA_RESOURCES_DIR", "")',
            '    unused = cfg.get("alpha_resources_dir") or env("STP_ALPHA_RESOURCES_DIR", "")\n'
            '    base = env("STP_ALPHA_RESOURCES_DIR", "") or cfg.get("alpha_resources_dir")',
        )
        expect(_errors_of(_write_fixture(Path(tmp) / "r29", alpha_lib=lib)), "env 先于 param", "R29 未使用链掩盖")

        # --- 红 R16：base ref 不可解析 = 不可验证 ---
        git_root = Path(tmp) / "gitrepo"
        _write_fixture(git_root)
        subprocess.run(["git", "-C", str(git_root), "init", "-q"], check=False)
        try:
            load_ref_contract(git_root, "no-such-ref")
            failures.append("R16 base 不可解析: 应抛 Unverifiable")
        except Unverifiable:
            pass

        # --- 绿 G2：显式 override 锚（无可达默认锚，仅 param/env 通道） ---
        override_root = Path(tmp) / "g2"
        _write_fixture(override_root)
        lib = (override_root / "backend/agent/scripts/alpha_setup/_lib.py").read_text(encoding="utf-8")
        lib = lib.replace(
            'from config import AGENT_DIR\n    return Path(AGENT_DIR) / "resources" / "alpha"',
            'return Path(".")',
        )
        (override_root / "backend/agent/scripts/alpha_setup/_lib.py").write_text(lib, encoding="utf-8")
        contract = _fixture_contract()
        contract["anchors"][0]["kind"] = KIND_EXPLICIT_OVERRIDE
        contract["anchors"][0]["authority"] = "explicit"
        expect_clean(_errors_of(override_root, contract), "G2 显式 override")

        # --- 绿 G3：parents 字样（schema）与 tools_cache 路径不得自身触发误报 ---
        expect_clean(_errors_of(_write_fixture(Path(tmp) / "g3")), "G3 字样不误报")

    if failures:
        for failure in failures:
            print(f"[SELFTEST-FAIL] {failure}", file=sys.stderr)
        return 1
    print(
        "[OK] check_resource_anchors self-test 红绿双向（旧深度 fallback / 别名 / join / relative tuple / "
        "cache 祖先 / dead 接入 / 别名导入接入 / 函数内别名导入 / import * 接入 / getattr 接入 / 导出 / "
        "绑定缺失 / 错误 env / 成员缺失 / 未声明候选 / 未解析表达式 / 零候选 / 急切默认锚 / 错误返回根 / "
        "额外错误目录 / 多余后缀 / 片段错序 / 前缀多余片段 / 别名复用（多来源）/ 未使用链掩盖 / "
        "删 param override / env 先于 param / 变量 env 优先 / 例外扩张 / base 不可验证 → 红；"
        "agent-dir 形态 / 显式 override / 变量 param 先行 / parents 与 tools_cache 字样不误报 → 绿）"
    )
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo-root", type=Path, default=REPO_ROOT, help="仓库根（默认按本文件位置；测试用）")
    ap.add_argument("--contract", default=CONTRACT_REL, help="契约相对路径")
    ap.add_argument("--base", default=None, help="比较基线 ref（增量防新增/例外防扩张）")
    ap.add_argument("-q", "--quiet", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        return run_self_test()

    repo: Path = args.repo_root.resolve()
    notes: list[str] = []
    base_errors: list[str] = []
    if args.base:
        try:
            base_contract = load_ref_contract(repo, args.base, args.contract)
        except Unverifiable as exc:
            print(f"[FAIL] 基线不可验证：{exc}", file=sys.stderr)
            return 2
        head_contract = load_contract(repo, args.contract)
        base_errors, notes = compare_base(base_contract, head_contract)

    analysis = analyze(repo, load_contract(repo, args.contract))
    errors = analysis.errors + base_errors
    for note in notes:
        print(f"[NOTE] {note}")
    if errors:
        print(f"[FAIL] check_resource_anchors（B4/G2 #3321，契约 {args.contract}）：", file=sys.stderr)
        for err in errors:
            print(f"  ✗ {err}", file=sys.stderr)
        print(f"[FAIL] {len(errors)} 项违例（扫描 {analysis.stats['files']} 文件 / "
              f"{analysis.stats['families']} 族；候选 {analysis.stats['candidates']}）", file=sys.stderr)
        return 1
    if not args.quiet:
        kinds: dict[str, int] = {}
        for anchor in analysis.anchors:
            kinds[anchor.get("kind", "?")] = kinds.get(anchor.get("kind", "?"), 0) + 1
        kind_desc = ", ".join(f"{k}={v}" for k, v in sorted(kinds.items()))
        print(
            f"[OK] check_resource_anchors 绿：扫描 {analysis.stats['files']} 文件 / "
            f"{analysis.stats['families']} 族；候选 {analysis.stats['candidates']}；"
            f"声明 {analysis.stats['declared']} 定位点 / {len(analysis.anchors)} 锚（{kind_desc}）；"
            f"legacy 豁免 {analysis.stats['legacy']}（函数级）；未解析 {analysis.stats['unresolved']}"
            + (f"；base={args.base} 防新增/防扩张通过" if args.base else "")
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
