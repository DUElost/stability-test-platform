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
from .validation import passed

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


def prepare_database(
    ops: Ops,
    *,
    database: str,
    role: str,
    password: str,
    dry_run: bool,
    fix: bool,
    admin: str = "sudo -u postgres psql",
) -> tuple[list[str], str]:
    """Create an empty database + role; returns (actions, dsn)."""
    dsn = f"postgresql+psycopg://{role}:{password}@127.0.0.1:5432/{database}"
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
    else:
        actions.append(f"role already exists: {role}")
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


def _fstab_entries(ops: Ops, *, disk: str, host_mount: str, subtree: Path, mount_path: str) -> list[str]:
    fstab = FSTAB
    try:
        text = fstab.read_text(encoding="utf-8")
    except OSError:
        return [f"fstab not updated: {fstab} unreadable"]
    if mount_path in text and host_mount in text:
        return [f"fstab already lists {host_mount} and {mount_path}"]
    uuid = ops.run(["blkid", "-s", "UUID", "-o", "value", disk]).stdout.strip()
    lines = []
    if uuid and host_mount not in text:
        lines.append(f"UUID={uuid} {host_mount} ext4 defaults,nofail 0 2")
    if str(subtree) not in text:
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
    db_password = secrets.token_urlsafe(24)
    admin_password = secrets.token_urlsafe(ADMIN_PASSWORD_BYTES)
    fernet_key = _fernet_key()
    actions: list[str] = []
    if fix and not dry_run:
        actions.extend(ensure_tool_venv(ops))
    db_actions, dsn = prepare_database(
        ops, database=database, role=role, password=db_password, dry_run=dry_run, fix=fix,
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
    }
    header = _header(provenance, facts, disk_note)
    if not dry_run:
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        Path(output).write_text(
            header + yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8",
        )
        _write_bindings(
            bindings_dir, dsn=dsn, redis_index=redis_index, admin=(admin_username, admin_password),
            fernet_key=fernet_key,
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
        "config": config,
        "admin_credentials": {
            "username": admin_username,
            "password_file": str(Path(bindings_dir) / "site_admin"),
        },
        "dry_run": dry_run,
    }


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
) -> None:
    directory = Path(bindings_dir)
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    payloads = {
        "site_database": f"DATABASE_URL={dsn}\n",
        "site_redis": f"REDIS_URL=redis://127.0.0.1:6379/{redis_index}\n",
        "site_admin": f"USERNAME={admin[0]}\nPASSWORD={admin[1]}\n",
        "site_ssh_encryption": f"SSH_CREDENTIALS_FERNET_KEY={fernet_key}\n",
    }
    for name, text in payloads.items():
        path = directory / name
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(path, 0o600)


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
