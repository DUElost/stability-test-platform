"""#2265 守卫：Agent 时区由站点声明驱动，不再硬编码。

背景：`set_timezone.yml` 原把 `tz_target` 写死 `Asia/Shanghai`（连 `tz_expected_offset: "+0800"`
一起），而控制面 OS 与 `site.timezone` 都无人保证——238 现场出现「声明 UTC / 控制面 PDT /
Agent CST」三面矛盾（差 15 小时，审计与心跳窗口整体错位）。本文件锁定：

1. 目标时区必须引用 `agent_timezone`（站点声明经 install_options 下发）；
2. 不得回退成硬编码的目标/偏移字面量；
3. 期望偏移必须由目标时区现算（避免换时区时忘记改偏移）。
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
PLAYBOOK = REPO_ROOT / "tools/ansible/playbooks/set_timezone.yml"


def _vars() -> dict:
    plays = yaml.safe_load(PLAYBOOK.read_text(encoding="utf-8"))
    return plays[0]["vars"]


def test_target_timezone_follows_the_declaration():
    """目标值必须来自 agent_timezone（fallback 链里的默认字面量是允许的）。"""
    expr = str(_vars()["tz_target"])
    assert "agent_timezone" in expr, "tz_target 未引用 agent_timezone（回退成硬编码了）"
    assert "|" in expr and "default(" in expr, "tz_target 必须是带缺省的派生表达式"
    assert expr.strip() not in {"Asia/Shanghai", "'Asia/Shanghai'", '"Asia/Shanghai"'}


def test_expected_offset_is_computed_from_the_target():
    expr = str(_vars()["tz_expected_offset"])
    assert "tz_target" in expr, "期望偏移必须由 tz_target 现算"
    assert "+0800" not in expr, "偏移不得写死（换时区会忘改）"


def test_controller_timezone_is_the_fallback_not_a_hardcode():
    """缺 agent_timezone 时跟随控制面本机时区，最后才落历史默认。"""
    expr = str(_vars()["tz_target"])
    assert "tz_controller" in expr
    controller = str(_vars()["tz_controller"])
    assert "/etc/timezone" in controller or "timedatectl" in controller
