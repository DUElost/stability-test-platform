from pathlib import Path

import yaml

from tools.dev.source_anchor import SourceGuard

PLAYBOOK = Path("tools/ansible/playbooks/update_agent.yml")
_PLAYBOOK_REL = "tools/ansible/playbooks/update_agent.yml"

#: 锚点必须是**当前真实存在**的语义位：锚点找不到 = `AnchorDrift`（用例已过期，
#: 改指新真源），禁词复活 = `FormRegression`（防线抓到了东西）。见 #2639。
_SYNC_TASK_ANCHOR = "Sync changed agent code into installed agent directory"
_API_URL_LINE_ANCHOR = 'line: "API_URL={{ agent_upgrade_api_url }}"'


def _tasks():
    plays = yaml.safe_load(PLAYBOOK.read_text(encoding="utf-8"))
    tasks = []
    for play in plays:
        tasks.extend(play.get("pre_tasks", []))
        tasks.extend(play.get("tasks", []))
    return tasks


def test_update_agent_syncs_directly_without_remote_staging_copy():
    """更新链直接 rsync 到安装目录，不得回到「先把整棵树 copy 到远端临时目录」的旧形态。

    旧写法是裸 `assert 词 not in text`：playbook 一旦改名或这段同步逻辑搬走，它会**恒真**
    而继续报绿（#2639）。先以同步任务名证明扫的还是那段实现，再判退役形态。
    """
    guard = SourceGuard.of_repo_path(_PLAYBOOK_REL).anchored(_SYNC_TASK_ANCHOR)

    guard.assert_absent(
        "Copy latest agent source tree to remote temp directory",
        why="远端暂存 copy 任务已随直连 rsync 退役，复活即回到两段式发布",
    )
    guard.assert_absent(
        "agent_remote_tmp_dir",
        why="远端临时目录变量只为旧暂存形态存在，留着就是死配置面",
    )
    guard.assert_absent(
        "ansible.builtin.copy:\n        src: \"{{ agent_source_dir }}/\"",
        why="整棵源码树的 copy 模块会带 .git/__pycache__ 越界，已由 rsync 过滤策略取代",
    )


def test_update_agent_previews_changes_before_syncing_or_restarting():
    text = PLAYBOOK.read_text(encoding="utf-8")
    task_names = {task.get("name") for task in _tasks()}

    assert "Preview agent code changes with rsync dry-run" in task_names
    assert "Preview agentctl changes with rsync dry-run" in task_names
    assert "rsync" in text
    assert "--dry-run" in text
    assert "--itemize-changes" in text
    assert "--delete-excluded" in text
    assert "{{ agent_source_dir }}/" in text
    assert "{{ agent_install_dir }}/agent/" in text


def test_update_agent_only_backs_up_and_restarts_when_changes_exist():
    text = PLAYBOOK.read_text(encoding="utf-8")

    assert "agent_code_change_lines | length > 0" in text
    assert "agentctl_change_lines | length > 0" in text
    assert "agent_env_changed | bool" in text
    assert "agent_update_requires_restart | bool" in text
    assert "reject('match', '^\\\\.[^ ]\\\\s{10}')" in text
    assert "reject('match', '^\\\\.[fd]\\\\.\\\\.t\\\\.\\\\.\\\\.\\\\.\\\\.\\\\.\\\\s')" in text
    assert "__pycache__/" in text
    assert "\\\\.pyc$" in text


def test_update_agent_uses_stable_backup_timestamp():
    text = PLAYBOOK.read_text(encoding="utf-8")

    assert 'agent_update_timestamp: "{{ ansible_date_time.iso8601_basic_short' in text
    assert 'agent_backup_dir: "{{ agent_install_dir }}/agent.bak.{{ agent_update_timestamp }}"' in text
    assert (
        'agent_agentctl_backup_path: "{{ agent_install_dir }}/agentctl.bak.{{ agent_update_timestamp }}"'
        in text
    )
    assert (
        'agent_service_backup_path: "/etc/systemd/system/{{ agent_service_name }}.service.bak.{{ agent_update_timestamp }}"'
        in text
    )


def test_update_agent_previews_and_syncs_service_unit():
    text = PLAYBOOK.read_text(encoding="utf-8")
    task_names = {task.get("name") for task in _tasks()}

    assert "Preview service unit changes with rsync dry-run" in task_names
    assert "Refresh systemd service unit from local source" in text
    assert "Snapshot current service unit before sync" in text
    assert "Roll back service unit from snapshot" in text
    assert "{{ agent_source_dir }}/stability-test-agent.service" in text
    assert "/etc/systemd/system/{{ agent_service_name }}.service" in text
    assert "agent_service_change_lines | length > 0" in text


def test_update_agent_reenables_service_on_restart_and_rollback():
    text = PLAYBOOK.read_text(encoding="utf-8")

    assert "Reload systemd and restart service" in text
    assert "Restart service after rollback" in text
    assert "enabled: true" in text


def test_update_agent_refreshes_pipeline_schema_and_version_marker():
    """运行时工件随升级同步（#1247）：schema 进 install_dir/schemas/，
    版本标识进 agent/VERSION；两者都按变更检测决定是否写。"""
    text = PLAYBOOK.read_text(encoding="utf-8")

    assert "Stat installed pipeline schema before sync" in text
    assert "Ensure schemas directory exists" in text
    assert "Refresh pipeline schema from local source" in text
    assert "Refresh agent VERSION marker" in text
    assert "{{ stp_repo_root }}/backend/schemas/pipeline_schema.json" in text
    assert "{{ agent_install_dir }}/schemas/pipeline_schema.json" in text
    assert "{{ agent_install_dir }}/agent/VERSION" in text
    # 升级后不再有「schema 已更新但进程仍缓存旧 schema」的窗口
    assert "agent_schema_changed | bool" in text


def test_update_agent_requires_control_plane_upgrade_gate():
    """所有升级入口复用 ADR-0021 D7/D8 协议（#1249）：门禁必须 fail-closed，
    有活跃 Job 默认拒绝，abort 需显式开关；正常与回滚路径都释放窗口。"""
    text = PLAYBOOK.read_text(encoding="utf-8")
    task_names = {task.get("name") for task in _tasks()}

    assert "Read deployed agent identity for the upgrade gate" in task_names
    assert "Assert upgrade gate target is resolvable" in task_names
    assert "Request control-plane upgrade gate" in task_names
    assert "Assert upgrade gate acquired" in task_names
    assert "/upgrade-gate" in text
    assert "/upgrade-gate/release" in text
    assert "X-Agent-Secret" in text
    # 只有 200 才放行（409/504/404/401 都是拒绝）
    assert "agent_upgrade_gate_response.status == 200" in text
    # 显式 abort 开关，默认关闭
    assert "agent_abort_running_jobs | default(false) | bool" in text
    # 正常路径与 rollback 路径都必须释放窗口
    assert "Release control-plane upgrade gate" in text
    assert "Release control-plane upgrade gate after rollback" in text
    assert text.count('holder: "{{ agent_upgrade_holder }}"') >= 2


def test_api_url_refresh_never_writes_empty_override():
    """#1250 迁移暴露的缺陷：API_URL 回写曾直接用未注入的 agent_api_url（默认空），
    无条件覆盖目标机 .env → 心跳 URL 变空、agentctl health rc=1。

    回写必须用 pre_tasks 解析出的 agent_upgrade_api_url（-e agent_api_url= 优先，
    否则取目标机现值），空值场景由解析后的 assert 提前拦截。"""
    text = PLAYBOOK.read_text(encoding="utf-8")

    assert 'line: "API_URL={{ agent_upgrade_api_url }}"' in text
    # 先锚在**替代它的那一行**上：回写目标一旦被搬走，这条判据必须报「用例过期」而不是恒真
    SourceGuard.of_repo_path(_PLAYBOOK_REL).anchored(_API_URL_LINE_ANCHOR).assert_absent(
        'line: "API_URL={{ agent_api_url }}"',
        why="#1250：未注入的 agent_api_url 默认空，无条件回写会把心跳 URL 打成空",
    )
    assert "Resolve upgrade gate target (explicit vars win, else deployed .env)" in text
