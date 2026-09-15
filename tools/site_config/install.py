"""Local site installer (I3): S0 confirmation, S1–S4 orchestration, state, report.

The installer runs on the declared control-plane target, never inherits
installer-local defaults, never formats storage, never creates databases and
never elevates existing accounts.  Re-runs re-verify actual state instead of
trusting the recorded progress.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import stat
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Callable

from .agents import stage_s5_agents
from .manifest import load_release_manifest
from .ops import LocalOps, Ops
from .stages import (
    InstallContext,
    load_bindings,
    stage_s1_basics,
    stage_s2_release_env,
    stage_s3_database_admin,
    stage_s4_entry,
)
from .validation import DEFERRED_CHECKS, Check, ConfigValidationError, failure, load_site_config

STATE_FILE = "install-state.json"
LOCK_FILE = "install.lock"

_DIGEST_SCRIPT = """
import json, os
from backend.agent.artifact_digest import collect_artifact_entries, digest_entries

bundle = os.environ["STP_INSTALL_BUNDLE"]
agent_dir = os.path.join(bundle, "backend", "agent")
extra = {"stp_schemas/pipeline_schema.json": os.path.join(bundle, "backend", "schemas", "pipeline_schema.json")}
print(json.dumps({
    "agent-code": digest_entries(collect_artifact_entries(agent_dir, extra, kind="code")),
    "host-resources": digest_entries(collect_artifact_entries(agent_dir, extra, kind="resources")),
}))
"""

DatabaseProbe = Callable[[str], tuple[str, str | None]]


def _pass(check_id: str, role: str, location: str, code: str, message: str, remediation: str) -> Check:
    return Check(check_id, role, "PASS", location, code, message, remediation)


def _safe(checks: list[Check], code: str, *, location: str, role: str, check_id: str) -> list[Check]:
    checks.append(failure(code, location=location, role=role, check_id=check_id))
    return checks


def _deferred_checks() -> list[Check]:
    return [check for check in DEFERRED_CHECKS if check.check_id != "release.compatibility"]


def _report(
    checks: list[Check],
    stages: list[dict],
    *,
    state_path: str | None = None,
    agent_stage: bool = False,
) -> dict:
    summary = (
        "Local install stage results only; release origin, remote targets and business acceptance remain unverified."
    )
    if agent_stage:
        summary += (
            " Agent onboarding ran against this site's own API; controlled device and storage"
            " acceptance (S6) remains unverified."
        )
    report = {
        "stage": "install",
        "status": "FAIL" if any(check.status == "FAIL" for check in checks) else "PASS",
        "summary": summary,
        "checks": [asdict(check) for check in checks],
        "deferred_checks": [asdict(check) for check in _deferred_checks()],
        "stages": stages,
    }
    if state_path is not None:
        report["state_file"] = state_path
    return report


def _stage_entry(name: str, checks: list[Check]) -> dict:
    status = "FAIL" if any(check.status == "FAIL" for check in checks) else "PASS"
    return {"stage": name, "status": status, "checks": [check.check_id for check in checks]}


def _state_dir_checks(state_dir: Path) -> Check | None:
    try:
        info = os.lstat(state_dir)
    except OSError:
        return failure("state_dir", role="site", check_id="install.state")
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        return failure("state_dir", role="site", check_id="install.state")
    return None


def _write_state(state_dir: Path, payload: dict) -> str:
    path = state_dir / STATE_FILE
    temporary = state_dir / f".{STATE_FILE}.tmp"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    os.replace(temporary, path)
    os.chmod(path, 0o600)
    return path.name


def _persist_state(ctx: InstallContext, stages: list[dict]) -> str | None:
    if ctx.dry_run:
        return None
    return _write_state(ctx.state_dir, _state_payload(ctx, stages))


def _config_digest(config_path: Path) -> str:
    try:
        return hashlib.sha256(config_path.read_bytes()).hexdigest()
    except OSError:
        return ""


def _digest_bundle(ctx: InstallContext) -> dict[str, str] | None:
    env = {
        "PATH": os.environ.get("PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"),
        "PYTHONPATH": str(ctx.bundle),
        "PYTHONDONTWRITEBYTECODE": "1",
        "STP_INSTALL_BUNDLE": str(ctx.bundle),
    }
    result = ctx.ops.run([sys.executable, "-c", _DIGEST_SCRIPT], env=env)
    if result.returncode != 0 or not result.stdout:
        return None
    try:
        payload = json.loads(result.stdout.splitlines()[-1])
    except (ValueError, IndexError):
        return None
    return payload if isinstance(payload, dict) else None


def _code_head(ctx: InstallContext) -> str | None:
    python = ctx.deploy_root / "venv" / "bin" / "python"
    if not python.is_file():
        return None
    result = ctx.ops.run([str(python), "-m", "alembic", "heads"], cwd=ctx.deploy_root / "backend")
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        token = line.strip().split()
        if token:
            return token[0]
    return None


def probe_database(dsn: str) -> tuple[str, str | None]:
    """Classify the declared database without writing to it."""
    url = dsn.replace("postgresql+asyncpg://", "postgresql://").replace("postgresql+psycopg://", "postgresql://")
    try:
        import psycopg
    except ImportError:
        return "driver_missing", None
    try:
        with psycopg.connect(url, connect_timeout=5, autocommit=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT to_regclass('public.alembic_version')")
                row = cursor.fetchone()
                if row is not None and row[0] is not None:
                    cursor.execute("SELECT version_num FROM public.alembic_version LIMIT 1")
                    version_row = cursor.fetchone()
                    return "managed", (version_row[0] if version_row else None)
                cursor.execute(
                    "SELECT count(*) FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
                )
                count_row = cursor.fetchone()
                count = int(count_row[0]) if count_row else 0
                return ("empty" if count == 0 else "unmanaged"), None
    except Exception:
        return "unreachable", None


def _hostname_evidence(ctx: InstallContext) -> bool:
    target = ctx.config.control_plane.target.lower()
    hostname = ctx.ops.hostname().lower()
    addresses = ctx.ops.local_addresses()
    return hostname == target or target in addresses


def run_install(
    config_path: str | Path,
    *,
    bindings_dir: str | Path,
    state_dir: str | Path,
    confirm_site: str,
    confirm_target: str,
    dry_run: bool = False,
    through_agents: bool = False,
    ops: Ops | None = None,
    db_probe: DatabaseProbe | None = None,
    system_root: Path | None = None,
) -> dict:
    ops = ops or LocalOps()
    probe = db_probe or probe_database
    state_dir = Path(state_dir)
    checks: list[Check] = []

    state_problem = _state_dir_checks(state_dir)
    if state_problem is not None:
        return _report([state_problem], [])
    lock_fd = os.open(state_dir / LOCK_FILE, os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(lock_fd)
        return _report([failure("state_locked", role="site", check_id="install.lock")], [])

    try:
        return _run_locked(
            config_path, bindings_dir, state_dir, confirm_site, confirm_target, dry_run, ops, probe, checks,
            system_root, through_agents,
        )
    finally:
        os.close(lock_fd)


def _run_locked(
    config_path, bindings_dir, state_dir, confirm_site, confirm_target, dry_run, ops, probe, checks, system_root,
    through_agents=False,
) -> dict:
    config_path = Path(config_path)
    try:
        config = load_site_config(config_path)
    except ConfigValidationError as error:
        return _report(list(error.checks), [])

    ctx = InstallContext(
        config=config,
        config_path=config_path,
        bundle=Path(config.release.bundle),
        bindings_dir=Path(bindings_dir),
        state_dir=state_dir,
        ops=ops,
        dry_run=dry_run,
        db_probe=probe,
        system_root=system_root or Path("/"),
    )
    stages: list[dict] = []

    # S0 — input and target confirmation; no writes are allowed before it passes.
    if confirm_site != config.site.id or confirm_target != config.control_plane.target:
        _safe(checks, "install_confirm", location="$.site.id", role="site", check_id="install.s0.confirm")
        return _report(checks, stages)
    if not _hostname_evidence(ctx):
        _safe(checks, "install_hostname", location="$.control_plane.target", role="control_plane", check_id="install.s0.hostname")
        return _report(checks, stages)
    release = ops.os_release()
    machine = ops.machine()
    version = release.get("VERSION_ID", "").split(".")[0]
    if version != config.control_plane.os.version.split(".")[0] or machine != config.platform.cpu_arch:
        _safe(checks, "install_platform", location="$.platform", role="site", check_id="install.s0.platform")
        return _report(checks, stages)
    root = ctx.deploy_root
    marker_file = root / ".stp-site.json"
    if root.exists() and any(root.iterdir()):
        marker = None
        if marker_file.is_file():
            try:
                marker = json.loads(marker_file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                marker = None
        if not isinstance(marker, dict) or marker.get("site_id") != config.site.id:
            _safe(checks, "install_root_taken", location="$.control_plane.deploy_root", role="control_plane", check_id="install.s0.root")
            return _report(checks, stages)

    if config.release.manifest is None:
        _safe(checks, "manifest_missing", location="$.release.manifest", role="site", check_id="install.s0.release")
        return _report(checks, stages)
    try:
        manifest = load_release_manifest(config.release.manifest)
    except ConfigValidationError as error:
        checks.extend(error.checks)
        return _report(checks, stages)
    if manifest.product.version != config.release.expected_release:
        _safe(checks, "release_version_mismatch", location="$.release.expected_release", role="site", check_id="install.s0.release")
        return _report(checks, stages)
    declared = {component.name: component.digest for component in manifest.components}
    actual = _digest_bundle(ctx)
    if (
        actual is None
        or declared.get("agent-code") != actual.get("agent-code")
        or declared.get("host-resources") != actual.get("host-resources")
    ):
        _safe(checks, "release_digest", location="$.release.bundle", role="site", check_id="install.s0.digest")
        return _report(checks, stages)
    checks.append(_pass(
        "install.s0.digest", "site", "$.release.bundle", "digest_matched",
        "Bundle content matches the declared agent-code and host-resources digests.",
        "Digests prove integrity only; release origin still requires the pipeline attestation.",
    ))
    bindings_checks = load_bindings(ctx)
    checks.extend(bindings_checks)
    if any(check.status == "FAIL" for check in bindings_checks):
        return _report(checks, stages)
    checks.append(_pass(
        "install.s0", "site", "$", "target_confirmed",
        "Explicit confirmations, local target evidence and the declared manifest were verified before any write.",
        "Never bypass confirmation; never run against an unverified target.",
    ))
    stages.append({"stage": "S0", "status": "PASS", "checks": ["install.s0.digest", "install.bindings"]})

    for name, stage in (("S1", stage_s1_basics), ("S2", stage_s2_release_env)):
        result = stage(ctx)
        checks.extend(result)
        stages.append(_stage_entry(name, result))
        if any(check.status == "FAIL" for check in result):
            _persist_state(ctx, stages)
            return _report(checks, stages)

    code_head = _code_head(ctx)
    if code_head is None and not dry_run:
        _safe(checks, "install_command", location="$.dependencies", role="control_plane", check_id="install.s3.head")
        _persist_state(ctx, stages)
        return _report(checks, stages)
    if code_head and code_head != manifest.database.schema_target:
        _safe(checks, "release_schema", location="$.release.manifest", role="site", check_id="install.s3.schema")
        _persist_state(ctx, stages)
        return _report(checks, stages)

    database_state, database_version = probe(ctx.binding_values[config.dependencies.database_ref]["DATABASE_URL"])
    if database_state == "managed":
        database_state = "at_head" if database_version == code_head else "behind"
    result = stage_s3_database_admin(ctx, database_state, code_head)
    checks.extend(result)
    stages.append(_stage_entry("S3", result))
    if any(check.status == "FAIL" for check in result):
        _persist_state(ctx, stages)
        return _report(checks, stages)

    result = stage_s4_entry(ctx)
    checks.extend(result)
    stages.append(_stage_entry("S4", result))
    state_name = _persist_state(ctx, stages)
    if any(check.status == "FAIL" for check in result) or not through_agents:
        return _report(checks, stages, state_path=state_name)

    # S5 — Agent onboarding over this site's own API (I4).  Opt-in: a site that
    # installs the control plane before its Agents must stay a supported flow.
    result = stage_s5_agents(ctx)
    checks.extend(result)
    stages.append(_stage_entry("S5", result))
    state_name = _persist_state(ctx, stages) or state_name
    return _report(checks, stages, state_path=state_name, agent_stage=True)


def _state_runs(state_dir: Path) -> int:
    """安装记录里的运行次数（重跑证据：MS-04 的「不重置、不轮换」需要它）。"""
    try:
        payload = json.loads((state_dir / STATE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    try:
        return int(payload.get("runs") or 0)
    except (TypeError, ValueError):
        return 0


def _state_payload(ctx: InstallContext, stages: list[dict]) -> dict:
    return {
        "site_id": ctx.config.site.id,
        "runs": _state_runs(ctx.state_dir) + 1,
        "target": ctx.config.control_plane.target,
        "release": ctx.config.release.expected_release,
        "config_digest": _config_digest(ctx.config_path),
        "dry_run": ctx.dry_run,
        "stages": stages,
    }
