"""Redacted check records and their stable remediation texts (stdlib only).

Kept free of pydantic/PyYAML on purpose: ``python -m tools.site_config
preflight`` must still produce its itemised report on a machine that has no
installer dependencies yet — telling the operator what is missing is exactly
its job.  ``validation`` re-exports everything here for existing callers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

MESSAGES = {
    "input_file": "Provide an explicitly selected, readable regular UTF-8 file, not a symlink or device.",
    "input_size": "Keep the configuration below the one MiB input limit.",
    "input_encoding": "Encode the configuration as UTF-8.",
    "yaml_syntax": "Use one safe YAML document with string mapping keys.",
    "yaml_duplicate_key": "Remove duplicate YAML keys; overrides are not supported.",
    "yaml_reference": "Expand YAML anchors and aliases explicitly; references and merge keys are not supported.",
    "yaml_depth": "Reduce YAML nesting; the maximum depth is 32.",
    "missing": "Supply the required field explicitly.",
    "extra_forbidden": "Remove the unknown field; secret values and runtime environment fields do not belong here.",
    "invalid_text": "Use nonempty text without control characters.",
    "invalid_path": "Use a canonical absolute path with unescaped letters, digits, underscores, dots or hyphens; no traversal.",
    "dedicated_path_required": "Use a dedicated subdirectory, not a shared root or operating-system directory.",
    "invalid_target": "Use a bare hostname or non-local unicast IP, not a URL, local alias, port or shell expression.",
    "invalid_url": "Use an explicit HTTP(S) URL without credentials, queries, fragments, escapes or template expressions.",
    "origin_required": "Use a site origin with an empty path or a single slash.",
    "invalid_timezone": "Use a valid IANA timezone from the local timezone database.",
    "explicit_architecture_required": "Supply the shared Linux uname architecture identifier explicitly.",
    "invalid_os_version": "Quote an explicit Debian numeric version or Ubuntu YY.04/YY.10 release, not a moving alias.",
    "non_root_service_user_required": "Choose a dedicated non-root control-plane service account.",
    "production_https_required": "Use HTTPS for production; do not silently downgrade the security profile.",
    "tls_reference_required": "Provide a TLS binding name for an HTTPS entrance.",
    "http_tls_conflict": "Remove the TLS binding for explicit internal HTTP, or use HTTPS.",
    "storage_management_required": "Managed Linux storage requires its OS version, SSH user and SSH binding name.",
    "storage_share_required": "Declare the share target, protocol and share name; local_mount is the only exception.",
    "local_mount_fields_conflict": "Drop target/protocol/share and management fields for local_mount; it is a path on this control-plane host.",
    "existing_share_management_conflict": "Existing-share attachment must not declare server-management inputs.",
    "nfs_credential_conflict": "NFS attachment does not consume a CIFS credential binding.",
    "invalid_cifs_share": "Specify only the dedicated CIFS share name; declare the server separately.",
    "cifs_credential_required": "Provide a CIFS credential binding name, not its secret value.",
    "agent_local_path_overlap": "Keep Agent installation and local AEE directories disjoint.",
    "explicit_release_required": "Name a fixed release, not a moving branch or placeholder.",
    "duplicate_agent_key": "Assign a distinct logical key to every Agent; Host IDs are allocated by the site API.",
    "agent_install_root_mismatch": "Declare one Agent install_root per site; the script runtime root is a single site-wide value.",
    "duplicate_agent_target": "Declare each Agent target only once; hostname case and IP spelling are normalized.",
    "shared_path_overlap": "Keep deployment, Agent installation and local AEE directories outside shared storage and its ancestors.",
    "role_target_collision": "Use distinct targets for the initial control-plane, storage and Agent roles.",
    "secret_reference_collision": "Use distinct binding names for database, Redis, JWT, Agent, SSH encryption and initial administrator.",
    "invalid_value": "Use the field type, literal or identifier format defined by schema version 1; null is not a confirmed value.",
    "manifest_missing": "Declare the local release manifest path in release.manifest before planning.",
    "manifest_input": "Provide an explicitly selected, readable regular UTF-8 JSON manifest, not a symlink or device.",
    "manifest_size": "Keep the release manifest below the one MiB input limit.",
    "manifest_syntax": "Use one JSON object without duplicate keys.",
    "manifest_digest": "Use lowercase sha256 content digests; a digest proves integrity only, never release origin.",
    "manifest_component_duplicate": "Declare each release component name only once.",
    "release_version_mismatch": "Make expected_release match the manifest product version exactly.",
    "release_components_missing": "Include at least the agent-code and host-resources component digests.",
    "release_platform_unsupported": "Extend the release support matrix or correct the declared role OS/CPU; equal CPU architecture alone is not support evidence.",
    "output_dir": "Choose an existing directory you own that is not a symlink; the report is written with owner-only permissions.",
    "output_dir_exists": "Remove or rename the existing plan report; this command never overwrites a previous report.",
    "binding_dir": "Point --bindings-dir at an owner-only directory (0700) you own; it must not be a symlink.",
    "binding_file": "Provide each binding as a regular 0600 file named after its reference.",
    "binding_content": "Use one KEY=VALUE per line with exactly the keys the binding declares; values must be non-empty.",
    "state_dir": "Point --state-dir at an owner-only directory (0700) you own; install state is written atomically.",
    "state_locked": "Another installation run holds the site lock; wait for it to finish before retrying.",
    "install_platform": "Run the installer on a host whose OS release and CPU architecture match the declared platform.",
    "install_confirm": "Pass --confirm-site/--confirm-target exactly matching the declared site and control-plane target.",
    "install_hostname": "Run on the declared control-plane target, or correct control_plane.target before installing.",
    "install_root_taken": "Choose an empty deploy root or the same declared site for resume; existing data is never overwritten.",
    "install_dependency": "Install the declared base dependencies on the target before re-running the installer.",
    "install_storage": "Mount the declared central storage before installing; the installer never formats or creates shares.",
    "release_tree": "Provide the declared release bundle as a directory with the documented layout and wheelhouse when offline.",
    "release_digest": "The bundle content does not match the declared component digests; re-obtain the release.",
    "release_schema": "The declared database schema target does not match the release migration head.",
    "install_conflict": "Resolve the conflicting existing state manually; the installer never overwrites or elevates it.",
    "install_command": "A required system command failed; inspect the target journal, fix the cause, and re-run.",
    "db_unmanaged": "The database is not empty and was not initialized by this platform; resolve manually before fresh-install.",
    "bootstrap_database_role": "A role with that name exists with a different password; re-run with --reset-db-password to ALTER it, or pick another database/role.",
    "db_unreachable": "The database rejected the binding; the usual cause is a pre-existing role whose password differs from this run (re-run init with --reset-db-password), then check the role, pg_hba and that postgres listens on 127.0.0.1.",
    "db_driver": "Install psycopg into the tool environment that runs the installer (wheelhouse or controlled index).",
    "db_migrate_failed": "The schema migration failed; the control-plane service was not started.",
    "admin_conflict": "A non-admin user already uses the initial administrator name; resolve manually and re-run.",
    "install_units": "systemd unit installation, reload, or service start failed.",
    "install_nginx": "Nginx configuration failed the syntax check or reload.",
    # ── I5.5 一站式部署（preflight / init / inventory / bundle）────────
    "preflight_resources": "Bump CPU/RAM/disk before installing: 2 cores, 4 GiB RAM and 20 GiB free are the floor.",
    "preflight_ports": "Free the entry ports (ss -ltnp) before installing; the site entry must own 80/443.",
    "preflight_time": "Enable NTP (systemctl enable --now systemd-timesyncd) so audit and lease times agree.",
    "preflight_toolenv": "Install the installer's own dependencies (pydantic, PyYAML, psycopg) or run deploy/install.sh.",
    "preflight_redis": "Make the declared Redis reachable (redis-cli ping) before installing.",
    "inventory_file": "Point --agents-inventory at a readable inventory file (default ~/hosts.ini).",
    "inventory_shape": "Fix the offending inventory line: keys are ansible_* plus agent_key/install_root/local_aee_root/ssh_credential_ref.",
    "inventory_user_missing": "Add ansible_user for that host, or set it once in [stp_agents:vars].",
    "inventory_credential_missing": "Give the host ansible_password or ansible_ssh_private_key_file, or set one in [stp_agents:vars].",
    "inventory_credential_conflict": "Two hosts share one ssh_credential_ref but carry different secrets; give one host its own ssh_credential_ref.",
    "inventory_empty": "Add at least one Agent host under [stp_agents] before running the Agent install.",
    "bootstrap_toolenv": "Creating the installer venv failed; install python3-venv and retry.",
    "bootstrap_database": "Preparing the empty database failed; create the role/database manually and re-run init.",
    "bootstrap_storage": "Preparing the storage mount failed; mount the declared share first and re-run init.",
    "bundle_layout": "The working tree is missing required directories; build the front-end first.",
    "bundle_frontend": "Build the front-end (cd frontend && npm ci && npm run build:prod) before building a bundle.",
    "bundle_revision": "Not a git checkout; pass an explicit --revision for the release manifest.",
    "bundle_schema_target": "Derive the alembic head or pass --schema-target explicitly.",
    "bundle_digest": "Computing the ADR-0040 digests failed; check backend/agent and the pipeline schema.",
    "bundle_wheelhouse": "Downloading wheels failed; retry with a reachable index or ship wheels out of band.",
    "install_frontend": "The public entry did not serve the front-end bundle; check the deploy-root traversal bits and the Nginx root.",
    "install_health": "The control plane did not reach a healthy, schema-aligned state in time.",
    # ── S5 Agent 接入（I4）───────────────────────────────────────────────
    "agent_key_permissions": "Own the declared private key with mode 0600 as the control-plane service account so Ansible can read it.",
    "agent_install_unconfigured": "Set STP_AGENT_INSTALL_API_URL on the control plane (S2 renders it) and retry the install.",
    "host_conflict": "A different Host already owns this name or address; resolve the ownership manually; never re-create or steal a Host row.",
    "host_retired": "The Host is retired; unretire it explicitly before installing an Agent.",
    "host_not_found": "The Host row disappeared between reconciliation and install; re-run after checking concurrent changes.",
    "host_create_failed": "Host creation was rejected; inspect the API response and the audit trail.",
    "agent_install_failed": "The Agent installation run failed; inspect the RunConsole log, fix the cause, and re-run.",
    "install_timeout": "The Agent installation did not reach a terminal state before the deadline; inspect the RunConsole log before retrying.",
    "install_trigger_failed": "The install request was rejected; inspect the RunConsole log and the audit trail.",
    "agent_offline": "The Agent is not heartbeating to this control plane; check the service, its API_URL and AGENT_SECRET.",
    "agent_identity": "The Agent did not report an instance identity and boot ID; verify the deployed Agent version.",
    "agent_endpoint": "No audited install points this Host at the site's public entry; re-install the Agent from this site.",
    "agent_digest_mismatch": "The content deployed on the Agent does not match the declared release digests; re-install from the declared bundle.",
    "agent_digest_missing": "The Agent reported no deployment digest; install a release that publishes content identity (ADR-0040).",
    "api_auth": "The initial administrator was rejected by this site's API; verify the binding and the administrator state.",
    "api_response": "The site API answered with an unusable payload; inspect the control-plane log for the failing route.",
    "install_state": "The install state file is missing or unreadable; run the installer before handover.",
    "verify_report": "The verify report is missing or unreadable; re-run `verify --json` and pass the file.",
    "evidence_failed": "An acceptance item has a failing mapped check; fix it before handover.",
    "evidence_missing": "An acceptance item lacks evidence in the provided artifacts; it stays BLOCKED and is listed as pending.",
    "handover_write": "The site navigation directory is not writable; fix ownership/permissions before handover.",
    "handover_redaction": "The generated handover content hit the redaction guard and was not written; remove the offending value.",
    "navigation_missing": "The site navigation entry is missing or incomplete; re-run the install to publish it.",
    "api_unreachable": "The site's public entry is unreachable from the control plane; verify DNS, TLS trust and Nginx before retrying.",
    # ── S6 受控验收（I4 verify）─────────────────────────────────────────
    "csrf_not_enforced": "The public entry accepted a cookie-less cross-origin write; keep STP_CSRF_ENABLED=1 for production and internal profiles.",
    "device_not_found": "The declared test device is not discovered on this site; connect it and confirm the Agent reports it.",
    "device_unavailable": "The declared test device is not ONLINE; clear the offline/busy state before running the controlled chain.",
    "specialty_missing": "No plan specialty exists on this site; complete migrations before running the controlled chain.",
    "script_scan_failed": "Registering the control plane's script root failed; inspect the API response before retrying.",
    "plan_create_failed": "The controlled noop plan was rejected; inspect the API response and the script catalog.",
    "run_trigger_failed": "The controlled run was not admitted; inspect the response and the device state.",
    "run_failed": "The controlled run did not finish successfully; inspect the run timeline and the Agent log.",
    "run_evidence_missing": "The run reached a successful terminal state but no job/step evidence appeared; inspect the timeline before claiming success.",
    "run_timeout": "The controlled run did not reach a terminal state before the deadline; inspect the run timeline before retrying.",
    "watcher_not_observed": "No watcher lifecycle event was recorded for this run.",
    "probe_not_implemented": "Storage write/read probes are not implemented in this slice; they require an authorized probe directory.",
    "not_covered": "This command cannot cover the requested path; complete it separately with real artifacts.",
}



@dataclass(frozen=True)
class Check:
    check_id: str
    role: str
    status: Literal["PASS", "FAIL", "BLOCKED"]
    location: str
    code: str
    message: str
    remediation: str


def failure(code: str, *, location: str = "$", role: str = "site", check_id: str = "config.schema") -> Check:
    safe_code = code if code in MESSAGES else "invalid_value"
    return Check(check_id, role, "FAIL", location, safe_code, "Configuration rejected.", MESSAGES[safe_code])


def passed(check_id: str, role: str, location: str, code: str, message: str, remediation: str) -> Check:
    return Check(check_id, role, "PASS", location, code, message, remediation)


def blocked(check_id: str, role: str, location: str, code: str, message: str, remediation: str) -> Check:
    """Verified as *not verifiable* here; it never certifies the step either."""
    return Check(check_id, role, "BLOCKED", location, code, message, remediation)

