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

import base64
import importlib.util
import json
import os
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
        "argparse", "base64", "contextlib", "grp", "io", "json", "os", "pwd", "re", "shutil",
        "stat", "subprocess", "sys", "uuid",
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
    assert "os.fwalk(" in text
    assert "follow_symlinks=False" in text
    assert "os.O_NOFOLLOW" in text
    # 不提供任意目标路径参数（固定 INSTALL_DIR）
    assert "--dest" not in text


def test_wrapper_protect_only_paths():
    """#1950 / ADR-0040 §4.3 P2 前置：resources/ 只防删除、不拦同步。

    HOST_LOCAL_PATHS 的 exclude+protect 对会让 rsync 停止分发 resources/
    （P2 独立通道尚不存在 = 分发断档）；PROTECT_ONLY_PATHS 只追加
    protect 过滤。载荷收缩（P2 剔除 resources/）后分发自然停止。
    """
    module = _load_wrapper()

    assert module.PROTECT_ONLY_PATHS == ["resources/"]
    # mtbf 语义不变：exclude+protect（不同步 + 不删）
    assert module.HOST_LOCAL_PATHS == ["resources/mtbf/"]

    text = WRAPPER.read_text(encoding="utf-8")
    # protect-only 追加环存在，且不产生 --exclude=resources/
    assert "for item in PROTECT_ONLY_PATHS:" in text
    assert '"--exclude=resources/"' not in text
    assert '"--exclude=%s" % item' in text  # mtbf 的 exclude 仍在


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


# ── #1553：安装锚点护栏（INSTALL_DIR 不得指向系统目录；conf 不得被重新指向） ──


def test_install_dir_guard_rejects_system_directories():
    """`fix-ownership` 会对 INSTALL_DIR 做 `chown -R`、`apply-code` 往其 rsync，
    两者都是 root 操作——INSTALL_DIR 指向系统目录等于把那些目录交给 AGENT_USER。"""
    module = _load_wrapper()

    for bad in (
        "/etc", "/", "/usr", "/usr/local", "/usr/local/sbin", "/var",
        "/boot", "/home", "/root", "/etc/sudoers.d", "/opt/../etc", "//etc",
    ):
        try:
            module._validate_install_dir(bad)
        except module.PrivError:
            continue
        raise AssertionError(f"应当拒绝 INSTALL_DIR={bad}")

    # 标准安装目录与自定义目录都必须放行
    for ok in ("/opt/stability-test-agent", "/opt/agent", "/data/stp-agent"):
        module._validate_install_dir(ok)


def test_load_conf_rejects_poisoned_install_dir(tmp_path):
    """即使主机上已存在被写坏的 conf，读它的子命令也要先拒绝（纵深防御）。"""
    module = _load_wrapper()
    conf = tmp_path / "conf"
    conf.write_text(
        "INSTALL_DIR=/etc\nAGENT_USER=android\nAGENT_GROUP=android\n"
        "SERVICE_NAME=stability-test-agent\n",
        encoding="utf-8",
    )
    # _load_conf 还要求 root 属主；非 root 环境下先以属主错误失败也算拒绝，
    # 但本用例只想证明 INSTALL_DIR 这一关——直接调 _validate_install_dir 已覆盖。
    try:
        module._load_conf(str(conf))
    except module.PrivError:
        return
    raise AssertionError("被写坏的 conf 必须被拒绝")


def test_anchor_drift_guard_allows_first_time_and_idempotent_rerun(tmp_path):
    """安装链与 Ansible 更新每次传同一组值 → 首次与重跑都必须放行。"""
    module = _load_wrapper()
    conf = tmp_path / "conf"

    # 首次：conf 不存在
    module._reject_anchor_drift(
        str(conf), "/opt/stability-test-agent", "android", "android", "stability-test-agent",
    )

    conf.write_text(
        "INSTALL_DIR=/opt/stability-test-agent\nAGENT_USER=android\nAGENT_GROUP=android\n"
        "SERVICE_NAME=stability-test-agent\n",
        encoding="utf-8",
    )
    # 幂等重跑（Ansible 更新路径的形态）
    module._reject_anchor_drift(
        str(conf), "/opt/stability-test-agent", "android", "android", "stability-test-agent",
    )


def test_anchor_drift_guard_rejects_repointing(tmp_path):
    """#1553 的核心：把 INSTALL_DIR 移向 /etc 必须被拒（否则 fix-ownership 会
    `chown -R agent:agent /etc`）。"""
    module = _load_wrapper()
    conf = tmp_path / "conf"
    conf.write_text(
        "INSTALL_DIR=/opt/stability-test-agent\nAGENT_USER=android\nAGENT_GROUP=android\n"
        "SERVICE_NAME=stability-test-agent\n",
        encoding="utf-8",
    )

    for kwargs in (
        {"install_dir": "/etc", "user": "android", "group": "android", "service": "stability-test-agent"},
        {"install_dir": "/opt/stability-test-agent", "user": "root", "group": "android", "service": "stability-test-agent"},
        {"install_dir": "/opt/stability-test-agent", "user": "android", "group": "android", "service": "other-svc"},
    ):
        try:
            module._reject_anchor_drift(
                str(conf), kwargs["install_dir"], kwargs["user"],
                kwargs["group"], kwargs["service"],
            )
        except module.PrivError:
            continue
        raise AssertionError(f"应当拒绝锚点漂移: {kwargs}")


def test_smoke_script_covers_install_dir_guard():
    """smoke 脚本是**唯一**能以 root 真跑 bootstrap 的载体（helper 级单测过不了
    `_require_root`），锚点护栏必须在它里面有一席之地——否则这条护栏只有静态断言
    守，无法证明它在真实 root 路径上生效。"""
    text = SMOKE.read_text(encoding="utf-8")
    assert "INSTALL_DIR_GUARD_OK" in text
    assert "ANCHOR_DRIFT_GUARD_OK" in text
    assert 'for bad_dir in /etc / /usr/local /usr/local/sbin; do' in text


# ── #2069: sync-env 值侧与键侧同档校验（换行 = 向 .env 注入新行） ──────────


def _b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


class _SyncEnvArgs:
    """`_sync_env` 的最小 args 替身（只带它读取的三个 b64 载荷）。"""

    def __init__(self, overrides=None, path_keys=None, secret=""):
        self.secret_b64 = _b64(secret) if secret else ""
        self.overrides_b64 = _b64(json.dumps(overrides or {}))
        self.path_keys_b64 = _b64(json.dumps(path_keys or []))


def _env_dir_fd(tmp_path, body: str = "API_URL=http://cp\n"):
    env = tmp_path / ".env"
    env.write_text(body, encoding="utf-8")
    return os.open(str(tmp_path), os.O_RDONLY | os.O_DIRECTORY)


def _expect_priv_error(module, callable_, *args):
    try:
        callable_(*args)
    except module.PrivError:
        return
    raise AssertionError("应当以 PrivError 拒绝（值内含 CR/LF/NUL）")


def test_sync_env_rejects_newline_in_override_value(tmp_path):
    """#2069：值内换行会顶出额外行（如 LD_PRELOAD）→ 必须拒绝且不改写 .env。"""
    module = _load_wrapper()
    fd = _env_dir_fd(tmp_path)
    try:
        _expect_priv_error(
            module,
            module._sync_env,
            _SyncEnvArgs({"STP_FOO": "bar\nLD_PRELOAD=/tmp/x.so"}),
            fd,
        )
    finally:
        os.close(fd)
    assert (tmp_path / ".env").read_text(encoding="utf-8") == "API_URL=http://cp\n"


def test_sync_env_rejects_carriage_return_and_nul(tmp_path):
    module = _load_wrapper()
    for evil in ("bar\rLD_PRELOAD=/tmp/x.so", "bar\x00baz"):
        fd = _env_dir_fd(tmp_path)
        try:
            _expect_priv_error(module, module._sync_env, _SyncEnvArgs({"STP_FOO": evil}), fd)
        finally:
            os.close(fd)
        assert (tmp_path / ".env").read_text(encoding="utf-8") == "API_URL=http://cp\n"


def test_sync_env_writes_normal_value(tmp_path):
    """正向态：正常值照旧写入（拒绝面不得扩大到普通配置）。"""
    module = _load_wrapper()
    fd = _env_dir_fd(tmp_path)
    try:
        rc = module._sync_env(
            _SyncEnvArgs({"STP_FOO": "bar", "STP_PATHS": "/srv/a:/srv/b"}),
            fd,
        )
    finally:
        os.close(fd)
    assert rc == 0
    body = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "API_URL=http://cp" in body
    assert "STP_FOO=bar" in body
    assert "STP_PATHS=/srv/a:/srv/b" in body


def test_sync_env_rejects_newline_in_secret_payload(tmp_path):
    """第二层防线同时覆盖 secret 分支（写入路径统一收口在 _write_env_preserving_owner）。"""
    module = _load_wrapper()
    fd = _env_dir_fd(tmp_path)
    try:
        _expect_priv_error(module, module._sync_env, _SyncEnvArgs(secret="abc\ndef"), fd)
    finally:
        os.close(fd)
    assert (tmp_path / ".env").read_text(encoding="utf-8") == "API_URL=http://cp\n"


def test_write_env_preserving_owner_is_second_line_of_defense(tmp_path):
    """#2069：任何写路径都不得让控制字符落盘（未来新增写路径同样被拦）。"""
    module = _load_wrapper()

    class _Meta:
        st_mode = 0o644
        st_uid = os.getuid()
        st_gid = os.getgid()

    fd = _env_dir_fd(tmp_path)
    try:
        for bad in ("A=1\nB=2", "A=1\rB=2", "A=1\x00B=2"):
            _expect_priv_error(
                module, module._write_env_preserving_owner, fd, [bad], _Meta(),
            )
        # 正常行集仍可写（守卫的负向对照）
        assert module._write_env_preserving_owner(fd, ["A=1", "B=2"], _Meta()) is None
    finally:
        os.close(fd)
    assert (tmp_path / ".env").read_text(encoding="utf-8") == "A=1\nB=2\n"
