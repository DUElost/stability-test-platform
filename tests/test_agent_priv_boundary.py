"""提权边界 wrapper 契约（#1250 / ADR-0037）。

守什么：
- sudoers 规则面只剩「固定 systemctl + wrapper 单命令」，不再有任意
  rsync/cp/chmod/chown/ln 免密；
- wrapper 的路径判定、版本/摘要校验、协议哨兵不随实现漂移；
- 安装链与 Ansible 更新链都部署 wrapper 并 bootstrap（存量主机迁移面）。

wrapper 以文件方式加载（与部署形态一致：/usr/local/sbin 单文件脚本），
避免拉起 backend.agent 包的副作用。
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
WRAPPER = REPO_ROOT / "backend/agent/stp_agent_priv.py"
SMOKE = REPO_ROOT / "tools/dev/stp_agent_priv_smoke.sh"
INSTALL_SCRIPT = REPO_ROOT / "backend/agent/install_agent.sh"
UPDATE_PLAYBOOK = REPO_ROOT / "tools/ansible/playbooks/update_agent.yml"
ROLE_DEFAULTS = REPO_ROOT / "tools/ansible/roles/agent_deploy/defaults/main.yml"


def _load_wrapper():
    spec = importlib.util.spec_from_file_location("stp_agent_priv", WRAPPER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_wrapper_is_standalone_system_python_script():
    text = WRAPPER.read_text(encoding="utf-8")

    assert text.startswith("#!/usr/bin/python3")
    # 只能是 stdlib：部署环境不保证 venv/三方包，root 执行面越小越好
    imports = re.findall(r"^(?:import|from)\s+([a-zA-Z_][\w.]*)", text, re.MULTILINE)
    allowed_roots = {
        "argparse", "base64", "json", "os", "re", "shutil", "stat",
        "subprocess", "sys", "tempfile",
    }
    assert {name.split(".")[0] for name in imports} <= allowed_roots


def test_wrapper_path_check_is_component_wise():
    module = _load_wrapper()

    assert module.is_within("/opt/app/agent/x.py", "/opt/app") is True
    assert module.is_within("/opt/app", "/opt/app") is True
    # 字符串前缀陷阱：/opt/app2 不属于 /opt/app
    assert module.is_within("/opt/app2/x.py", "/opt/app") is False
    assert module.is_within("/etc/passwd", "/opt/app") is False


def test_wrapper_fixed_policy_flags():
    text = WRAPPER.read_text(encoding="utf-8")

    # 同步：不跟随外指 symlink + mtbf 主机本地保护（#1248 同源语义）
    assert '"--safe-links"' in text
    assert '"--delete-excluded"' in text
    assert "--filter=protect %s" in text
    assert "resources/mtbf/" in text
    # 属主回收必须 -h，防止代码树内 symlink 把 root chown 引到目录外
    assert '"chown", "-R", "-h"' in text
    # 不提供任意目标路径参数（固定 INSTALL_DIR）
    assert "--dest" not in text


def test_sudoers_rules_are_wrapper_plus_fixed_systemctl_only():
    module = _load_wrapper()
    lines = module.build_sudoers_lines("android", "stability-test-agent")
    body = "\n".join(lines)

    assert "NOPASSWD: /usr/local/sbin/stp-agent-priv" in body
    assert "NOPASSWD: /usr/bin/systemctl restart stability-test-agent" in body
    # 旧宽规则不得再出现（Any-arg 文件操作免密面）
    for banned in (
        "NOPASSWD: /usr/bin/rsync", "NOPASSWD: /bin/rsync",
        "NOPASSWD: /usr/bin/cp", "NOPASSWD: /usr/bin/chmod",
        "NOPASSWD: /usr/bin/chown", "NOPASSWD: /usr/bin/ln",
        "NOPASSWD: /usr/bin/stat",
    ):
        assert banned not in body, banned


def test_wrapper_validators_reject_unsafe_values():
    module = _load_wrapper()

    assert module._VERSION_RE.match("a1b2c3d") is not None
    assert module._VERSION_RE.match("x; rm -rf /") is None
    assert module._SHA256_RE.match("a" * 64) is not None
    assert module._SHA256_RE.match("a" * 63) is None
    assert module._NAME_RE.match("stability-test-agent") is not None
    assert module._NAME_RE.match("evil name") is None
    assert module._NAME_RE.match("evil\nALL=(ALL) NOPASSWD: ALL") is None


def test_wrapper_selftest_fails_without_conf(tmp_path):
    conf = tmp_path / "missing.conf"
    completed = subprocess.run(
        [sys.executable, str(WRAPPER), "selftest"],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "STP_AGENT_PRIV_CONF": str(conf)},
    )

    assert completed.returncode == 1
    assert "STP_AGENT_PRIV_SELFTEST_FAIL" in completed.stdout


def test_install_script_deploys_wrapper_and_drops_broad_rules():
    text = INSTALL_SCRIPT.read_text(encoding="utf-8")

    assert "install -D -m 0755 -o root -g root \"$WRAPPER_SRC\" /usr/local/sbin/stp-agent-priv" in text
    assert "/usr/local/sbin/stp-agent-priv bootstrap" in text
    assert "/usr/local/sbin/stp-agent-priv selftest" in text
    # 旧宽规则不得回潮
    for banned in (
        "NOPASSWD: /usr/bin/rsync", "NOPASSWD: /bin/rsync",
        "NOPASSWD: /usr/bin/cp, /bin/cp", "NOPASSWD: /usr/bin/chown, /bin/chown",
        "NOPASSWD: /usr/bin/ln, /bin/ln", "NOPASSWD: /usr/bin/stat, /bin/stat",
    ):
        assert banned not in text, banned
    # wrapper 源文件不进安装目录
    assert 'rm -f "$INSTALL_DIR/agent/stp_agent_priv.py"' in text


def test_update_playbook_migrates_existing_hosts():
    plays = yaml.safe_load(UPDATE_PLAYBOOK.read_text(encoding="utf-8"))
    task_names = {
        task.get("name")
        for play in plays
        for task in play.get("tasks", [])
    }
    text = UPDATE_PLAYBOOK.read_text(encoding="utf-8")

    assert "Install agent privilege wrapper" in task_names
    assert "Bootstrap privilege wrapper (conf + sudoers)" in task_names
    assert "Verify privilege wrapper self-test" in task_names
    assert "{{ stp_repo_root }}/backend/agent/stp_agent_priv.py" in text
    assert "dest: /usr/local/sbin/stp-agent-priv" in text


def test_role_defaults_exclude_wrapper_from_agent_tree():
    defaults = yaml.safe_load(ROLE_DEFAULTS.read_text(encoding="utf-8"))

    assert "stp_agent_priv.py" in defaults["agent_install_excludes"]


def test_wrapper_has_lf_line_endings():
    # CRLF 会破坏 shebang 执行（部署脚本必须 LF）
    assert b"\r\n" not in WRAPPER.read_bytes()


def test_smoke_script_never_executes_payload_on_host():
    text = SMOKE.read_text(encoding="utf-8")

    assert '[ ! -e /.dockerenv ]' in text
    assert "host mode never executes payloads" in text
    assert "docker run --rm -i -v \"$REPO_ROOT\":/src:ro" in text
