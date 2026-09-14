from __future__ import annotations

import os
import stat
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

import yaml
from pydantic import ValidationError
from yaml.events import AliasEvent
from yaml.nodes import MappingNode, ScalarNode

from .models import SAFE_FIELD_NAMES, SiteConfig

MAX_CONFIG_BYTES = 1024 * 1024
MAX_YAML_DEPTH = 32

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
    "existing_share_management_conflict": "Existing-share attachment must not declare server-management inputs.",
    "nfs_credential_conflict": "NFS attachment does not consume a CIFS credential binding.",
    "invalid_cifs_share": "Specify only the dedicated CIFS share name; declare the server separately.",
    "cifs_credential_required": "Provide a CIFS credential binding name, not its secret value.",
    "agent_local_path_overlap": "Keep Agent installation and local AEE directories disjoint.",
    "explicit_release_required": "Name a fixed release, not a moving branch or placeholder.",
    "duplicate_agent_key": "Assign a distinct logical key to every Agent; Host IDs are allocated by the site API.",
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


class ConfigValidationError(ValueError):
    def __init__(self, checks: list[Check]):
        self.checks = tuple(checks)
        super().__init__("Site configuration validation failed; inspect the redacted checks.")


class YAMLPolicyError(yaml.YAMLError):
    def __init__(self, code: str, mark):
        self.code = code
        self.location = f"line:{mark.line + 1}:column:{mark.column + 1}"
        super().__init__(code)


class SiteLoader(yaml.SafeLoader):
    def __init__(self, stream):
        super().__init__(stream)
        self.compose_depth = 0

    def compose_node(self, parent, index):
        event = self.peek_event()
        if isinstance(event, AliasEvent) or getattr(event, "anchor", None) is not None:
            raise YAMLPolicyError("yaml_reference", event.start_mark)
        self.compose_depth += 1
        try:
            if self.compose_depth > MAX_YAML_DEPTH:
                raise YAMLPolicyError("yaml_depth", event.start_mark)
            return super().compose_node(parent, index)
        finally:
            self.compose_depth -= 1

    def construct_mapping(self, node, deep=False):
        if not isinstance(node, MappingNode):
            raise YAMLPolicyError("yaml_syntax", node.start_mark)
        mapping = {}
        for key_node, value_node in node.value:
            if not isinstance(key_node, ScalarNode) or key_node.tag != "tag:yaml.org,2002:str":
                raise YAMLPolicyError("yaml_syntax", key_node.start_mark)
            key = key_node.value
            if key == "<<":
                raise YAMLPolicyError("yaml_reference", key_node.start_mark)
            if key in mapping:
                raise YAMLPolicyError("yaml_duplicate_key", key_node.start_mark)
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


def schema_checks(
    error: ValidationError,
    *,
    check_id: str = "config.schema",
    safe_names: frozenset[str] = SAFE_FIELD_NAMES,
    default_role: str = "site",
) -> list[Check]:
    checks = []
    for detail in error.errors(include_url=False, include_context=False, include_input=False):
        location = "$"
        for part in detail["loc"]:
            if isinstance(part, int):
                location += f"[{part}]"
            else:
                location += "." + (part if part in safe_names else "<unknown>")
        root = detail["loc"][0] if detail["loc"] else default_role
        role = "agent" if root == "agents" else root if root in {"control_plane", "storage"} else default_role
        checks.append(failure(detail["type"], location=location, role=role, check_id=check_id))
    return checks


def parse_site_config(text: str) -> SiteConfig:
    try:
        if len(text.encode("utf-8")) > MAX_CONFIG_BYTES:
            raise ConfigValidationError([failure("input_size", check_id="config.input")])
    except UnicodeError:
        raise ConfigValidationError([failure("input_encoding", check_id="config.input")]) from None
    loader = None
    try:
        loader = SiteLoader(text)
        data = loader.get_single_data()
    except YAMLPolicyError as error:
        raise ConfigValidationError([
            failure(error.code, location=error.location, check_id="config.yaml"),
        ]) from None
    except (yaml.YAMLError, ValueError, OverflowError) as error:
        mark = getattr(error, "problem_mark", None)
        location = f"line:{mark.line + 1}:column:{mark.column + 1}" if mark is not None else "$"
        raise ConfigValidationError([
            failure("yaml_syntax", location=location, check_id="config.yaml"),
        ]) from None
    finally:
        if loader is not None:
            loader.dispose()
    try:
        return SiteConfig.model_validate(data)
    except ValidationError as error:
        raise ConfigValidationError(schema_checks(error)) from None


def load_site_config(path: str | Path) -> SiteConfig:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC)
        with os.fdopen(descriptor, "rb") as source:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                raise ConfigValidationError([failure("input_file", check_id="config.input")])
            content = source.read(MAX_CONFIG_BYTES + 1)
    except ConfigValidationError:
        raise
    except (OSError, ValueError):
        raise ConfigValidationError([failure("input_file", check_id="config.input")]) from None
    if len(content) > MAX_CONFIG_BYTES:
        raise ConfigValidationError([failure("input_size", check_id="config.input")])
    try:
        text = content.decode("utf-8")
    except UnicodeError:
        raise ConfigValidationError([failure("input_encoding", check_id="config.input")]) from None
    return parse_site_config(text)


DEFERRED_CHECKS = (
    Check("release.compatibility", "site", "BLOCKED", "$.release", "not_checked",
          "Release contents, provenance and OS/CPU support matrix were not read or verified.",
          "Complete release planning separately; equal CPU architecture does not prove distribution compatibility."),
    Check("secrets.bindings", "site", "BLOCKED", "$", "not_checked",
          "Secret binding values, permissions and cross-site uniqueness were not inspected.",
          "Supply protected bindings only to a future explicitly authorized operation."),
    Check("targets.preflight", "site", "BLOCKED", "$", "not_checked",
          "No network, SSH, database or storage probe ran; target OS/CPU, aliases, symlinks and mounts are unverified.",
          "Perform authorized target-specific preflight when available; never inherit installer-local services."),
    Check("installation.verify", "site", "BLOCKED", "$", "not_checked",
          "No installation, runtime security, administrator bootstrap or device execution was verified.",
          "Complete the remaining I2-I5 implementation and isolated installation acceptance."),
)


def validate_config_file(path: str | Path) -> dict:
    try:
        load_site_config(path)
        checks = [Check("config.schema", "site", "PASS", "$", "configuration_valid",
                        "Configuration structure and offline consistency checks passed.",
                        "Do not treat this result as deployment readiness.")]
    except ConfigValidationError as error:
        checks = error.checks
    return {
        "stage": "validate",
        "status": "PASS" if all(check.status == "PASS" for check in checks) else "FAIL",
        "summary": "Configuration only; this command does not certify installation readiness.",
        "checks": [asdict(check) for check in checks],
        "deferred_checks": [asdict(check) for check in DEFERRED_CHECKS],
    }
