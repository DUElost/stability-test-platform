"""刷机前置归位守卫（#2133 / #2284 / ADR-0037 D5）：install / update 链保证。

运行期不再装包/加组/写规则（`flash_preflight` 只检不修、wrapper 只做固定 udev
自愈），因此「provisioning 链确实保证 dialout + ttyACM 规则 + 依赖包」必须由门禁
守住：

1. `install_agent.sh`：dialout 成员、ttyACM 规则（#2284 起按本机 dialout 组二选一：
   0660 + GROUP=dialout / 0666 退化）、包集合（含 t64 兜底与跳过开关）；
2. `update_agent.yml`：opt-in provisioning 段——每个任务都受
   `agent_ensure_flash_prereqs` 门控，且**位于升级门禁释放之后**（失败不得
   让门禁悬挂，#1249 教训）；
3. `group_vars/linux_hosts.yml`：包集合与 `flash_preflight._DEFAULT_PACKAGES`
   逐项一致；
4. 四个面的规则文本与 `flash_preflight` **最新版本**的两个形态常量逐字一致
   （#2284：0660+dialout 与 0666 并存，车队升级渐进不破链）。
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


def test_install_script_writes_dual_form_udev_rule():
    """#2284/#2353：install 链按「Agent 用户**是否属于** dialout」二选一写规则。"""
    pf = _load_preflight()
    text = _install_text()
    assert pf._UDEV_RULE_PATH in text
    assert "UDEV_MTK_LINE_0660='%s'" % pf._UDEV_RULE_LINE.strip() in text
    assert "UDEV_MTK_LINE_0666='%s'" % pf._UDEV_RULE_LINE_LEGACY.strip() in text
    assert "udevadm control --reload-rules" in text

    # 形态判据 = **成员资格**（#2353）：组存在而用户不是成员时，0660 对该用户
    # 等同于不可写（刷机中途 EACCES/STATUS_ERR）——故 §4c 不能再按「组是否存在」选。
    rule_block = text[text.index("UDEV_MTK_RULE="):]
    rule_block = rule_block[:rule_block.index("udevadm control --reload-rules")]
    assert re.search(r'id -nG "\$USER"[^\n]*grep -qx dialout', rule_block), rule_block
    assert "getent group dialout" not in rule_block, (
        "§4c 的形态选择不得再看「组是否存在」（#2353）"
    )


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

    rule_tasks = [
        t for t in tasks
        if t.get("ansible.builtin.copy", {}).get("dest")
        == "/etc/udev/rules.d/98-ttyacm-mtk.rules"
    ]
    contents = {t["ansible.builtin.copy"]["content"].strip() for t in rule_tasks}
    assert contents == {
        pf._UDEV_RULE_LINE.strip(), pf._UDEV_RULE_LINE_LEGACY.strip(),
    }, (
        "playbook 必须写两种形态（0660+dialout / 0666）且与 flash_preflight 常量"
        f"逐字一致，实际 {contents}"
    )
    # 两个任务互补门控：**成员**走 0660，非成员才 0666（#2284/#2353）——
    # 判据必须是成员资格，不能是「组是否存在」。
    whens = [str(t.get("when")) for t in rule_tasks]
    assert all("agent_flash_dialout_member" in w for w in whens), whens
    assert any("== 0" in w for w in whens), whens
    assert any("!= 0" in w for w in whens), whens
    assert all("agent_flash_dialout_group" not in w for w in whens), (
        "规则形态不得再按「组是否存在」门控（#2353）"
    )
    member_tasks = [
        t for t in tasks
        if "dialout member" in t.get("name", "")
        and "ansible.builtin.shell" in t
    ]
    assert member_tasks, "playbook 需要「Agent 用户是否属于 dialout」的显式检查任务"
    assert "id -nG {{ agent_user }}" in str(
        member_tasks[0]["ansible.builtin.shell"]
    ), member_tasks[0]


def test_wrapper_udev_constants_match_preflight(monkeypatch):
    """#2284/#2353：wrapper 窄面两个形态常量与**最新** preflight 同源，判据是成员资格。"""
    spec = importlib.util.spec_from_file_location(
        "stp_agent_priv_prereqs", REPO_ROOT / "backend/agent/stp_agent_priv.py",
    )
    wrapper = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(wrapper)
    pf = _load_preflight()

    assert wrapper.UDEV_RULE_PATH == pf._UDEV_RULE_PATH
    assert wrapper.UDEV_RULE_LINE == pf._UDEV_RULE_LINE
    assert wrapper.UDEV_RULE_LINE_LEGACY == pf._UDEV_RULE_LINE_LEGACY
    # 形态选择只依赖**调用者（Agent 用户）的成员资格**（#2353），调用方无参数面
    monkeypatch.setattr(wrapper, "_invoking_user", lambda: "android")
    monkeypatch.setattr(wrapper, "_user_in_dialout", lambda user: False)
    assert wrapper._udev_rule_line() == wrapper.UDEV_RULE_LINE_LEGACY
    monkeypatch.setattr(wrapper, "_user_in_dialout", lambda user: True)
    assert wrapper._udev_rule_line() == wrapper.UDEV_RULE_LINE


def test_group_vars_package_list_matches_preflight():
    pf = _load_preflight()
    data = yaml.safe_load(GROUP_VARS.read_text(encoding="utf-8"))
    assert data["agent_flash_prereq_packages"] == list(pf._DEFAULT_PACKAGES)
    assert data["agent_ensure_flash_prereqs"] is False, "默认必须关闭（opt-in）"
