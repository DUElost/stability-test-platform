"""check_schema_sync 守卫回归（#934/#944）。纯函数层，不连库。

- #934：`_run_upgrade` 必须在调用 alembic 前把目标库写进 ambient
  `DATABASE_URL`——`alembic/env.py` 模块导入即用环境解析结果覆写 config
  URL（alembic.ini 自带 sqlite 占位使「显式传入优先」不可行），ambient
  是解析顺序最高位，是唯一防住「测试目标被 .env.backend 生产配置顶掉」
  的通道。
- #944：成对白名单语义——add_/remove_ 同对象同时进基线只豁免 diff 成对
  共现（autogenerate 渲染差异的既知噪音）；索引完全丢失（单边 add_index）
  必须拦截。
"""
from __future__ import annotations

import json
import os
import sys

import backend.scripts.check_schema_sync as css


def test_run_upgrade_sets_ambient_database_url(monkeypatch):
    """#934：upgrade 时刻 ambient DATABASE_URL 必须等于显式目标库。"""
    captured: dict[str, str | None] = {}

    def fake_upgrade(cfg, rev):
        captured["db"] = os.environ.get("DATABASE_URL")

    monkeypatch.setattr(css.command, "upgrade", fake_upgrade)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    css._run_upgrade("postgresql+psycopg://u:p@127.0.0.1:5599/guard")
    assert captured["db"] == "postgresql+psycopg://u:p@127.0.0.1:5599/guard"
    # 脚本是一次性进程，ambient 写回无害；测试内清理防串扰
    monkeypatch.delenv("DATABASE_URL", raising=False)


def test_paired_baseline_excuses_pair_only():
    """#944 核心：成对基线项只豁免成对共现。"""
    baseline = {"add_index|t|idx", "remove_index|t|idx"}
    # 成对共现（表达式渲染差异形态）→ 豁免
    assert css._filter_new_keys(
        ["add_index|t|idx", "remove_index|t|idx"], baseline
    ) == []
    # 索引完全丢失只出单边 add → 拦截（issue 复现形态）
    assert css._filter_new_keys(["add_index|t|idx"], baseline) == ["add_index|t|idx"]
    # 单边 remove（库多出模型没有的索引）同样拦截
    assert css._filter_new_keys(["remove_index|t|idx"], baseline) == [
        "remove_index|t|idx"
    ]


def test_baseline_outside_keys_still_fail():
    """基线外新漂移照拦（原语义不回退）。"""
    assert css._filter_new_keys(["add_index|t|other"], set()) == ["add_index|t|other"]
    assert css._filter_new_keys(
        ["add_index|t|other"], {"add_index|t|idx"}
    ) == ["add_index|t|other"]


def test_counterpart_key_families():
    assert css._counterpart_key("add_index|plan_run|i") == "remove_index|plan_run|i"
    assert css._counterpart_key("remove_fk|t|a,b") == "add_fk|t|a,b"
    assert css._counterpart_key("remove_table|x") == "add_table|x"
    assert css._counterpart_key("modify_type|t|c") == ""


def test_real_baseline_admission_pair_semantics():
    """真实基线的 admission 索引成对项：渲染噪音豁免、丢失形态拦截。"""
    baseline = set(json.loads(css._BASELINE_FILE.read_text(encoding="utf-8")))
    add_key = "add_index|plan_run|idx_plan_run_admission_queue"
    remove_key = "remove_index|plan_run|idx_plan_run_admission_queue"
    assert add_key in baseline and remove_key in baseline
    # 当前实测 diff 形态（remove+add 成对，表达式 text 渲染差异）→ 豁免
    assert css._filter_new_keys([remove_key, add_key], baseline) == []
    # 索引丢失形态 → 拦截
    assert css._filter_new_keys([add_key], baseline) == [add_key]


def test_baseline_no_longer_whitelists_action_template_ghost():
    """#1890：表真删后基线不得继续兜住 remove_table|action_template。"""
    baseline = set(json.loads(css._BASELINE_FILE.read_text(encoding="utf-8")))
    assert "remove_table|action_template" not in baseline
    assert "remove_index|action_template|ix_action_template_active" not in baseline


# ── #708：基线缩水的信号（噪音被上游修掉时，只有人跑一次 --rebaseline 才拿得到）──────
_REAL_BASELINE = {
    "add_index|plan_run|idx_plan_run_admission_queue",
    "modify_type|alert_rules|event_type",
    "modify_type|jira_run|issue_keys",
    "modify_type|notification_channels|type",
    "remove_index|plan_run|idx_plan_run_admission_queue",
}


def test_current_diff_against_real_baseline_has_no_stale_entries():
    """今天（SQLAlchemy 2.0.52 / alembic 1.19.1）空库实测就是这 5 项 → 不得冒出 stale 提示。

    这条同时是 #708 的复核结论：**触发条件未达成**，5 项 alembic 比较噪音一项未缩。
    """
    assert css._stale_baseline_keys(sorted(_REAL_BASELINE), set(_REAL_BASELINE)) == []


def test_converged_noise_item_is_reported_stale():
    """某项整条消失（上游修好）→ 必须点出来，否则基线会停在旧尺寸继续豁免它。"""
    keys = [k for k in sorted(_REAL_BASELINE) if k != "modify_type|jira_run|issue_keys"]
    assert css._stale_baseline_keys(keys, set(_REAL_BASELINE)) == [
        "modify_type|jira_run|issue_keys"
    ]


def test_one_sided_pair_is_not_reported_as_converged():
    """判别力所在：对偶只剩一边出现在 diff 里，是 #944 的**拦截**形态，不是收敛。

    若把它算成 stale，输出就会在同一次运行里既喊「新增漂移」又喊「可以缩基线」，
    诱导人在错误的时刻跑覆盖式 --rebaseline——那正好把该拦的单边形态洗进新基线。
    """
    keys = [
        "add_index|plan_run|idx_plan_run_admission_queue",  # 对偶 remove_ 缺席
        "modify_type|alert_rules|event_type",
        "modify_type|jira_run|issue_keys",
        "modify_type|notification_channels|type",
    ]
    assert css._stale_baseline_keys(keys, set(_REAL_BASELINE)) == [], (
        "单边形态不得冒充收敛提示"
    )
    assert css._filter_new_keys(keys, set(_REAL_BASELINE)) == [
        "add_index|plan_run|idx_plan_run_admission_queue"
    ]


def _diff_for(key: str):
    """造一条能让真实 `_diff_key()` 还原成该 key 的 diff 项（不 stub 掉 key 规范化）。"""
    kind, table, name = key.split("|")
    if kind in ("add_index", "remove_index"):

        class _Idx:
            def __init__(self, tname, iname):
                self.table = type("T", (), {"name": tname})()
                self.name = iname

        return (kind, _Idx(table, name))
    return (kind, None, table, name)  # modify_type 走 f"{kind}|{d[2]}|{d[3]}"


def test_main_prints_hint_but_keeps_exit_zero(tmp_path, monkeypatch, capsys):
    """信号到位但**不判红**：exit 仍 0，HINT 列出未命中项。"""
    baseline_file = tmp_path / "schema_sync_baseline.json"
    baseline_file.write_text(json.dumps(sorted(_REAL_BASELINE)), encoding="utf-8")
    remaining = [k for k in sorted(_REAL_BASELINE) if k != "modify_type|jira_run|issue_keys"]

    monkeypatch.setattr(css, "_BASELINE_FILE", baseline_file)
    monkeypatch.setattr(css, "_resolve_url", lambda: "postgresql+psycopg://x@127.0.0.1:1/none")
    monkeypatch.setattr(css, "_run_upgrade", lambda db_url: None)
    monkeypatch.setattr(css, "_collect_diffs", lambda db_url: [_diff_for(k) for k in remaining])
    monkeypatch.setattr(sys, "argv", ["check_schema_sync"])

    assert css.main() == 0, "基线缩水不是失败——判红会把无关 PR 一起拦下"
    out = capsys.readouterr().out
    assert "基线未命中 1" in out, out
    assert "[stale] modify_type|jira_run|issue_keys" in out, out
    assert "rebaseline" in out  # 提示要说清下一步是谁、动作是什么


def test_main_does_not_hint_when_baseline_fully_exercised(tmp_path, monkeypatch, capsys):
    """反向对照：5 项全命中时不得冒出 HINT（否则提示沦为常驻噪声，很快被忽略）。"""
    baseline_file = tmp_path / "schema_sync_baseline.json"
    baseline_file.write_text(json.dumps(sorted(_REAL_BASELINE)), encoding="utf-8")

    monkeypatch.setattr(css, "_BASELINE_FILE", baseline_file)
    monkeypatch.setattr(css, "_resolve_url", lambda: "postgresql+psycopg://x@127.0.0.1:1/none")
    monkeypatch.setattr(css, "_run_upgrade", lambda db_url: None)
    monkeypatch.setattr(css, "_collect_diffs",
                        lambda db_url: [_diff_for(k) for k in sorted(_REAL_BASELINE)])
    monkeypatch.setattr(sys, "argv", ["check_schema_sync"])

    assert css.main() == 0
    out = capsys.readouterr().out
    assert "基线未命中 0" in out
    assert "HINT" not in out and "[stale]" not in out
