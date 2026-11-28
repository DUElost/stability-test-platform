"""B11-U3（#4199 v1.1 §4.2）B 段受控导出 5 类必测反例。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
import threading
import time
from typing import Any

import pytest

from backend.services.jira_handoff_builder import (
    HANDOFF_MANIFEST_SCHEMA,
    HANDOFF_MANIFEST_SCHEMA_VERSION,
    JiraHandoffError,
    _atomic_write_bytes,
    _canonical_json_bytes,
    compute_manifest_sha256,
)
from backend.services.jira_handoff_export import (
    HANDOFF_STATUS_EXPORTED,
    export_handoff_bundle,
    get_active_extract_peak_for_tests,
    reset_export_runtime_state_for_tests,
    set_extract_hook_for_tests,
    validate_target_folder_name,
)
from backend.services.jira_handoff_identity import HANDOFF_STATUS_PENDING_EXPORT


class _FakeBeginNested:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _DummyDB:
    def __init__(self) -> None:
        self.audit_entries: list = []
        self.committed = False

    def add(self, obj) -> None:
        self.audit_entries.append(obj)

    def flush(self) -> None:
        return None

    def begin_nested(self) -> _FakeBeginNested:
        return _FakeBeginNested()

    def commit(self) -> None:
        self.committed = True


def _write_evidence_tree(root: Path, *, serial: str, entry: str, payload: bytes = b"BUILD") -> Path:
    event_dir = root / "devices" / "1" / serial / entry
    event_dir.mkdir(parents=True, exist_ok=True)
    (event_dir / "BUILD_INFO").write_bytes(payload)
    (event_dir / "sample.dbg").write_bytes(b"DBG-BYTES")
    return event_dir


def _issue(
    *,
    build: str,
    serial: str,
    entry: str,
    evidence_location: str,
    caused_by: str = "NPE",
) -> dict[str, Any]:
    return {
        "issue_index": 0,
        "build": build,
        "count": 1,
        "device_count": 1,
        "device_ids": [serial],
        "source_runs": [1],
        "baseline_class": "current_only",
        "clock_fallback_count": 0,
        "preview_path": f"PENDING_EXPORT://hint/{serial}/aee_exp/{entry}/x.DEC/__exp_main.txt",
        "representative": {
            "dedup_key": f"{serial}:{entry}",
            "identity_source": "occurrence_key",
            "occurrence_key": "occ-1",
            "dle_id": "dle-1",
            "serial": serial,
            "entry": entry,
            "evidence_location": evidence_location,
            "evidence_tier": "devices",
        },
        "member_dedup_keys": [f"{serial}:{entry}"],
        "form_fields": {
            "ExpTime": "2026-11-20 12:00:00",
            "ExpClass": "Java (JE)",
            "ExpType": "JE",
            "CurProcess": "com.demo.app",
            "Package": "com.demo.app",
            "Detail": "NullPointerException",
            "CausedBy": caused_by,
            "extraTag": "",
            "Activity": "MainActivity",
            "Rom_Ram": "8+256",
        },
    }


def _seed_frozen_manifest(
    storage: Path,
    *,
    handoff_id: str = "hf_test_export_u3",
    build: str = "V100",
    serial: str = "SN001",
    entry: str = "e1",
    evidence_payload: bytes = b"BUILD",
    mutate: Any = None,
) -> tuple[Path, dict[str, Any], str, Path]:
    """写入冻结 manifest + 中心证据目录；返回 (bundle_dir, manifest, sha, evidence_dir)。"""
    evidence_dir = _write_evidence_tree(storage, serial=serial, entry=entry, payload=evidence_payload)
    bundle_dir = storage / "_meta" / "handoff" / handoff_id
    bundle_dir.mkdir(parents=True, exist_ok=True)
    body: dict[str, Any] = {
        "schema": HANDOFF_MANIFEST_SCHEMA,
        "schema_version": HANDOFF_MANIFEST_SCHEMA_VERSION,
        "handoff_id": handoff_id,
        "status": HANDOFF_STATUS_PENDING_EXPORT,
        "platform": "mtk",
        "created_at": "2026-11-27T00:00:00+00:00",
        "created_by": "tester",
        "plan_run_ids": [1],
        "snapshot_cutoff": "2026-11-27T00:00:00+00:00",
        "first_run_started_at": "2026-11-20T00:00:00+00:00",
        "target_folder_hint": None,
        "scan_tool_release": {"version": "2.1.1", "git_commit": "abc"},
        "gates": {
            "coverage_complete": True,
            "identity_degraded": False,
            "blocking_issues": [],
            "warnings": [],
        },
        "identity_gate": {
            "total_org_rows": 1,
            "unique_events": 1,
            "keyed_rows": 1,
            "compat_fallback_rows": 0,
            "unbound_rows": 0,
            "after_cutoff_skipped_rows": 0,
            "unbound_by_reason": {},
        },
        "runs": [],
        "unbound_rows": [],
        "builds": [
            {
                "build": build,
                "build_slug": "V100",
                "product": "MLD",
                "device_count": 1,
                "unique_events": 1,
                "issue_count": 1,
                "grouped_events": 1,
                "filtered_events": 0,
                "filtered_by_reason": {},
                "baseline_distribution": {"current_only": 1, "mixed": 0, "baseline_only": 0},
                "artifacts": {},
                "issues": [
                    _issue(
                        build=build,
                        serial=serial,
                        entry=entry,
                        evidence_location=str(evidence_dir),
                    )
                ],
                "filtered_rows": [],
                "events": [],
            }
        ],
    }
    if mutate is not None:
        mutate(body)
    digest = compute_manifest_sha256(body)
    body["manifest_sha256"] = digest
    _atomic_write_bytes(bundle_dir / "manifest.json", _canonical_json_bytes(body))
    return bundle_dir, body, digest, evidence_dir


def _extract_hook_factory(delay: float = 0.05):
    def _hook(dbg_path: Path, work_dir: Path) -> Path:
        time.sleep(delay)
        dec = work_dir / f"{dbg_path.name}.DEC"
        dec.mkdir(parents=True, exist_ok=True)
        exp = dec / "__exp_main.txt"
        exp.write_text("ok", encoding="utf-8")
        return exp

    return _hook


@pytest.fixture(autouse=True)
def _reset_export_state(monkeypatch, tmp_path):
    reset_export_runtime_state_for_tests()
    lock_dir = tmp_path / "dec-locks"
    lock_dir.mkdir()
    monkeypatch.setenv("STP_JIRA_HANDOFF_DEC_EXTRACT_LOCK_DIR", str(lock_dir))
    monkeypatch.setenv("STP_JIRA_HANDOFF_DEC_EXTRACT_MAX_CONCURRENT", "1")
    reset_export_runtime_state_for_tests()
    yield
    reset_export_runtime_state_for_tests()


# ── 反例 ①：篡改冻结 manifest → manifest_sha256 不匹配拒写 ───────────────


def test_b11_u3_01_tampered_manifest_sha256_rejected(tmp_path, monkeypatch):
    storage = tmp_path / "center"
    export_root = tmp_path / "export"
    export_root.mkdir()
    monkeypatch.setenv("STP_AEE_NFS_ROOT", str(storage))
    monkeypatch.setenv("STP_JIRA_HANDOFF_EXPORT_ROOT", str(export_root))
    monkeypatch.setenv("STP_JIRA_HANDOFF_VENDOR_PATH_PREFIX", "/home/jxtinno/sonic_tinno")

    bundle_dir, manifest, digest, _ = _seed_frozen_manifest(storage)
    # 篡改任一字段但不更新 digest
    raw = json.loads((bundle_dir / "manifest.json").read_text(encoding="utf-8"))
    raw["created_by"] = "attacker"
    (bundle_dir / "manifest.json").write_text(json.dumps(raw), encoding="utf-8")

    set_extract_hook_for_tests(_extract_hook_factory())
    with pytest.raises(JiraHandoffError) as ei:
        export_handoff_bundle(
            _DummyDB(),
            manifest["handoff_id"],
            manifest_sha256=digest,
            builds=[
                {
                    "build": "V100",
                    "target_folder_name": "MLD_V100_1127_MonkeyAEEinfo",
                    "merge_strategy": "create_new",
                }
            ],
            storage_root=str(storage),
            export_root=str(export_root),
        )
    assert ei.value.code in {"handoff_manifest_tampered", "handoff_manifest_sha256_mismatch"}
    assert not (export_root / "MLD_V100_1127_MonkeyAEEinfo").exists()
    assert not (bundle_dir / "export_receipt.json").exists()


# ── 反例 ②：目录穿越 / 内网 IP / symlink 拦截 ─────────────────────────────


def test_b11_u3_02_target_folder_traversal_ip_symlink_rejected(tmp_path, monkeypatch):
    storage = tmp_path / "center"
    export_root = tmp_path / "export"
    export_root.mkdir()
    monkeypatch.setenv("STP_AEE_NFS_ROOT", str(storage))
    monkeypatch.setenv("STP_JIRA_HANDOFF_EXPORT_ROOT", str(export_root))
    monkeypatch.setenv("STP_JIRA_HANDOFF_VENDOR_PATH_PREFIX", "/home/jxtinno/sonic_tinno")

    _, manifest, digest, _ = _seed_frozen_manifest(storage)
    set_extract_hook_for_tests(_extract_hook_factory())

    for bad in (
        "../escape",
        "a/b",
        "a\\b",
        "MLD_172.21.15.66_info",
        "host-172-21-15-66-folder",
    ):
        with pytest.raises(JiraHandoffError) as ei:
            validate_target_folder_name(bad)
        assert ei.value.code == "handoff_invalid_target_folder"

    # symlink 导出根祖先 → 拒绝
    real_root = tmp_path / "real-export"
    real_root.mkdir()
    link_root = tmp_path / "link-export"
    link_root.symlink_to(real_root)
    with pytest.raises(JiraHandoffError) as ei:
        export_handoff_bundle(
            _DummyDB(),
            manifest["handoff_id"],
            manifest_sha256=digest,
            builds=[
                {
                    "build": "V100",
                    "target_folder_name": "MLD_V100_1127_MonkeyAEEinfo",
                    "merge_strategy": "create_new",
                }
            ],
            storage_root=str(storage),
            export_root=str(link_root),
        )
    assert ei.value.code == "handoff_symlink_rejected"


# ── 反例 ③：既有目录不一致不覆盖 + 一致幂等 + .xls 备份 ───────────────────


def test_b11_u3_03_no_overwrite_idempotent_and_xls_backup(tmp_path, monkeypatch):
    storage = tmp_path / "center"
    export_root = tmp_path / "export"
    export_root.mkdir()
    monkeypatch.setenv("STP_AEE_NFS_ROOT", str(storage))
    monkeypatch.setenv("STP_JIRA_HANDOFF_EXPORT_ROOT", str(export_root))
    monkeypatch.setenv("STP_JIRA_HANDOFF_VENDOR_PATH_PREFIX", "/home/jxtinno/sonic_tinno")

    _, manifest, digest, evidence_dir = _seed_frozen_manifest(storage)
    set_extract_hook_for_tests(_extract_hook_factory())
    folder = "MLD_V100_1127_MonkeyAEEinfo"
    targets = [{"build": "V100", "target_folder_name": folder, "merge_strategy": "create_new"}]

    receipt1 = export_handoff_bundle(
        _DummyDB(),
        manifest["handoff_id"],
        manifest_sha256=digest,
        builds=targets,
        storage_root=str(storage),
        export_root=str(export_root),
    )
    assert receipt1["status"] == HANDOFF_STATUS_EXPORTED
    dest = export_root / folder / "SN001" / "aee_exp" / "e1"
    assert (dest / "BUILD_INFO").read_bytes() == b"BUILD"

    # 内容一致 → 幂等跳过（改 merge_existing + 新 handoff 同证据）
    bundle2, manifest2, digest2, _ = _seed_frozen_manifest(
        storage, handoff_id="hf_test_export_u3_b", evidence_payload=b"BUILD"
    )
    # 证据路径更新到同一源
    raw2 = json.loads((bundle2 / "manifest.json").read_text(encoding="utf-8"))
    raw2["builds"][0]["issues"][0]["representative"]["evidence_location"] = str(evidence_dir)
    digest2 = compute_manifest_sha256(raw2)
    raw2["manifest_sha256"] = digest2
    _atomic_write_bytes(bundle2 / "manifest.json", _canonical_json_bytes(raw2))

    receipt2 = export_handoff_bundle(
        _DummyDB(),
        "hf_test_export_u3_b",
        manifest_sha256=digest2,
        builds=[{"build": "V100", "target_folder_name": folder, "merge_strategy": "merge_existing"}],
        storage_root=str(storage),
        export_root=str(export_root),
    )
    assert receipt2["builds"][0]["representatives"][0]["action"] == "skipped"
    # 已有同名终稿 → 生成 .bak-<ts>
    assert receipt2["builds"][0]["final_xls"]["backups"], "expected .bak-<ts> backup of prior xls"

    # 内容不一致 → fail-closed 不覆盖
    bundle3, _, digest3, _ = _seed_frozen_manifest(
        storage, handoff_id="hf_test_export_u3_c", evidence_payload=b"DIFFERENT"
    )
    raw3 = json.loads((bundle3 / "manifest.json").read_text(encoding="utf-8"))
    # 用不同内容的新证据目录
    other = _write_evidence_tree(storage, serial="SN001", entry="e1-other", payload=b"DIFFERENT")
    # 把源指到不同内容，但目标 entry 名仍为 e1（已存在）
    raw3["builds"][0]["issues"][0]["representative"]["evidence_location"] = str(other)
    raw3["builds"][0]["issues"][0]["representative"]["entry"] = "e1"
    digest3 = compute_manifest_sha256(raw3)
    raw3["manifest_sha256"] = digest3
    _atomic_write_bytes(bundle3 / "manifest.json", _canonical_json_bytes(raw3))

    with pytest.raises(JiraHandoffError) as ei:
        export_handoff_bundle(
            _DummyDB(),
            "hf_test_export_u3_c",
            manifest_sha256=digest3,
            builds=[{"build": "V100", "target_folder_name": folder, "merge_strategy": "merge_existing"}],
            storage_root=str(storage),
            export_root=str(export_root),
        )
    assert ei.value.code == "handoff_export_overwrite_rejected"
    assert (dest / "BUILD_INFO").read_bytes() == b"BUILD"


# ── 反例 ④：并发补解进程数恒 <= 1 ─────────────────────────────────────────


def test_b11_u3_04_concurrent_dec_extract_serialized(tmp_path, monkeypatch):
    storage = tmp_path / "center"
    export_root = tmp_path / "export"
    export_root.mkdir()
    monkeypatch.setenv("STP_AEE_NFS_ROOT", str(storage))
    monkeypatch.setenv("STP_JIRA_HANDOFF_EXPORT_ROOT", str(export_root))
    monkeypatch.setenv("STP_JIRA_HANDOFF_VENDOR_PATH_PREFIX", "/home/jxtinno/sonic_tinno")

    # 两个独立 handoff，各自缺 .DEC，强制走补解路径；全局 flock 串行化。
    manifests = []
    for idx in (1, 2):
        entry = f"e{idx}"
        folder = f"MLD_V100_1127_batch{idx}_MonkeyAEEinfo"
        _bundle, body, digest, _ = _seed_frozen_manifest(
            storage, handoff_id=f"hf_conc_{idx}", entry=entry
        )
        manifests.append((body["handoff_id"], digest, folder, entry))

    delay = 0.15
    started = threading.Event()

    def _hook(dbg_path: Path, work_dir: Path) -> Path:
        started.set()
        time.sleep(delay)
        dec = work_dir / f"{dbg_path.name}.DEC"
        dec.mkdir(parents=True, exist_ok=True)
        exp = dec / "__exp_main.txt"
        exp.write_text("ok", encoding="utf-8")
        return exp

    set_extract_hook_for_tests(_hook)

    def _run(item):
        handoff_id, digest, folder, _entry = item
        return export_handoff_bundle(
            _DummyDB(),
            handoff_id,
            manifest_sha256=digest,
            builds=[{"build": "V100", "target_folder_name": folder, "merge_strategy": "create_new"}],
            storage_root=str(storage),
            export_root=str(export_root),
        )

    t0 = time.monotonic()
    with ThreadPoolExecutor(max_workers=2) as pool:
        futs = [pool.submit(_run, m) for m in manifests]
        results = [f.result(timeout=30) for f in as_completed(futs)]
    elapsed = time.monotonic() - t0

    assert len(results) == 2
    assert all(r["status"] == HANDOFF_STATUS_EXPORTED for r in results)
    assert get_active_extract_peak_for_tests() <= 1
    # 串行补解：总耗时应接近 2*delay（允许调度开销）
    assert elapsed >= delay * 1.5
    assert started.is_set()


# ── 反例 ⑤：缺 .DEC / Path 不可达 → 阻断终稿且不转 EXPORTED ───────────────


def test_b11_u3_05_missing_dec_blocks_final_xls_and_exported(tmp_path, monkeypatch):
    storage = tmp_path / "center"
    export_root = tmp_path / "export"
    export_root.mkdir()
    monkeypatch.setenv("STP_AEE_NFS_ROOT", str(storage))
    monkeypatch.setenv("STP_JIRA_HANDOFF_EXPORT_ROOT", str(export_root))
    monkeypatch.setenv("STP_JIRA_HANDOFF_VENDOR_PATH_PREFIX", "/home/jxtinno/sonic_tinno")

    bundle_dir, manifest, digest, evidence_dir = _seed_frozen_manifest(storage)
    # 去掉 .dbg，使补解也无法进行
    dbg = evidence_dir / "sample.dbg"
    dbg.unlink()

    # 注入失败 hook（即使误触发也不产出）
    def _fail_hook(dbg_path: Path, work_dir: Path) -> Path:
        raise AssertionError("extract hook must not be called without dbg")

    set_extract_hook_for_tests(_fail_hook)

    folder = "MLD_V100_1127_MonkeyAEEinfo"
    with pytest.raises(JiraHandoffError) as ei:
        export_handoff_bundle(
            _DummyDB(),
            manifest["handoff_id"],
            manifest_sha256=digest,
            builds=[{"build": "V100", "target_folder_name": folder, "merge_strategy": "create_new"}],
            storage_root=str(storage),
            export_root=str(export_root),
        )
    assert ei.value.code == "handoff_dec_missing"
    assert not (bundle_dir / "export_receipt.json").exists()
    # 即使目录可能已部分复制，也不得出现终稿 xls
    folder_dir = export_root / folder
    if folder_dir.exists():
        assert list(folder_dir.glob("Result_None_None_MonkeyAEE_SH_*.xls")) == []
    # 冻结 manifest 状态仍为 PENDING_EXPORT
    frozen = json.loads((bundle_dir / "manifest.json").read_text(encoding="utf-8"))
    assert frozen["status"] == HANDOFF_STATUS_PENDING_EXPORT
