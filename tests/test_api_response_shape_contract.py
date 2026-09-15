"""响应形状契约：手搓 dict 响应 ↔ ``types.ts`` 声明的键集合（#2129）/ docstring 承诺 ↔ 实现（#2141）。

## 为什么需要它

本仓是 Python + TS 双语言、**没有共享 schema**，而一部分端点返回的是**手搓 dict**
（不是 Pydantic 模型），前端则在 ``types.ts`` 里独立声明同一形状。于是「同一个形状有两处
独立声明」，它们会漂移，且**现有兜底看不见**：

- ``tsc`` 放行「TS 声明了后端永不返回的键」（可选字段声明不会报错）——``#787`` 一轮就删过
  6 个这样的幽灵键；
- ``tsc`` 也放行「后端返回了 TS 没声明的键」——消费者看不见新字段；
- ``#2089`` 在同一个 ``PlanRunAbortResult`` 上又发现一次（``released_leases`` 恒为 0 却被
  两侧承诺）。

同一个形状还有**第三处**声明：后端函数的 **docstring**（``Returns a summary dict::`` 块）。
它同样会漂移（``#2141``），而且比 TS 更隐蔽——``tsc`` 至少还能看见 TS 侧，docstring 与实现
不一致则完全没有读者会被编译器拦住。

## 判据（双向，缺一不可）

TS ↔ 后端（#2129）：

1. **后端每个 ``return`` 字典的键 ⊆ TS 字段集**——返回了前端没声明的键，消费者看不见；
2. **TS 字段集 ⊆ 后端所有 ``return`` 字典键的并集**——声明了永不返回的键 = 幽灵键。

docstring ↔ 实现（#2141）：同样双向，**逐函数**比对 docstring 块里的键与实现 ``return``
字典键的并集。

只看并集不够：逐分支包含关系才拦得住「某个分支返回了额外键」（``abort_plan_run`` 就是多分支
返回、键集合各不相同的形状）。

## 覆盖边界（如实写明）

- TS 侧只覆盖 ``_PAIRS`` 里**显式登记**的配对（增量契约，不是全量普查：端点与 TS 类型之间
  没有机器可读映射，假装能自动发现只会做出恒真的守卫）。新增配对要加一行。
- docstring 侧是**自动发现**的（AST 扫 ``backend/``，凡 docstring 里列了 ≥3 个
  ``"key": …`` 行的函数都进入检查）——这一侧的信号是可靠的（实测 4 个函数；不排除
  ``backend/agent/scripts`` 也是 4 个，故不需要路径例外）。发现到但**实现不以 dict 字面量
  返回**的函数无法比对，必须在 ``_DOC_UNCHECKABLE`` 里显式豁免并写明原因；豁免项失效时
  本检查也会红（防僵尸豁免）。

同一个形状还有**第三处**声明：**Pydantic 响应模型**（``response_model=ApiResponse[X]``）。
它比手搓 dict 更容易被误认为"已经单一权威"——``types.ts`` 仍是一份独立声明，``tsc`` 同样
看不见两侧差异（``WatcherPlatformBucketOut`` 加字段时，TS 不补也不会报错）。

三条轴线判据一致（双向包含），差别只在"后端声明面在哪里"：

| 轴线 | 后端声明面 | 登记方式 |
|---|---|---|
| A | 手搓 dict 的 ``return`` 字面量 | ``_PAIRS`` |
| B | 函数 docstring 的键块 | 自动发现 + ``_DOC_UNCHECKABLE`` |
| C | Pydantic 模型的注解字段 | ``_MODEL_PAIRS`` |

轴线 C 的覆盖边界（如实写明）：

- 只覆盖 ``_MODEL_PAIRS`` 里**显式登记**的配对（同轴线 A：端点与 TS 类型之间没有机器可读
  映射，假装能自动发现只会做出恒真的守卫）；
- **不处理 ``Field(alias=…)`` / ``serialization_alias``**：对拍的是 Python 字段名，若模型
  有别名则线上键名不同，登记前必须确认无别名（有别名时本检查会给出假绿，故不允许登记）；
- 基类字段只在**同一文件内**递归解析；遇到文件外、且不是 ``BaseModel`` 的基类会**直接报错**
  而不是静默少收字段（少收会让"幽灵字段"判据假绿）。

当前覆盖：轴线 A 1 对（``abort_plan_run`` ↔ ``PlanRunAbortResult``，``#787`` 与 ``#2089``
两次漂移都在这一对）；轴线 B 4 个 docstring 键块函数中的 2 个可比对者；轴线 C 8 对——
watcher-summary 3（``WatcherSummaryOut`` / ``WatcherPlatformBucketOut`` / ``WatcherCategoryOut``）、
log-events 2（``PlanRunLogEventOut`` / ``PlanRunLogEventsOut``）、scan/merge 状态 3
（``DedupStatusOut`` / ``DedupArtifactOut`` / ``DedupScanArchiveOut``）。

轴线 C 的第 3 组来自一次**正规化**：``GET /plan-runs/{id}/dedup/status`` 原为
``response_model=ApiResponse[dict]`` + ``ok({...})`` 手搓 dict（轴线 A 的 AST 判据识别不到
包在 ``ok(...)`` 调用里的字典字面量，故它此前**任何**轴线都覆盖不到），改为
``ApiResponse[DedupStatusOut]`` 后由本门禁自动覆盖——"两处声明"变成"一处声明 + 机器对拍"。
"""

from __future__ import annotations

import ast
import functools
import re
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# (后端文件, 函数名, TS 文件, 接口名)
_PAIRS: tuple[tuple[str, str, str, str], ...] = (
    (
        "backend/services/plan_run_abort.py",
        "abort_plan_run",
        "frontend/src/utils/api/types.ts",
        "PlanRunAbortResult",
    ),
)

# docstring 里形如 `"key": 类型,` 的行（文档化的返回形状）。
_DOC_KEY_RE = re.compile(r'^\s*"([a-z_][a-z0-9_]*)"\s*:', re.M)
_DOC_MIN_KEYS = 3

# 发现到、但**实现不以 dict 字面量返回**的函数：本检查无从比对，必须显式豁免并写明原因。
# 豁免项失效（不再被发现、或变得可比对）时 TestDocstringKeyContract 会红，防止僵尸豁免。
_DOC_UNCHECKABLE: dict[str, str] = {
    "from_job": "返回 policy 对象（非 dict 字面量），docstring 描述其字段",
    "process_device_logs": "非 dict 字面量返回（含无值 return），docstring 描述其载荷",
}


def _return_dict_key_sets(py_path: Path, func_name: str) -> list[set[str]]:
    """函数内**每个** ``return`` 的字典字面量的顶层键集合（跳过非字典返回与 docstring）。"""
    tree = ast.parse(py_path.read_text(encoding="utf-8"))
    func = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == func_name
        ),
        None,
    )
    assert func is not None, f"{py_path.name} 里找不到函数 {func_name}（登记表过期？）"

    key_sets: list[set[str]] = []
    for node in ast.walk(func):
        if not isinstance(node, ast.Return) or not isinstance(node.value, ast.Dict):
            continue
        keys: set[str] = set()
        for key in node.value.keys:
            assert isinstance(key, ast.Constant) and isinstance(key.value, str), (
                f"{func_name}: return 字典存在非字面量键（本契约只对拍字面量键）"
            )
            keys.add(key.value)
        key_sets.append(keys)
    assert key_sets, f"{func_name} 没有任何字典字面量 return（登记表过期？）"
    return key_sets


_TS_MEMBER_RE = re.compile(r"([A-Za-z_$][\w$]*)\s*\??\s*:")


def _ts_interface_fields(ts_path: Path, interface: str) -> set[str]:
    """``export interface X { … }`` 的**顶层**成员名（按缩进排除嵌套对象成员）。"""
    source = ts_path.read_text(encoding="utf-8")
    match = re.search(rf"export interface {re.escape(interface)}\b[^{{]*\{{", source)
    assert match is not None, f"{ts_path.name} 里找不到 interface {interface}（登记表过期？）"

    depth = 0
    body_start = match.end() - 1
    for index in range(body_start, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                body = source[body_start + 1 : index]
                break
    else:  # pragma: no cover - 花括号不配对
        raise AssertionError(f"interface {interface} 花括号不配对")

    lines = [line for line in body.splitlines() if line.strip()]
    assert lines, f"interface {interface} 是空的"
    member_indent = len(lines[0]) - len(lines[0].lstrip())

    fields: set[str] = set()
    for raw in lines:
        indent = len(raw) - len(raw.lstrip())
        if indent != member_indent:
            continue  # 嵌套对象/联合类型的成员不属顶层声明面
        text = raw.split("//")[0].strip()
        found = _TS_MEMBER_RE.match(text)
        if found:
            fields.add(found.group(1))
    assert fields, f"interface {interface} 没解析出任何顶层字段"
    return fields


@dataclass(frozen=True)
class _DocKeyFunc:
    rel_path: str
    name: str
    doc_keys: frozenset[str]
    return_dict_keys: frozenset[str]

    @property
    def checkable(self) -> bool:
        """实现是否有 dict 字面量返回（决定能否与 docstring 比对）。"""
        return bool(self.return_dict_keys)


@functools.lru_cache(maxsize=1)
def _discover_docstring_key_funcs() -> list[_DocKeyFunc]:
    """扫 ``backend/``：docstring 里列了 ≥``_DOC_MIN_KEYS`` 个 ``"key": …`` 行的函数。

    ``lru_cache``：本函数被 5 个用例调用，而一次全仓 AST 扫描约 1.5s——不缓存会让这个纯离线
    契约测试多花数秒（实测 8.6s → 缓存后 1s 级）。用例集内仓库状态不变，缓存是安全的。
    """
    out: list[_DocKeyFunc] = []
    for path in sorted((ROOT / "backend").rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):  # pragma: no cover - 解析失败即忽略
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            doc = ast.get_docstring(node, clean=False) or ""
            doc_keys = set(_DOC_KEY_RE.findall(doc))
            if len(doc_keys) < _DOC_MIN_KEYS:
                continue
            ret_keys: set[str] = set()
            for sub in ast.walk(node):
                if isinstance(sub, ast.Return) and isinstance(sub.value, ast.Dict):
                    for key in sub.value.keys:
                        if isinstance(key, ast.Constant) and isinstance(key.value, str):
                            ret_keys.add(key.value)
            out.append(
                _DocKeyFunc(
                    rel_path=path.relative_to(ROOT).as_posix(),
                    name=node.name,
                    doc_keys=frozenset(doc_keys),
                    return_dict_keys=frozenset(ret_keys),
                )
            )
    return out


def test_registry_pairs_resolve():
    """登记表自证：每对都能解析出「≥1 个字典 return」与「≥1 个顶层字段」。

    少了这一条，改名/删除任一侧会让下面的对拍**静默变空**（对空集恒真的守卫）。
    """
    for py_file, func, ts_file, interface in _PAIRS:
        key_sets = _return_dict_key_sets(ROOT / py_file, func)
        fields = _ts_interface_fields(ROOT / ts_file, interface)
        assert key_sets, f"{func}: 未解析出字典 return"
        assert fields, f"{interface}: 未解析出字段"


def test_backend_return_keys_are_declared_in_ts():
    """判据 1：后端逐分支返回的键都必须在前端声明里。"""
    problems: list[str] = []
    for py_file, func, ts_file, interface in _PAIRS:
        fields = _ts_interface_fields(ROOT / ts_file, interface)
        for keys in _return_dict_key_sets(ROOT / py_file, func):
            undeclared = sorted(keys - fields)
            if undeclared:
                problems.append(
                    f"{func} 的某分支返回了 {interface} 未声明的键 {undeclared}"
                    f"（消费者看不见）"
                )
    assert not problems, "\n".join(problems)


def test_ts_declared_fields_are_all_returned_by_backend():
    """判据 2：TS 声明的字段必须出现在后端某个分支的返回键并集里（幽灵键即红）。"""
    problems: list[str] = []
    for py_file, func, ts_file, interface in _PAIRS:
        fields = _ts_interface_fields(ROOT / ts_file, interface)
        union: set[str] = set()
        for keys in _return_dict_key_sets(ROOT / py_file, func):
            union |= keys
        ghosts = sorted(fields - union)
        if ghosts:
            problems.append(
                f"{interface} 声明了 {func} 永不返回的键 {ghosts}（幽灵键，tsc 会因可选而放行）"
            )
    assert not problems, "\n".join(problems)


class TestDocstringKeyContract:
    """#2141：docstring 里列出的返回键 vs 实现返回键（同一形状的第三处声明）。"""

    def test_discovery_finds_functions(self):
        found = _discover_docstring_key_funcs()
        assert len(found) >= 2, (
            f"只发现 {len(found)} 个 docstring 键块函数（已知至少 2 个可比对者）；"
            "解析器或扫描范围已失效"
        )
        names = [f.name for f in found]
        assert len(names) == len(set(names)), (
            f"存在重名函数，豁免表与断言会串味：{sorted(names)}"
        )

    def test_known_canary_is_still_discovered(self):
        """canary：``abort_plan_run`` 是 ``#2089`` 漂移对的声明面。

        它不再被发现（docstring 块被删、或解析器失效）时，本检查会**静默**失去最有价值的
        那一例——所以这里显式锚一下，让「静默」变「红」。
        """
        names = {f.name for f in _discover_docstring_key_funcs()}
        assert "abort_plan_run" in names, f"canary 丢失（当前发现：{sorted(names)}）"

    def test_docstring_keys_match_return_dict_keys(self):
        problems: list[str] = []
        for found in _discover_docstring_key_funcs():
            if not found.checkable or found.name in _DOC_UNCHECKABLE:
                continue
            ghosts = sorted(found.doc_keys - found.return_dict_keys)
            undocumented = sorted(found.return_dict_keys - found.doc_keys)
            if ghosts:
                problems.append(
                    f"{found.rel_path}:{found.name} 的 docstring 承诺了实现不返回的键 "
                    f"{ghosts}（幽灵承诺）"
                )
            if undocumented:
                problems.append(
                    f"{found.rel_path}:{found.name} 实现了 docstring 未列的键 "
                    f"{undocumented}（未文档化）"
                )
        assert not problems, "\n".join(problems)

    def test_uncheckable_functions_are_exempted(self):
        """发现到但无法比对的必须显式豁免，否则新出现的这类函数会静默逃过检查。"""
        missing = sorted(
            f"{found.rel_path}:{found.name}"
            for found in _discover_docstring_key_funcs()
            if not found.checkable and found.name not in _DOC_UNCHECKABLE
        )
        assert not missing, (
            "以下函数的 docstring 列了键，但实现不以 dict 字面量返回，无法比对——"
            f"必须在 _DOC_UNCHECKABLE 里显式豁免并写明原因：{missing}"
        )

    def test_exemptions_are_not_stale(self):
        """豁免项必须仍然存在且仍然不可比对，否则它是僵尸豁免。"""
        discovered = {found.name: found for found in _discover_docstring_key_funcs()}
        stale: list[str] = []
        for name in _DOC_UNCHECKABLE:
            found = discovered.get(name)
            if found is None:
                stale.append(f"{name}（已不再被发现有 docstring 键块）")
            elif found.checkable:
                stale.append(f"{name}（现在有 dict 字面量返回、可比对了，应移出豁免表）")
        assert not stale, f"僵尸豁免：{stale}"


# ── 轴线 C：Pydantic 响应模型 ↔ TS 接口 ──────────────────────────────────────

# (后端模型文件, 模型名, TS 文件, 接口名)
_MODEL_PAIRS: tuple[tuple[str, str, str, str], ...] = (
    (
        "backend/api/schemas/plan_run.py",
        "WatcherSummaryOut",
        "frontend/src/utils/api/types.ts",
        "WatcherSummary",
    ),
    (
        "backend/api/schemas/plan_run.py",
        "WatcherPlatformBucketOut",
        "frontend/src/utils/api/types.ts",
        "WatcherPlatformBucket",
    ),
    (
        "backend/api/schemas/plan_run.py",
        "WatcherCategoryOut",
        "frontend/src/utils/api/types.ts",
        "WatcherCategory",
    ),
    # 日志链（#529 归档权威）：GET /plan-runs/{id}/log-events
    (
        "backend/api/schemas/plan_run.py",
        "PlanRunLogEventOut",
        "frontend/src/utils/api/types.ts",
        "PlanRunLogEvent",
    ),
    (
        "backend/api/schemas/plan_run.py",
        "PlanRunLogEventsOut",
        "frontend/src/utils/api/types.ts",
        "PlanRunLogEventsPayload",
    ),
    # scan/merge 状态（本轮由 response_model=ApiResponse[dict] 正规化为模型）
    (
        "backend/api/schemas/dedup.py",
        "DedupStatusOut",
        "frontend/src/utils/api/types.ts",
        "DedupStatusPayload",
    ),
    (
        "backend/api/schemas/dedup.py",
        "DedupArtifactOut",
        "frontend/src/utils/api/types.ts",
        "DedupArtifact",
    ),
    (
        "backend/api/schemas/dedup.py",
        "DedupScanArchiveOut",
        "frontend/src/utils/api/types.ts",
        "DedupScanArchive",
    ),
)

#: 允许作为基类、但其字段不在本解析范围内的类型（框架基类，无业务字段）。
_PYDANTIC_BASE_ALLOWED = frozenset({"BaseModel"})


def _pydantic_model_fields(py_path: Path, model: str) -> set[str]:
    """``class X(BaseModel):`` 的**注解字段名**，含同文件内基类的字段。

    文件外基类（除 ``_PYDANTIC_BASE_ALLOWED``）会让本函数**报错**而不是静默少收字段——
    少收会让「TS 声明了模型不返回的键」这条判据假绿，那正是本门禁要拦的漂移。
    """
    tree = ast.parse(py_path.read_text(encoding="utf-8"))
    classes = {
        node.name: node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)
    }
    assert model in classes, f"{py_path.name} 里找不到模型 {model}（登记表过期？）"

    fields: set[str] = set()
    pending = [model]
    seen: set[str] = set()
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        node = classes[name]
        for stmt in node.body:
            if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                fields.add(stmt.target.id)
        for base in node.bases:
            if not isinstance(base, ast.Name):
                continue  # 泛型/下标基类：本解析器不展开，登记时需确认无业务字段
            if base.id in classes:
                pending.append(base.id)
            elif base.id not in _PYDANTIC_BASE_ALLOWED:
                raise AssertionError(
                    f"{name} 的基类 {base.id} 不在本文件内且非 BaseModel——"
                    "无法解析其字段；登记该模型前需先扩展本解析器"
                )
    assert fields, f"{model} 没解析出任何注解字段"
    return fields


def test_model_registry_pairs_resolve():
    """登记表自证：每对两侧都能解析出字段（改名/删除会让对拍静默变空）。"""
    for py_file, model, ts_file, interface in _MODEL_PAIRS:
        assert _pydantic_model_fields(ROOT / py_file, model), f"{model} 解析为空"
        assert _ts_interface_fields(ROOT / ts_file, interface), f"{interface} 解析为空"


def test_model_fields_are_declared_in_ts():
    """轴线 C 判据 1：模型字段必须在前端声明里（否则消费者看不见新字段）。"""
    problems: list[str] = []
    for py_file, model, ts_file, interface in _MODEL_PAIRS:
        fields = _ts_interface_fields(ROOT / ts_file, interface)
        undeclared = sorted(_pydantic_model_fields(ROOT / py_file, model) - fields)
        if undeclared:
            problems.append(
                f"{model} 的字段 {undeclared} 未在 {interface} 声明（消费者看不见）"
            )
    assert not problems, "\n".join(problems)


def test_ts_fields_are_all_declared_in_model():
    """轴线 C 判据 2：TS 字段必须在模型里有对应声明（幽灵字段即红）。"""
    problems: list[str] = []
    for py_file, model, ts_file, interface in _MODEL_PAIRS:
        fields = _ts_interface_fields(ROOT / ts_file, interface)
        ghosts = sorted(fields - _pydantic_model_fields(ROOT / py_file, model))
        if ghosts:
            problems.append(
                f"{interface} 声明了 {model} 不含的字段 {ghosts}"
                "（幽灵字段，tsc 因可选而放行）"
            )
    assert not problems, "\n".join(problems)


def test_model_axis_canary_sees_platform_support_flag():
    """canary：``reconciler_supported``（R4-b b1，2026-09-15）必须两轴都可见。

    它正是轴线 C 要拦的那种漂移的最近一次实例（后端模型加字段、TS 不补也不会报错）。
    解析器若失效，本用例先红，而不是让对拍静默变空。
    """
    assert "reconciler_supported" in _pydantic_model_fields(
        ROOT / "backend/api/schemas/plan_run.py", "WatcherPlatformBucketOut",
    )
    assert "reconciler_supported" in _ts_interface_fields(
        ROOT / "frontend/src/utils/api/types.ts", "WatcherPlatformBucket",
    )
