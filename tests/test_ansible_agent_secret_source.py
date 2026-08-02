"""Ansible 取 AGENT_SECRET 的兜底源必须是仓库根 .env.backend（#121）。

2026-08-01 指纹比对（sha256 前 12 位）：

    运行中控制面    c688a87187ef
    20 台 Agent     c688a87187ef
    backend/.env    66992dc770c3   ← 没有任何人在用

而 Ansible 链此前正是从 backend/.env 兜底。跑一次 update_agent.yml 就会把
那个没人认的密钥写进每台 Agent 的 .env（playbook 里 `line: "AGENT_SECRET=..."`），
导致 20 台集体 SocketIO 认证失败 —— 而且失败发生在部署**之后**，不是执行时。

所以这条断言守的不是风格，是一次会打掉整个 Agent 集群的操作。
"""

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
GROUP_VARS = REPO_ROOT / "tools/ansible/group_vars/linux_hosts.yml"
ANSIBLE_DIR = REPO_ROOT / "tools/ansible"


def test_fallback_reads_env_backend_not_backend_env():
    text = GROUP_VARS.read_text(encoding="utf-8")
    assert "'/.env.backend'" in text or "/.env.backend" in text
    # backend/.env 只允许出现在解释性注释里，不能出现在 lookup 的 file= 里
    for line in text.splitlines():
        if "lookup(" in line or "file=" in line:
            assert "backend/.env" not in line, line


def test_fallback_is_anchored_to_playbook_dir_not_inventory_dir():
    """真实 inventory 常放在仓库外（如 /home/debian13/hosts.ini）。

    锚 inventory_dir 时相对路径会指到仓库外，兜底**静默**失效 —— 然后
    playbook 的 assert 才会报 “agent_secret 未注入”，看起来像配置漏了。
    """
    text = GROUP_VARS.read_text(encoding="utf-8")
    assert "playbook_dir" in text
    secret_block = text.split("agent_secret_from_env_backend:", 1)[1][:400]
    assert "inventory_dir" not in secret_block


def test_group_vars_is_valid_yaml():
    assert isinstance(yaml.safe_load(GROUP_VARS.read_text(encoding="utf-8")), dict)


def test_no_playbook_or_doc_still_points_at_backend_env():
    """报错文案指错源头，会把人直接引到错的密钥上。"""
    offenders = []
    for path in ANSIBLE_DIR.rglob("*"):
        if path.suffix not in {".yml", ".yaml", ".md"} or not path.is_file():
            continue
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "backend/.env" not in line:
                continue
            # 允许「不要用 backend/.env」这类明确的反面说明
            if "不要用" in line or "不是 backend/.env" in line or "没人认" in line:
                continue
            offenders.append(f"{path.relative_to(REPO_ROOT)}:{i}: {line.strip()[:90]}")
    assert not offenders, "仍有指向 backend/.env 的说明:\n" + "\n".join(offenders)
