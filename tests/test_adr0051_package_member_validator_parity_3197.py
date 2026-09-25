# -*- coding: utf-8 -*-
"""#3197 项1：两份「包内相对路径」校验器的**判据对拍**（漂移由测试闭合，不靠注释自称）。

`tools/dev/package_tool_asset.py::validate_relative_member`（登记/分发侧）与
`tools/dev/check_tool_manifest.py::validate_relative_member`（门禁侧）是**有意**的两份
独立实现——门禁刻意不 import 工具，避免跨工具脆链。代价是「同判据」这句话只能靠人记，
而它已经漂过：门禁侧少掉反斜杠与盘符两个拒绝分支，于是 `script: "a\\b"`、`"C:x"`
能过门禁 lint 进 Git manifest，登记工具侧却判非法——同一份 manifest 两侧结论相反。

本文件不做「抄一份常量表比对」，而是拿**同一输入集**跑两侧、断言**裁决**（合法/非法）
逐点相同，并钉住每条分支至少有一个样本命中（否则删分支仍能通过——那正是假阴性的门）。
消息文案不参与对拍：两侧文案本就可以不同，判据才是契约。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, relpath: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relpath)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


packer = _load("package_tool_asset_3197", "tools/dev/package_tool_asset.py")
gate = _load("check_tool_manifest_3197", "tools/dev/check_tool_manifest.py")
#: 第三份：Agent 消费侧（#3169）——`tool_cache` 在拼接 manifest 的 python/script 前再判一次。
#: Agent 包不带 tools/，同样只能独立实现；它与登记侧漂开时，门禁放行的 manifest 会在主机上被判非法（或反之）。
#: （模块含 dataclass，须经正常 import 注册进 sys.modules，不能走 `_load` 的匿名加载。）
from backend.agent import tool_cache as consumer  # noqa: E402

#: 输入集 → 期望裁决（True = 合法）。覆盖两条实现的**每一条**拒绝分支。
CASES: dict[str, bool] = {
    # 合法形态
    "demo.py": True,
    "venv/bin/python": True,
    "sub/_lib.py": True,
    "a/b.tar.gz": True,
    # 空 / 绝对
    "": False,
    "/abs/path.py": False,
    # 空段与上跳
    "a//b": False,
    "../etc/passwd": False,
    "a/../../b": False,
    "a/": False,
    # 反斜杠分支（#3197 漂移点之一）
    "a\\b": False,
    "C:\\stp\\demo.py": False,
    # 盘符分支（首段含冒号；#3197 漂移点之二）
    "C:x": False,
    "a:b/c.py": False,
    # 首段无冒号则放行（两侧同判据：只查首段）
    "a/b:c.py": True,
}


@pytest.mark.parametrize("value,expected_ok", sorted(CASES.items()))
def test_two_validators_agree_on_verdict(value: str, expected_ok: bool) -> None:
    """同一输入在两侧必须给出**同一裁决**（文案不比）。"""
    p_err = packer.validate_relative_member(value, field="script")
    g_err = gate.validate_relative_member(value, field="script")
    c_err = consumer._relative_member_error(value)
    assert (p_err is None) is expected_ok, f"工具侧裁决变了：{value!r} → {p_err!r}"
    assert (g_err is None) is expected_ok, f"门禁侧裁决变了：{value!r} → {g_err!r}"
    assert (c_err is None) is expected_ok, f"Agent 消费侧裁决变了：{value!r} → {c_err!r}"


def test_case_set_covers_every_branch() -> None:
    """样本集退化即红：删掉反斜杠/盘符样本，本文件必须失败而不是静默通过。"""
    backslash_only = [v for v, ok in CASES.items() if "\\" in v and not ok]
    drive_only = [v for v, ok in CASES.items() if ":" in v.split("/")[0] and not ok]
    assert backslash_only and drive_only, "对拍集丢了分支样本（判据会重新失明）"
    # 「只查首段冒号」这条边界两侧都必须存在（首段无冒号 ⇒ 合法）
    assert packer.validate_relative_member("a/b:c.py", field="script") is None
    assert gate.validate_relative_member("a/b:c.py", field="script") is None
    assert consumer._relative_member_error("a/b:c.py") is None


def test_gate_tolerates_non_str_without_crashing() -> None:
    """门禁侧额外承担 JSON 里的 null/数字：不得抛异常（工具侧调用点已先行 str 化）。"""
    for bad in (None, 7, ["x"]):
        assert gate.validate_relative_member(bad, field="script") is not None  # type: ignore[arg-type]
        assert consumer._relative_member_error(bad) is not None
