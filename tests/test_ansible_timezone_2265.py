"""#2265 守卫：Agent 时区由站点声明驱动，不再硬编码。

背景：`set_timezone.yml` 原把 `tz_target` 写死 `Asia/Shanghai`（连 `tz_expected_offset: "+0800"`
一起），而控制面 OS 与 `site.timezone` 都无人保证——238 现场出现「声明 UTC / 控制面 PDT /
Agent CST」三面矛盾（差 15 小时，审计与心跳窗口整体错位）。本文件锁定：

1. 目标时区必须引用 `agent_timezone`（站点声明经 install_options 下发）；
2. 不得回退成硬编码的目标/偏移字面量；
3. 期望偏移必须由目标时区现算（避免换时区时忘记改偏移）；
4. #2317：`timedatectl` 需要 system D-Bus——无 bus 的目标必须有**文件级回退**
   （zoneinfo 链接 + /etc/timezone），且回退后仍走同一条断言。
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


def _tasks() -> list:
    plays = yaml.safe_load(PLAYBOOK.read_text(encoding="utf-8"))
    return plays[0]["tasks"]


def _task(name_prefix: str) -> dict:
    return next(t for t in _tasks() if t.get("name", "").startswith(name_prefix))


def test_set_timezone_has_a_file_level_fallback():
    """#2317：`timedatectl` 连不上 system bus 时必须退化为文件级对齐。

    容器 / 精简镜像上没有 system D-Bus，`timedatectl set-timezone` 直接非零退出——重装
    被阻断（I4 实验室 10.99.0.11/12 现场）。文件层面（zoneinfo 链接 + /etc/timezone）
    同样能对齐，故用 block/rescue 兜住。
    """
    task = _task("Set timezone to")

    assert "block" in task and "rescue" in task, "缺 rescue —— 无 bus 目标会直接判失败"
    rescue_names = [step.get("name", "") for step in task["rescue"]]
    assert any("file level" in name for name in rescue_names), rescue_names

    fallback = next(step for step in task["rescue"] if "file level" in step.get("name", ""))
    script = str(fallback["ansible.builtin.shell"])
    assert "ln -sfn /usr/share/zoneinfo/" in script and "/etc/localtime" in script, script
    assert "/etc/timezone" in script, "文件级回退必须同时写 /etc/timezone（zoneinfo 链接之外的第二面）"


def test_rescue_reports_why_timedatectl_failed():
    """失败路径要可读（不再只有一句「非零返回」）。"""
    task = _task("Set timezone to")
    debug = next(
        (step for step in task["rescue"] if step.get("ansible.builtin.debug")), None,
    )
    assert debug is not None, "rescue 未说明原因"
    msg = str(debug["ansible.builtin.debug"]["msg"])
    assert "rc=" in msg and "timedatectl" in msg, msg


def test_target_shape_is_asserted_before_any_write():
    """#2317：文件级回退把 tz_target 拼进路径/写入 /etc/timezone——先用形状断言兜住。"""
    names = [t.get("name", "") for t in _tasks()]
    shape_idx = next(i for i, n in enumerate(names) if "zoneinfo name" in n)
    set_idx = next(i for i, n in enumerate(names) if n.startswith("Set timezone to"))
    assert shape_idx < set_idx, "形状断言必须在写入动作之前"

    assert_task = _tasks()[shape_idx]
    that = [str(clause) for clause in assert_task["ansible.builtin.assert"]["that"]]
    assert any("is match(" in clause and "tz_target" in clause for clause in that), that


def _flat_tasks() -> list[dict]:
    """展开 play 的 tasks（含 block/rescue/always 内的步骤）。"""
    out: list[dict] = []

    def walk(steps: list | None) -> None:
        for step in steps or []:
            if not isinstance(step, dict):
                continue
            out.append(step)
            for key in ("block", "rescue", "always"):
                walk(step.get(key))

    walk(_tasks())
    return out


def test_rescue_registered_vars_are_preinitialized():
    """#2410：**rescue 路径才 register** 的变量，必须在 play 内先有定义。

    #2317 把 `tz_set_fallback` 只 register 在 rescue 里，而 success_msg 无条件引用它——
    健康主机（时区已对齐、不走 rescue）渲染文案即报 `'tz_set_fallback' is undefined`，
    时区 play（被 `install_agent.yml` import）在所有健康真机上失败（238 现场 S5 实测；
    容器因走 rescue 反而不受影响）。本用例锁住结构：新增此类变量必须预初始化。
    """
    tasks = _flat_tasks()

    rescue_registers: set[str] = set()
    for task in tasks:
        for step in task.get("rescue") or []:
            if isinstance(step, dict) and step.get("register"):
                rescue_registers.add(str(step["register"]))
    assert rescue_registers, "用例前提失效：set_timezone.yml 里没有 rescue 内 register 的变量"

    preinitialized: set[str] = set()
    for task in tasks:
        fact = task.get("ansible.builtin.set_fact")
        if isinstance(fact, dict):
            preinitialized.update(str(key) for key in fact)

    missing = sorted(rescue_registers - preinitialized)
    assert not missing, (
        f"rescue 内 register 但未预初始化的变量：{missing}"
        "（健康路径会引用到未定义变量，安装链在真机上必失败）"
    )
