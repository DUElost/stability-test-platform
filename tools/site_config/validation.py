from __future__ import annotations

import os
import stat
from dataclasses import asdict
from pathlib import Path

import yaml
from pydantic import ValidationError
from yaml.events import AliasEvent
from yaml.nodes import MappingNode, ScalarNode

from .checks import MESSAGES, Check, blocked, failure, passed  # noqa: F401  (既有调用方沿用本模块路径)
from .models import SAFE_FIELD_NAMES, SiteConfig

MAX_CONFIG_BYTES = 1024 * 1024
MAX_YAML_DEPTH = 32


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
