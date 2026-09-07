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
