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

当前覆盖：``abort_plan_run`` ↔ ``PlanRunAbortResult``（``#787`` 与 ``#2089`` 两次漂移都
发生在这一对），以及 4 个 docstring 键块函数中的 2 个可比对者。
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
