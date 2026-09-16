"""Probe-driven site input generation (I5.5 ``init``).

Turns "fill in 33 fields" into "confirm four answers": every input that can be
derived from the host (OS, architecture, timezone, addresses, local database and
Redis, the data disk) is probed and written with its provenance in a comment;
only the site identity, the public entry, the database/Redis choice and the
storage choice are asked.  Secrets are generated here and never printed except
the one-time administrator password.

``--fix`` (default) also prepares the host: the tool venv, the empty database
and role, and the storage mount.  ``--no-fix`` reports the exact commands
instead.  ``--dry-run`` writes nothing at all.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import secrets
from pathlib import Path
from typing import Callable

import yaml

from .ops import LocalOps, Ops
from .preflight import preflight_facts
from .validation import failure, passed

SITE_FILE_NAME = "site.yaml"
DEFAULT_DEPLOY_ROOT_PREFIX = "/opt/stp-"
DEFAULT_STORAGE_DIR = "/srv/stp-aee"
# 数据盘落点与 fstab：模块级常量，便于测试重定向（不得直接写宿主机）
HOST_MOUNT = "/srv/hdd"
FSTAB = Path("/etc/fstab")
DEFAULT_DB_ROLE = "stp"
ADMIN_PASSWORD_BYTES = 18

PROBE = "probe"
DEFAULT = "default"
ANSWER = "answer"


class BootstrapError(RuntimeError):
    def __init__(self, code: str, detail: str = ""):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}{': ' + detail if detail else ''}")


def _ask(prompt: str, default: str, *, interactive: bool, answers: dict[str, str], key: str) -> tuple[str, str]:
    """Return (value, provenance)."""
    if key in answers:
        return answers[key], ANSWER
    if not interactive:
        return default, DEFAULT
    try:
        reply = input(f"{prompt} [{default}]: ").strip()
    except EOFError:
        reply = ""
    return (reply or default, ANSWER if reply else DEFAULT)


def probe_data_disk(ops: Ops) -> tuple[str, int]:
    """Largest whole disk that is safe to auto-propose; never auto-formatted.

    Only a disk with **no partitions and no children** qualifies: a disk whose
    partitions are mounted (or hold data) must never be proposed as "unmounted",
    and the production AEE disk looks exactly like that.  Anything else must be
    named explicitly with ``--data-disk``.
    """
    result = ops.run(["lsblk", "-P", "-b", "-o", "NAME,TYPE,SIZE,MOUNTPOINT,PKNAME,RO"])
    disks: list[tuple[str, int]] = []
    children: set[str] = set()
    for line in result.stdout.splitlines():
        fields = dict(re.findall(r'(\w+)="([^"]*)"', line))
        parent = fields.get("PKNAME", "")
        if parent:
            children.add(parent)
        if fields.get("TYPE") != "disk" or fields.get("RO") == "1":
            continue
        # #2273：已挂载的整盘绝不能被提案——`MOUNTPOINT` 是唯一的判别信号，此前取回
        # 却丢弃。「整盘文件系统（mkfs /dev/sdb，无分区表）」没有 PKNAME 子项、TYPE
        # 仍是 disk、blkid 也返回类型，正是会被误提案的形态；init --yes 会二次挂载它
        # 并把 AEE 写入压到另一个角色正在使用的文件系统上。
        if fields.get("MOUNTPOINT"):
            continue
        try:
            size = int(fields.get("SIZE", "0"))
        except ValueError:
            continue
        disks.append((fields.get("NAME", ""), size))
    best, best_size = "", 0
    for name, size in sorted(disks, key=lambda item: item[1], reverse=True):
        if name in children:
            continue
        device = f"/dev/{name}"
        # 只有已带文件系统的裸盘才可能挂得上（本工具从不格式化）
        if not ops.run(["blkid", "-s", "TYPE", "-o", "value", device]).stdout.strip():
            continue
        best, best_size = device, size
        break
    return best, best_size // (1024 ** 3)


def ensure_tool_venv(ops: Ops, *, python: str = "/usr/bin/python3", target: Path = Path("/opt/stp-tool")) -> list[str]:
    """Create the tool venv when missing; returns the actions taken."""
    if (target / "bin" / "python").exists():
        return [f"tool venv already present: {target}"]
    actions: list[str] = []
    venv = ops.run([python, "-m", "venv", str(target)])
    if venv.returncode != 0:
        raise BootstrapError("bootstrap_toolenv", f"python3 -m venv {target}")
    actions.append(f"created tool venv: {target}")
    pip = target / "bin" / "pip"
    install = ops.run([str(pip), "install", "-q", "pydantic", "pyyaml", "psycopg[binary]"])
    if install.returncode != 0:
        raise BootstrapError("bootstrap_toolenv", "pip install pydantic pyyaml psycopg[binary]")
    actions.append("installed tool dependencies: pydantic, pyyaml, psycopg[binary]")
    return actions


def _role_password_works(ops: Ops, dsn: str, password: str) -> bool | None:
    """Try one login with the generated credentials; None when it cannot be judged."""
    try:
        import psycopg
    except ImportError:
        if not ops.command_exists("psql"):
            return None
        url = dsn.replace("postgresql+psycopg://", "postgresql://")
        path = os.environ.get("PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")
        result = ops.run(["psql", url, "-tAc", "SELECT 1"], env={"PGPASSWORD": password, "PATH": path})
        return result.returncode == 0 and "1" in result.stdout
    try:
        with psycopg.connect(dsn.replace("postgresql+psycopg://", "postgresql://"), connect_timeout=5):
            return True
    except Exception:
        return False


def prepare_database(
    ops: Ops,
    *,
    database: str,
    role: str,
    password: str,
    dry_run: bool,
    fix: bool,
    reset_password: bool = False,
    role_probe=None,
    dsn: str | None = None,
) -> tuple[list[str], str]:
    """Create an empty database + role; returns (actions, dsn).

    ``dsn`` carries an existing binding's URL verbatim (an existing site must
    keep the exact connection string the site already runs with).  An existing
    role is never silently re-passworded: the binding in hand must work, so
    either it already matches (nothing to do), the operator explicitly allowed
    a reset (``reset_password``), or init fails closed.
    """
    dsn = dsn or f"postgresql+psycopg://{role}:{password}@127.0.0.1:5432/{database}"
    commands = [
        f"CREATE ROLE {role} LOGIN PASSWORD '<generated>'",
        f"CREATE DATABASE {database} OWNER {role}",
    ]
    if dry_run or not fix:
        return [f"would run: {command}" for command in commands], dsn
    actions: list[str] = []
    check = ops.run(
        ["sudo", "-u", "postgres", "psql", "-tAc", f"SELECT 1 FROM pg_roles WHERE rolname='{role}'"],
    )
    if check.returncode != 0:
        raise BootstrapError("bootstrap_database", "cannot reach postgres as superuser")
    if "1" not in check.stdout:
        created = ops.run([
            "sudo", "-u", "postgres", "psql", "-c",
            f"CREATE ROLE {role} LOGIN PASSWORD '{password}'",
        ])
        if created.returncode != 0:
            raise BootstrapError("bootstrap_database", f"CREATE ROLE {role}")
        actions.append(f"created role: {role}")
    elif reset_password:
        altered = ops.run([
            "sudo", "-u", "postgres", "psql", "-c",
            f"ALTER ROLE {role} LOGIN PASSWORD '{password}'",
        ])
        if altered.returncode != 0:
            raise BootstrapError("bootstrap_database", f"ALTER ROLE {role}")
        actions.append(f"reset password for existing role: {role}")
    else:
        actions.append(f"role already exists: {role}")
        probe = role_probe or _role_password_works
        works = probe(ops, dsn, password)
        if works is False:
            raise BootstrapError("bootstrap_database_role", f"{role} already exists with a different password")
        if works is None:
            actions.append(
                "cannot verify the existing role password (no psycopg/psql);"
                " re-run with --reset-db-password if S3 reports db_unreachable"
            )
    exists = ops.run(
        ["sudo", "-u", "postgres", "psql", "-tAc", f"SELECT 1 FROM pg_database WHERE datname='{database}'"],
    )
    if "1" in exists.stdout:
        actions.append(f"database already exists: {database}")
    else:
        created = ops.run(["sudo", "-u", "postgres", "createdb", "-O", role, database])
        if created.returncode != 0:
            raise BootstrapError("bootstrap_database", f"createdb {database}")
        actions.append(f"created empty database: {database}")
    return actions, dsn


def prepare_storage(
    ops: Ops,
    *,
    disk: str,
    mount_path: str,
    subdir: str,
    dry_run: bool,
    fix: bool,
) -> tuple[list[str], str]:
    """Mount the data disk at ``HOST_MOUNT`` and bind the site subtree to ``mount_path``."""
    host_mount = HOST_MOUNT
    steps = [
        f"mount {disk} {host_mount}",
        f"mkdir -p {host_mount}/{subdir}",
        f"mount --bind {host_mount}/{subdir} {mount_path}",
        f"append fstab entries for {disk} (UUID, nofail) and the bind mount",
    ]
    if dry_run or not fix:
        return [f"would run: {step}" for step in steps], mount_path
    actions: list[str] = []
    # 建目录走 ops.ensure_plain_dir：绝不递归 chown——/srv/hdd 在重跑时已是挂载点，
    # 递归改属主会静默改写整盘既有数据的属主（城市 B 演练前发现）。
    ops.ensure_plain_dir(Path(host_mount))
    if not _mounted(host_mount):
        mount = ops.run(["mount", disk, host_mount])
        if mount.returncode != 0:
            raise BootstrapError("bootstrap_storage", f"mount {disk} {host_mount}")
        actions.append(f"mounted {disk} at {host_mount}")
    subtree = Path(host_mount) / subdir
    ops.ensure_plain_dir(subtree)
    ops.ensure_plain_dir(Path(mount_path))
    if not _mounted(mount_path):
        bind = ops.run(["mount", "--bind", str(subtree), mount_path])
        if bind.returncode != 0:
            raise BootstrapError("bootstrap_storage", f"mount --bind {subtree} {mount_path}")
        actions.append(f"bound {subtree} to {mount_path}")
    actions.extend(_fstab_entries(ops, disk=disk, host_mount=host_mount, subtree=subtree, mount_path=mount_path))
    return actions, mount_path


def _mounted(target: str) -> bool:
    try:
        lines = Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    target_path = Path(target)
    for line in lines:
        fields = line.split()
        if len(fields) > 4 and fields[4] == str(target_path):
            return True
    return False


def _fstab_mountpoints(text: str) -> set[str]:
    """fstab 文本里已声明的挂载点（第 2 字段；忽略注释与空行）。

    #2273：此前用「整文件子串」判重——`/srv/hdd` 会命中注释、也会命中 `/srv/hdd2`
    这类更长的路径；按字段判才问的是事实。
    """
    points: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        fields = stripped.split()
        if len(fields) >= 2:
            points.add(fields[1])
    return points


def _fstab_entries(ops: Ops, *, disk: str, host_mount: str, subtree: Path, mount_path: str) -> list[str]:
    fstab = FSTAB
    try:
        text = fstab.read_text(encoding="utf-8")
    except OSError:
        return [f"fstab not updated: {fstab} unreadable"]
    mounted = _fstab_mountpoints(text)
    if mount_path in mounted and host_mount in mounted:
        return [f"fstab already lists {host_mount} and {mount_path}"]
    uuid = ops.run(["blkid", "-s", "UUID", "-o", "value", disk]).stdout.strip()
    # #2273：文件系统类型此前硬编码 ext4——XFS/Btrfs 盘会写出无法开机的 fstab 条目
    # （nofail 只保不挂起开机，不会让错误的类型生效）。类型同样来自 blkid。
    fstype = ops.run(["blkid", "-s", "TYPE", "-o", "value", disk]).stdout.strip()
    lines = []
    if uuid and host_mount not in mounted:
        lines.append(f"UUID={uuid} {host_mount} {fstype or 'auto'} defaults,nofail 0 2")
    if mount_path not in mounted:
        lines.append(f"{subtree} {mount_path} none bind,nofail 0 0")
    if not lines:
        return []
    try:
        with fstab.open("a", encoding="utf-8") as handle:
            handle.write("\n" + "\n".join(lines) + "\n")
    except OSError:
        return [f"fstab not updated; add manually: {'; '.join(lines)}"]
    return [f"fstab updated: {len(lines)} entr{'y' if len(lines) == 1 else 'ies'}"]


def init_site(
    *,
    output: str | Path,
    bindings_dir: str | Path,
    reset_db_password: bool = False,
    site_id: str | None = None,
    display_name: str | None = None,
    public_url: str | None = None,
    database: str | None = None,
    redis_index: int = 1,
    storage_mount: str | None = None,
    data_disk: str | None = None,
    bundle: str | None = None,
    bundle_url: str = "/srv/stp-bundle",
    admin_username: str = "admin",
    ops: Ops | None = None,
    interactive: bool | None = None,
    fix: bool = True,
    dry_run: bool = False,
    answers: dict[str, str] | None = None,
    ask: Callable[..., tuple[str, str]] = _ask,
) -> dict:
    ops = ops or LocalOps()
    answers = dict(answers or {})
    if interactive is None:
        interactive = bool(os.isatty(0)) and not answers
    # 探测一律走同一个 ops：本机与替身走同一条代码路径（否则替身只能测到一半）
    facts = preflight_facts(ops)
    # 时区是**声明**：读不到就停在这里（在写任何东西之前），绝不落到某个默认值上——
    # 238 现场就是被静默默认坑过（声明 UTC / 主机 PDT，差 15 小时，#2265）。
    if not facts["timezone"]:
        check = failure(
            "host_timezone_unknown", location="$.site.timezone", role="site",
            check_id="init.host_timezone",
        )
        return {
            "stage": "init",
            "status": "FAIL",
            "summary": "Host timezone could not be probed; nothing was written.",
            "checks": [check.__dict__],
            "deferred_checks": [],
            "output": str(output),
            "bindings_dir": str(bindings_dir),
            "actions": [
                "set the host timezone first (e.g. `sudo timedatectl set-timezone Asia/Shanghai`) "
                "and re-run init; the declared site timezone must be the host's real one",
            ],
        }
    provenance: dict[str, str] = {}

    def decide(prompt: str, default: str, key: str) -> str:
        value, source = ask(prompt, default, interactive=interactive, answers=answers, key=key)
        provenance[key] = source
        return value

    site_id = site_id or decide("站点标识 (site.id)", "city-b", "site_id")
    display_name = display_name or decide("站点显示名", f"站点 {site_id}", "display_name")
    public_url = (public_url or decide(
        "平台入口 (public_url)", f"http://{facts['address']}" if facts["address"] else "http://127.0.0.1",
        "public_url",
    )).rstrip("/")
    database = database or decide("数据库名", f"{DEFAULT_DB_ROLE}_{site_id.replace('-', '_')}", "database")
    storage_choice = storage_mount or decide("中心存储挂载路径", DEFAULT_STORAGE_DIR, "storage_mount")
    disk = data_disk
    disk_note = "declared path only"
    if disk is None:
        disk, disk_gib = probe_data_disk(ops)
        disk_note = f"detected data disk {disk} ({disk_gib} GiB)" if disk else "no extra data disk detected"

    role = DEFAULT_DB_ROLE
    # 既有绑定就是站点的现状：重跑必须沿用它，否则站点（.env.backend 里已是首次
    # 渲染的值）与绑定会静默分叉。
    existing = {name: _read_binding(Path(bindings_dir), name) for name in BINDING_NAMES}
    existing_dsn = existing["site_database"].get("DATABASE_URL", "")
    admin_username = existing["site_admin"].get("USERNAME") or admin_username
    db_password = _dsn_password(existing_dsn) or secrets.token_urlsafe(24)
    admin_password = existing["site_admin"].get("PASSWORD") or secrets.token_urlsafe(ADMIN_PASSWORD_BYTES)
    fernet_key = existing["site_ssh_encryption"].get("SSH_CREDENTIALS_FERNET_KEY") or _fernet_key()
    redis_index = _redis_index(existing["site_redis"].get("REDIS_URL", "")) or redis_index
    actions: list[str] = []
    if fix and not dry_run:
        actions.extend(ensure_tool_venv(ops))
    db_actions, dsn = prepare_database(
        ops, database=database, role=role, password=db_password, dry_run=dry_run, fix=fix,
        reset_password=reset_db_password, dsn=existing_dsn or None,
    )
    actions.extend(db_actions)
    storage_actions: list[str] = []
    mount_path = storage_choice
    if disk:
        storage_actions, mount_path = prepare_storage(
            ops, disk=disk, mount_path=storage_choice,
            subdir=f"{site_id}/aee_events", dry_run=dry_run, fix=fix,
        )
    else:
        storage_actions = [
            f"no separate data disk: {mount_path} must already be a mount point (S1 verifies it;"
            " pass --data-disk <device> to bind one instead)"
        ]
    actions.extend(storage_actions)

    provenance["timezone"] = "host probe: /etc/timezone → timedatectl"
    config = {
        "schema_version": 1,
        "site": {"id": site_id, "display_name": display_name, "timezone": facts["timezone"]},
        "platform": {
            "os_family": "linux", "cpu_arch": facts["machine"], "service_manager": "systemd",
        },
        "network": {"dependency_mode": "controlled_mirror"},
        "control_plane": {
            "target": facts["hostname"],
            "os": {"distribution": facts["distribution"] or "debian", "version": facts["version"] or "13"},
            "ssh_user": None,
            "ssh_credential_ref": None,
            "deploy_root": f"{DEFAULT_DEPLOY_ROOT_PREFIX}{site_id}",
            "deploy_user": role,
            "public_url": public_url,
            "security_profile": "internal" if public_url.startswith("http://") else "production",
            "tls_ref": None,
        },
        "storage": {
            # 本机磁盘子树（bind 到 mount_path）：不是 NFS/CIFS 分享，不写 target/share
            "provisioning": "local_mount",
            "protocol": None,
            "target": None,
            "os": None,
            "ssh_user": None,
            "ssh_credential_ref": None,
            "share": None,
            "credential_ref": None,
            "mount_path": mount_path,
            # 站点自建的子树默认导出给本站 Agent：STP_AEE_NFS_ROOT 才有实际落点
            "export_to_agents": True,
        },
        "agents": [],
        "dependencies": {
            "database_ref": "site_database", "redis_ref": "site_redis", "tools_profile": "site_tools",
        },
        "security": {
            "jwt_key_ref": "site_jwt",
            "agent_secret_ref": "site_agent_secret",
            "ssh_encryption_key_ref": "site_ssh_encryption",
            "initial_admin_ref": "site_admin",
        },
        "release": {
            "bundle": bundle or bundle_url,
            "manifest": f"{bundle or bundle_url}/release-manifest.json",
            "expected_release": _release_version(bundle or bundle_url),
        },
        "navigation": {
            "contact": decide("站点负责人 (contact)", f"{role}-ops", "contact"),
            "documentation_url": decide("运维文档 URL", "https://docs.example.invalid/site-ops", "documentation_url"),
        },
        "monitoring": {
            # 站点本地监控栈：/storage 页的数据源（本地 Prometheus + node-exporter）。
            # 默认装——装了存储却没装监控栈的站点，页面永远是空的。
            "enabled": True,
            "prometheus_port": 9091,
        },
    }
    header = _header(provenance, facts, disk_note)
    if not dry_run:
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        Path(output).write_text(
            header + yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8",
        )
        written, reused, drifted = _write_bindings(
            bindings_dir, dsn=dsn, redis_index=redis_index, admin=(admin_username, admin_password),
            fernet_key=fernet_key,
        )
        if reused:
            # 重跑不轮换：既有绑定（站点口令/Fernet/DSN）必须原样保留
            actions.append(f"kept existing bindings (not rotated): {', '.join(reused)}")
        if drifted:
            actions.append(
                f"note: kept bindings differ from this run's values (not overwritten): {', '.join(drifted)}"
            )
    checks = [
        passed(
            "init.site", "site", "$.site.id", "site_inputs_ready",
            f"site.yaml written for {site_id}; only four answers were required.",
            "Keep the bindings directory owner-only (0700) with 0600 files.",
        ),
    ]
    return {
        "stage": "init",
        "status": "PASS",
        "summary": "Site inputs generated from host probes; secrets generated, not printed.",
        "checks": [check.__dict__ for check in checks],
        "deferred_checks": [],
        "output": str(output),
        "bindings_dir": str(bindings_dir),
        "actions": actions,
        "provenance": provenance,
        # 回显探测结果：时区是「声明 = 控制面 = Agent」三面同源的第一面，创建时就让人看见
        "timezone": facts["timezone"],
        "config": config,
        "admin_credentials": {
            "username": admin_username,
            "password_file": str(Path(bindings_dir) / "site_admin"),
        },
        "dry_run": dry_run,
    }


BINDING_NAMES = ("site_database", "site_redis", "site_admin", "site_ssh_encryption")


def _read_binding(directory: Path, name: str) -> dict[str, str]:
    """Parse one KEY=VALUE binding file; {} when it does not exist yet."""
    path = directory / name
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    values: dict[str, str] = {}
    for line in text.splitlines():
        key, separator, value = line.partition("=")
        if separator:
            values[key.strip()] = value.strip()
    return values


def _dsn_password(dsn: str) -> str:
    """Password component of an existing DSN; "" when unusable."""
    from urllib.parse import urlsplit

    try:
        return urlsplit(dsn).password or ""
    except ValueError:
        return ""


def _redis_index(url: str) -> int | None:
    """db index from an existing REDIS_URL; None when it carries none."""
    tail = url.rstrip("/").rsplit("/", 1)[-1]
    return int(tail) if tail.isdigit() else None


def _fernet_key() -> str:
    import base64

    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()


def _release_version(bundle_path: str) -> str:
    manifest = Path(bundle_path) / "release-manifest.json"
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        version = data["product"]["version"]
        if isinstance(version, str) and version:
            return version
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return f"local-{dt.datetime.now(dt.timezone.utc):%Y%m%d}"


def _write_bindings(
    bindings_dir: str | Path,
    *,
    dsn: str,
    redis_index: int,
    admin: tuple[str, str],
    fernet_key: str,
) -> tuple[list[str], list[str], list[str]]:
    """Write the binding files once; an existing file is never overwritten.

    Rotation would silently desync the site from its own bindings: S2 already
    rendered the first secrets into `.env.backend`, and the initial
    administrator was already created with the first password.
    """
    directory = Path(bindings_dir)
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    payloads = {
        "site_database": f"DATABASE_URL={dsn}\n",
        "site_redis": f"REDIS_URL=redis://127.0.0.1:6379/{redis_index}\n",
        "site_admin": f"USERNAME={admin[0]}\nPASSWORD={admin[1]}\n",
        "site_ssh_encryption": f"SSH_CREDENTIALS_FERNET_KEY={fernet_key}\n",
    }
    written: list[str] = []
    reused: list[str] = []
    drifted: list[str] = []
    for name, text in payloads.items():
        path = directory / name
        if path.exists():
            os.chmod(path, 0o600)
            reused.append(name)
            try:
                if path.read_text(encoding="utf-8") != text:
                    drifted.append(name)
            except OSError:
                drifted.append(name)
            continue
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(path, 0o600)
        written.append(name)
    return written, reused, drifted


def _header(provenance: dict[str, str], facts: dict, disk_note: str) -> str:
    lines = [
        "# 由 `python -m tools.site_config init` 生成（I5.5）。",
        f"# 探测：{facts['hostname']} / {facts['distribution']} {facts['version']} / {facts['machine']}"
        f" / tz={facts['timezone']} / addr={facts['address'] or '-'}",
        f"# 存储：{disk_note}",
        "# 取值来源：",
    ]
    for key, source in sorted(provenance.items()):
        lines.append(f"#   {key}: {source}")
    lines.append("# 秘密与首管理员口令在绑定目录（0700/0600），不入本文件。")
    return "\n".join(lines) + "\n\n"
