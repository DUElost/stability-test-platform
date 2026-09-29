"""刷机前置归位守卫（#2133 / #2284 / #3493 / ADR-0037 D5）：install / update 链保证。

运行期不再装包/加组/写规则（`flash_preflight` 只检不修、wrapper 只做固定 udev
自愈），因此「provisioning 链确实保证 dialout + plugdev + ttyACM 规则 + Android
USB 规则 + 依赖包」必须由门禁守住：

1. `install_agent.sh`：dialout 与 plugdev 成员（§1.2/§1.3）、ttyACM 规则（§4c，
   #2284 起按成员资格二选一：0660 + GROUP=dialout / 0666 退化）、Android USB
   规则（§4d，#3493，0660 + GROUP=plugdev / 0666 退化）、包集合（含 t64 兜底与
   跳过开关）；
2. `update_agent.yml`：opt-in provisioning 段——每个任务都受
   `agent_ensure_flash_prereqs` 门控，且**位于升级门禁释放之后**（失败不得
   让门禁悬挂，#1249 教训）；
3. `group_vars/linux_hosts.yml`：包集合与 `flash_preflight._DEFAULT_PACKAGES`
   逐项一致；
4. 四个面的规则文本与 `flash_preflight` **最新版本**的两个形态常量逐字一致
   （#2284：0660+dialout 与 0666 并存，车队升级渐进不破链）；Android USB 规则
   的两个形态由本文件常量锁定（#3493 无 preflight 判据面）。
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALL_SCRIPT = REPO_ROOT / "backend/agent/install_agent.sh"
UPDATE_PLAYBOOK = REPO_ROOT / "tools/ansible/playbooks/update_agent.yml"
ENSURE_PLAYBOOK = REPO_ROOT / "tools/ansible/playbooks/ensure_flash_prereqs.yml"
GROUP_VARS = REPO_ROOT / "tools/ansible/group_vars/linux_hosts.yml"
PREFLIGHT_DIR = REPO_ROOT / "backend/agent/scripts/flash_preflight"


def _version_key(name: str) -> tuple:
    return tuple(int(part) for part in name[1:].split("."))


def _latest_preflight_entry() -> Path:
    """最新 preflight 入口 = 族树入口（ADR-0051 Phase 3：版本目录已退役，树即最新版本）。"""
    entries = sorted(p for p in PREFLIGHT_DIR.glob("*.py") if not p.name.startswith("_"))
    assert entries, "flash_preflight 族树无入口文件"
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


#: Android USB（adb/fastboot）usbfs 节点全集规则（#3493）。fleet 为 MTK + 展锐混编、
#: agent host 上插上即测试终端，故取全集而非 VID:PID 白名单；0660+plugdev 仍把
#: 非 plugdev 用户挡在外面。Agent 用户不属 plugdev（无该组/加组失败）时退化 0666
#: （#2353 同法）。收窄出口：host 出现不可信本地用户/多租户时按 #2284 同法收窄。
_ADB_USB_RULE_PATH = "/etc/udev/rules.d/90-android.rules"
_ADB_USB_RULE_LINE = 'SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", MODE="0660", GROUP="plugdev"'
_ADB_USB_RULE_LINE_FALLBACK = 'SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", MODE="0666"'


def test_install_script_adds_agent_user_to_plugdev():
    """#3493：§1.3 在安装期保证 plugdev 成员资格（运行期不 usermod），组缺失跳过。"""
    text = _install_text()
    assert re.search(r'usermod -aG plugdev "\$USER"', text), \
        "install_agent.sh 必须把 Agent 用户加入 plugdev（adb usbfs 节点 0660 面它可写）"
    assert "getent group plugdev" in text, "plugdev 组缺失须跳过而非失败"


def test_install_script_writes_adb_usb_rule():
    """#3493：§4d 写 90-android.rules，形态按「Agent 用户是否属于 plugdev」二选一。"""
    text = _install_text()
    assert f'UDEV_ADB_RULE="{_ADB_USB_RULE_PATH}"' in text
    assert "UDEV_ADB_LINE_0660='%s'" % _ADB_USB_RULE_LINE in text
    assert "UDEV_ADB_LINE_0666='%s'" % _ADB_USB_RULE_LINE_FALLBACK in text
    assert "udevadm control --reload-rules" in text

    # 形态判据 = 成员资格（#2353 同法），不能看「组是否存在」。
    rule_block = text[text.index("UDEV_ADB_RULE="):]
    rule_block = rule_block[:rule_block.index("udevadm control --reload-rules")]
    assert re.search(r'id -nG "\$USER"[^\n]*grep -qx plugdev', rule_block), rule_block
    assert "getent group plugdev" not in rule_block, (
        "§4d 的形态选择不得再看「组是否存在」（#2353 同法）"
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
    section = [
        t for t in tasks
        if "#2133" in t.get("name", "") or "#3493" in t.get("name", "")
    ]
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
    section_idx = next(
        i for i, n in enumerate(names) if "#2133" in n or "#3493" in n
    )
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


def test_ensure_flash_prereqs_playbook_matches_preflight_udev():
    """主机页入口 playbook 与 flash_preflight 两种 udev 形态逐字一致。"""
    assert ENSURE_PLAYBOOK.exists()
    pf = _load_preflight()
    data = yaml.safe_load(ENSURE_PLAYBOOK.read_text(encoding="utf-8"))
    tasks = data[0]["tasks"]
    rule_tasks = [
        t for t in tasks
        if t.get("ansible.builtin.copy", {}).get("dest")
        == "/etc/udev/rules.d/98-ttyacm-mtk.rules"
    ]
    contents = {t["ansible.builtin.copy"]["content"].strip() for t in rule_tasks}
    assert contents == {
        pf._UDEV_RULE_LINE.strip(), pf._UDEV_RULE_LINE_LEGACY.strip(),
    }
    text = ENSURE_PLAYBOOK.read_text(encoding="utf-8")
    assert "groups: dialout" in text
    assert "agent_flash_prereq_packages | join(' ')" in text
    assert "stp-agent-priv restart" in text


#: ModemManager 忽略 MTK 设备的厂商规则（SP Flash Tool 自带；已与 flashtool@1.2444.00.100 工具包
#: 包根的 99-ttyacms.rules 逐字核对）。刷机期间防 MM 抢占 preloader/BROM 串口。
_MM_IGNORE_RULE_PATH = "/etc/udev/rules.d/99-ttyacms.rules"
_MM_IGNORE_RULE_LINE = 'ATTRS{idVendor}=="0e8d", ENV{ID_MM_DEVICE_IGNORE}="1"'


def _mm_rule_tasks(playbook: Path) -> list[dict]:
    tasks = yaml.safe_load(playbook.read_text(encoding="utf-8"))[0]["tasks"]
    return [
        t for t in tasks
        if t.get("ansible.builtin.copy", {}).get("dest") == _MM_IGNORE_RULE_PATH
    ]


def test_modemmanager_ignore_rule_fixed_form_on_all_provisioning_surfaces():
    """ADR-0040 D8 R2：MM 忽略规则不再从 agent/resources/flashtool 拷——资源层退役后安装链不下发
    resources/，三个供给面（install_agent.sh §4b / ensure_flash_prereqs.yml / update_agent.yml
    opt-in 段）改写同一固定形态，与厂商原件逐字一致。"""
    text = _install_text()
    assert f'UDEV_MM_RULE="{_MM_IGNORE_RULE_PATH}"' in text
    assert f"UDEV_MM_LINE='{_MM_IGNORE_RULE_LINE}'" in text
    for playbook in (ENSURE_PLAYBOOK, UPDATE_PLAYBOOK):
        tasks = _mm_rule_tasks(playbook)
        assert len(tasks) == 1, f"{playbook.name} 需恰好一个 MM 忽略规则任务"
        assert tasks[0]["ansible.builtin.copy"]["content"] == _MM_IGNORE_RULE_LINE + "\n"


def test_update_playbook_mm_rule_is_opt_in_and_reloads_udev():
    """update 链的 MM 规则与 #2133 段同一开关门控（常规更新不碰系统规则面），且变更触发 udev 重载。"""
    (task,) = _mm_rule_tasks(UPDATE_PLAYBOOK)
    when = task.get("when")
    conds = when if isinstance(when, list) else [when]
    assert any("agent_ensure_flash_prereqs" in str(c) for c in conds), task
    tasks = _playbook_tasks()
    reload_task = next(t for t in tasks if t.get("name") == "Reload udev rules after rule change (#2133)")
    assert "agent_flash_udev_mm_rule.changed" in str(reload_task.get("when")), reload_task
    ensure_tasks = yaml.safe_load(ENSURE_PLAYBOOK.read_text(encoding="utf-8"))[0]["tasks"]
    ensure_reload = next(t for t in ensure_tasks if t.get("name") == "Reload udev rules after rule change (#2133)")
    assert "agent_flash_udev_mm_rule.changed" in str(ensure_reload.get("when")), ensure_reload


def _adb_rule_tasks(playbook: Path) -> list[dict]:
    tasks = yaml.safe_load(playbook.read_text(encoding="utf-8"))[0]["tasks"]
    return [
        t for t in tasks
        if t.get("ansible.builtin.copy", {}).get("dest") == _ADB_USB_RULE_PATH
    ]


def test_update_playbook_covers_adb_usb_rule():
    """#3493：update 链 opt-in 段写两种形态（0660+plugdev / 0666），按成员资格互补门控，
    变更并入 udev 重载条件。"""
    tasks = _playbook_tasks()
    rule_tasks = _adb_rule_tasks(UPDATE_PLAYBOOK)
    contents = {t["ansible.builtin.copy"]["content"].strip() for t in rule_tasks}
    assert contents == {_ADB_USB_RULE_LINE, _ADB_USB_RULE_LINE_FALLBACK}, (
        f"update playbook 必须写两种形态且逐字一致，实际 {contents}"
    )
    whens = [str(t.get("when")) for t in rule_tasks]
    assert all("agent_ensure_flash_prereqs" in w for w in whens), whens
    assert all("agent_flash_plugdev_member" in w for w in whens), whens
    assert any("== 0" in w for w in whens), whens
    assert any("!= 0" in w for w in whens), whens
    assert all("agent_flash_plugdev_group" not in w for w in whens), (
        "规则形态不得按「组是否存在」门控（#2353 同法）"
    )
    member_tasks = [
        t for t in tasks
        if "plugdev member" in t.get("name", "") and "ansible.builtin.shell" in t
    ]
    assert member_tasks, "playbook 需要「Agent 用户是否属于 plugdev」的显式检查任务"
    assert "id -nG {{ agent_user }}" in str(
        member_tasks[0]["ansible.builtin.shell"]
    ) and "plugdev" in str(member_tasks[0]["ansible.builtin.shell"]), member_tasks[0]
    reload_task = next(
        t for t in tasks if t.get("name") == "Reload udev rules after rule change (#2133)"
    )
    assert "agent_flash_adb_rule_0660.changed" in str(reload_task.get("when")), reload_task


def test_ensure_flash_prereqs_playbook_covers_adb_usb_rule():
    """#3493：主机页入口 playbook 与 install 链的 Android USB 规则逐字同源；
    plugdev 变更同样触发 Agent 重启（进程组集合刷新）。"""
    rule_tasks = _adb_rule_tasks(ENSURE_PLAYBOOK)
    contents = {t["ansible.builtin.copy"]["content"].strip() for t in rule_tasks}
    assert contents == {_ADB_USB_RULE_LINE, _ADB_USB_RULE_LINE_FALLBACK}, (
        f"ensure playbook 必须写两种形态且逐字一致，实际 {contents}"
    )
    whens = [str(t.get("when")) for t in rule_tasks]
    assert all("agent_flash_plugdev_member" in w for w in whens), whens
    assert any("== 0" in w for w in whens) and any("!= 0" in w for w in whens), whens
    tasks = yaml.safe_load(ENSURE_PLAYBOOK.read_text(encoding="utf-8"))[0]["tasks"]
    reload_task = next(
        t for t in tasks if t.get("name") == "Reload udev rules after rule change (#2133)"
    )
    assert "agent_flash_adb_rule_0660.changed" in str(reload_task.get("when")), reload_task
    text = ENSURE_PLAYBOOK.read_text(encoding="utf-8")
    assert re.search(r"groups: plugdev", text), "需要 plugdev 加组任务"
    restart_task = next(
        t for t in tasks if t.get("name", "").startswith("Restart agent after group membership change")
    )
    assert "agent_flash_plugdev.changed" in str(restart_task.get("when")), restart_task


def test_both_playbooks_trigger_udev_after_rule_change():
    """#3493 勘误（.58 实测 2026-09-29）：`udevadm control --reload` 只重载规则库，
    **已插设备的节点不会重算**——规则落盘后 /dev/bus/usb/* 仍 root:root 0664。
    两个 playbook 必须在 reload 之后显式 `udevadm trigger`（重放 uevent，幂等），
    且与 reload 同一变更条件（任一规则任务 changed）。"""
    for playbook in (ENSURE_PLAYBOOK, UPDATE_PLAYBOOK):
        tasks = yaml.safe_load(playbook.read_text(encoding="utf-8"))[0]["tasks"]
        names = [t.get("name", "") for t in tasks]
        reload_idx = next(i for i, n in enumerate(names) if n == "Reload udev rules after rule change (#2133)")
        trigger_idx = next(
            i for i, n in enumerate(names)
            if n.startswith("Trigger udev to re-apply rules")
        )
        assert trigger_idx == reload_idx + 1, (
            f"{playbook.name}: trigger 必须紧跟 reload（位置 {trigger_idx} vs reload {reload_idx}）"
        )
        trigger = tasks[trigger_idx]
        assert trigger["ansible.builtin.command"] == "udevadm trigger", trigger
        when = str(trigger.get("when"))
        assert "agent_flash_adb_rule_0660.changed" in when, trigger
        assert "agent_flash_udev_rule_0660.changed" in when, trigger
        if playbook == UPDATE_PLAYBOOK:
            assert "agent_ensure_flash_prereqs" in when, (
                "update 链的 trigger 任务必须受 opt-in 开关门控"
            )
