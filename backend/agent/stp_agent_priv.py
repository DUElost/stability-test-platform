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
    apply-resources  把暂存 resources/ 同步进 agent/resources/（P2 独立通道）
    install-schema   安装 Pipeline schema（校验 JSON 与调用者属主）
    write-version    写 agent/VERSION（校验短 SHA）
    write-digest     写 agent/ARTIFACT_DIGEST（ADR-0040，校验 sha256:<hex>）
    sync-env         改 .env：AGENT_SECRET 或受控 overrides（保持原哨兵输出）
    deps-marker      写依赖刷新标记
    fix-ownership    安装目录属主回收（symlink 安全：chown -h）
    restart          重启 Agent systemd 服务

不变量：
- 所有写路径固定或经前缀校验，永不接受任意目标路径；
- 目录逐级以 O_NOFOLLOW 打开，写入与属主变更不重新解析可变父路径；
- apply-code 使用 ``--safe-links``，且 rsync 以配置中的非 root Agent 身份运行；
- 运行需要 root（sudo）；``bootstrap`` 校验用户名/服务名的字符集后写
  sudoers，避免注入。

Python 3.6+（主机系统 python3，不使用第三方依赖）。exit code：0 成功、
1 selftest 失败、2 拒绝（STP_AGENT_PRIV_ERROR）。
"""

import argparse
import base64
from contextlib import contextmanager
import grp
import json
import os
import pwd
import re
import shutil
import stat
import subprocess
import sys
import uuid

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
# ADR-0040 §4.3 P2 前置（#1950）：resources/ 仅加 protect（防 --delete 清掉
# 229MB 大件），**不 exclude**——载荷仍携带 resources/ 期间分发照旧；P2 载荷
# 收缩（agent-code 剔除 resources/）后分发自然停止、保护已在位。mtbf/ 的
# protect 语义被 resources/ 传递覆盖，其 exclude 仍必需。
PROTECT_ONLY_PATHS = ["resources/"]


class PrivError(RuntimeError):
    """可读拒绝原因；统一 exit 2 + stderr 哨兵。"""


def _fail(message):
    raise PrivError(message)


# ---------------------------------------------------------------------------
# 基础校验与进程工具
# ---------------------------------------------------------------------------

def _run(argv, pass_fds=(), preexec_fn=None):
    """返回 (rc, stdout, stderr)，全部为 str。"""
    proc = subprocess.Popen(
        argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        pass_fds=pass_fds, preexec_fn=preexec_fn,
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
    if normalized != os.path.normpath(normalized) or normalized.startswith("//"):
        _fail("INSTALL_DIR must be a normalized absolute path")
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


def _open_directory(path, dir_fd=None, create=False):
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    descriptor = os.open("/" if os.path.isabs(path) else ".", flags, dir_fd=dir_fd)
    try:
        for component in path.split(os.sep):
            if not component or component == ".":
                continue
            if component == "..":
                _fail("parent traversal is not allowed")
            if create:
                try:
                    os.mkdir(component, mode=0o755, dir_fd=descriptor)
                except FileExistsError:
                    pass
            child = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


@contextmanager
def _target_directory(conf, child=None, create=False):
    descriptor = _open_directory(conf["INSTALL_DIR"])
    try:
        if child:
            child_descriptor = _open_directory(child, dir_fd=descriptor, create=create)
            os.close(descriptor)
            descriptor = child_descriptor
        yield descriptor
    finally:
        os.close(descriptor)


def _agent_identity(conf):
    account = pwd.getpwnam(conf["AGENT_USER"])
    return account.pw_uid, grp.getgrnam(conf["AGENT_GROUP"]).gr_gid


def _atomic_write_at(directory_fd, name, body, mode, owner=None):
    temporary = ".stp-priv-" + uuid.uuid4().hex
    descriptor = os.open(
        temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600, dir_fd=directory_fd,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(body)
            handle.flush()
            if owner is not None:
                os.fchown(handle.fileno(), *owner)
            os.fchmod(handle.fileno(), mode)
        os.replace(temporary, name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
    except Exception:
        try:
            os.unlink(temporary, dir_fd=directory_fd)
        except OSError:
            pass
        raise


def _atomic_write(path, body, mode):
    descriptor = _open_directory(os.path.dirname(path) or ".")
    try:
        _atomic_write_at(descriptor, os.path.basename(path), body, mode)
    finally:
        os.close(descriptor)


def _read_regular_at(directory_fd, name, limit, owner=None):
    descriptor = os.open(
        name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd,
    )
    with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
        metadata = os.fstat(handle.fileno())
        if not stat.S_ISREG(metadata.st_mode):
            _fail("input must be a regular file")
        if owner is not None and metadata.st_uid != owner:
            _fail("input must be owned by the calling user")
        if metadata.st_size > limit:
            _fail("input too large")
        body = handle.read(limit + 1)
        if len(body.encode("utf-8")) > limit:
            _fail("input too large")
        return body, metadata


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
    staged = os.path.abspath(args.staged)
    if is_within(staged, conf["INSTALL_DIR"]):
        _fail("--staged must be outside INSTALL_DIR")
    if not os.path.isfile(RSYNC_BIN):
        _fail("rsync not found: %s" % RSYNC_BIN)
    agent_uid, agent_gid = _agent_identity(conf)
    if agent_uid == 0:
        _fail("apply-code requires a non-root AGENT_USER")

    def drop_privileges():
        os.initgroups(conf["AGENT_USER"], agent_gid)
        os.setgid(agent_gid)
        os.setuid(agent_uid)

    argv = [
        RSYNC_BIN, "-a", "--no-owner", "--no-group", "--delete",
        "--delete-excluded", "--safe-links",
    ]
    for item in FIXED_EXCLUDES:
        argv.append("--exclude=%s" % item)
    for item in HOST_LOCAL_PATHS:
        argv.append("--exclude=%s" % item)
        argv.append("--filter=protect %s" % item)
    for item in PROTECT_ONLY_PATHS:
        # 只防删除不拦同步（#1950：载荷仍携带 resources/ 期间分发照旧）
        argv.append("--filter=protect %s" % item)
    staged_fd = _open_directory(staged)
    try:
        if caller_uid is not None and os.fstat(staged_fd).st_uid != caller_uid:
            _fail("--staged must be owned by the calling user")
        with _target_directory(conf, "agent", create=True) as target_fd:
            os.fchown(target_fd, agent_uid, agent_gid)
            argv += ["/proc/self/fd/%d/" % staged_fd, "/proc/self/fd/%d/" % target_fd]
            rc, _, err = _run(
                argv, pass_fds=(staged_fd, target_fd), preexec_fn=drop_privileges,
            )
    finally:
        os.close(staged_fd)
    if rc != 0:
        _fail("rsync failed rc=%s: %s" % (rc, err.strip()[:300]))
    print("STP_APPLY_CODE_OK")
    return 0


def cmd_apply_resources(args, conf):
    """ADR-0040 §5-3 P2-B（#1975）：resources 载荷独立收敛通道。

    rsync 范围限定 ``$INSTALL_DIR/agent/resources/`` 子树（--delete 不出界）；
    ``resources/mtbf/`` 永远主机本地——exclude+protect（#214/#216/#1248 语义）。
    其余边界与 apply-code 同模式：staged 属主校验、--safe-links、降权执行。
    """
    _require_root()
    caller_uid, _ = _caller_uid()
    staged = os.path.abspath(args.staged)
    if is_within(staged, conf["INSTALL_DIR"]):
        _fail("--staged must be outside INSTALL_DIR")
    if not os.path.isfile(RSYNC_BIN):
        _fail("rsync not found: %s" % RSYNC_BIN)
    agent_uid, agent_gid = _agent_identity(conf)
    if agent_uid == 0:
        _fail("apply-resources requires a non-root AGENT_USER")

    def drop_privileges():
        os.initgroups(conf["AGENT_USER"], agent_gid)
        os.setgid(agent_gid)
        os.setuid(agent_uid)

    staged_fd = _open_directory(staged)
    try:
        if caller_uid is not None and os.fstat(staged_fd).st_uid != caller_uid:
            _fail("--staged must be owned by the calling user")
        with _target_directory(conf, "agent", create=True) as target_fd:
            argv = [
                RSYNC_BIN, "-a", "--no-owner", "--no-group", "--delete",
                "--delete-excluded", "--safe-links",
                "--exclude=mtbf/",
                "--filter=protect mtbf/",
                "/proc/self/fd/%d/resources/" % staged_fd,
                "/proc/self/fd/%d/resources/" % target_fd,
            ]
            rc, _, err = _run(
                argv, pass_fds=(staged_fd, target_fd), preexec_fn=drop_privileges,
            )
    finally:
        os.close(staged_fd)
    if rc != 0:
        _fail("rsync failed rc=%s: %s" % (rc, err.strip()[:300]))
    print("STP_APPLY_RESOURCES_OK")
    return 0


def cmd_install_schema(args, conf):
    _require_root()
    caller_uid, _ = _caller_uid()
    source = os.path.abspath(args.file)
    source_fd = _open_directory(os.path.dirname(source))
    try:
        body, _ = _read_regular_at(
            source_fd, os.path.basename(source), MAX_SCHEMA_BYTES, owner=caller_uid,
        )
        payload = json.loads(body)
    except (OSError, ValueError) as exc:
        _fail("--file is not valid JSON: %s" % exc)
    finally:
        os.close(source_fd)
    properties = payload.get("properties") if isinstance(payload, dict) else None
    if not isinstance(payload, dict) or "$schema" not in payload or not isinstance(properties, dict) \
            or "lifecycle" not in properties:
        _fail("--file is not a Pipeline schema ($schema/lifecycle mismatch)")

    with _target_directory(conf, "schemas", create=True) as target_fd:
        _atomic_write_at(
            target_fd, "pipeline_schema.json", body, 0o644, _agent_identity(conf),
        )
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
    with _target_directory(conf, "agent") as target_fd:
        _atomic_write_at(target_fd, "VERSION", version + "\n", 0o644, _agent_identity(conf))
    print("STP_WRITE_VERSION_OK version=%s" % version)
    return 0


_DIGEST_FILENAMES = {"code": "ARTIFACT_DIGEST", "resources": "ARTIFACT_DIGEST_RESOURCES"}


def cmd_write_digest(args, conf):
    """ADR-0040 D2：部署收敛成功后受控写入 ARTIFACT_DIGEST（write-version 同族）。

    ``--kind``（#1963，P2 身份分层）：code → ARTIFACT_DIGEST（默认，向后
    兼容）；resources → ARTIFACT_DIGEST_RESOURCES。
    """
    _require_root()
    digest = args.digest.strip()
    kind = getattr(args, "kind", "code") or "code"
    if kind not in _DIGEST_FILENAMES:
        _fail("--kind must be 'code' or 'resources'")
    if not digest:
        print("STP_WRITE_DIGEST_SKIPPED")
        return 0
    prefix, _, hexpart = digest.partition(":")
    if prefix != "sha256" or not _SHA256_RE.match(hexpart):
        _fail("--digest must be 'sha256:<64 hex chars>' (ADR-0040 D1)")
    with _target_directory(conf, "agent") as target_fd:
        _atomic_write_at(
            target_fd, _DIGEST_FILENAMES[kind], digest + "\n", 0o644, _agent_identity(conf),
        )
    print("STP_WRITE_DIGEST_OK kind=%s digest=%s" % (kind, digest))
    return 0


def _write_env_preserving_owner(directory_fd, lines, metadata):
    """原子替换 .env，但保留原 uid/gid 与 mode——与旧写法（原地截断）等价。"""
    body = "\n".join(lines) + ("\n" if lines else "")
    _atomic_write_at(
        directory_fd, ".env", body, metadata.st_mode & 0o777,
        (metadata.st_uid, metadata.st_gid),
    )


def cmd_sync_env(args, conf):
    _require_root()
    with _target_directory(conf) as target_fd:
        return _sync_env(args, target_fd)


def _sync_env(args, target_fd):
    body, metadata = _read_regular_at(target_fd, ".env", MAX_ENV_PAYLOAD_BYTES)
    lines = body.splitlines()

    if args.secret_b64:
        secret = _decode_b64(args.secret_b64, "secret")
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
        _write_env_preserving_owner(target_fd, updated, metadata)
        print("STP_ENV_SECRET_SYNCED=1")
        return 0

    overrides = json.loads(_decode_b64(args.overrides_b64 or "", "overrides"))
    path_keys = json.loads(_decode_b64(args.path_keys_b64 or "", "path_keys"))
    if not isinstance(overrides, dict) or not isinstance(path_keys, list):
        _fail("overrides/path_keys payload shape invalid")
    for key, value in overrides.items():
        if not _NAME_RE.match(str(key)) or not isinstance(value, str):
            _fail("override entry invalid: %r" % key)

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
    _write_env_preserving_owner(target_fd, new_lines, metadata)
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
    with _target_directory(conf) as target_fd:
        _atomic_write_at(
            target_fd, ".deps_installed_sha", sha + "\n", 0o644, _agent_identity(conf),
        )
    print("STP_DEPS_MARKER_OK")
    return 0


def cmd_fix_ownership(args, conf):
    _require_root()
    owner = _agent_identity(conf)
    with _target_directory(conf) as target_fd:
        for _, directories, files, directory_fd in os.fwalk(
            ".", topdown=False, onerror=_fail, follow_symlinks=False, dir_fd=target_fd,
        ):
            for name in directories + files:
                os.chown(name, *owner, dir_fd=directory_fd, follow_symlinks=False)
            os.fchown(directory_fd, *owner)
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

    p = sub.add_parser("write-digest", help="write agent/ARTIFACT_DIGEST (ADR-0040)")
    p.add_argument("--kind", default="code", choices=["code", "resources"])

    sub.add_parser(
        "apply-resources", help="sync staged resources/ into agent/resources/ (ADR-0040 P2)",
    )
    p.add_argument("--digest", default="")

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
        "write-digest": cmd_write_digest,
        "apply-resources": cmd_apply_resources,
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
