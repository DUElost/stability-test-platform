"""Minimal protected secret-binding store for the local installer (I3).

A binding is one owner-only file named after its ``_ref``, holding
``KEY=VALUE`` lines.  Values are read into memory only where a stage needs
them; they are never printed, logged, or passed through argv.
"""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path

MAX_BINDING_BYTES = 4096
_KEY_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


class BindingError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _ensure_owner_only(info: os.stat_result, code: str) -> None:
    if info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise BindingError(code)


def load_binding(directory: Path | str, ref: str) -> dict[str, str]:
    directory = Path(directory)
    try:
        info = os.lstat(directory)
    except OSError:
        raise BindingError("binding_dir") from None
    if not stat.S_ISDIR(info.st_mode):
        raise BindingError("binding_dir")
    _ensure_owner_only(info, "binding_dir")

    path = directory / ref
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError:
        raise BindingError("binding_file") from None
    with os.fdopen(descriptor, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise BindingError("binding_file")
        _ensure_owner_only(info, "binding_file")
        content = handle.read(MAX_BINDING_BYTES + 1)
    if len(content) > MAX_BINDING_BYTES:
        raise BindingError("binding_content")
    try:
        text = content.decode("utf-8")
    except UnicodeError:
        raise BindingError("binding_content") from None

    values: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, separator, value = stripped.partition("=")
        key, value = key.strip(), value.strip()
        if not separator or not _KEY_PATTERN.fullmatch(key) or not value:
            raise BindingError("binding_content")
        normalized = key.upper()
        if normalized in values:
            raise BindingError("binding_content")
        values[normalized] = value
    if not values:
        raise BindingError("binding_content")
    return values


def require_keys(values: dict[str, str], required: set[str]) -> None:
    if not required.issubset(values):
        raise BindingError("binding_content")
    if set(values) - required:
        raise BindingError("binding_content")
