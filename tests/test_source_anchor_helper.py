"""`tools/dev/source_anchor.py` 的自证测试（#2639）。

写这个助手不是为了少敲几行，而是为了让**两类红**不再长得一样：

- 「用例已过期（锚点漂移）」= 被扫逻辑搬走了，该修测试；
- 「防线回归（真实形态退化）」= 逻辑还在，产品代码退化了，该修代码。

因此本文件的主判据不是「助手能跑」，而是**三类失败的 distinguishability**。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.dev.source_anchor import (
    ANCHOR_DRIFT_PREFIX,
    FORM_REGRESSION_PREFIX,
    MISUSE_PREFIX,
    AnchorDrift,
    FormRegression,
    GuardMisuse,
    SourceGuard,
)

# 模拟 #2025 那条防线的宿主模块：真源在此，裸赋值不在
HOST_SRC = """
def create_row(ev):
    return Model(state=resolve_initial_upload_state(ev.event_type, ev.state))


def update_row(row, ev):
    target_state = resolve_initial_upload_state(ev.event_type, ev.state)
    row.state = target_state
"""

# 模拟 #2639 第 3 例：逻辑整段搬走后留在原地的文件（既没有归一调用，也没有裸赋值）
HOLLOWED_SRC = """
def create_row(ev):
    return Model(state=ev.state)
"""


def _guard(text: str) -> SourceGuard:
    return SourceGuard(text, origin="inline-fixture").anchored(
        "resolve_initial_upload_state(ev.event_type, ev.state)"
    )


def test_absent_needle_is_a_form_regression_not_drift() -> None:
    guard = _guard(HOST_SRC + '\nrow.state = ev.state  # 回潮\n')
    with pytest.raises(FormRegression) as exc:
        guard.assert_absent("row.state = ev.state", why="#2025 裸赋值不得回潮")
    assert str(exc.value).startswith(FORM_REGRESSION_PREFIX)


def test_hollowed_host_reports_stale_case_not_regression() -> None:
    """宿主被搬空时**不得**报「防线回归」——那会把人引向不存在的产品缺陷。

    #2639 第 3 例的原始症状是否定断言恒真：这里更进一步，锚点先失败，
    红侧消息必须说「用例已过期」并指向要改的测试。
    """
    with pytest.raises(AnchorDrift) as exc:
        SourceGuard(HOLLOWED_SRC, origin="hollowed").anchored(
            "resolve_initial_upload_state(ev.event_type, ev.state)"
        )
    message = str(exc.value)
    assert message.startswith(ANCHOR_DRIFT_PREFIX)
    assert "用例" in message and "真源模块" in message


def test_three_failure_kinds_are_pairwise_distinct(tmp_path: Path) -> None:
    """三类失败的前缀两两不同（同前缀就等于没区分）。"""
    with pytest.raises(AnchorDrift) as e1:
        SourceGuard("nothing here", origin="m").anchored("needle")
    with pytest.raises(FormRegression) as e2:
        _guard(HOST_SRC).assert_present("row.state = target_state_x", why="t")
    with pytest.raises(GuardMisuse) as e3:
        SourceGuard(HOST_SRC, origin="m").assert_absent("needle", why="t")
    # 先要求**三个前缀常量本身**互不相同：只比消息全文时，两类红共用同一前缀
    # 仍会因为后半句不同而"看起来可区分"（实测的弱判据）。
    assert len({ANCHOR_DRIFT_PREFIX, FORM_REGRESSION_PREFIX, MISUSE_PREFIX}) == 3, (
        "三类红的消息前缀常量彼此相同——失败原因无法从红侧第一行区分"
    )
    msgs = {
        "用例过期": str(e1.value).splitlines()[0],
        "防线回归": str(e2.value).splitlines()[0],
        "空守": str(e3.value).splitlines()[0],
    }
    assert len(set(msgs.values())) == 3, msgs
    assert msgs["用例过期"].startswith(ANCHOR_DRIFT_PREFIX), msgs
    assert msgs["防线回归"].startswith(FORM_REGRESSION_PREFIX), msgs
    assert msgs["空守"].startswith(MISUSE_PREFIX), msgs


def test_anchor_count_drift_is_caught() -> None:
    """锚点被复制/部分搬走（次数变了）也算漂移——只判「≥1」会放过这一种。"""
    with pytest.raises(AnchorDrift) as exc:
        SourceGuard(HOST_SRC, origin="m").anchored(
            "resolve_initial_upload_state(ev.event_type, ev.state)", expect=5
        )
    assert "命中 2 次" in str(exc.value)


def test_missing_file_is_drift_not_filenotfound() -> None:
    """被扫文件改名/删除必须是「用例已过期」，不能退成 FileNotFoundError。"""
    with pytest.raises(AnchorDrift) as exc:
        SourceGuard.of_repo_path("backend/services/no_such_module_any_more.py")
    assert "被扫文件不存在" in str(exc.value)


def test_of_repo_path_reads_real_repo_file() -> None:
    guard = SourceGuard.of_repo_path("tools/dev/source_anchor.py")
    assert guard.origin == "tools/dev/source_anchor.py"
    assert "class SourceGuard" in guard.text
    guard.anchored("def assert_absent(")


def test_of_module_rejects_files_outside_repo() -> None:
    """守卫站外代码（stdlib/三方包）按写法不合法处理——它的字面量不受本仓重构约束。"""
    with pytest.raises(GuardMisuse):
        SourceGuard.of_module(json)


def test_of_module_resolves_repo_module() -> None:
    from tools.site_config import models

    guard = SourceGuard.of_module(models)
    assert not Path(guard.origin).is_absolute()
    assert "class " in guard.text


def test_assertions_cannot_run_before_anchor() -> None:
    """`assert_*` 在未声明锚点前一律拒绝执行（恒真断言的入口被封死）。"""
    bare = SourceGuard("whatever", origin="m")
    for call in (
        lambda: bare.assert_absent("x", why="t"),
        lambda: bare.assert_present("x"),
        lambda: bare.assert_count("x", 1),
    ):
        with pytest.raises(GuardMisuse) as exc:
            call()
        assert str(exc.value).startswith(MISUSE_PREFIX)
    assert bare.anchors == ()
    bare.anchored("whatever")
    assert bare.anchors == ("whatever",)
    bare.assert_absent("not there at all", why="t")  # 有锚点后正常放行
