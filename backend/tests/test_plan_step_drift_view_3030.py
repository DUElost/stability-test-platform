"""#3030 重指漂移视图：磁盘 head vs plan_step 钉版（纯函数，合成事实）。

锁四件事（含 owner 两轴裁决，2026-09-21）：
① 判据自证——pin 改成 head 必须从视图消失；族在磁盘无 head 必须跳过并计数（无从
   判定 ≠ 不落后）；
② 轴 2 活跃度：以 PlanRun 为主判据（14d/30d 两界），schedule 只作补充；
③ 轴 1 Δ 类型三态与优先级：review_required（登记表）> metadata_diff（DB 元数据差异
   或库缺行）> metadata_compatible；
④ 登记表复查期过期（含值不可解析）必须单列「须重新裁决」——不许沉默冻结。
"""
from __future__ import annotations

from datetime import date

from backend.scripts.check_unreferenced_script_versions import (
    classify_plan_activity,
    plan_step_drift_view,
)

TODAY = date(2026, 9, 21)


def _step(plan_id=1, name="check_device", version="1.0.0", last_run=None, sched=False,
          plan_name="plan-x"):
    return {
        "plan_id": plan_id,
        "plan_name": plan_name,
        "script_name": name,
        "script_version": version,
        "last_run_on": last_run,
        "schedule_enabled": sched,
    }


def _meta(*triples):
    """(name, version) -> (default_params, param_schema) 便捷构造。"""
    return {(n, v): (dp, ps) for n, v, dp, ps in triples}


# ── ① 判据自证 ──────────────────────────────────────────────────────────────

def test_pin_at_head_not_listed():
    """pin == 磁盘 head → 不出现在落后集合（视图空 = 该步已追平）。"""
    disk = {"check_device": ["1.0.0", "1.0.2"]}
    steps = [_step(version="1.0.2")]
    view = plan_step_drift_view(disk, steps, _meta(), today=TODAY)
    assert view["steps"] == []
    assert view["summary"]["lagging_steps"] == 0


def test_family_without_disk_head_skipped_and_counted():
    """族在磁盘无 head（未收录/已删）→ 跳过并计数；无从判定不得静默算作已追平。"""
    view = plan_step_drift_view({}, [_step(name="ghost_tool")], _meta(), today=TODAY)
    assert view["steps"] == []
    assert view["summary"]["skipped_no_disk_head"] == 1


def test_head_version_uses_numeric_ordering():
    """head 取数值序最大（1.0.10 > 1.0.9；字典序会选错——同 #2931 教训）。"""
    disk = {"monkey_setup": ["2.3.9", "2.3.10"]}
    view = plan_step_drift_view(disk, [_step(name="monkey_setup", version="2.3.9")],
                                _meta(), today=TODAY)
    assert view["steps"][0]["head_version"] == "2.3.10"


# ── ② 轴 2 活跃度 ──────────────────────────────────────────────────────────

def test_activity_boundaries():
    assert classify_plan_activity(date(2026, 9, 7), False, today=TODAY) == "active"     # 14d
    assert classify_plan_activity(date(2026, 9, 6), False, today=TODAY) == "semi_active"  # 15d
    assert classify_plan_activity(date(2026, 8, 22), False, today=TODAY) == "semi_active"  # 30d
    assert classify_plan_activity(date(2026, 8, 21), False, today=TODAY) == "historical"   # 31d
    assert classify_plan_activity(None, False, today=TODAY) == "historical"
    # schedule 是补充判据：有启用 schedule 时即便很久没跑也算 active
    assert classify_plan_activity(date(2026, 1, 1), True, today=TODAY) == "active"


def test_view_buckets_and_counts_by_activity():
    disk = {"check_device": ["1.0.2"]}
    steps = [
        _step(plan_id=1, last_run=date(2026, 9, 21)),   # active
        _step(plan_id=2, last_run=date(2026, 9, 5)),    # semi_active（16d）
        _step(plan_id=3, last_run=None),                # historical
    ]
    view = plan_step_drift_view(disk, steps, _meta(), today=TODAY, registry={})
    assert view["summary"]["by_activity"] == {"active": 1, "semi_active": 1, "historical": 1}
    assert view["summary"]["lagging_plans"] == 3


# ── ③ 轴 1 Δ 类型 ─────────────────────────────────────────────────────────

def test_delta_metadata_compatible_by_default():
    disk = {"check_device": ["1.0.2"]}
    meta = _meta(("check_device", "1.0.0", "{}", "{}"),
                 ("check_device", "1.0.2", "{}", "{}"))
    view = plan_step_drift_view(disk, [_step()], meta, today=TODAY, registry={})
    assert view["steps"][0]["delta_type"] == "metadata_compatible"


def test_builtin_registry_marks_check_device_review_required():
    """默认登记表（内置复核项）必须命中——check_device 的预算风险是登记而非自动化判据。"""
    disk = {"check_device": ["1.0.2"]}
    meta = _meta(("check_device", "1.0.0", "{}", "{}"),
                 ("check_device", "1.0.2", "{}", "{}"))
    view = plan_step_drift_view(disk, [_step()], meta, today=TODAY)
    assert view["steps"][0]["delta_type"] == "review_required"
    assert "#2981" in view["steps"][0]["review_reason"]


def test_delta_metadata_diff_on_declared_param_change():
    disk = {"install_apk": ["1.1.0"]}
    meta = _meta(("install_apk", "1.0.1", '{"a": 1}', "{}"),
                 ("install_apk", "1.1.0", '{"a": 2}', "{}"))
    view = plan_step_drift_view(disk, [_step(name="install_apk", version="1.0.1")], meta,
                                today=TODAY, registry={})
    assert view["steps"][0]["delta_type"] == "metadata_diff"


def test_delta_metadata_diff_when_db_row_missing():
    """head 在库缺行 → 保守判 metadata_diff（元数据对拍不可得，不冒充「兼容」）。"""
    disk = {"check_device": ["1.0.2"]}
    view = plan_step_drift_view(disk, [_step()], _meta(), today=TODAY, registry={})
    assert view["steps"][0]["delta_type"] == "metadata_diff"


def test_review_registry_takes_priority_over_metadata_diff():
    """登记表命中优先标 review_required，并带上 reason + 复查期。"""
    disk = {"check_device": ["1.0.2"]}
    reg = {"family:check_device": {"reason": "需 timeout ≥180s（#2981）",
                                   "review_by": "2026-10-21"}}
    meta = _meta(("check_device", "1.0.0", '{"a": 1}', "{}"),
                 ("check_device", "1.0.2", '{"a": 2}', "{}"))
    view = plan_step_drift_view(disk, [_step()], meta, today=TODAY, registry=reg)
    item = view["steps"][0]
    assert item["delta_type"] == "review_required"
    assert item["review_by"] == "2026-10-21"
    assert "#2981" in item["review_reason"]
    assert view["summary"]["by_delta"]["review_required"] == 1


def test_plan_level_registry_entry_wins_over_family():
    disk = {"check_device": ["1.0.2"]}
    reg = {
        "family:check_device": {"reason": "family", "review_by": "2026-10-21"},
        "plan:7": {"reason": "该 Plan 停用待归档", "review_by": "2026-11-01"},
    }
    view = plan_step_drift_view(disk, [_step(plan_id=7)], _meta(), today=TODAY, registry=reg)
    assert view["steps"][0]["review_reason"] == "该 Plan 停用待归档"


# ── ④ 登记表复查期 ─────────────────────────────────────────────────────────

def test_expired_review_entry_surfaced():
    disk = {"check_device": ["1.0.2"]}
    reg = {"family:check_device": {"reason": "x", "review_by": "2026-09-01"}}
    view = plan_step_drift_view(disk, [_step()], _meta(), today=TODAY, registry=reg)
    assert view["expired_review_entries"] == [
        {"key": "family:check_device", "reason": "x", "review_by": "2026-09-01"}
    ]


def test_unparsable_review_by_treated_as_expired():
    """复查期不可解析 = 登记失效，按过期处理（宁红不漏）。"""
    disk = {"check_device": ["1.0.2"]}
    reg = {"family:check_device": {"reason": "x", "review_by": "下个月"}}
    view = plan_step_drift_view(disk, [_step()], _meta(), today=TODAY, registry=reg)
    assert len(view["expired_review_entries"]) == 1


def test_live_review_entry_not_flagged_expired():
    disk = {"check_device": ["1.0.2"]}
    reg = {"family:check_device": {"reason": "x", "review_by": "2026-10-21"}}
    view = plan_step_drift_view(disk, [_step()], _meta(), today=TODAY, registry=reg)
    assert view["expired_review_entries"] == []


# ── 过滤 ───────────────────────────────────────────────────────────────────

def test_name_filter_narrows_output():
    disk = {"a": ["2.0.0"], "b": ["2.0.0"]}
    steps = [_step(plan_id=1, name="a"), _step(plan_id=2, name="b")]
    view = plan_step_drift_view(disk, steps, _meta(), today=TODAY, name_filter="b")
    assert [s["script_name"] for s in view["steps"]] == ["b"]
