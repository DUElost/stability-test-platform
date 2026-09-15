"""刷机前置归位守卫（#2133 / ADR-0037 D5）：install / update 链保证。

运行期不再装包/加组/写规则（`flash_preflight v1.0.2` 只检不修、wrapper 只做
固定 udev 自愈），因此「provisioning 链确实保证 dialout + 0666 规则 + 依赖包」
必须由门禁守住：

1. `install_agent.sh`：dialout 成员、固定 0666 规则、包集合（含 t64 兜底与
   跳过开关）；
2. `update_agent.yml`：opt-in provisioning 段——每个任务都受
   `agent_ensure_flash_prereqs` 门控，且**位于升级门禁释放之后**（失败不得
   让门禁悬挂，#1249 教训）；
3. `group_vars/linux_hosts.yml`：包集合与 `flash_preflight._DEFAULT_PACKAGES`
   逐项一致；
4. 三处 0666 规则文本与 `flash_preflight v1.0.2` 常量逐字一致。
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALL_SCRIPT = REPO_ROOT / "backend/agent/install_agent.sh"
UPDATE_PLAYBOOK = REPO_ROOT / "tools/ansible/playbooks/update_agent.yml"
GROUP_VARS = REPO_ROOT / "tools/ansible/group_vars/linux_hosts.yml"
PREFLIGHT_DIR = REPO_ROOT / "backend/agent/scripts/flash_preflight"


def _version_key(name: str) -> tuple:
    return tuple(int(part) for part in name[1:].split("."))


def _latest_preflight_entry() -> Path:
    """最新 preflight 版本入口（数字段排序——字典序会把 v1.0.9 排在 v1.0.10 后）。"""
    versions = [p for p in PREFLIGHT_DIR.iterdir()
                if p.is_dir() and p.name.startswith("v")]
    latest = max(versions, key=lambda p: _version_key(p.name))
    entries = [p for p in latest.glob("*.py") if not p.name.startswith("_")]
    assert entries, f"flash_preflight {latest.name} 无入口文件"
    return entries[0]


def _load_preflight():
    spec = importlib.util.spec_from_file_location(
        "flash_preflight_prereqs", _latest_preflight_entry()
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _install_text() -> str:
    return INSTALL_SCRIPT.read_text(encoding="utf-8")


def test_install_script_adds_agent_user_to_dialout():
    text = _install_text()
    assert re.search(r'usermod -aG dialout "\$USER"', text), \
        "install_agent.sh 必须把 Agent 用户加入 dialout（运行期不再 usermod）"
    assert "getent group dialout" in text, "dialout 组缺失须跳过而非失败"


def test_install_script_writes_fixed_0666_rule():
    pf = _load_preflight()
    text = _install_text()
    assert pf._UDEV_RULE_PATH in text
    assert "UDEV_MTK_LINE='%s'" % pf._UDEV_RULE_LINE.strip() in text
    assert "udevadm control --reload-rules" in text


def test_install_script_package_list_matches_preflight():
    pf = _load_preflight()
    text = _install_text()
    match = re.search(r"for pkg in ([^;]+); do", text)
    assert match, "install_agent.sh 未找到包循环"
    packages = match.group(1).split()
    assert packages == list(pf._DEFAULT_PACKAGES), (
        "install 链包集合必须与 flash_preflight._DEFAULT_PACKAGES 同源："
        f"{packages} vs {list(pf._DEFAULT_PACKAGES)}"
    )
    assert "AGENT_SKIP_FLASH_PREREQ_PKGS" in text, "需提供离线精装跳过开关"
    assert "${pkg}t64" in text, "Debian 13 t64 改名需要兜底（同 preflight）"


def _playbook_tasks():
    data = yaml.safe_load(UPDATE_PLAYBOOK.read_text(encoding="utf-8"))
    return data[0]["tasks"]


def _task_names(tasks) -> list:
    return [t.get("name", "") for t in tasks]


def test_update_playbook_has_gated_flash_prereq_section():
    tasks = _playbook_tasks()
    section = [t for t in tasks if "#2133" in t.get("name", "")]
    assert len(section) >= 4, f"provisioning 段任务数异常：{_task_names(tasks)}"
    for task in section:
        when = task.get("when")
        assert when is not None, f"任务未门控：{task.get('name')}"
        conds = when if isinstance(when, list) else [when]
        assert any("agent_ensure_flash_prereqs" in str(c) for c in conds), (
            f"任务未受 agent_ensure_flash_prereqs 门控：{task.get('name')}"
        )


def test_update_playbook_flash_prereq_section_is_after_gate_release():
    """门禁释放之后：provisioning 失败不得让升级门禁悬挂（#1249 教训）。"""
    names = _task_names(_playbook_tasks())
    release_idx = next(
        i for i, n in enumerate(names) if "Release control-plane upgrade gate" in n
    )
    section_idx = next(i for i, n in enumerate(names) if "#2133" in n)
    assert section_idx > release_idx, (
        "刷机前置段必须位于升级门禁释放之后（否则失败会悬挂门禁）"
    )


def test_update_playbook_covers_dialout_rule_packages():
    tasks = _playbook_tasks()
    pf = _load_preflight()
    text = UPDATE_PLAYBOOK.read_text(encoding="utf-8")
    assert re.search(r"groups: dialout", text)
    assert "agent_flash_prereq_packages | join(' ')" in text

    rule_task = next(
        t for t in tasks
        if t.get("ansible.builtin.copy", {}).get(
            "dest") == "/etc/udev/rules.d/98-ttyacm-mtk.rules"
    )
    content = rule_task["ansible.builtin.copy"]["content"]
    assert content.strip() == pf._UDEV_RULE_LINE.strip(), (
        "playbook 写的 0666 规则文本必须与 flash_preflight 常量逐字一致"
    )


def test_group_vars_package_list_matches_preflight():
    pf = _load_preflight()
    data = yaml.safe_load(GROUP_VARS.read_text(encoding="utf-8"))
    assert data["agent_flash_prereq_packages"] == list(pf._DEFAULT_PACKAGES)
    assert data["agent_ensure_flash_prereqs"] is False, "默认必须关闭（opt-in）"
