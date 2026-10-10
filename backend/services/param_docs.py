"""参数说明登记表加载与解析。

登记表只提供说明，不参与合并、保存校验、派发或快照写入。版本比较使用
三段非负整数，不用字符串字典序。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

_DOCS_DIR = Path(__file__).resolve().parents[1] / "schemas" / "param_docs"
_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_MISSING = object()


@dataclass(frozen=True)
class VersionRange:
    min_inclusive: tuple[int, int, int] | None
    max_exclusive: tuple[int, int, int] | None

    def contains(self, version: tuple[int, int, int]) -> bool:
        if self.min_inclusive is not None and version < self.min_inclusive:
            return False
        if self.max_exclusive is not None and version >= self.max_exclusive:
            return False
        return True


@dataclass(frozen=True)
class DocEntry:
    path: tuple
    label: str
    meaning: str
    unit: str | None
    cautions: str | None
    sensitive: bool
    version_range: VersionRange | None


@dataclass(frozen=True)
class ResolvedDoc:
    label: str
    meaning: str
    unit: str | None = None
    cautions: str | None = None
    diagnostic: str | None = None
    from_registry: bool = False


@dataclass(frozen=True)
class DocLoad:
    entries: tuple[DocEntry, ...]
    errors: tuple[str, ...]


def parse_version(text: Any) -> tuple[int, int, int] | None:
    """DB 版本无 ``v`` 前缀。``1.10.0`` 大于 ``1.9.0``（数值元组，不是字典序）。"""
    if not isinstance(text, str) or not _VERSION_RE.fullmatch(text):
        return None
    major, minor, patch = (int(part) for part in text.split("."))
    return (major, minor, patch)


def validate_param_doc_document(data: Any) -> list[str]:
    """结构校验。合法：通用、互不重叠的范围、通用加一条范围、嵌套路径。

    拒绝：缺中文标签/含义、非法 path/type/version、空或反向范围、重复通用、重叠范围。
    """
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["document must be an object"]
    if data.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    if not isinstance(data.get("script_name"), str) or not data.get("script_name"):
        errors.append("script_name must be a non-empty string")
    entries = data.get("entries")
    if not isinstance(entries, list):
        return errors + ["entries must be a list"]

    parsed: list[tuple[tuple, VersionRange | None]] = []
    for index, entry in enumerate(entries):
        prefix = f"entries[{index}]"
        if not isinstance(entry, dict):
            errors.append(f"{prefix} must be an object")
            continue
        path = _parse_path(entry.get("path"), f"{prefix}.path", errors)
        label = entry.get("label")
        meaning = entry.get("meaning")
        if not isinstance(label, str) or not _CJK_RE.search(label):
            errors.append(f"{prefix}.label must be Chinese text")
        if not isinstance(meaning, str) or not _CJK_RE.search(meaning):
            errors.append(f"{prefix}.meaning must be Chinese text")
        for optional in ("unit", "cautions"):
            value = entry.get(optional, _MISSING)
            if value is not _MISSING and value is not None and not isinstance(value, str):
                errors.append(f"{prefix}.{optional} must be a string")
        if "sensitive" in entry and not isinstance(entry.get("sensitive"), bool):
            errors.append(f"{prefix}.sensitive must be a bool")
        version_range, range_errors = _parse_range(entry.get("version_range", _MISSING), prefix)
        errors.extend(range_errors)
        if path is not None and not range_errors:
            parsed.append((path, version_range))

    errors.extend(_conflict_errors(parsed))
    return errors


def _parse_path(value: Any, label: str, errors: list[str]) -> tuple | None:
    if not isinstance(value, list) or not value:
        errors.append(f"{label} must be a non-empty list")
        return None
    segments: list[Any] = []
    for segment in value:
        if isinstance(segment, bool) or not isinstance(segment, (str, int)):
            errors.append(f"{label} segments must be strings or integers")
            return None
        if isinstance(segment, str) and segment == "":
            errors.append(f"{label} string segments must be non-empty")
            return None
        segments.append(segment)
    return tuple(segments)


def _parse_range(value: Any, prefix: str) -> tuple[VersionRange | None, list[str]]:
    if value is _MISSING or value is None or value == {}:
        return None, []
    if not isinstance(value, dict):
        return None, [f"{prefix}.version_range must be an object"]
    errors: list[str] = []
    bounds: dict[str, tuple[int, int, int] | None] = {}
    for key in ("min_inclusive", "max_exclusive"):
        if key not in value or value[key] is None:
            bounds[key] = None
            continue
        parsed = parse_version(value[key])
        if parsed is None:
            errors.append(f"{prefix}.version_range.{key} must be x.y.z")
        bounds[key] = parsed
    extra = set(value) - {"min_inclusive", "max_exclusive"}
    if extra:
        errors.append(f"{prefix}.version_range has unknown keys")
    if errors:
        return None, errors
    min_i = bounds.get("min_inclusive")
    max_e = bounds.get("max_exclusive")
    if min_i is None and max_e is None:
        return None, []
    if min_i is not None and max_e is not None and min_i >= max_e:
        return None, [f"{prefix}.version_range is empty or reversed"]
    return VersionRange(min_i, max_e), []


def _conflict_errors(parsed: list[tuple[tuple, VersionRange | None]]) -> list[str]:
    by_path: dict[tuple, list[VersionRange | None]] = {}
    for path, version_range in parsed:
        by_path.setdefault(path, []).append(version_range)
    errors: list[str] = []
    for path, ranges in by_path.items():
        universals = [item for item in ranges if item is None]
        bounded = [item for item in ranges if item is not None]
        if len(universals) > 1:
            errors.append(f"path {list(path)} has duplicate universal entries")
        for left in range(len(bounded)):
            for right in range(left + 1, len(bounded)):
                if _ranges_overlap(bounded[left], bounded[right]):
                    errors.append(f"path {list(path)} has overlapping version ranges")
    return errors


def _ranges_overlap(left: VersionRange, right: VersionRange) -> bool:
    left_min = left.min_inclusive or (0, 0, 0)
    right_min = right.min_inclusive or (0, 0, 0)
    left_max = left.max_exclusive
    right_max = right.max_exclusive
    if left_max is not None and left_max <= right_min:
        return False
    if right_max is not None and right_max <= left_min:
        return False
    return True


def entries_from_document(data: Mapping[str, Any]) -> list[DocEntry]:
    entries: list[DocEntry] = []
    for raw in data.get("entries") or []:
        version_range, _errors = _parse_range(raw.get("version_range", _MISSING), "entry")
        entries.append(
            DocEntry(
                path=tuple(raw["path"]),
                label=raw["label"],
                meaning=raw["meaning"],
                unit=raw.get("unit"),
                cautions=raw.get("cautions"),
                sensitive=bool(raw.get("sensitive")),
                version_range=version_range,
            )
        )
    return entries


def load_param_docs(script_name: str) -> DocLoad:
    path = _DOCS_DIR / f"{script_name}.json"
    if not path.is_file():
        return DocLoad((), ())
    data = json.loads(path.read_text(encoding="utf-8"))
    errors = validate_param_doc_document(data)
    if data.get("script_name") != script_name:
        errors.append("script_name does not match file name")
    if errors:
        return DocLoad((), tuple(errors))
    return DocLoad(tuple(entries_from_document(data)), ())


def sensitive_registry_paths(entries: Iterable[DocEntry]) -> set[tuple]:
    """敏感性按该路径全部条目的并集，不因版本匹配失败而取消。"""
    return {entry.path for entry in entries if entry.sensitive}


def resolve_doc(
    entries: Iterable[DocEntry],
    path: Sequence[Any],
    version: str | None,
    schema_field: Mapping[str, Any] | None = None,
) -> ResolvedDoc:
    """恰一条适用范围优先；否则唯一通用。多条、非法版本或未知版本不挑说明。"""
    path_t = tuple(path)
    fallback = _schema_fallback(path_t, schema_field)
    matched = [entry for entry in entries if entry.path == path_t]
    parsed = parse_version(version) if version is not None else None
    if version is not None and parsed is None:
        return ResolvedDoc(
            fallback.label,
            fallback.meaning,
            diagnostic="版本无法解析，未套用登记表说明",
        )
    if parsed is None:
        return ResolvedDoc(
            fallback.label,
            fallback.meaning,
            diagnostic="未知版本不套用范围说明" if matched else None,
        )
    ranged = [entry for entry in matched if entry.version_range is not None]
    universal = [entry for entry in matched if entry.version_range is None]
    applicable = [entry for entry in ranged if entry.version_range.contains(parsed)]  # type: ignore[union-attr]
    if len(applicable) > 1 or len(universal) > 1:
        return ResolvedDoc(
            fallback.label,
            fallback.meaning,
            diagnostic="登记表说明冲突，已回落 schema 或键名",
        )
    if len(applicable) == 1:
        return _from_entry(applicable[0])
    if len(universal) == 1:
        return _from_entry(universal[0])
    return fallback


def _from_entry(entry: DocEntry) -> ResolvedDoc:
    return ResolvedDoc(
        label=entry.label,
        meaning=entry.meaning,
        unit=entry.unit,
        cautions=entry.cautions,
        from_registry=True,
    )


def _schema_fallback(path: tuple, schema_field: Mapping[str, Any] | None) -> ResolvedDoc:
    field = schema_field if isinstance(schema_field, Mapping) else {}
    label = field.get("label") if isinstance(field.get("label"), str) and field.get("label") else None
    meaning = (
        field.get("description")
        if isinstance(field.get("description"), str) and field.get("description")
        else None
    )
    key_name = str(path[-1]) if path else ""
    return ResolvedDoc(label or key_name, meaning or key_name)


def schema_field_for_path(schema: Mapping[str, Any] | None, path: Sequence[Any]) -> dict | None:
    if not isinstance(schema, Mapping) or not path:
        return None
    current: Any = schema
    for index, segment in enumerate(path):
        if not isinstance(current, Mapping) or not isinstance(segment, str):
            return None
        if index == 0 and segment in current and isinstance(current[segment], dict):
            node = current[segment]
        elif isinstance(current.get("properties"), dict) and segment in current["properties"]:
            node = current["properties"][segment]
        else:
            return None
        if index == len(path) - 1:
            return node if isinstance(node, dict) else None
        current = node
    return None
