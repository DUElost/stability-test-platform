"""安装工件链契约：Pipeline schema 与 VERSION 必须随安装落盘（#1247）。

背景：pipeline_validator 按 ``<install_dir>/../schemas/../schemas`` 解析
（``parent.parent/schemas/pipeline_schema.json``），而 install_agent.sh 与
Ansible 只同步 ``backend/agent/`` 源码目录——干净安装/升级后合法 Pipeline
会在校验阶段失败。API 热更新含 schema，不能补偿其他安装入口。

本文件守行为而非文案：
- ``resolve_pipeline_schema`` 对「仓库同构布局」「Ansible 暂存布局」的解析；
- ``resolve_code_version`` 的注入/派生/缺省分支；
- 安装脚本确实把两个工件装到运行时解析路径，并以自检收口。
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALL_SCRIPT = REPO_ROOT / "backend/agent/install_agent.sh"
INSTALL_PLAYBOOK = REPO_ROOT / "tools/ansible/playbooks/install_agent.yml"


def _function_source(name: str) -> str:
    source = INSTALL_SCRIPT.read_text(encoding="utf-8")
    match = re.search(rf"(?ms)^{name}\(\) \{{\n.*?^\}}\n", source)
    assert match is not None, f"{name}() not found in install_agent.sh"
    return match.group(0)


def _resolve(script_dir: Path) -> subprocess.CompletedProcess:
    """在临时目录布局上执行 resolve_pipeline_schema <script_dir>。"""
    script = f"{_function_source('resolve_pipeline_schema')}\n" 'resolve_pipeline_schema "$1"\n'
    completed = subprocess.run(
        ["bash", "-c", script, "resolver", str(script_dir)],
        capture_output=True,
        text=True,
    )
    # 解析结果可能含 .. 未归一（安装脚本原样交给 install），比较前归一
    resolved = completed.stdout.strip()
    completed.stdout = str(Path(resolved).resolve()) if resolved else ""
    return completed


def _code_version(agent_dir: Path, injected: str = "") -> subprocess.CompletedProcess:
    script = f"{_function_source('resolve_code_version')}\n" 'resolve_code_version "$1"\n'
    env = dict(os.environ)
    env.pop("AGENT_CODE_VERSION", None)
    if injected:
        env["AGENT_CODE_VERSION"] = injected
    return subprocess.run(
        ["bash", "-c", script, "resolver", str(agent_dir)],
        capture_output=True,
        text=True,
        env=env,
    )


def test_resolver_finds_repo_layout_schema(tmp_path):
    script_dir = tmp_path / "agent"
    schema = tmp_path / "schemas/pipeline_schema.json"
    schema.parent.mkdir(parents=True)
    schema.write_text("{}", encoding="utf-8")
    script_dir.mkdir()

    completed = _resolve(script_dir)

    assert completed.returncode == 0
    assert completed.stdout.strip() == str(schema)


def test_resolver_finds_ansible_staged_schema(tmp_path):
    script_dir = tmp_path / "agent"
    staged = script_dir / "stp_schemas/pipeline_schema.json"
    staged.parent.mkdir(parents=True)
    staged.write_text("{}", encoding="utf-8")

    completed = _resolve(script_dir)

    assert completed.returncode == 0
    assert completed.stdout.strip() == str(staged)


def test_resolver_prefers_repo_layout_over_staged(tmp_path):
    script_dir = tmp_path / "agent"
    repo_schema = tmp_path / "schemas/pipeline_schema.json"
    staged = script_dir / "stp_schemas/pipeline_schema.json"
    repo_schema.parent.mkdir(parents=True)
    repo_schema.write_text("{}", encoding="utf-8")
    staged.parent.mkdir(parents=True)
    staged.write_text("{}", encoding="utf-8")

    completed = _resolve(script_dir)

    assert completed.stdout.strip() == str(repo_schema)


def test_resolver_fails_when_schema_absent(tmp_path):
    script_dir = tmp_path / "agent"
    script_dir.mkdir()

    completed = _resolve(script_dir)

    assert completed.returncode != 0
    assert completed.stdout.strip() == ""


def test_code_version_prefers_injected_value(tmp_path):
    completed = _code_version(tmp_path, injected="abc1234")

    assert completed.returncode == 0
    assert completed.stdout.strip() == "abc1234"


def test_code_version_empty_outside_git_repo(tmp_path):
    completed = _code_version(tmp_path)

    assert completed.returncode == 0
    assert completed.stdout.strip() == ""


def test_install_script_installs_schema_and_version_to_runtime_paths():
    text = INSTALL_SCRIPT.read_text(encoding="utf-8")

    assert 'install -m 0644 "$SCHEMA_SRC" "$INSTALL_DIR/schemas/pipeline_schema.json"' in text
    assert 'echo "$CODE_VERSION" > "$INSTALL_DIR/agent/VERSION"' in text
    # schema 缺失必须中止安装（fail-fast），不能静默产出取不到 schema 的安装
    assert re.search(
        r'SCHEMA_SRC="\$\(resolve_pipeline_schema "\$SCRIPT_DIR"\)" \|\| \{.*?exit 1',
        text,
        re.DOTALL,
    )


def test_install_script_runs_selfcheck_from_installed_tree():
    text = INSTALL_SCRIPT.read_text(encoding="utf-8")

    assert "agent.install_selfcheck" in text
    # cwd 必须切到 INSTALL_DIR：python -m 会把 cwd 加入 sys.path，
    # 沿用调用方 cwd（Ansible 暂存源码树）会导入开发布局而非安装产物
    assert re.search(
        r'\(cd "\$INSTALL_DIR" && .*agent\.install_selfcheck', text, re.DOTALL
    )


def test_install_playbook_stages_schema_and_injects_version():
    plays = yaml.safe_load(INSTALL_PLAYBOOK.read_text(encoding="utf-8"))
    tasks = [task for play in plays for task in play.get("tasks", [])]
    task_names = {task.get("name") for task in tasks}
    text = INSTALL_PLAYBOOK.read_text(encoding="utf-8")

    assert "Ensure schema staging directory exists" in task_names
    assert "Stage pipeline schema for install script" in task_names
    assert '"{{ agent_remote_tmp_dir }}/stp_schemas/pipeline_schema.json"' in text
    assert "{{ stp_repo_root }}/backend/schemas/pipeline_schema.json" in text
    assert "AGENT_CODE_VERSION" in text


def test_install_playbook_stages_schema_outside_agent_source_tree():
    text = INSTALL_PLAYBOOK.read_text(encoding="utf-8")
    staging = yaml.safe_load(text)
    stage_task = next(
        task
        for play in staging
        for task in play.get("tasks", [])
        if task.get("name") == "Stage pipeline schema for install script"
    )

    # schema 不能混进 agent 源码目录，否则会被复制进安装后的 agent/ 成为死文件
    assert stage_task["ansible.builtin.copy"]["dest"] == (
        "{{ agent_remote_tmp_dir }}/stp_schemas/pipeline_schema.json"
    )
