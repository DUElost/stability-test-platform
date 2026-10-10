"""参数说明登记表：结构拒绝与运行期回落。说明不参与合并。"""
from __future__ import annotations

import json

from backend.services.param_docs import (
    DocEntry,
    VersionRange,
    load_param_docs,
    parse_version,
    resolve_doc,
    sensitive_registry_paths,
    validate_param_doc_document,
)
from backend.services.script_params import merge_effective_params

_BASE = {
    "schema_version": 1,
    "script_name": "demo",
    "entries": [
        {
            "path": ["wifi", "ssid"],
            "label": "网络名",
            "meaning": "要连接的网络",
        }
    ],
}


def _doc(**overrides):
    data = json.loads(json.dumps(_BASE))
    data.update(overrides)
    return data


def test_version_order_and_range_edges():
    assert parse_version("1.10.0") > parse_version("1.9.0")
    assert parse_version("v1.0.0") is None
    span = VersionRange((1, 0, 0), (2, 0, 0))
    assert span.contains((1, 9, 0))
    assert span.contains((1, 0, 0))
    assert not span.contains((2, 0, 0))
    adjacent = _doc(entries=[
        {
            "path": ["ssid"], "label": "甲", "meaning": "乙",
            "version_range": {"min_inclusive": "1.0.0", "max_exclusive": "2.0.0"},
        },
        {
            "path": ["ssid"], "label": "丙", "meaning": "丁",
            "version_range": {"min_inclusive": "2.0.0", "max_exclusive": "3.0.0"},
        },
    ])
    assert validate_param_doc_document(adjacent) == []


def test_structure_accepts_universal_plus_one_range_and_nested_path():
    data = _doc(entries=[
        {"path": ["wifi", "ssid"], "label": "网络名", "meaning": "通用说明"},
        {
            "path": ["wifi", "ssid"],
            "label": "新网络名",
            "meaning": "新版本说明",
            "version_range": {"min_inclusive": "1.10.0", "max_exclusive": "2.0.0"},
        },
    ])
    assert validate_param_doc_document(data) == []


def test_structure_rejects_bad_documents():
    missing_zh = _doc(entries=[{"path": ["ssid"], "label": "ssid", "meaning": "name"}])
    assert any("Chinese" in item for item in validate_param_doc_document(missing_zh))
    bad_path = _doc(entries=[{"path": [True], "label": "甲", "meaning": "乙"}])
    assert validate_param_doc_document(bad_path)
    bad_version = _doc(entries=[{
        "path": ["ssid"], "label": "甲", "meaning": "乙",
        "version_range": {"min_inclusive": "v1"},
    }])
    assert validate_param_doc_document(bad_version)
    empty_range = _doc(entries=[{
        "path": ["ssid"], "label": "甲", "meaning": "乙",
        "version_range": {"min_inclusive": "1.0.0", "max_exclusive": "1.0.0"},
    }])
    assert validate_param_doc_document(empty_range)
    duplicate_universal = _doc(entries=[
        {"path": ["ssid"], "label": "甲", "meaning": "乙"},
        {"path": ["ssid"], "label": "丙", "meaning": "丁"},
    ])
    assert any("universal" in item for item in validate_param_doc_document(duplicate_universal))
    overlap = _doc(entries=[
        {
            "path": ["ssid"], "label": "甲", "meaning": "乙",
            "version_range": {"min_inclusive": "1.0.0", "max_exclusive": "2.0.0"},
        },
        {
            "path": ["ssid"], "label": "丙", "meaning": "丁",
            "version_range": {"min_inclusive": "1.5.0", "max_exclusive": "3.0.0"},
        },
    ])
    assert any("overlapping" in item for item in validate_param_doc_document(overlap))


def test_resolve_prefers_one_range_then_universal_and_falls_back():
    universal = DocEntry(("ssid",), "通用", "通用含义", None, None, False, None)
    ranged = DocEntry(
        ("ssid",), "新标签", "新含义", None, None, False,
        VersionRange((1, 10, 0), (2, 0, 0)),
    )
    hit = resolve_doc([universal, ranged], ["ssid"], "1.10.0", {"label": "schema", "description": "desc"})
    assert hit.label == "新标签"
    miss = resolve_doc([universal, ranged], ["ssid"], "1.9.0", None)
    assert miss.label == "通用"
    unknown = resolve_doc([universal, ranged], ["ssid"], "v1.0.0", {"label": "schema", "description": "desc"})
    assert unknown.label == "schema"
    assert unknown.from_registry is False
    overlap_a = DocEntry(("ssid",), "甲", "甲义", None, None, True, VersionRange((1, 0, 0), (2, 0, 0)))
    overlap_b = DocEntry(("ssid",), "乙", "乙义", None, None, False, VersionRange((1, 5, 0), (3, 0, 0)))
    conflict = resolve_doc([overlap_a, overlap_b], ["ssid"], "1.6.0", {"description": "desc"})
    assert conflict.label == "ssid"
    assert conflict.meaning == "desc"
    assert "冲突" in (conflict.diagnostic or "")
    assert ("ssid",) in sensitive_registry_paths([overlap_a, overlap_b])


def test_missing_file_is_empty_and_docs_do_not_change_merge(tmp_path, monkeypatch):
    assert load_param_docs("no-such-script").entries == ()
    before = merge_effective_params({"n": {"default": 1}}, {"n": 2}, {"n": 3})
    monkeypatch.setattr(
        "backend.services.param_docs._DOCS_DIR", tmp_path,
    )
    (tmp_path / "demo.json").write_text(json.dumps(_BASE), encoding="utf-8")
    loaded = load_param_docs("demo")
    assert loaded.entries and loaded.errors == ()
    after = merge_effective_params({"n": {"default": 1}}, {"n": 2}, {"n": 3})
    assert before == after == {"n": 3}
