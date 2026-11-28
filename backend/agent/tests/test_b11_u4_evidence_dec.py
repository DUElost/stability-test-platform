"""B11-U4：代表证据上传保全并校验 ``*.DEC/__exp_main.txt``（#4199 §4.2）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.agent import evidence_uploader as module
from backend.agent.contracts.evidence_layout import (
    EXP_MAIN_NAME,
    EvidenceLayoutError,
    dir_sha256_and_size,
    list_dec_exp_mains,
)
from backend.agent.evidence_uploader import EvidenceUploader

FOLDER = "MLD-NX3-P535AA-HONOR-NX3-16-1031V78_1031_MonkeyAEEinfo"
EVENT = "2026_1031_120000_123_db.74.ANR"
SERIAL = "AZ6Q123456"
DEC_BODY = b"Exception Class: ANR\nException Type: system_app_anr\n"


class _Response:
    status_code = 200
    text = ""

    def __init__(self, data):
        self._data = data

    def json(self):
        return {"data": self._data}


@pytest.fixture
def env(tmp_path, monkeypatch):
    local_root = tmp_path / "local"
    log_root = tmp_path / "log"
    local_root.mkdir()
    log_root.mkdir()
    monkeypatch.setenv("STP_AEE_LOCAL_ROOT", str(local_root))
    monkeypatch.setenv("STP_AEE_NFS_ROOT", str(log_root))
    monkeypatch.delenv("STP_AGENT_STATE_DB", raising=False)
    monkeypatch.delenv("STP_DEVICE_LOG_EVENT_ENABLED", raising=False)
    monkeypatch.delenv("STP_DEDUP_SCAN_SCRIPT", raising=False)
    uploader = EvidenceUploader()
    uploader.configure(
        api_url="http://cp", agent_secret="s", host_id="101",
        nfs_root=str(log_root),
    )
    return uploader, local_root, log_root


def _event_dir(local_root: Path, *, with_dbg: bool = True) -> Path:
    event = local_root / FOLDER / SERIAL / "aee_exp" / EVENT
    event.mkdir(parents=True)
    (event / "BUILD_INFO").write_text("build=test\n", encoding="utf-8")
    (event / "mobilelog").mkdir()
    (event / "mobilelog" / "main.log").write_text("log\n", encoding="utf-8")
    if with_dbg:
        (event / "main.dbg").write_bytes(b"dbg-bytes")
    return event


def _task(event: Path):
    return {
        "publication_id": 11,
        "dle_id": "0f7b9c1e-0000-0000-0000-000000000011",
        "local_path": str(event),
        "destination_rel_path": f"evidence/42_{FOLDER}/{SERIAL}/{EVENT}",
        "root_plan_run_id": 42,
        "serial": SERIAL,
        "event_dir": EVENT,
        "attempts": 0,
    }


def _capture_posts(monkeypatch):
    calls = []

    def fake_post(url, json, headers, timeout):
        calls.append(json)
        if json["state"] == "VERIFIED":
            return _Response({"state": "VERIFIED"})
        return _Response({"state": json["state"]})

    monkeypatch.setattr(module.requests, "post", fake_post)
    return calls


def _install_successful_materializer(monkeypatch):
    """测试替身：在隔离 temp 内写入真实形态 ``main.dbg.DEC/__exp_main.txt``。"""

    def materialize(self, temp: Path) -> None:
        dec = temp / "main.dbg.DEC"
        dec.mkdir(exist_ok=True)
        (dec / EXP_MAIN_NAME).write_bytes(DEC_BODY)

    monkeypatch.setattr(EvidenceUploader, "_materialize_dec_in_temp", materialize)


def _file_inventory(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.is_symlink()
    }


def test_upload_keeps_src_pristine_and_publishes_dec(env, monkeypatch):
    """① 原件零污染，且 dest 含 ``*.DEC/__exp_main.txt``。"""
    uploader, local_root, log_root = env
    event = _event_dir(local_root)
    before_inv = _file_inventory(event)
    before_hash = dir_sha256_and_size(event)
    _install_successful_materializer(monkeypatch)
    calls = _capture_posts(monkeypatch)

    uploader._upload(_task(event))

    dest = log_root / f"evidence/42_{FOLDER}/{SERIAL}/{EVENT}"
    assert event.is_dir()
    assert _file_inventory(event) == before_inv
    assert dir_sha256_and_size(event) == before_hash
    mains = list_dec_exp_mains(dest)
    assert len(mains) == 1
    assert mains[0].read_bytes() == DEC_BODY
    assert (dest / "BUILD_INFO").is_file()
    assert (dest / "main.dbg").is_file()
    assert calls and calls[-1]["state"] == "VERIFIED"
    assert calls[-1]["checksum"] == dir_sha256_and_size(dest)[0]
    assert calls[-1]["checksum"] != before_hash[0]


def test_tampered_dec_rejects_atomic_rename(env, monkeypatch):
    """② 篡改 temp 内 ``*.DEC/__exp_main.txt`` 后复验失败，拒绝 rename。"""
    uploader, local_root, log_root = env
    event = _event_dir(local_root)
    before_hash = dir_sha256_and_size(event)
    _install_successful_materializer(monkeypatch)

    real_hash = module.dir_sha256_and_size
    seen_temps: list[Path] = []

    def wrapped(path):
        result = real_hash(path)
        path_obj = Path(path)
        # 第一次对含 DEC 的 temp 取哈希后立即篡改，逼出发布前复验失败。
        if (
            path_obj.name.startswith(".stp-evidence-tmp-")
            and list_dec_exp_mains(path_obj)
            and path_obj not in seen_temps
        ):
            seen_temps.append(path_obj)
            target = list_dec_exp_mains(path_obj)[0]
            target.write_bytes(b"TAMPERED-DEC-BYTES")
        return result

    monkeypatch.setattr(module, "dir_sha256_and_size", wrapped)
    calls = _capture_posts(monkeypatch)

    uploader._upload(_task(event))

    dest = log_root / f"evidence/42_{FOLDER}/{SERIAL}/{EVENT}"
    assert not dest.exists()
    assert dir_sha256_and_size(event) == before_hash
    assert calls and calls[-1]["state"] == "RETRYING"
    assert calls[-1]["reason"] == "copy_failed"


def test_symlink_under_dec_is_rejected(env, monkeypatch):
    """③ temp / ``.DEC`` 含 symlink 时被 ``assert_no_symlinks`` 拒绝。"""
    uploader, local_root, log_root = env
    event = _event_dir(local_root)
    before_hash = dir_sha256_and_size(event)

    def materialize_with_symlink(self, temp: Path) -> None:
        dec = temp / "main.dbg.DEC"
        dec.mkdir(exist_ok=True)
        (dec / EXP_MAIN_NAME).write_bytes(DEC_BODY)
        (dec / "evil.link").symlink_to(dec / EXP_MAIN_NAME)

    monkeypatch.setattr(
        EvidenceUploader, "_materialize_dec_in_temp", materialize_with_symlink,
    )
    calls = _capture_posts(monkeypatch)

    uploader._upload(_task(event))

    dest = log_root / f"evidence/42_{FOLDER}/{SERIAL}/{EVENT}"
    assert not dest.exists()
    assert dir_sha256_and_size(event) == before_hash
    assert calls and calls[-1]["state"] == "RETRYING"
    assert calls[-1]["reason"] == "copy_failed"


def test_upload_success_or_failure_never_prunes_src(env, monkeypatch):
    """④ 上传成功或失败均不删除本地原始事件目录（不提前 prune）。"""
    uploader, local_root, log_root = env
    event = _event_dir(local_root)
    _install_successful_materializer(monkeypatch)
    calls = _capture_posts(monkeypatch)

    uploader._upload(_task(event))
    assert event.is_dir()
    assert (event / "main.dbg").is_file()
    assert calls[-1]["state"] == "VERIFIED"

    # 失败路径：目的已存在且内容冲突 → 仍保留 src。
    conflict = _event_dir(local_root / "other")
    dest = log_root / f"evidence/42_{FOLDER}/{SERIAL}/{EVENT}"
    # 既有 dest 与新 temp（含 DEC）哈希不同 → destination_conflict
    (dest / "extra_marker").write_text("stale\n", encoding="utf-8")
    before = dir_sha256_and_size(conflict)
    uploader._upload(_task(conflict))
    assert conflict.is_dir()
    assert dir_sha256_and_size(conflict) == before
    assert calls[-1]["state"] == "FAILED"
    assert calls[-1]["reason"] == "destination_conflict"


def test_copytree_symlink_in_src_still_rejected(env, monkeypatch):
    """③ 补充：源树 symlink 在 copytree_no_symlinks 阶段即拒绝。"""
    uploader, local_root, log_root = env
    event = _event_dir(local_root)
    (event / "sneaky").symlink_to(event / "main.dbg")
    before_files = set(_file_inventory(event))
    _install_successful_materializer(monkeypatch)
    calls = _capture_posts(monkeypatch)

    uploader._upload(_task(event))

    assert not (log_root / "evidence").exists() or not (
        log_root / f"evidence/42_{FOLDER}/{SERIAL}/{EVENT}"
    ).exists()
    assert set(_file_inventory(event)) == before_files
    assert calls and calls[-1]["reason"] == "copy_failed"


def test_materialize_skips_without_tool_and_keeps_src(env, monkeypatch):
    """无 aee_extract 时告警降级：仍可上传原树，不污染 src。"""
    uploader, local_root, log_root = env
    event = _event_dir(local_root)
    before = dir_sha256_and_size(event)
    monkeypatch.setattr(EvidenceUploader, "_resolve_aee_extract_tool", staticmethod(lambda: None))
    calls = _capture_posts(monkeypatch)

    uploader._upload(_task(event))

    dest = log_root / f"evidence/42_{FOLDER}/{SERIAL}/{EVENT}"
    assert dest.is_dir()
    assert list_dec_exp_mains(dest) == []
    assert dir_sha256_and_size(event) == before
    assert dir_sha256_and_size(dest) == before
    assert calls[-1]["state"] == "VERIFIED"


def test_direct_copy_rejects_when_recheck_raises(env, monkeypatch):
    """直接打 ``_copy_to_destination``：复验失败抛 EvidenceLayoutError。"""
    uploader, local_root, log_root = env
    event = _event_dir(local_root)
    dest = log_root / "evidence" / f"42_{FOLDER}" / SERIAL / EVENT
    _install_successful_materializer(monkeypatch)

    real_hash = module.dir_sha256_and_size
    calls = {"n": 0}

    def flip_on_recheck(path):
        result = real_hash(path)
        path_obj = Path(path)
        if path_obj.name.startswith(".stp-evidence-tmp-") and list_dec_exp_mains(path_obj):
            calls["n"] += 1
            if calls["n"] == 1:
                list_dec_exp_mains(path_obj)[0].write_bytes(b"x")
        return result

    monkeypatch.setattr(module, "dir_sha256_and_size", flip_on_recheck)
    with pytest.raises(EvidenceLayoutError, match="temp verification failed"):
        uploader._copy_to_destination(event, dest)
    assert not dest.exists()
    assert event.is_dir()
