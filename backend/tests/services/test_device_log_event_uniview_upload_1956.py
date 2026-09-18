"""#1956 — 无 scan 门禁平台（展锐 UNIVIEW）的事件必须可被上送。

设计依据（不是我新立的规则，而是既有语义的自洽修正）：

* ``device_log_event.py`` 的 ``count_pending_upload_events`` 注释：LOCAL 是
  「已被 scan 但未被 xls 引用 → 有意不传」；
* ``saq_tasks.py`` 里既有 "barrier … before UNISOC uploads land" 的预期；
* Agent 侧 ``event_uploader._recover_states()`` **只**上送 ``UPLOAD_PENDING``。

展锐没有 scan 产物（其有效性由 Agent 解析期判定，#1946），因此入库即可上送，
否则会永远停在 LOCAL——即外部观察到的「DLE 有行、状态不动」。
"""

from __future__ import annotations

from backend.services import agent_device_log_events
from backend.services.device_log_event import resolve_initial_upload_state
from tools.dev.source_anchor import SourceGuard


def test_uniview_local_is_promoted_to_upload_pending():
    assert resolve_initial_upload_state("UNIVIEW", "LOCAL") == "UPLOAD_PENDING"
    assert resolve_initial_upload_state("uniview", "DETECTED") == "UPLOAD_PENDING"


def test_mtk_semantics_unchanged():
    # MTK（AEE 家族）保持「scan xls 引用后才传」：LOCAL 原样保留
    assert resolve_initial_upload_state("AEE", "LOCAL") == "LOCAL"
    assert resolve_initial_upload_state("JE", "LOCAL") == "LOCAL"
    # 已在流程中的状态不被改写
    assert resolve_initial_upload_state("UNIVIEW", "REMOTE") == "REMOTE"
    assert resolve_initial_upload_state("UNIVIEW", "UPLOADING") == "UPLOADING"


def test_all_ingest_sites_use_the_helper():
    """防线：三处 DLE 落库点（``agent_device_log_events``）都必须接入归一（创建 / 预分配 id 重试 / 更新分支）。

    #2025 的反例：更新分支原先写裸 `row.state = ev.state`，而此前那条
    `"state=ev.state" not in src` 因多了 `row.` 前缀与空格**抓不到它**——
    字面断言必须覆盖赋值形态本身，否则守的是「措辞」不是「行为」。

    #2639 的反例（本用例自己就是第 3 例）：落库点随 #1520 从 `agent_api` 搬到本模块后，
    守卫仍读旧文件，于是**两条否定断言恒真**了一个完整窗口——红的是正向断言，
    否定断言静默失效。现在锚点不在场会被判「用例已过期」，与「防线回归」可区分。
    """
    guard = (
        SourceGuard.of_module(agent_device_log_events)
        .anchored("resolve_initial_upload_state(ev.event_type, ev.state)")
    )
    # 创建两处：直接以归一值构造模型
    guard.assert_count("state=resolve_initial_upload_state(ev.event_type, ev.state)", 2)
    # 更新一处：先归一为 target_state，再做迁移校验与赋值（#2025）
    guard.assert_present("target_state = resolve_initial_upload_state(ev.event_type, ev.state)")
    guard.assert_present("row.state = target_state")
    # 任何形态的裸赋值都不得回潮
    guard.assert_absent("state=ev.state", why="#2025 绕开归一的裸赋值不得回潮")
    guard.assert_absent("row.state = ev.state", why="#2025 更新分支不得直接写事件原状态")
