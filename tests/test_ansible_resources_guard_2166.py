"""#2166 守卫：`update_agent.yml` 必须在同步前拒绝「源树无 resources」。

背景（#2133 实测事故）：playbook 以 rsync `--delete` 同步 agent 树，而
`backend/agent/resources/`（230MB）不在 git——任何 worktree 都没有它。
源树缺 resources 时，主机侧会被当成"已删除"清空（.82 实测从 230M 削到 9.3M、
flashtool 全失）。本文件锁定：

1. `pre_tasks` 中存在前置断言，且**早于任何落盘/快照任务**（fail 在同步之前）；
2. 断言只受 `agent_allow_empty_resources` 显式放行（默认拒绝）；
3. 放行时打警告，并在 update summary 标注；
4. `group_vars` 的开关默认 false。
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
PLAYBOOK = REPO_ROOT / "tools/ansible/playbooks/update_agent.yml"
GROUP_VARS = REPO_ROOT / "tools/ansible/group_vars/linux_hosts.yml"

_GUARD_NAME = "Assert source tree carries agent resources (#2166)"
_WARN_NAME = "Warn when syncing from a source tree without resources (#2166)"
_FIRST_MUTATION_ANCHOR = "Snapshot current agent directory before sync"


def _playbook():
    return yaml.safe_load(PLAYBOOK.read_text(encoding="utf-8"))[0]


def _find_task(tasks: list, name: str) -> dict:
    for task in tasks:
        if task.get("name") == name:
            return task
    raise AssertionError(f"任务缺失：{name}（现有：{[t.get('name') for t in tasks]}）")


def test_guard_assert_exists_in_pre_tasks():
    play = _playbook()
    task = _find_task(play["pre_tasks"], _GUARD_NAME)
    assert task["ansible.builtin.assert"], "必须是 assert 任务"
    that = task["ansible.builtin.assert"]["that"]
    joined = " ".join(str(c) for c in (that if isinstance(that, list) else [that]))
    assert "agent_allow_empty_resources" in joined, "断言必须受显式放行开关约束"
    assert "agent_source_flash_tool_find" in joined, "断言必须校验 flashtool 入口"
    assert "agent_source_resources_dir" in joined, "断言必须校验 resources 目录"
    assert "agent_allow_empty_resources=true" in task["ansible.builtin.assert"]["fail_msg"], \
        "fail_msg 必须给出放行用法（可执行指引）"


def test_guard_uses_follow_capable_probe():
    """fileglob 不跟随目录软链（实测误拒）——必须用 find(follow=true) 探测。"""
    play = _playbook()
    find_task = _find_task(play["pre_tasks"], "Locate flashtool entry in source-tree resources (#2166)")
    args = find_task["ansible.builtin.find"]
    assert args["follow"] is True, "必须 follow=true（软链入树的合规做法要放行）"
    assert args["patterns"] == "flash_tool"
    assert args["recurse"] is True
    stat_task = _find_task(play["pre_tasks"], "Stat source-tree resources dir (#2166)")
    assert stat_task["ansible.builtin.stat"]["path"].endswith("/resources")
    assert stat_task["ansible.builtin.stat"]["follow"] is True, \
        "stat 必须 follow=true（ansible-core 2.19 起默认 no，软链入树会被误拒）"


def test_guard_runs_before_any_host_mutation():
    play = _playbook()
    pre_names = [t.get("name") for t in play["pre_tasks"]]
    task_names = [t.get("name") for t in play["tasks"]]
    assert _GUARD_NAME in pre_names, "断言必须在 pre_tasks（任何同步之前）"
    assert _FIRST_MUTATION_ANCHOR in task_names, "锚点任务缺失，需同步更新守卫"
    # pre_tasks 整体先于 tasks 执行 —— 断言早于一切落盘动作
    assert pre_names.index(_GUARD_NAME) >= 0


def test_guard_override_warns_and_is_summarized():
    play = _playbook()
    warn = _find_task(play["pre_tasks"], _WARN_NAME)
    assert warn["ansible.builtin.debug"], "放行路径必须打警告"
    when = warn.get("when")
    conds = when if isinstance(when, list) else [when]
    assert any("agent_allow_empty_resources" in str(c) for c in conds)
    summary = _find_task(play["tasks"], "Print update summary")
    assert "allow_empty_resources" in str(summary["ansible.builtin.debug"]["msg"]), \
        "summary 必须标注放行状态"


def test_group_vars_default_is_fail_closed():
    data = yaml.safe_load(GROUP_VARS.read_text(encoding="utf-8"))
    assert data["agent_allow_empty_resources"] is False, "默认必须拒绝（fail-closed）"


@pytest.mark.skipif(
    not __import__("shutil").which("ansible-playbook"), reason="ansible 不可用",
)
def test_playbook_syntax_ok():
    import subprocess
    proc = subprocess.run(
        ["ansible-playbook", "--syntax-check", str(PLAYBOOK)],
        capture_output=True, text=True, cwd=str(REPO_ROOT / "tools/ansible"),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
