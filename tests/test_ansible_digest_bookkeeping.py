"""Ansible 通道的 digest 簿记契约（#2112）。

背景：`update_agent.yml` 用 `stdout | regex_search('CODE_DIGEST=(\\S+)', '\\1')` 提取
digest——**多行 stdout 下返回 list**，`copy content` 因此把 marker 写成
``["sha256:…"]``；agent 正则 ``^sha256:[0-9a-f]{64}$`` 判非法 → 上报空 → 控制面
「空值不覆盖」→ 列保留旧值、门禁可能误判收敛（详见 #2112 时序实验）。

本文件守三件事（PR 路径执行，纯离线）：
1. 提取表达式必须语义确定（`regex_findall` 取首元素），不得回退到 `regex_search`
   的替换形态；
2. 两个写入任务之后必须有**回读 + 断言**（写完即验，fail-closed），且断言同时校验
   「形态」与「取值」；
3. 断言所用的形态正则与 agent 侧校验一致。
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
PLAYBOOK = REPO_ROOT / "tools/ansible/playbooks/update_agent.yml"

DIGEST_FORMAT_RE = "^sha256:[0-9a-f]{64}$"


def _iter_tasks(node):
    """递归展开 playbook 里的 task（含 block / rescue / always）。"""
    if isinstance(node, list):
        for item in node:
            yield from _iter_tasks(item)
    elif isinstance(node, dict):
        if "name" in node and any(
            k in node for k in ("ansible.builtin.copy", "ansible.builtin.slurp",
                                "ansible.builtin.assert", "ansible.builtin.set_fact",
                                "ansible.builtin.command")
        ):
            yield node
        for key in ("block", "rescue", "always"):
            if key in node:
                yield from _iter_tasks(node[key])


def _tasks() -> list[dict]:
    plays = yaml.safe_load(PLAYBOOK.read_text(encoding="utf-8"))
    collected: list[dict] = []
    for play in plays:
        collected.extend(_iter_tasks(play.get("tasks", [])))
    return collected


def test_extract_uses_findall_not_replacing_regex_search():
    tasks = [t for t in _tasks() if t["name"] == "Extract deployment digests"]
    assert len(tasks) == 1, "Extract deployment digests 任务应唯一存在"
    facts = tasks[0]["ansible.builtin.set_fact"]

    for key in ("agent_code_artifact_digest", "agent_resources_artifact_digest"):
        expr = str(facts[key])
        assert "regex_findall" in expr, f"{key} 必须用 regex_findall（#2112）: {expr}"
        assert "regex_search" not in expr, f"{key} 不得回退到 regex_search 替换形态（#2112）: {expr}"
        assert "| first" in expr, f"{key} 必须取首元素（regex_findall 返回列表）: {expr}"
        assert "or ['']" in expr, f"{key} 需空匹配兜底（避免 first 作用在空列表）: {expr}"


def test_write_then_verify_pair_exists_after_writes():
    names = [t["name"] for t in _tasks()]
    write_code = names.index("Write agent ARTIFACT_DIGEST (ADR-0040 D2)")
    write_res = names.index("Write agent ARTIFACT_DIGEST_RESOURCES (ADR-0040 P2)")
    readback = next(i for i, n in enumerate(names) if n.startswith("Read back deployed artifact digests"))
    assert readback > max(write_code, write_res), "回读任务必须在两个写入任务之后（#2112 ②）"

    tasks = _tasks()
    slurp = tasks[readback]["ansible.builtin.slurp"]
    assert "{{ item.file }}" in str(slurp["src"]), slurp
    loop_files = [item["file"] for item in tasks[readback]["loop"]]
    assert loop_files == ["ARTIFACT_DIGEST", "ARTIFACT_DIGEST_RESOURCES"], loop_files

    assert_task = next(
        t for t in tasks if t["name"].startswith("Assert deployed digests are well-formed")
    )
    that = [str(clause) for clause in assert_task["ansible.builtin.assert"]["that"]]
    assert any("is match(" in c and DIGEST_FORMAT_RE in c for c in that), that
    assert any("item.item.want" in c for c in that), that
    assert "agent_digest_readback.results" in str(assert_task["loop"]), assert_task["loop"]


def test_assert_format_regex_matches_agent_validation():
    """形态正则必须与 agent 侧 `_ARTIFACT_DIGEST_RE` 同口径。"""
    version_info = (REPO_ROOT / "backend/agent/version_info.py").read_text(encoding="utf-8")
    agent_re = re.search(r'_ARTIFACT_DIGEST_RE = re\.compile\(r"([^"]+)"\)', version_info)
    assert agent_re, "未找到 agent 侧 digest 校验正则"
    assert agent_re.group(1) == DIGEST_FORMAT_RE, (
        f"两侧正则不一致：agent={agent_re.group(1)!r} playbook={DIGEST_FORMAT_RE!r}"
    )

    assert_task = next(
        t for t in _tasks() if t["name"].startswith("Assert deployed digests are well-formed")
    )
    serialized = str(assert_task["ansible.builtin.assert"]["that"])
    assert DIGEST_FORMAT_RE in serialized
