#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""stp-agent-priv —— Agent 主机提权边界 wrapper（#1250 / ADR-0037）。

背景：install_agent.sh 此前给 Agent 用户落无参数限制的免密
``rsync/cp/chmod/chown/ln`` —— 拿到 Agent 用户执行能力即可对任意 root
文件提权写。本脚本是唯一被 NOPASSWD sudoers 授权的入口（root:root、位于
``/usr/local/sbin``，不在 Agent 可写的安装目录内），把热更新与安装链所需的
root 操作收敛为**固定子命令 + 路径/属主/内容校验**：

    selftest         探活与自检（host_updater 用它决定 wrapper/legacy 分支）
    bootstrap        写 /etc/stp-agent-priv.conf 与 sudoers（仅 root 安装期）
    apply-code       把 Agent 暂存代码树同步进 $INSTALL_DIR/agent/
    install-schema   安装 Pipeline schema（校验 JSON 与调用者属主）
    write-version    写 agent/VERSION（校验短 SHA）
    sync-env         改 .env：AGENT_SECRET 或受控 overrides（保持原哨兵输出）
    deps-marker      写依赖刷新标记
    fix-ownership    安装目录属主回收（symlink 安全：chown -h）
    restart          重启 Agent systemd 服务

不变量：
- 所有写路径固定或经前缀校验，永不接受任意目标路径；
- chown 一律 ``-h``，避免代码树内 symlink 把 root 的改属主操作引到目录外；
- apply-code 使用 ``--safe-links`` 且只接受调用者自有的暂存目录；
- 运行需要 root（sudo）；``bootstrap`` 校验用户名/服务名的字符集后写
  sudoers，避免注入。

Python 3.6+（主机系统 python3，不使用第三方依赖）。exit code：0 成功、
1 selftest 失败、2 拒绝（STP_AGENT_PRIV_ERROR）。
"""

import argparse
import base64
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile

WRAPPER_PATH = "/usr/local/sbin/stp-agent-priv"
DEFAULT_CONF_PATH = "/etc/stp-agent-priv.conf"
SUDOERS_DIR = "/etc/sudoers.d"
RSYNC_BIN = "/usr/bin/rsync"
SYSTEMCTL_BIN = "/usr/bin/systemctl"
MAX_SCHEMA_BYTES = 2 * 1024 * 1024
MAX_ENV_PAYLOAD_BYTES = 64 * 1024

_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_VERSION_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

# 与 tools/ansible/roles/agent_deploy/defaults/main.yml 的 agent_install_excludes
# 保持同源；stp_schemas/ 与 wrapper 自身不进安装目录。
FIXED_EXCLUDES = [
    "__pycache__/",
    "*.pyc",
    "test_agent*.py",
    "test_aimonkey*.py",
    "test_main*.py",
    "tests/",
    "install_agent.sh",
    "agentctl.sh",
    "DEPLOY.md",
    ".env.example",
    "stability-test-agent.service",
    "hosts.txt",
    "stp_schemas/",
    "stp_agent_priv.py",
]
# 主机本地资源：不传输 + 不被 --delete-excluded 删除（#1248 语义）
HOST_LOCAL_PATHS = ["resources/mtbf/"]


class PrivError(RuntimeError):
    """可读拒绝原因；统一 exit 2 + stderr 哨兵。"""


def _fail(message):
    raise PrivError(message)


# ---------------------------------------------------------------------------
# 基础校验与进程工具
# ---------------------------------------------------------------------------

def _run(argv):
    """返回 (rc, stdout, stderr)，全部为 str。"""
    proc = subprocess.Popen(
        argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    out, err = proc.communicate()
    return (
        proc.returncode,
        out.decode("utf-8", "replace"),
        err.decode("utf-8", "replace"),
    )


def _caller_uid():
    """sudo 调用者 uid；直接以 root 运行时返回 (None, None) 表示可信。"""
    raw = os.environ.get("SUDO_UID", "").strip()
    if not raw:
        return None, None
    try:
        uid = int(raw)
    except ValueError:
        _fail("SUDO_UID is not an integer")
    gid_raw = os.environ.get("SUDO_GID", "").strip()
    gid = int(gid_raw) if gid_raw.isdigit() else None
    if uid == 0:
        return None, None
    return uid, gid


def _require_root():
    if os.geteuid() != 0:
        _fail("must run as root (sudo)")


def is_within(path, parent):
    """realpath(path) 是否位于 realpath(parent) 内（组件级，非字符串前缀）。"""
    path_real = os.path.realpath(path)
    parent_real = os.path.realpath(parent)
    if path_real == parent_real:
        return True
    return path_real.startswith(parent_real.rstrip(os.sep) + os.sep)


# #1553：`fix-ownership` 对 conf 的 INSTALL_DIR 做 `chown -R -h`，`apply-code` 往
# `INSTALL_DIR/agent/` 做 `rsync --delete`——两者都以 root 执行。若 INSTALL_DIR 被
# 指到系统目录（`/etc`、`/`、`/usr/local` 等），等于把这些目录整棵交给 AGENT_USER。
#
# 刻意**不含 `/srv`**：`/srv/stp` 是既有的安装形态（容器冒烟
# tools/dev/stp_agent_priv_smoke.sh 即用它），把它禁掉会打断合法部署。
_INSTALL_DIR_FORBIDDEN_ROOTS = (
    "/etc", "/usr", "/var", "/bin", "/sbin", "/lib", "/lib64", "/boot",
    "/root", "/home", "/dev", "/proc", "/sys", "/run",
)


def _validate_install_dir(install_dir):
    """INSTALL_DIR 基线护栏（#1553）：绝对非根，且与系统关键目录不得互相包含。

    「互相包含」两个方向都要拦：
    - install_dir 在关键目录**之下**（如 `/usr/local`）——对它 `chown -R` 会把
      `/usr/local/sbin/stp-agent-priv` 本体交给 AGENT_USER，随后用同一条 NOPASSWD
      规则以 root 执行任意代码；
    - install_dir 是某关键目录的**祖先**（如 `/etc` 之于 `/etc/sudoers.d`，`/` 之于
      一切）——`chown -R` 一次就把整棵子树交出去。

    放在这里而不是只放 cmd_bootstrap：任何读 conf 的子命令都要过，这样即使某台
    主机上已经存在被写坏的 conf，`apply-code` / `fix-ownership` 也会先拒绝。
    标准安装目录 `/opt/stability-test-agent` 两个方向都不命中。
    """
    if not install_dir.startswith("/") or install_dir == "/":
        _fail("INSTALL_DIR must be an absolute non-root path")
    normalized = install_dir.rstrip("/")
    for critical in _INSTALL_DIR_FORBIDDEN_ROOTS:
        if (
            normalized == critical
            or normalized.startswith(critical + "/")
            or critical.startswith(normalized + "/")
        ):
            _fail(
                "INSTALL_DIR must not overlap a system directory: %s vs %s "
                "(fix-ownership chowns it recursively and apply-code rsyncs into it "
                "as root)" % (install_dir, critical)
            )


def _reject_anchor_drift(conf_path, install_dir, user, group, service):
    """conf 一旦存在，安装锚点不得再变（#1553）。

    安装链（``install_agent.sh``）与 Ansible 更新
    （``update_agent.yml`` 的 ``become: true``）每次都传同一组值，因此重跑
    bootstrap 是幂等的、不受影响；而被边界圈住的主体即使能经 NOPASSWD 面调用
    bootstrap，也无法把 INSTALL_DIR 移向系统目录。

    刻意**不做**「要求 SUDO_UID 未设置」的调用者校验：Ansible 更新以
    ``ansible_user=android`` 执行 ``become: true``，此时 ``SUDO_UID`` 已被设置，
    那条检查会打断正常部署。合法迁移安装目录时，先由 root 删除 conf 再 bootstrap。
    """
    try:
        with open(conf_path, "r", encoding="utf-8") as handle:
            existing = {}
            for line in handle:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                existing[key.strip()] = value.strip()
    except OSError:
        return  # 首次 bootstrap：conf 尚不存在

    wanted = {
        "INSTALL_DIR": install_dir,
        "AGENT_USER": user,
        "AGENT_GROUP": group,
        "SERVICE_NAME": service,
    }
    drift = [
        "%s: %r -> %r" % (key, existing.get(key), value)
        for key, value in wanted.items()
        if existing.get(key) not in (None, value)
    ]
    if drift:
        _fail(
            "bootstrap must not re-point an existing install (deliberate migration: "
            "remove %s as root first): %s" % (conf_path, "; ".join(drift))
        )


def _load_conf(path):
    try:
        st = os.stat(path)
    except OSError as exc:
        _fail("config not found: %s (%s)" % (path, exc))
    if not stat.S_ISREG(st.st_mode):
        _fail("config is not a regular file: %s" % path)
    if st.st_uid != 0:
        _fail("config must be owned by root: %s" % path)
    if st.st_mode & 0o022:
        _fail("config must not be group/other writable: %s" % path)

    conf = {}
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            conf[key.strip()] = value.strip()

    for key in ("INSTALL_DIR", "AGENT_USER", "AGENT_GROUP", "SERVICE_NAME"):
        if not conf.get(key):
            _fail("config missing %s: %s" % (key, path))
    install_dir = conf["INSTALL_DIR"]
    _validate_install_dir(install_dir)
    for key in ("AGENT_USER", "AGENT_GROUP", "SERVICE_NAME"):
        if not _NAME_RE.match(conf[key]):
            _fail("config %s has invalid characters" % key)
    return conf


def _conf_path():
    return os.environ.get("STP_AGENT_PRIV_CONF", DEFAULT_CONF_PATH)


def _atomic_write(path, body, mode):
    directory = os.path.dirname(path) or "."
    fd, tmp_path = tempfile.mkstemp(prefix=".stp-priv-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(body)
        os.chmod(tmp_path, mode)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _atomic_copy(source, target, mode):
    directory = os.path.dirname(target) or "."
    fd, tmp_path = tempfile.mkstemp(prefix=".stp-priv-", dir=directory)
    try:
        os.close(fd)
        shutil.copyfile(source, tmp_path)
        os.chmod(tmp_path, mode)
        os.replace(tmp_path, target)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _chown_path(path, user, group):
    if user is None:
        return
    rc, _, err = _run(["chown", "-h", "%s:%s" % (user, group), path])
    if rc != 0:
        _fail("chown failed rc=%s: %s" % (rc, err.strip()[:200]))


def _visudo_check(path):
    visudo = shutil.which("visudo") or "/usr/sbin/visudo"
    if not os.path.exists(visudo):
        return
    rc, _, err = _run([visudo, "-cf", path])
    if rc != 0:
        try:
            os.unlink(path)
        except OSError:
            pass
        _fail("visudo rejected generated sudoers: %s" % err.strip()[:300])


def _decode_b64(raw, label):
    if not raw or len(raw) > MAX_ENV_PAYLOAD_BYTES:
        _fail("%s payload missing or too large" % label)
    try:
        return base64.b64decode(raw).decode("utf-8")
    except Exception as exc:
        _fail("%s payload is not valid base64/utf-8: %s" % (label, exc))


# ---------------------------------------------------------------------------
# 子命令
# ---------------------------------------------------------------------------

def cmd_selftest(args, conf):
    problems = []
    try:
        st = os.stat(WRAPPER_PATH)
        if st.st_uid != 0 or (st.st_mode & 0o022):
            problems.append("wrapper must be root-owned and not writable by others")
    except OSError as exc:
        problems.append("wrapper stat failed: %s" % exc)
    if problems:
        print("STP_AGENT_PRIV_SELFTEST_FAIL: " + "; ".join(problems))
        return 1
    print("STP_AGENT_PRIV_SELFTEST_OK wrapper=%s conf=%s install_dir=%s"
          % (WRAPPER_PATH, _conf_path(), conf["INSTALL_DIR"]))
    return 0


def build_sudoers_lines(user, service):
    """生成 NOPASSWD sudoers 内容（纯函数，便于单测锁定规则面）。

    只允许：固定服务名的 systemctl 子命令 + wrapper 单命令；不得包含
    任意 rsync/cp/chmod/chown/ln/stat 规则（#1250 修复面）。
    """
    lines = [
        "# Generated by stp-agent-priv bootstrap (#1250) - do not edit by hand.",
        "# 服务管理（固定命令，无参数面）",
    ]
    for binary in ("/bin/systemctl", "/usr/bin/systemctl"):
        for action in ("start", "stop", "restart", "status"):
            lines.append(
                "%s ALL=(root) NOPASSWD: %s %s %s"
                % (user, binary, action, service)
            )
        lines.append(
            "%s ALL=(root) NOPASSWD: %s daemon-reload" % (user, binary)
        )
    lines += [
        "# 升级提权入口：wrapper 内部校验参数与路径（ADR-0037）",
        "%s ALL=(root) NOPASSWD: %s" % (user, WRAPPER_PATH),
    ]
    return lines


def cmd_bootstrap(args, _conf):
    _require_root()
    install_dir = os.path.realpath(args.install_dir)
    _validate_install_dir(install_dir)
    if not os.path.isdir(install_dir):
        _fail("--install-dir does not exist: %s" % install_dir)
    for label, value in (
        ("--user", args.user), ("--group", args.group), ("--service", args.service),
    ):
        if not _NAME_RE.match(value):
            _fail("%s has invalid characters" % label)

    conf_path = _conf_path()
    _reject_anchor_drift(conf_path, install_dir, args.user, args.group, args.service)
    conf_body = (
        "# stp-agent-priv config (#1250) - generated by bootstrap, do not edit\n"
        "INSTALL_DIR=%s\nAGENT_USER=%s\nAGENT_GROUP=%s\nSERVICE_NAME=%s\n"
        % (install_dir, args.user, args.group, args.service)
    )
    _atomic_write(conf_path, conf_body, 0o644)

    sudoers_lines = build_sudoers_lines(args.user, args.service)
    sudoers_path = os.path.join(SUDOERS_DIR, args.service)
    os.makedirs(SUDOERS_DIR, exist_ok=True)
    _atomic_write(sudoers_path, "\n".join(sudoers_lines) + "\n", 0o440)
    _visudo_check(sudoers_path)
    print("STP_AGENT_PRIV_BOOTSTRAP_OK conf=%s sudoers=%s" % (conf_path, sudoers_path))
    return 0


def cmd_apply_code(args, conf):
    _require_root()
    caller_uid, _ = _caller_uid()
    if os.path.islink(args.staged):
        _fail("--staged must not be a symlink")
    staged = os.path.realpath(args.staged)
    if not os.path.isdir(staged):
        _fail("--staged is not a directory: %s" % staged)
    if is_within(staged, conf["INSTALL_DIR"]):
        _fail("--staged must be outside INSTALL_DIR")
    if caller_uid is not None and os.stat(staged).st_uid != caller_uid:
        _fail("--staged must be owned by the calling user")
    if not os.path.isfile(RSYNC_BIN):
        _fail("rsync not found: %s" % RSYNC_BIN)

    dest = os.path.join(conf["INSTALL_DIR"], "agent") + os.sep
    argv = [RSYNC_BIN, "-a", "--delete", "--delete-excluded", "--safe-links"]
    for item in FIXED_EXCLUDES:
        argv.append("--exclude=%s" % item)
    for item in HOST_LOCAL_PATHS:
        argv.append("--exclude=%s" % item)
        argv.append("--filter=protect %s" % item)
    argv += [staged + os.sep, dest]
    rc, _, err = _run(argv)
    if rc != 0:
        _fail("rsync failed rc=%s: %s" % (rc, err.strip()[:300]))
    print("STP_APPLY_CODE_OK")
    return 0


def cmd_install_schema(args, conf):
    _require_root()
    caller_uid, _ = _caller_uid()
    source = args.file
    if os.path.islink(source):
        _fail("--file must not be a symlink")
    if not os.path.isfile(source):
        _fail("--file is not a file: %s" % source)
    st = os.stat(source)
    if caller_uid is not None and st.st_uid != caller_uid:
        _fail("--file must be owned by the calling user")
    if st.st_size > MAX_SCHEMA_BYTES:
        _fail("--file too large (%d bytes)" % st.st_size)
    try:
        with open(source, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError) as exc:
        _fail("--file is not valid JSON: %s" % exc)
    properties = payload.get("properties") if isinstance(payload, dict) else None
    if "$schema" not in payload or not isinstance(properties, dict) \
            or "lifecycle" not in properties:
        _fail("--file is not a Pipeline schema ($schema/lifecycle mismatch)")

    target_dir = os.path.join(conf["INSTALL_DIR"], "schemas")
    os.makedirs(target_dir, exist_ok=True)
    target = os.path.join(target_dir, "pipeline_schema.json")
    _atomic_copy(source, target, 0o644)
    _chown_path(target, conf["AGENT_USER"], conf["AGENT_GROUP"])
    print("STP_INSTALL_SCHEMA_OK")
    return 0


def cmd_write_version(args, conf):
    _require_root()
    version = args.version.strip()
    if not version:
        print("STP_WRITE_VERSION_SKIPPED")
        return 0
    if not _VERSION_RE.match(version):
        _fail("--version must be a short git SHA (7-40 hex chars)")
    target = os.path.join(conf["INSTALL_DIR"], "agent", "VERSION")
    _atomic_write(target, version + "\n", 0o644)
    _chown_path(target, conf["AGENT_USER"], conf["AGENT_GROUP"])
    print("STP_WRITE_VERSION_OK version=%s" % version)
    return 0


def _read_env(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read().splitlines()
    except OSError as exc:
        _fail(".env not readable: %s (%s)" % (path, exc))


def _write_env_preserving_owner(path, lines):
    """原子替换 .env，但保留原 uid/gid 与 mode——与旧写法（原地截断）等价。"""
    try:
        st = os.stat(path)
    except OSError as exc:
        _fail(".env stat failed: %s (%s)" % (path, exc))
    body = "\n".join(lines) + ("\n" if lines else "")
    _atomic_write(path, body, st.st_mode & 0o777)
    os.chown(path, st.st_uid, st.st_gid)


def cmd_sync_env(args, conf):
    _require_root()
    env_path = os.path.join(conf["INSTALL_DIR"], ".env")
    if not os.path.exists(env_path):
        _fail(".env missing at %s" % env_path)

    if args.secret_b64:
        secret = _decode_b64(args.secret_b64, "secret")
        lines = _read_env(env_path)
        replaced = False
        updated = []
        for line in lines:
            if line.startswith("AGENT_SECRET="):
                updated.append("AGENT_SECRET=" + secret)
                replaced = True
            else:
                updated.append(line)
        if not replaced:
            updated.append("AGENT_SECRET=" + secret)
        _write_env_preserving_owner(env_path, updated)
        print("STP_ENV_SECRET_SYNCED=1")
        return 0

    overrides = json.loads(_decode_b64(args.overrides_b64 or "", "overrides"))
    path_keys = json.loads(_decode_b64(args.path_keys_b64 or "", "path_keys"))
    if not isinstance(overrides, dict) or not isinstance(path_keys, list):
        _fail("overrides/path_keys payload shape invalid")
    for key, value in overrides.items():
        if not _NAME_RE.match(str(key)) or not isinstance(value, str):
            _fail("override entry invalid: %r" % key)

    lines = _read_env(env_path)
    if not overrides:
        print("STP_ENV_SYNCED=")
        print("STP_ENV_PATH_MISSING=")
        return 0

    seen = set()
    updated_keys = []
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            new_lines.append(line)
            continue
        key = line.partition("=")[0].strip()
        if key in overrides:
            new_lines.append("%s=%s" % (key, overrides[key]))
            seen.add(key)
            updated_keys.append(key)
        else:
            new_lines.append(line)
    for key, value in overrides.items():
        if key not in seen:
            new_lines.append("%s=%s" % (key, value))
            updated_keys.append(key)
    _write_env_preserving_owner(env_path, new_lines)
    print("STP_ENV_SYNCED=" + ",".join(sorted(updated_keys)))

    missing = {
        key: overrides[key]
        for key in sorted(path_keys)
        if key in overrides and not os.path.exists(overrides[key])
    }
    encoded = base64.b64encode(
        json.dumps(missing, sort_keys=True).encode("utf-8")
    ).decode("ascii")
    print("STP_ENV_PATH_MISSING=" + encoded)
    return 0


def cmd_deps_marker(args, conf):
    _require_root()
    sha = args.sha.strip()
    if not _SHA256_RE.match(sha):
        _fail("--sha must be a sha256 hex digest")
    target = os.path.join(conf["INSTALL_DIR"], ".deps_installed_sha")
    _atomic_write(target, sha + "\n", 0o644)
    _chown_path(target, conf["AGENT_USER"], conf["AGENT_GROUP"])
    print("STP_DEPS_MARKER_OK")
    return 0


def cmd_fix_ownership(args, conf):
    _require_root()
    rc, _, err = _run([
        "chown", "-R", "-h",
        "%s:%s" % (conf["AGENT_USER"], conf["AGENT_GROUP"]),
        conf["INSTALL_DIR"],
    ])
    if rc != 0:
        _fail("chown failed rc=%s: %s" % (rc, err.strip()[:300]))
    print("STP_FIX_OWNERSHIP_OK")
    return 0


def cmd_restart(args, conf):
    _require_root()
    rc, _, err = _run([SYSTEMCTL_BIN, "restart", conf["SERVICE_NAME"]])
    if rc != 0:
        _fail("systemctl restart failed rc=%s: %s" % (rc, err.strip()[:300]))
    print("STP_RESTART_OK")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser():
    parser = argparse.ArgumentParser(
        prog="stp-agent-priv",
        description="Privilege boundary wrapper for STP Agent hosts (#1250).",
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("selftest", help="check wrapper/config integrity")

    p = sub.add_parser("bootstrap", help="write conf + sudoers (install time)")
    p.add_argument("--install-dir", required=True)
    p.add_argument("--user", required=True)
    p.add_argument("--group", required=True)
    p.add_argument("--service", required=True)

    p = sub.add_parser("apply-code", help="sync staged agent tree into install dir")
    p.add_argument("--staged", required=True)

    p = sub.add_parser("install-schema", help="install pipeline schema")
    p.add_argument("--file", required=True)

    p = sub.add_parser("write-version", help="write agent/VERSION")
    p.add_argument("--version", default="")

    p = sub.add_parser("sync-env", help="update .env secret/overrides")
    p.add_argument("--secret-b64", default="")
    p.add_argument("--overrides-b64", default="")
    p.add_argument("--path-keys-b64", default="")

    p = sub.add_parser("deps-marker", help="write deps installed sha marker")
    p.add_argument("--sha", required=True)

    sub.add_parser("fix-ownership", help="restore install dir ownership")
    sub.add_parser("restart", help="restart the agent service")
    return parser


def main(argv=None):
    parser = _build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 2

    conf = None
    if args.command != "bootstrap":
        try:
            conf = _load_conf(_conf_path())
        except PrivError as exc:
            if args.command == "selftest":
                print("STP_AGENT_PRIV_SELFTEST_FAIL: %s" % exc)
                return 1
            print("STP_AGENT_PRIV_ERROR: %s" % exc, file=sys.stderr)
            return 2

    handlers = {
        "selftest": cmd_selftest,
        "bootstrap": cmd_bootstrap,
        "apply-code": cmd_apply_code,
        "install-schema": cmd_install_schema,
        "write-version": cmd_write_version,
        "sync-env": cmd_sync_env,
        "deps-marker": cmd_deps_marker,
        "fix-ownership": cmd_fix_ownership,
        "restart": cmd_restart,
    }
    try:
        return handlers[args.command](args, conf)
    except PrivError as exc:
        print("STP_AGENT_PRIV_ERROR: %s" % exc, file=sys.stderr)
        return 2
    except Exception as exc:  # 未预期错误也不能静默
        print("STP_AGENT_PRIV_ERROR: unexpected: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
