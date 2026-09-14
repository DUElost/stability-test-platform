"""Offline site plan (I2): release-manifest checks plus a redacted template plan.

The plan stage never resolves secret bindings, never contacts targets and never
writes outside an explicitly selected, protected directory.  Reports carry
field names, counts and repository template names only: no input values,
targets, configuration paths or binding names.
"""

from __future__ import annotations

import json
import os
import stat
from collections.abc import Iterable, Iterator
from dataclasses import asdict
from pathlib import Path

from .manifest import ReleaseManifest, load_release_manifest
from .models import LinuxDistribution, SiteConfig
from .validation import DEFERRED_CHECKS, Check, ConfigValidationError, failure, load_site_config

PLAN_REPORT_NAME = "plan-report.json"

GENERATED_SECRET_KEYS = ("JWT_SECRET_KEY", "AGENT_SECRET", "WS_TOKEN")
BINDING_FIELD_PATHS = (
    "dependencies.database_ref",
    "dependencies.redis_ref",
    "security.jwt_key_ref",
    "security.agent_secret_ref",
    "security.ssh_encryption_key_ref",
    "security.initial_admin_ref",
)

_DEPLOY_ROOT = {"name": "<deploy-root>", "source": "control_plane.deploy_root", "resolved": True}
_DEPLOY_USER = {"name": "<deploy-user>", "source": "control_plane.deploy_user", "resolved": True}

TEMPLATE_PLAN = (
    {
        "template": "deploy/control-plane/systemd/stability-backend.service",
        "role": "control_plane",
        "placeholders": (_DEPLOY_ROOT, _DEPLOY_USER),
    },
    {
        "template": "deploy/control-plane/systemd/stability-backend-migrate.service",
        "role": "control_plane",
        "placeholders": (_DEPLOY_ROOT, _DEPLOY_USER),
    },
    {
        "template": "deploy/control-plane/systemd/stability-backend-nomigrate.service",
        "role": "control_plane",
        "placeholders": (_DEPLOY_ROOT, _DEPLOY_USER),
    },
    {
        "template": "deploy/control-plane/logrotate/stability-backend",
        "role": "control_plane",
        "placeholders": (_DEPLOY_ROOT,),
    },
    {
        "template": "deploy/control-plane/nginx/stability-platform.conf",
        "role": "control_plane",
        "placeholders": (_DEPLOY_ROOT,),
    },
    {
        "template": "deploy/control-plane/nginx/stability-platform-https.conf",
        "role": "control_plane",
        "placeholders": (
            _DEPLOY_ROOT,
            {"name": "<server-name>", "source": "control_plane.public_url", "resolved": True},
            {"name": "<tls-cert-path>", "source": "binding", "resolved": False},
            {"name": "<tls-key-path>", "source": "binding", "resolved": False},
        ),
    },
    {
        "template": "deploy/control-plane/nginx/stability-platform-preview.conf",
        "role": "control_plane",
        "placeholders": (_DEPLOY_ROOT,),
    },
)


def _deferred_checks(exclude: Iterable[str] = ()) -> list[Check]:
    excluded = set(exclude)
    return [check for check in DEFERRED_CHECKS if check.check_id not in excluded]


def _report(checks: list[Check], *, deferred: list[Check], extra: dict | None = None) -> dict:
    report = {
        "stage": "plan",
        "status": "FAIL" if any(check.status == "FAIL" for check in checks) else "PASS",
        "summary": "Offline plan only: release origin, real file contents and target state were not verified.",
        "checks": [asdict(check) for check in checks],
        "deferred_checks": [asdict(check) for check in deferred],
    }
    if extra:
        report.update(extra)
    return report


def _role_operating_systems(config: SiteConfig) -> Iterator[tuple[str, str, LinuxDistribution]]:
    yield "control_plane", "$.control_plane.os", config.control_plane.os
    if config.storage.os is not None:
        yield "storage", "$.storage.os", config.storage.os
    for index, agent in enumerate(config.agents):
        yield "agent", f"$.agents[{index}].os", agent.os


def _platform_supported(manifest: ReleaseManifest, distribution: LinuxDistribution, cpu_arch: str) -> bool:
    return any(
        platform.distribution == distribution.distribution
        and distribution.version in platform.versions
        and cpu_arch in platform.cpu_arch
        for platform in manifest.compatibility.platforms
    )


def _plan_steps(config: SiteConfig) -> list[dict]:
    pending = sum(
        1
        for entry in TEMPLATE_PLAN
        for placeholder in entry["placeholders"]
        if not placeholder["resolved"]
    )
    return [
        {
            "step": "render_control_plane_templates",
            "role": "control_plane",
            "template_count": len(TEMPLATE_PLAN),
            "pending_binding_placeholders": pending,
        },
        {
            "step": "generate_control_plane_env",
            "role": "control_plane",
            "profile": config.control_plane.security_profile,
            "generated_secret_keys": list(GENERATED_SECRET_KEYS),
            "binding_field_paths": list(BINDING_FIELD_PATHS),
        },
        {"step": "attach_central_storage", "role": "storage"},
        {"step": "install_agents", "role": "agent", "agent_count": len(config.agents)},
    ]


def _release_extra(config: SiteConfig, manifest: ReleaseManifest) -> dict:
    return {
        "release": {
            "product_version_matched": manifest.product.version == config.release.expected_release,
            "components": sorted(component.name for component in manifest.components),
            "attestation": manifest.provenance.attestation,
            "attestation_verified": False,
        },
        "template_changes": [dict(entry) for entry in TEMPLATE_PLAN],
        "steps": _plan_steps(config),
    }


def _save_report(report: dict, directory: Path) -> list[Check]:
    try:
        info = os.stat(directory, follow_symlinks=False)
    except OSError:
        return [failure("output_dir", check_id="plan.output")]
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
        return [failure("output_dir", check_id="plan.output")]
    try:
        descriptor = os.open(directory / PLAN_REPORT_NAME, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return [failure("output_dir_exists", check_id="plan.output")]
    except OSError:
        return [failure("output_dir", check_id="plan.output")]
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    return []


def _with_output(report: dict, save_dir: str | Path | None) -> dict:
    if save_dir is None:
        return report
    checks = _save_report(report, Path(save_dir))
    if checks:
        report["checks"].extend(asdict(check) for check in checks)
        report["status"] = "FAIL"
        return report
    report["saved_file"] = PLAN_REPORT_NAME
    return report


def plan_site_report(config_path: str | Path, *, save_dir: str | Path | None = None) -> dict:
    try:
        config = load_site_config(config_path)
    except ConfigValidationError as error:
        checks = list(error.checks)
        checks.append(Check(
            "release.compatibility", "site", "BLOCKED", "$.release", "not_checked",
            "Release checks did not run because the configuration was rejected.",
            "Fix the configuration first; this plan is not a release check.",
        ))
        return _with_output(_report(checks, deferred=_deferred_checks()), save_dir)

    checks: list[Check] = []
    if config.release.manifest is None:
        checks.append(failure("manifest_missing", location="$.release.manifest", check_id="release.manifest"))
        checks.append(Check(
            "release.compatibility", "site", "BLOCKED", "$.release", "not_checked",
            "Release compatibility could not be checked without a declared manifest.",
            "Declare release.manifest and re-run the plan.",
        ))
        return _with_output(_report(checks, deferred=_deferred_checks()), save_dir)

    try:
        manifest = load_release_manifest(config.release.manifest)
    except ConfigValidationError as error:
        checks.extend(error.checks)
        checks.append(Check(
            "release.compatibility", "site", "BLOCKED", "$.release", "not_checked",
            "Release compatibility could not be checked because the manifest was rejected.",
            "Provide a valid manifest and re-run the plan.",
        ))
        return _with_output(_report(checks, deferred=_deferred_checks()), save_dir)

    checks.append(Check(
        "release.manifest", "site", "PASS", "$.release.manifest", "manifest_accepted",
        "Manifest shape, component digests and a source attestation are declared.",
        "Presence only: signatures, contents and channel authorization remain unverified.",
    ))
    if manifest.product.version == config.release.expected_release:
        checks.append(Check(
            "release.version", "site", "PASS", "$.release.expected_release", "release_version_matched",
            "expected_release matches the declared manifest product version.",
            "Re-check after any repackaging; do not compare moving aliases.",
        ))
    else:
        checks.append(failure(
            "release_version_mismatch", location="$.release.expected_release", check_id="release.version",
        ))
    checks.append(Check(
        "release.components", "site", "PASS", "$.release.manifest", "components_declared",
        "Required component digests (agent-code, host-resources) are declared.",
        "Digests prove integrity only; they never prove release origin.",
    ))
    unsupported = [
        (role, location)
        for role, location, distribution in _role_operating_systems(config)
        if not _platform_supported(manifest, distribution, config.platform.cpu_arch)
    ]
    if unsupported:
        for role, location in unsupported:
            checks.append(failure(
                "release_platform_unsupported", location=location, role=role, check_id="release.platform",
            ))
    else:
        checks.append(Check(
            "release.platform", "site", "PASS", "$.platform", "platform_supported",
            "Every declared role OS and the shared CPU architecture are inside the manifest support matrix.",
            "Re-verify against real target hardware during preflight; declarations are not measurements.",
        ))
    checks.append(Check(
        "release.provenance", "site", "BLOCKED", "$.release.manifest", "verification_deferred",
        "A source attestation is declared but not verified offline.",
        "Verify signatures or channel authorization in the release pipeline and re-confirm at install.",
    ))
    report = _report(
        checks,
        deferred=_deferred_checks(exclude={"release.compatibility"}),
        extra=_release_extra(config, manifest),
    )
    return _with_output(report, save_dir)
