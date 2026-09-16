"""Offline release-manifest consumption for site planning (I2).

The manifest is a local JSON document produced by the release pipeline (R2) and
explicitly declared in ``release.manifest``.  This module only reads and
structurally checks it: signature verification, real file hashing and package
contents belong to the release pipeline and the install stage.

A content digest proves integrity only; it never proves release origin.  The
manifest therefore also declares a source attestation (signature or controlled
channel) whose *presence* is checked here and whose validity is deliberately
reported as unverified.
"""

from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import Field, ValidationError, field_validator, model_validator

from .models import ConfigModel, Name, invalid
from .validation import ConfigValidationError, failure, schema_checks

if TYPE_CHECKING:
    from typing import Self  # #2268：同 `models.py` 的说明（安装器下限 3.10）

MAX_MANIFEST_BYTES = 1024 * 1024
REQUIRED_COMPONENTS = frozenset({"agent-code", "host-resources"})

_VERSION_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.-]*$"
_REVISION_PATTERN = r"^[0-9a-f]{7,40}$"
_COMPONENT_NAME_PATTERN = r"^[a-z][a-z0-9-]*$"
_DIGEST_PATTERN = r"sha256:[0-9a-f]{64}"

Version = Annotated[str, Field(min_length=1, max_length=96, pattern=_VERSION_PATTERN)]
Revision = Annotated[str, Field(pattern=_REVISION_PATTERN)]
ComponentName = Annotated[str, Field(min_length=1, max_length=32, pattern=_COMPONENT_NAME_PATTERN)]


class ManifestComponent(ConfigModel):
    name: ComponentName
    digest: str

    @field_validator("digest")
    @classmethod
    def content_digest(cls, value: str) -> str:
        if not re.fullmatch(_DIGEST_PATTERN, value):
            raise invalid("manifest_digest")
        return value


class ManifestPlatform(ConfigModel):
    distribution: Literal["debian", "ubuntu"]
    versions: Annotated[list[str], Field(min_length=1, max_length=64)]
    cpu_arch: Annotated[list[str], Field(min_length=1, max_length=16)]

    @field_validator("versions")
    @classmethod
    def explicit_versions(cls, values: list[str]) -> list[str]:
        for value in values:
            if not re.fullmatch(r"[1-9][0-9]*(?:\.[0-9]+)?|[0-9]{2}\.(?:04|10)(?:\.[0-9]+)?", value):
                raise invalid("invalid_os_version")
        return values

    @field_validator("cpu_arch")
    @classmethod
    def explicit_architectures(cls, values: list[str]) -> list[str]:
        for value in values:
            if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,31}", value):
                raise invalid("explicit_architecture_required")
        return values


class ManifestCompatibility(ConfigModel):
    agent_protocol: Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[0-9A-Za-z.,<>=\-]+$")]
    platforms: Annotated[list[ManifestPlatform], Field(min_length=1, max_length=32)]


class ManifestDatabase(ConfigModel):
    schema_target: Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[0-9a-zA-Z_]+$")]


class ManifestSource(ConfigModel):
    revision: Revision


class ManifestProduct(ConfigModel):
    version: Version


class ManifestProvenance(ConfigModel):
    attestation: Literal["signature", "controlled_channel"]
    evidence_ref: Name


class ReleaseManifest(ConfigModel):
    manifest_version: Annotated[int, Field(ge=1, le=1)]
    product: ManifestProduct
    source: ManifestSource
    components: Annotated[list[ManifestComponent], Field(min_length=1, max_length=64)]
    database: ManifestDatabase
    compatibility: ManifestCompatibility
    provenance: ManifestProvenance

    @model_validator(mode="after")
    def component_identity(self) -> Self:
        names = [component.name for component in self.components]
        if len(set(names)) != len(names):
            raise invalid("manifest_component_duplicate")
        if not REQUIRED_COMPONENTS.issubset(names):
            raise invalid("release_components_missing")
        return self


MANIFEST_FIELD_NAMES = frozenset(
    field_name
    for model in (
        ReleaseManifest, ManifestProduct, ManifestSource, ManifestComponent,
        ManifestDatabase, ManifestCompatibility, ManifestPlatform, ManifestProvenance,
    )
    for field_name in model.model_fields
)


def _pairs_without_duplicates(pairs: list[tuple[str, object]]) -> dict:
    seen: dict[str, object] = {}
    for key, value in pairs:
        if key in seen:
            raise ValueError("duplicate key")
        seen[key] = value
    return seen


def parse_release_manifest(text: str) -> ReleaseManifest:
    try:
        data = json.loads(text, object_pairs_hook=_pairs_without_duplicates)
    except (json.JSONDecodeError, ValueError, RecursionError):
        raise ConfigValidationError([failure("manifest_syntax", check_id="release.manifest")]) from None
    if not isinstance(data, dict):
        raise ConfigValidationError([failure("manifest_syntax", check_id="release.manifest")])
    try:
        return ReleaseManifest.model_validate(data)
    except ValidationError as error:
        raise ConfigValidationError(
            schema_checks(error, check_id="release.manifest", safe_names=MANIFEST_FIELD_NAMES)
        ) from None


def load_release_manifest(path: str | Path) -> ReleaseManifest:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC)
        with os.fdopen(descriptor, "rb") as source:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                raise ConfigValidationError([failure("manifest_input", check_id="release.manifest")])
            content = source.read(MAX_MANIFEST_BYTES + 1)
    except ConfigValidationError:
        raise
    except (OSError, ValueError):
        raise ConfigValidationError([failure("manifest_input", check_id="release.manifest")]) from None
    if len(content) > MAX_MANIFEST_BYTES:
        raise ConfigValidationError([failure("manifest_size", check_id="release.manifest")])
    try:
        text = content.decode("utf-8")
    except UnicodeError:
        raise ConfigValidationError([failure("manifest_input", check_id="release.manifest")]) from None
    return parse_release_manifest(text)
