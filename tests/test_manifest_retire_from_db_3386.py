"""manifest_retire_from_db 行缺失误判退役的收口测试（#3386）。

回归锚：#3262 引入的判据「不在 active 且不在 plan_step 引用 → flip retired」把
「库中无行」（空库 / 未 scan 的新站库 / 指错库）也当成退役证据——``--apply`` 会把
manifest 全部 script 条目翻成 ``retired:true``，而 ``check_tool_manifest`` 把
retired true→false 判为不可逆，后果不可撤销。

收口后判据：**行缺失 ≠ 已退役**——只有「库中存在该行且 ``is_active=false`` 且未被
引用」才可 flip；缺行单列 ``missing_rows`` 仅报告；``--apply`` 另设健全性下限
（空表 / 空 active / flip 超半数拒绝写盘，``--force`` 显式越过并留痕）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from tools.dev.manifest_retire_from_db import (
    MAX_FLIP_RATIO,
    apply_sanity_problems,
    classify_script_entries,
    main,
)


def _doc(*versions: dict, kind: str = "script", name: str = "check_device") -> dict:
    return {"tools": {name: {"kind": kind, "versions": list(versions)}}}


def _entry(version: str, **extra) -> dict:
    return {"version": version, **extra}


# ── classify_script_entries：行缺失 ≠ 已退役 ─────────────────────────────


def test_missing_rows_are_reported_not_flipped():
    """核心验收：库中无行的条目进 missing_rows，绝不进 flips。"""
    doc = _doc(_entry("1.0.0"), _entry("2.0.0"))

    flips, refused, missing = classify_script_entries(doc, all_rows=set(), active=set(), refs=set())

    assert flips == []
    assert refused == []
    assert missing == ["check_device@1.0.0", "check_device@2.0.0"]


def test_inactive_unreferenced_existing_row_flips():
    doc = _doc(_entry("1.0.0"), _entry("2.0.0"))
    all_rows = {("check_device", "1.0.0"), ("check_device", "2.0.0")}
    active = {("check_device", "2.0.0")}

    flips, refused, missing = classify_script_entries(doc, all_rows, active, refs=set())

    assert flips == ["check_device@1.0.0"]
    assert missing == []
    assert refused == []


def test_referenced_row_refused_even_if_inactive():
    """红线拒动优先于 flip 判据（与 SCRIPT_STILL_REFERENCED 同语义）。"""
    doc = _doc(_entry("1.0.0"))
    all_rows = {("check_device", "1.0.0")}

    flips, refused, _missing = classify_script_entries(
        doc, all_rows, active=set(), refs={("check_device", "1.0.0")}
    )

    assert flips == [] and refused == ["check_device@1.0.0"]


def test_active_and_already_retired_and_tool_kind_skipped():
    doc = {
        "tools": {
            "check_device": {
                "kind": "script",
                "versions": [_entry("1.0.0"), _entry("2.0.0", retired=True)],
            },
            "flashtool": {"kind": "tool", "versions": [_entry("1.2444.00.100")]},
        }
    }
    all_rows = {("check_device", "1.0.0")}

    flips, _refused, _missing = classify_script_entries(doc, all_rows, active={("check_device", "1.0.0")}, refs=set())

    assert flips == []


# ── apply_sanity_problems：--apply 健全性下限 ────────────────────────────


def test_sanity_empty_script_table_is_a_problem():
    problems = apply_sanity_problems(4, all_rows=set(), active=set(), flips=["a@1"])
    assert any("script 表为空" in p for p in problems)


def test_sanity_empty_active_set_is_a_problem():
    problems = apply_sanity_problems(
        4, all_rows={("a", "1")}, active=set(), flips=["a@1"]
    )
    assert any("active 集为空" in p for p in problems)


def test_sanity_flip_ratio_over_half_is_a_problem():
    total = 4
    flips = [f"n@{i}" for i in range(int(MAX_FLIP_RATIO * total) + 1)]
    rows = {("n", str(i)) for i in range(total)}
    problems = apply_sanity_problems(total, rows, rows - {("n", "0")} | {("n", "9")}, flips)
    assert any("阈值" in p for p in problems)


def test_sanity_normal_batch_passes():
    total = 4
    rows = {("n", str(i)) for i in range(1, total)}
    problems = apply_sanity_problems(total, rows | {("n", "0")}, rows, flips=["n@0"])
    assert problems == []


# ── main() 端到端（文件 sqlite；空库 --apply 不改 manifest 是验收标准①） ──


@pytest.fixture
def manifest_file(tmp_path: Path):
    doc = _doc(
        _entry("1.0.0"),
        _entry("2.0.0"),
        _entry("3.0.0"),
        _entry("4.0.0"),
    )
    path = tmp_path / "tool_manifest.json"
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _sqlite_db(tmp_path: Path, script_rows: list[tuple[str, str, bool]],
               refs: list[tuple[str, str]] = ()) -> str:
    db_path = tmp_path / "site.db"
    eng = create_engine(f"sqlite:///{db_path}")
    with eng.begin() as conn:
        conn.execute(text("create table script (name text, version text, is_active boolean)"))
        conn.execute(text("create table plan_step (script_name text, script_version text)"))
        for name, version, is_active in script_rows:
            conn.execute(
                text("insert into script (name, version, is_active) values (:n, :v, :a)"),
                {"n": name, "v": version, "a": is_active},
            )
        for script_name, script_version in refs:
            conn.execute(
                text("insert into plan_step (script_name, script_version) values (:n, :v)"),
                {"n": script_name, "v": script_version},
            )
    return f"sqlite:///{db_path}"


def _run_main(monkeypatch, db_url: str, manifest: Path, *extra: str) -> int:
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setattr(sys, "argv", ["manifest_retire_from_db.py", "--manifest", str(manifest), *extra])
    return main()


def test_apply_on_empty_db_never_touches_manifest(monkeypatch, tmp_path, manifest_file):
    """验收标准①：空库 / 无 script 行的库上 --apply 不改 manifest（缺行不是证据）。"""
    before = manifest_file.read_bytes()
    db_url = _sqlite_db(tmp_path, script_rows=[])

    rc = _run_main(monkeypatch, db_url, manifest_file, "--apply")

    assert rc == 2, "空库应被健全性下限拒绝"
    assert manifest_file.read_bytes() == before


def test_apply_flips_only_rows_proven_inactive(monkeypatch, tmp_path, manifest_file):
    """行存在 + inactive + 未引用 → flip；缺行单列报告；active/被引用不动。"""
    db_url = _sqlite_db(
        tmp_path,
        script_rows=[("check_device", "1.0.0", False), ("check_device", "2.0.0", True)],
        refs=[("check_device", "3.0.0")],
    )

    rc = _run_main(monkeypatch, db_url, manifest_file, "--apply")

    assert rc == 0
    doc = json.loads(manifest_file.read_text(encoding="utf-8"))
    versions = {v["version"]: v.get("retired", False) for v in doc["tools"]["check_device"]["versions"]}
    assert versions == {"1.0.0": True, "2.0.0": False, "3.0.0": False, "4.0.0": False}


def test_apply_mass_inactive_requires_force(monkeypatch, tmp_path, manifest_file):
    """行全在但全量 inactive（指错库形态）→ 下限拒绝；--force 显式越过并写盘。"""
    db_url = _sqlite_db(
        tmp_path,
        script_rows=[("check_device", v, False) for v in ("1.0.0", "2.0.0", "3.0.0", "4.0.0")],
    )

    before = manifest_file.read_bytes()
    rc = _run_main(monkeypatch, db_url, manifest_file, "--apply")
    assert rc == 2
    assert manifest_file.read_bytes() == before

    rc = _run_main(monkeypatch, db_url, manifest_file, "--apply", "--force")
    assert rc == 0
    doc = json.loads(manifest_file.read_text(encoding="utf-8"))
    assert all(v.get("retired") for v in doc["tools"]["check_device"]["versions"])


def test_apply_with_no_candidates_writes_nothing(monkeypatch, tmp_path, manifest_file):
    """全部条目缺行（新站库未 scan）→ 即使 --force 也没有可写变更（flips 恒空）。"""
    db_url = _sqlite_db(tmp_path, script_rows=[])

    rc = _run_main(monkeypatch, db_url, manifest_file, "--apply", "--force")

    assert rc == 0
    doc = json.loads(manifest_file.read_text(encoding="utf-8"))
    assert not any(v.get("retired") for v in doc["tools"]["check_device"]["versions"])
