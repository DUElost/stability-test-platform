"""响应形状契约：手搓 dict 响应 ↔ ``types.ts`` 声明的键集合（#2129）。

## 为什么需要它

本仓是 Python + TS 双语言、**没有共享 schema**，而一部分端点返回的是**手搓 dict**
（不是 Pydantic 模型），前端则在 ``types.ts`` 里独立声明同一形状。于是「同一个形状有两处
独立声明」，它们会漂移，且**现有兜底看不见**：

- ``tsc`` 放行「TS 声明了后端永不返回的键」（可选字段声明不会报错）——``#787`` 一轮就删过
  6 个这样的幽灵键；
- ``tsc`` 也放行「后端返回了 TS 没声明的键」——消费者看不见新字段；
- ``#2089`` 在同一个 ``PlanRunAbortResult`` 上又发现一次（``released_leases`` 恒为 0 却被
  两侧承诺）。

两次都不是代码坏了，而是两处声明漂移。所以这里做**机械对拍**：把已登记配对的键集合按
双向判据比对。

## 判据（双向，缺一不可）

1. **后端每个 ``return`` 字典的键 ⊆ TS 字段集**——返回了前端没声明的键，消费者看不见；
2. **TS 字段集 ⊆ 后端所有 ``return`` 字典键的并集**——声明了永不返回的键 = 幽灵键。

只看并集不够：逐分支包含关系才拦得住「某个分支返回了额外键」（``abort_plan_run`` 就是多分支
返回、键集合各不相同的形状）。

## 覆盖边界（如实写明）

只覆盖下面 ``_PAIRS`` 里**显式登记**的配对；新增配对要加一行。因此它是**增量契约**，
不是全量普查——登记谁由「已发生过漂移 / 声明面较大」决定，而不是自动发现（端点与 TS 类型
之间没有机器可读的映射，假装能自动发现只会做出恒真的守卫）。

当前覆盖：``abort_plan_run`` ↔ ``PlanRunAbortResult``（``#787`` 与 ``#2089`` 两次漂移都
发生在这一对）。
"""

from __future__ import annotations

import ast
import re
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
