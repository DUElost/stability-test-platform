"""#2958 可达集脚本：纯函数层锁口径（全集 / 两层可达 / n/a）。

锁五件事：
① 全集 = 启用步骤 ∩ active 脚本（与运行快照同口径，`enabled` 过滤是口径的一部分）；
② `device_ids` 的三种形态（NULL / list / JSON 字符串）都不得炸、不得静默丢设备；
③ 调度可达用**设备当前绑定**的 host，未绑定设备的计划不落到任何 host；
④ 可达集必须再与全集求交——停用/未注册版本即使被计划引用也不进可达集，
   否则 n/a 差集会被「超出全集的幽灵目标」污染；
⑤ 无任何来源的 host：可达集为空、n/a = 全集（如实暴露「没归属/没跑过」，不判绿）。
"""
from __future__ import annotations

from backend.scripts.compute_host_script_targets import summarize
from backend.services.script_presence import (
    build_full_target_set,
    compute_host_targets,
    group_steps_by_plan,
    observed_host_plans,
    parse_device_ids,
    scheduled_host_plans,
)


def _steps(*rows):
    return [
        {"plan_id": p, "script_name": n, "script_version": v}
        for p, n, v in rows
    ]


def test_full_target_set_is_steps_intersect_active_scripts():
    steps = _steps((1, "a", "1.0.0"), (1, "b", "1.0.0"))
    scripts = [
        {"name": "a", "version": "1.0.0", "is_active": True},
        {"name": "b", "version": "1.0.0", "is_active": False},   # 停用 → 不进全集
        {"name": "ghost", "version": "9.9.9", "is_active": True},  # active 但无启用步骤引用
        {"name": "idle", "version": "1.0.0", "is_active": True},   # 无引用
    ]
    assert build_full_target_set(steps, scripts) == [("a", "1.0.0")]


def test_parse_device_ids_variants():
    assert parse_device_ids(None) == set()
    assert parse_device_ids([1, 2]) == {1, 2}
    assert parse_device_ids([1, "2", "x", None]) == {1, 2}      # 非法项忽略、不炸
    assert parse_device_ids("[3, 4]") == {3, 4}                  # JSON 字符串形态
    assert parse_device_ids("not-json") == set()
    assert parse_device_ids({"a": 1}) == set()


def test_scheduled_reachability_uses_current_device_binding():
    schedules = [
        {"plan_id": 5, "device_ids": [1, 2]},   # device 2 未绑定 → 只落 h1
        {"plan_id": 6, "device_ids": None},     # 空清单 → 不落任何 host
        {"plan_id": 7, "device_ids": "[3]"},    # 字符串形态
    ]
    devices = [
        {"id": 1, "host_id": "h1"},
        {"id": 3, "host_id": "h2"},
    ]
    assert scheduled_host_plans(schedules, devices) == {"h1": {5}, "h2": {7}}


def test_observed_reachability_groups_and_dedupes():
    runs = [
        {"plan_id": 5, "host_id": "h1"},
        {"plan_id": 5, "host_id": "h1"},   # 重复行（多 run 同一 plan）
        {"plan_id": 6, "host_id": "h1"},
        {"plan_id": 6, "host_id": "h2"},
        {"plan_id": 7, "host_id": None},   # 脏行忽略
    ]
    assert observed_host_plans(runs) == {"h1": {5, 6}, "h2": {6}}


def test_compute_targets_union_of_two_sources_and_na():
    steps = _steps(
        (1, "a", "1.0.0"), (1, "b", "1.0.0"),   # 调度可达
        (2, "c", "2.0.0"),                      # 历史可达
        (3, "d", "3.0.0"),                      # 无来源 → 只应出现在 n/a
        (4, "off", "1.0.0"),                    # 被计划引用但脚本停用 → 不进全集
    )
    scripts = [
        {"name": "a", "version": "1.0.0", "is_active": True},
        {"name": "b", "version": "1.0.0", "is_active": True},
        {"name": "c", "version": "2.0.0", "is_active": True},
        {"name": "d", "version": "3.0.0", "is_active": True},
        {"name": "off", "version": "1.0.0", "is_active": False},
    ]
    full = build_full_target_set(steps, scripts)
    assert full == [("a", "1.0.0"), ("b", "1.0.0"), ("c", "2.0.0"), ("d", "3.0.0")]

    out = compute_host_targets(
        [{"id": "h1", "status": "ONLINE"}, {"id": "h2", "status": "OFFLINE"}],
        full=full,
        by_plan=group_steps_by_plan(steps),
        scheduled={"h1": {1, 4}},
        observed={"h1": {2}},
    )
    h1, h2 = out
    assert h1["reachable"] == [("a", "1.0.0"), ("b", "1.0.0"), ("c", "2.0.0")]
    assert h1["n_a"] == [("d", "3.0.0")]                    # 有归属但没跑过 d
    assert (h1["scheduled_plans"], h1["observed_plans"]) == (2, 1)
    assert h2["reachable"] == [] and h2["n_a"] == full      # 无来源 → 如实暴露
    assert all(pair != ("off", "1.0.0") for h in out for pair in h["reachable"])


def test_summarize_counts_union_coverage():
    full = [("a", "1.0.0"), ("b", "1.0.0"), ("c", "2.0.0")]
    targets = [
        {"host_id": "h1", "reachable": [("a", "1.0.0")], "n_a": [("b", "1.0.0"), ("c", "2.0.0")]},
        {"host_id": "h2", "reachable": [("a", "1.0.0"), ("b", "1.0.0")], "n_a": [("c", "2.0.0")]},
    ]
    s = summarize(targets, full)
    assert s["full_versions"] == 3 and s["full_families"] == 3
    assert s["reachable_cells"] == 3 and s["reachable_min"] == 1 and s["reachable_max"] == 2
    assert s["union_covered_versions"] == 2 and s["union_missing_versions"] == 1
    assert s["hosts_with_gap_free"] == 0
