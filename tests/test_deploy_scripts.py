"""`deploy/*.sh` 的结构不变量：薄封装，不允许把业务逻辑搬进 shell。

三个入口脚本要能在任何一台干净的机器上跑起来，唯一允许的「逻辑」是：
定位仓库根、准备解释器/目录、按顺序调 `python -m tools.site_config` 与
`tools/release/build_bundle.py`。一旦有人在 shell 里复制一份校验/挂盘/建库，
两份实现就会开始漂移（I4 曾在 bindings 目录上踩过一次）。
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from tools.site_config import bootstrap

REPO_ROOT = Path(__file__).resolve().parents[1]
DEPLOY = REPO_ROOT / "deploy"
ENTRY_SCRIPTS = (
    DEPLOY / "preflight.sh",
    DEPLOY / "install.sh",
    DEPLOY / "agent/install.sh",
)
COMMON_LIB = DEPLOY / "lib/deploy-common.sh"
ALL_SCRIPTS = (*ENTRY_SCRIPTS, COMMON_LIB)

# 只允许出现在注释/字符串里的宿主机命令：真正的调用必须走工具
COMMAND_OWNED_BY_TOOL = re.compile(
    r"^\s*(sudo\s+)?(systemctl|service|nginx|psql|createdb|dropdb|useradd|userdel|"
    r"mount|umount|iptables|nft|redis-cli|dnf|yum|apt-get|apt)\b",
    re.M,
)


def code_lines(path: Path) -> list[str]:
    return [
        line for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def logical_lines(path: Path) -> list[str]:
    """把续行拼回去：一行命令可能跨多行（`-c \\n ...`）。"""
    joined: list[str] = []
    for line in code_lines(path):
        if joined and joined[-1].endswith("\\"):
            joined[-1] = joined[-1][:-1] + " " + line.strip()
            continue
        joined.append(line)
    return joined


@pytest.mark.parametrize("script", ALL_SCRIPTS, ids=lambda path: path.name)
def test_scripts_are_executable_and_parse(script: Path):
    assert script.is_file(), script
    if script in ENTRY_SCRIPTS:
        assert stat.S_IMODE(os.lstat(script).st_mode) & stat.S_IXUSR
    text = script.read_text(encoding="utf-8")
    assert text.startswith("#!/usr/bin/env bash")
    assert "set -euo pipefail" in text
    result = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


def test_scripts_do_not_leave_root_owned_bytecode():
    """部署工具以 root 跑，必须在仓库树里禁用字节码写入。"""
    text = COMMON_LIB.read_text(encoding="utf-8")
    assert "PYTHONDONTWRITEBYTECODE=1" in text
    for script in ENTRY_SCRIPTS:
        assert "lib/deploy-common.sh" in script.read_text(encoding="utf-8")


def test_entry_scripts_source_the_shared_defaults():
    for script in ENTRY_SCRIPTS:
        text = script.read_text(encoding="utf-8")
        assert "lib/deploy-common.sh" in text, script
        assert "deploy_defaults" in text, script


@pytest.mark.parametrize("script", ALL_SCRIPTS, ids=lambda path: path.name)
def test_no_script_implements_host_changes_itself(script: Path):
    offenders = [
        line for line in code_lines(script) if COMMAND_OWNED_BY_TOOL.match(line)
    ]
    assert not offenders, offenders


@pytest.mark.parametrize("script", ALL_SCRIPTS, ids=lambda path: path.name)
def test_no_script_parses_tool_output_with_a_second_language(script: Path):
    """jq/awk/sed 解析报告 = 第二份实现；报告只有 --json 与逐项回显两种消费方式。"""
    for line in code_lines(script):
        for tool in ("| jq", "|jq", "awk ", "grep -o"):
            assert tool not in line, line


EXECUTES_PYTHON = re.compile(r'^\s*\(?\s*(python3?|"\$\{?DEPLOY_PYTHON\}?"|"\$target/bin/python")\b')


def test_only_the_two_documented_python_entry_points_are_called():
    """shell 只允许把 python 当入口调用；venv/pip 的引导是唯一例外。"""
    for script in ALL_SCRIPTS:
        for line in logical_lines(script):
            if not EXECUTES_PYTHON.match(line):
                continue
            if "-m venv" in line or "bin/pip" in line or "-m pip" in line:
                continue
            assert "tools.site_config" in line or "tools/release/build_bundle.py" in line, line


def test_scripts_do_not_hardcode_the_site_identity():
    """站点标识只能来自 site.yaml（否则确认机制形同虚设）。"""
    for script in ENTRY_SCRIPTS:
        text = script.read_text(encoding="utf-8")
        if script.name == "install.sh":
            assert "deploy_site_identity" in text
        for literal in ("city-b", "confirm-site city"):
            if literal == "city-b":
                continue
            assert literal not in text


def test_site_input_options_are_forwarded_to_init():
    """站点输入项（库名/入口/存储/盘）必须能传给 init——否则真实用例只能改文件。"""
    text = (DEPLOY / "install.sh").read_text(encoding="utf-8")
    assert "INIT_OPTIONS" in text
    for option in ("--database", "--public-url", "--storage-mount", "--data-disk",
                   "--display-name", "--redis-index", "--admin-username"):
        assert option in text, option
    init_call = text.split("deploy_stp init", 1)[1].split("fi", 1)[0]
    install_call = text.split("deploy_stp install", 1)[1].split("\n\n", 1)[0]
    assert "INIT_OPTIONS" in init_call
    assert "INIT_OPTIONS" not in install_call


def test_shell_venv_bootstrap_matches_the_python_one():
    """shell 里只能有一处替身：与 bootstrap.ensure_tool_venv 的目标与依赖必须一致。"""
    text = COMMON_LIB.read_text(encoding="utf-8")
    assert "/opt/stp-tool" in text
    for package in ("pydantic", "pyyaml", "psycopg"):
        assert package in text
    import inspect

    source = inspect.getsource(bootstrap.ensure_tool_venv)
    assert "/opt/stp-tool" in source
    for package in ("pydantic", "pyyaml", "psycopg"):
        assert package in source


def test_shell_defaults_are_the_single_source_for_paths():
    text = COMMON_LIB.read_text(encoding="utf-8")
    for path in ("/etc/stp/site.yaml", "/etc/stp/bindings", "/var/lib/stp", "/srv/stp-bundle"):
        assert path in text
    for script in ENTRY_SCRIPTS:
        for line in code_lines(script):
            assert "/etc/stp/" not in line, (script, line)


def test_agent_install_uses_the_repo_outside_inventory_and_writes_a_template(tmp_path):
    text = (DEPLOY / "agent/install.sh").read_text(encoding="utf-8")
    assert "hosts.ini" in COMMON_LIB.read_text(encoding="utf-8")
    assert "TEMPLATE" in text
    assert "--agents-inventory" in text
    assert "--through-agents" in text
    # 模板由 Python 侧常量生成：shell 里不得出现清单键名或组名（注释除外）
    for line in code_lines(REPO_ROOT / "deploy/agent/install.sh"):
        assert "ansible_" not in line, line
        assert "stp_agents" not in line, line


def test_install_script_orders_bundle_before_site_inputs():
    """init 要读清单里的 expected_release：顺序颠倒会永久不匹配。"""
    text = (DEPLOY / "install.sh").read_text(encoding="utf-8")
    bundle_at = text.index("build_bundle.py")
    init_at = text.index("deploy_stp init")
    assert bundle_at < init_at


def test_every_entry_script_has_its_own_help():
    """--help 必须由脚本自己答（打印用法），不能变成「参数错误」。"""
    for script in ENTRY_SCRIPTS:
        text = script.read_text(encoding="utf-8")
        assert "--help|-h" in text or '"--help"' in text, script


def test_post_install_subcommands_require_an_installed_site():
    """verify/handover 在没有任何站点输入时应当直接报错，而不是留下 venv/目录。"""
    text = (DEPLOY / "install.sh").read_text(encoding="utf-8")
    assert text.index("needs an installed site") < text.index("deploy_ensure_python")


def test_verify_dry_run_is_refused_before_any_write():
    text = (DEPLOY / "install.sh").read_text(encoding="utf-8")
    assert "verify has no --dry-run" in text
    assert text.index("verify has no --dry-run") < text.index("deploy_ensure_python")


def test_preflight_script_never_prepares_anything():
    text = (DEPLOY / "preflight.sh").read_text(encoding="utf-8")
    assert "deploy_find_python" in text
    for forbidden in ("deploy_ensure_python", "deploy_ensure_state_dir", "deploy_ensure_bindings_dir", "install -d"):
        assert forbidden not in text, forbidden


def test_scripts_are_shipped_in_the_bundle_layout():
    """bundle 里必须带 deploy/：否则远端只剩一个装不起来的树。"""
    assert shutil.which("bash") is not None
    from tools.release.build_bundle import TREE_LAYOUT

    assert "deploy" in TREE_LAYOUT
