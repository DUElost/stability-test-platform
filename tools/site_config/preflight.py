"""Read-only host preflight (I5.5): every item reports PASS/FAIL/BLOCKED plus a Fix.

Runs before any write so an operator sees "what is missing and how to fix it"
instead of failing halfway through the install.  It never modifies the host.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from .checks import Check, blocked, failure, passed
from .ops import LocalOps, Ops

REQUIRED_COMMANDS = ("python3", "systemctl", "nginx")
AGENT_PATH_COMMANDS = ("ansible-playbook", "sshpass", "ssh-keyscan")
ENTRY_PORTS = (80, 8000)
MIN_CORES = 2
MIN_MEMORY_MIB = 3800
MIN_DISK_GIB = 20
TOOL_MODULES = ("pydantic", "yaml", "psycopg")


def _fail(check_id: str, role: str, location: str, code: str, message: str) -> Check:
    """A FAIL that carries the observed fact, not just the generic sentence."""
    return replace(failure(code, location=location, role=role, check_id=check_id), message=message)


def _listening_ports() -> set[int]:
    ports: set[int] = set()
    for table in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            lines = Path(table).read_text(encoding="utf-8").splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            fields = line.split()
            if len(fields) < 4 or fields[3] != "0A":  # 0A = LISTEN
                continue
            try:
                ports.add(int(fields[1].rsplit(":", 1)[1], 16))
            except (IndexError, ValueError):
                continue
    return ports


def _memory_mib() -> int:
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) // 1024
    except (OSError, IndexError, ValueError):
        pass
    return 0


def _primary_address(ops: Ops) -> str:
    """The address this host reaches the network with — the useful default entry hint.

    Route lookup first: with bridges and container subnets present, an
    alphabetically-first interface address (172.17.x, docker0) would be a
    misleading suggestion.  The interface list is only the fallback.
    """
    address = ops.route_address()
    if address:
        return address
    addresses = sorted(a for a in ops.local_addresses() if ":" not in a and not a.startswith("127."))
    return addresses[0] if addresses else ""


def run_preflight(
    *,
    bindings_dir: str | Path | None = None,
    db_url: str | None = None,
    redis_url: str | None = None,
    bundle: str | Path | None = None,
    deploy_root: str | Path | None = None,
    ops: Ops | None = None,
    probe=None,
) -> dict:
    """Collect host readiness facts; no configuration file is required."""
    ops = ops or LocalOps()
    checks: list[Check] = []
    checks.append(_platform_check(ops))
    checks.append(_resources_check(ops))
    checks.extend(_command_checks(ops))
    checks.append(_ports_check(ops))
    checks.append(_time_check(ops))
    checks.append(_tool_env_check())
    if db_url:
        if probe is None:
            # 延迟导入 + 缺依赖时降级成一条 FAIL：preflight 必须在还没有
            # pydantic/yaml/psycopg 的机器上也能给出逐项报告。
            try:
                from .install import probe_database
            except ImportError:
                checks.append(_fail(
                    "preflight.database", "control_plane", "$.dependencies.database_ref", "preflight_toolenv",
                    "The installer environment is missing, so the declared database could not be probed.",
                ))
            else:
                checks.append(_database_check(db_url, probe_database))
        else:
            checks.append(_database_check(db_url, probe))
    else:
        checks.append(blocked(
            "preflight.database", "control_plane", "$.dependencies.database_ref", "input_required",
            "No database DSN was supplied to preflight.",
            "Pass --db-url postgresql+psycopg://user:pass@host:5432/db (empty database).",
        ))
    checks.append(_redis_check(ops, redis_url))
    if bindings_dir is not None:
        checks.append(_bindings_check(Path(bindings_dir)))
    if bundle is not None:
        checks.append(_bundle_check(Path(bundle)))
    if deploy_root is not None:
        checks.append(_existing_site_check(Path(deploy_root)))
    return {
        "stage": "preflight",
        "status": "FAIL" if any(check.status == "FAIL" for check in checks) else "PASS",
        "summary": "Read-only host readiness; nothing was written. Align each FAIL with its Fix line.",
        "checks": [asdict(check) for check in checks],
        "deferred_checks": [],
        "suggested_public_url": f"http://{_primary_address(ops)}" if _primary_address(ops) else "",
    }


def _platform_check(ops: Ops) -> Check:
    release = ops.os_release()
    distribution = release.get("ID", "")
    machine = ops.machine()
    if distribution not in {"debian", "ubuntu"}:
        return _fail(
            "preflight.platform", "site", "$.platform", "install_platform",
            f"Unsupported host: {distribution or 'unknown'}"
            f" {release.get('VERSION_ID', '')} on {machine or 'unknown'}.",
        )
    return passed(
        "preflight.platform", "site", "$.platform", "platform_ok",
        f"Host runs {distribution} {release.get('VERSION_ID', '')} on {machine}.",
        "Keep the host on a supported distribution and architecture.",
    )


def _resources_check(ops: Ops) -> Check:
    cores = os.cpu_count() or 0
    memory = _memory_mib()
    try:
        free_gib = shutil.disk_usage("/").free // (1024 ** 3)
    except OSError:
        free_gib = 0
    if cores < MIN_CORES or memory < MIN_MEMORY_MIB or free_gib < MIN_DISK_GIB:
        return _fail(
            "preflight.resources", "site", "$.platform", "preflight_resources",
            f"Host has {cores} cores / {memory} MiB RAM / {free_gib} GiB free on /;"
            f" the floor is {MIN_CORES} cores / {MIN_MEMORY_MIB} MiB / {MIN_DISK_GIB} GiB.",
        )
    return passed(
        "preflight.resources", "site", "$.platform", "resources_ok",
        f"{cores} cores / {memory} MiB RAM / {free_gib} GiB free on the root filesystem.",
        "Agent fleets scale with devices; grow CPU/RAM before adding many hosts.",
    )


def _command_checks(ops: Ops) -> list[Check]:
    checks: list[Check] = []
    missing = [name for name in REQUIRED_COMMANDS if not ops.command_exists(name)]
    if missing:
        checks.append(_fail(
            "preflight.dependencies", "site", "$.platform", "install_dependency",
            f"Missing required commands: {', '.join(missing)}.",
        ))
    else:
        checks.append(passed(
            "preflight.dependencies", "site", "$.platform", "dependencies_present",
            f"Required commands present: {', '.join(REQUIRED_COMMANDS)}.",
            "Install missing base packages with the site profile before re-running.",
        ))
    agent_missing = [name for name in AGENT_PATH_COMMANDS if not ops.command_exists(name)]
    if agent_missing:
        checks.append(blocked(
            "preflight.agent_commands", "control_plane", "$.agents", "agent_path_commands_missing",
            f"Agent onboarding needs: {', '.join(agent_missing)}.",
            "Fix: apt install -y ansible-core sshpass (needed by S5 / deploy/agent/install.sh).",
        ))
    else:
        checks.append(passed(
            "preflight.agent_commands", "control_plane", "$.agents", "agent_commands_present",
            "Ansible and sshpass are available for Agent onboarding.",
            "Keep the control plane able to reach Agent hosts over SSH.",
        ))
    return checks


def _ports_check(ops: Ops) -> Check:
    busy = sorted(_listening_ports() & set(ENTRY_PORTS))
    if busy:
        return _fail(
            "preflight.ports", "control_plane", "$.control_plane.public_url", "preflight_ports",
            f"Entry ports already in use: {', '.join(str(port) for port in busy)}.",
        )
    return passed(
        "preflight.ports", "control_plane", "$.control_plane.public_url", "ports_free",
        f"Entry ports are free: {', '.join(str(port) for port in ENTRY_PORTS)}.",
        "Fix: stop the process holding the port (ss -ltnp) before installing.",
    )


def _time_check(ops: Ops) -> Check:
    result = ops.run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"])
    synchronized = "yes" in result.stdout.strip().lower()
    # 时区一并回显：NTP 同步只说明「时钟准」，不说明「时区对」——声明与主机不一致要在
    # S1 之前就看得见（#2265：238 现场声明 UTC / 主机 PDT / Agent CST，差 15 小时）。
    timezone = ops.timezone() or "unknown"
    if not synchronized:
        return _fail(
            "preflight.time", "site", "$.site.timezone", "preflight_time",
            f"Host clock is not NTP-synchronized (timedatectl: {result.stdout.strip() or 'no answer'}; "
            f"timezone: {timezone}).",
        )
    return passed(
        "preflight.time", "site", "$.site.timezone", "time_synchronized",
        f"Host clock is NTP-synchronized (timezone: {timezone}).",
        "Fix: systemctl enable --now systemd-timesyncd; make the host timezone match site.timezone.",
    )


def _tool_env_check() -> Check:
    missing: list[str] = []
    for module in TOOL_MODULES:
        try:
            __import__(module)
        except ImportError:
            missing.append(module)
    if missing:
        return _fail(
            "preflight.toolenv", "site", "$.dependencies", "preflight_toolenv",
            f"The installer environment is missing: {', '.join(missing)}.",
        )
    return passed(
        "preflight.toolenv", "site", "$.dependencies", "toolenv_ready",
        "The installer's own environment carries pydantic, PyYAML and psycopg.",
        "Fix: deploy/install.sh creates the tool venv automatically when it is missing.",
    )


def _database_check(db_url: str, probe) -> Check:
    state, version = probe(db_url)
    if state == "empty":
        return passed(
            "preflight.database", "control_plane", "$.dependencies.database_ref", "database_empty",
            "The declared database is reachable and empty.",
            "Keep the database dedicated to this site; never share business tables.",
        )
    if state == "driver_missing":
        return _fail(
            "preflight.database", "control_plane", "$.dependencies.database_ref", "db_driver",
            "No PostgreSQL driver is importable in the installer environment.",
        )
    if state == "managed":
        return passed(
            "preflight.database", "control_plane", "$.dependencies.database_ref", "database_managed",
            f"The database already carries this platform's schema ({version or 'unknown revision'}).",
            "Re-runs reuse the schema; never delete it to force a fresh install.",
        )
    if state == "unreachable":
        return _fail(
            "preflight.database", "control_plane", "$.dependencies.database_ref", "db_unreachable",
            "The declared database rejected the connection; check host, port, role and password.",
        )
    return _fail(
        "preflight.database", "control_plane", "$.dependencies.database_ref", "db_unmanaged",
        "The declared database carries foreign tables and is not a dedicated, empty site database.",
    )


def _redis_check(ops: Ops, redis_url: str | None) -> Check:
    if not redis_url:
        return blocked(
            "preflight.redis", "control_plane", "$.dependencies.redis_ref", "input_required",
            "No Redis URL was supplied to preflight.",
            "Pass --redis-url redis://127.0.0.1:6379/1 (dedicated db index).",
        )
    executable = shutil.which("redis-cli")
    if not executable:
        return _fail(
            "preflight.redis", "control_plane", "$.dependencies.redis_ref", "preflight_redis",
            "redis-cli is not installed, so the declared Redis could not be probed.",
        )
    database = 0
    tail = redis_url.rstrip("/").rsplit("/", 1)[-1]
    if tail.isdigit():
        database = int(tail)
    result = ops.run([executable, "-n", str(database), "ping"])
    if "PONG" in result.stdout:
        return passed(
            "preflight.redis", "control_plane", "$.dependencies.redis_ref", "redis_ready",
            f"Redis answered PONG on db {database}.",
            "Use a dedicated db index per site on a shared Redis instance.",
        )
    return _fail(
        "preflight.redis", "control_plane", "$.dependencies.redis_ref", "preflight_redis",
        f"Redis did not answer PONG on db {database} (redis-cli output: {result.stdout.strip() or 'none'}).",
    )


def _bindings_check(directory: Path) -> Check:
    try:
        info = directory.lstat()
    except OSError:
        return _fail(
            "preflight.bindings", "site", "$.security", "binding_dir",
            f"Bindings directory does not exist: {directory}.",
        )
    if not info.st_mode & 0o040000:
        return _fail(
            "preflight.bindings", "site", "$.security", "binding_dir",
            f"Bindings path is not a directory: {directory}.",
        )
    if info.st_mode & 0o077:
        return _fail(
            "preflight.bindings", "site", "$.security", "binding_dir",
            f"Bindings directory is readable beyond its owner ({oct(info.st_mode & 0o777)}): {directory}.",
        )
    return passed(
        "preflight.bindings", "site", "$.security", "bindings_dir_ready",
        "The bindings directory exists with owner-only permissions.",
        "deploy/install.sh generates the bindings it can derive automatically.",
    )


BUNDLE_REQUIRED = (
    "release-manifest.json", "backend", "backend/agent", "backend/agent/resources",
    "backend/schemas", "frontend/dist-prod", "deploy", "tools",
)


def _bundle_check(bundle: Path) -> Check:
    required = BUNDLE_REQUIRED
    missing = [name for name in required if not (bundle / name).exists()]
    if missing:
        return _fail(
            "preflight.bundle", "site", "$.release.bundle", "release_tree",
            f"The release bundle at {bundle} is missing: {', '.join(missing)}.",
        )
    return passed(
        "preflight.bundle", "site", "$.release.bundle", "bundle_ready",
        "The release bundle carries the documented layout.",
        "Build it with tools/release/build_bundle.py when it is missing.",
    )


def _existing_site_check(deploy_root: Path) -> Check:
    marker = deploy_root / ".stp-site.json"
    if marker.is_file():
        return passed(
            "preflight.existing_site", "control_plane", "$.control_plane.deploy_root", "site_marker_present",
            "An existing site marker was found; the install will resume it instead of overwriting.",
            "Confirm the marker's site id matches the configuration you are installing.",
        )
    return passed(
        "preflight.existing_site", "control_plane", "$.control_plane.deploy_root", "deploy_root_empty",
        "No existing site marker was found at the declared deploy root.",
        "Never delete a deploy root to force a fresh install; move it aside instead.",
    )


def preflight_facts(ops: Ops | None = None) -> dict[str, Any]:
    """Small helper for callers that only need the probed defaults."""
    ops = ops or LocalOps()
    release = ops.os_release()
    return {
        "hostname": ops.hostname(),
        "distribution": release.get("ID", ""),
        "version": release.get("VERSION_ID", ""),
        "machine": ops.machine(),
        "address": _primary_address(ops),
        "timezone": Path("/etc/timezone").read_text(encoding="utf-8").strip()
        if Path("/etc/timezone").is_file() else "UTC",
    }
