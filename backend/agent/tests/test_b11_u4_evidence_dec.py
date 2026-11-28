"""B11-U4：代表证据上传保全并校验 ``*.DEC/__exp_main.txt``（#4199 §4.2）。"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess

import pytest

from backend.agent import evidence_uploader as module
from backend.agent.contracts.evidence_layout import (
    EXP_MAIN_NAME,
    EvidenceLayoutError,
    dir_sha256_and_size,
    list_dec_exp_mains,
    safe_rmtree_no_follow,
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


# ── B11-U5（#4199 v1.2）：rc=255 产出实证判据 ─────────────────────────────


class _FakeExtractProc:
    def __init__(self, returncode: int, *, stderr: bytes = b"mtk-rc255", stdout: bytes = b""):
        self.returncode = returncode
        self.stderr = stderr
        self.stdout = stdout


def _install_u5_fake_extract(monkeypatch, tmp_path: Path, *, rc: int, mode: str):
    tool = tmp_path / "fake_aee_extract_u4"
    tool.write_text("#!/bin/true\n", encoding="utf-8")
    tool.chmod(0o755)

    def _run(cmd, cwd=None, capture_output=True, timeout=None, check=False):  # noqa: ARG001
        work = Path(cwd)
        dbg = Path(cmd[-1])
        dec = work / f"{dbg.name}.DEC"
        if mode == "ok":
            dec.mkdir(parents=True, exist_ok=True)
            (dec / EXP_MAIN_NAME).write_bytes(DEC_BODY)
        elif mode == "missing_dec":
            pass
        elif mode == "missing_exp":
            dec.mkdir(parents=True, exist_ok=True)
        elif mode == "empty":
            dec.mkdir(parents=True, exist_ok=True)
            (dec / EXP_MAIN_NAME).write_bytes(b"")
        elif mode == "blank":
            dec.mkdir(parents=True, exist_ok=True)
            (dec / EXP_MAIN_NAME).write_text(" \n\t ", encoding="utf-8")
        elif mode == "symlink_inside":
            dec.mkdir(parents=True, exist_ok=True)
            (dec / EXP_MAIN_NAME).write_bytes(DEC_BODY)
            (dec / "evil.link").symlink_to(dec / EXP_MAIN_NAME)
        else:
            raise AssertionError(f"unknown fake extract mode: {mode}")
        return _FakeExtractProc(rc)

    monkeypatch.setattr(module.subprocess, "run", _run)
    monkeypatch.setattr(
        EvidenceUploader,
        "_resolve_aee_extract_tool",
        staticmethod(lambda: tool),
    )
    return tool


def test_b11_u5_u4_rc255_full_dec_publishes_with_warning(env, tmp_path, monkeypatch, caplog):
    """① 正例：rc=255 + 完整合法 DEC → dest 含 .DEC，src 零污染，记 WARNING。"""
    import logging

    uploader, local_root, log_root = env
    event = _event_dir(local_root)
    before_inv = _file_inventory(event)
    before_hash = dir_sha256_and_size(event)
    _install_u5_fake_extract(monkeypatch, tmp_path, rc=255, mode="ok")
    calls = _capture_posts(monkeypatch)

    with caplog.at_level(logging.WARNING, logger="backend.agent.evidence_uploader"):
        uploader._upload(_task(event))

    dest = log_root / f"evidence/42_{FOLDER}/{SERIAL}/{EVENT}"
    assert event.is_dir()
    assert _file_inventory(event) == before_inv
    assert dir_sha256_and_size(event) == before_hash
    mains = list_dec_exp_mains(dest)
    assert len(mains) == 1
    assert mains[0].read_bytes() == DEC_BODY
    assert calls and calls[-1]["state"] == "VERIFIED"
    assert any("aee_extract_nonzero_rc" in r.message for r in caplog.records)


def test_b11_u5_u4_rc255_missing_dec_or_exp_degrades(env, tmp_path, monkeypatch):
    """② 反例 A：rc=255 + 缺 .DEC / 缺 __exp_main → EvidenceLayoutError 降级，不发布坏产物。"""
    uploader, local_root, log_root = env

    for mode in ("missing_dec", "missing_exp"):
        event = _event_dir(local_root / mode)
        before = dir_sha256_and_size(event)
        _install_u5_fake_extract(monkeypatch, tmp_path, rc=255, mode=mode)
        calls = _capture_posts(monkeypatch)

        with pytest.raises(EvidenceLayoutError):
            EvidenceUploader._unpack_dbg_in_place(event / "main.dbg", tmp_path / "fake_aee_extract_u4")

        uploader._upload(_task(event))
        dest = log_root / f"evidence/42_{FOLDER}/{SERIAL}/{EVENT}"
        assert dest.is_dir()
        assert list_dec_exp_mains(dest) == []
        assert dir_sha256_and_size(event) == before
        assert calls[-1]["state"] == "VERIFIED"
        # 清理 dest 以免下一 mode 撞 destination_conflict
        shutil.rmtree(dest)


def test_b11_u5_u4_rc255_empty_or_blank_exp_fail_closed(env, tmp_path, monkeypatch):
    """③ 反例 B：rc=255 + 0 字节 / 纯空白 __exp_main → fail-closed，不发布坏 DEC。"""
    uploader, local_root, log_root = env

    for mode in ("empty", "blank"):
        event = _event_dir(local_root / mode)
        before = dir_sha256_and_size(event)
        tool = _install_u5_fake_extract(monkeypatch, tmp_path, rc=255, mode=mode)

        with pytest.raises(EvidenceLayoutError):
            EvidenceUploader._unpack_dbg_in_place(event / "main.dbg", tool)
        assert list_dec_exp_mains(event) == []

        calls = _capture_posts(monkeypatch)
        uploader._upload(_task(event))
        dest = log_root / f"evidence/42_{FOLDER}/{SERIAL}/{EVENT}"
        assert dest.is_dir()
        assert list_dec_exp_mains(dest) == []
        assert dir_sha256_and_size(event) == before
        assert calls[-1]["state"] == "VERIFIED"
        shutil.rmtree(dest)


def test_b11_u5_u4_rc255_symlink_inside_dec_fail_closed(env, tmp_path, monkeypatch):
    """④ 反例 C：rc=255 + *.DEC 含 symlink → fail-closed，绝不发布坏产物。"""
    uploader, local_root, log_root = env
    event = _event_dir(local_root)
    before = dir_sha256_and_size(event)
    tool = _install_u5_fake_extract(monkeypatch, tmp_path, rc=255, mode="symlink_inside")

    with pytest.raises(EvidenceLayoutError):
        EvidenceUploader._unpack_dbg_in_place(event / "main.dbg", tool)
    assert list_dec_exp_mains(event) == []

    calls = _capture_posts(monkeypatch)
    uploader._upload(_task(event))
    dest = log_root / f"evidence/42_{FOLDER}/{SERIAL}/{EVENT}"
    assert dest.is_dir()
    assert list_dec_exp_mains(dest) == []
    assert not any(p.is_symlink() for p in dest.rglob("*"))
    assert dir_sha256_and_size(event) == before
    assert calls[-1]["state"] == "VERIFIED"


# ── B11-U6（#4199 v1.3）：隔离 / 归属 / 权限收口 ─────────────────────────────


def _install_u6_fake_extract(monkeypatch, tmp_path: Path, *, rc: int, builder):
    """自定义 builder(work, dbg) → FakeExtractProc，供 U6 多场景反例复用。"""
    tool = tmp_path / "fake_aee_extract_u6"
    tool.write_text("#!/bin/true\n", encoding="utf-8")
    tool.chmod(0o755)

    def _run(cmd, cwd=None, capture_output=True, timeout=None, check=False):  # noqa: ARG001
        work = Path(cwd)
        dbg = Path(cmd[-1])
        return builder(work, dbg)

    monkeypatch.setattr(module.subprocess, "run", _run)
    monkeypatch.setattr(
        EvidenceUploader,
        "_resolve_aee_extract_tool",
        staticmethod(lambda: tool),
    )
    return tool


def test_b11_u6_u4_extra_sibling_empty_dec_not_leaked(env, tmp_path, monkeypatch):
    """① 额外同级空 .DEC 不泄漏：dest 仅含合法首选 DEC，不含 extra.DEC。"""
    uploader, local_root, log_root = env
    event = _event_dir(local_root)
    before = dir_sha256_and_size(event)

    def builder(work: Path, dbg: Path):
        preferred = work / f"{dbg.name}.DEC"
        preferred.mkdir(parents=True, exist_ok=True)
        (preferred / EXP_MAIN_NAME).write_bytes(DEC_BODY)
        extra = work / "extra.DEC"
        extra.mkdir(parents=True, exist_ok=True)
        (extra / EXP_MAIN_NAME).write_bytes(b"")
        return _FakeExtractProc(255)

    tool = _install_u6_fake_extract(monkeypatch, tmp_path, rc=255, builder=builder)
    calls = _capture_posts(monkeypatch)
    uploader._upload(_task(event))

    dest = log_root / f"evidence/42_{FOLDER}/{SERIAL}/{EVENT}"
    assert dest.is_dir()
    assert not (dest / "extra.DEC").exists()
    mains = list_dec_exp_mains(dest)
    assert len(mains) == 1
    assert mains[0].parent.name == "main.dbg.DEC"
    assert mains[0].read_bytes() == DEC_BODY
    assert dir_sha256_and_size(event) == before
    assert calls[-1]["state"] == "VERIFIED"
    # 直接解包路径同样不得把 extra 写入 dbg.parent
    event2 = _event_dir(local_root / "direct")
    EvidenceUploader._unpack_dbg_in_place(event2 / "main.dbg", tool)
    assert (event2 / "main.dbg.DEC" / EXP_MAIN_NAME).is_file()
    assert not (event2 / "extra.DEC").exists()


def test_b11_u6_u4_nested_blank_dec_not_leaked(env, tmp_path, monkeypatch):
    """② 嵌套坏 .DEC 不泄漏：核验失败降级，dest 不含 nested/partial.DEC。"""
    uploader, local_root, log_root = env
    event = _event_dir(local_root)
    before = dir_sha256_and_size(event)

    def builder(work: Path, dbg: Path):  # noqa: ARG001
        nested = work / "nested" / "partial.DEC"
        nested.mkdir(parents=True, exist_ok=True)
        (nested / EXP_MAIN_NAME).write_text("   \n", encoding="utf-8")
        return _FakeExtractProc(255)

    _install_u6_fake_extract(monkeypatch, tmp_path, rc=255, builder=builder)
    calls = _capture_posts(monkeypatch)
    uploader._upload(_task(event))

    dest = log_root / f"evidence/42_{FOLDER}/{SERIAL}/{EVENT}"
    assert dest.is_dir()
    assert list_dec_exp_mains(dest) == []
    assert not (dest / "nested" / "partial.DEC").exists()
    assert dir_sha256_and_size(event) == before
    assert calls[-1]["state"] == "VERIFIED"


def test_b11_u6_u4_timeout_half_product_not_leaked(env, tmp_path, monkeypatch):
    """③ 超时半成品不泄漏：TimeoutExpired 后 dest 不含半成品 .DEC。"""
    uploader, local_root, log_root = env
    event = _event_dir(local_root)
    before = dir_sha256_and_size(event)

    def builder(work: Path, dbg: Path):
        half = work / f"{dbg.name}.DEC"
        half.mkdir(parents=True, exist_ok=True)
        (half / EXP_MAIN_NAME).write_bytes(b"")
        raise subprocess.TimeoutExpired(cmd=["aee_extract", str(dbg)], timeout=1)

    tool = _install_u6_fake_extract(monkeypatch, tmp_path, rc=255, builder=builder)
    with pytest.raises(subprocess.TimeoutExpired):
        EvidenceUploader._unpack_dbg_in_place(event / "main.dbg", tool)
    assert list_dec_exp_mains(event) == []
    assert not (event / "main.dbg.DEC").exists()

    calls = _capture_posts(monkeypatch)
    uploader._upload(_task(event))
    dest = log_root / f"evidence/42_{FOLDER}/{SERIAL}/{EVENT}"
    assert dest.is_dir()
    assert list_dec_exp_mains(dest) == []
    assert not (dest / "main.dbg.DEC").exists()
    assert dir_sha256_and_size(event) == before
    assert calls[-1]["state"] == "VERIFIED"


def test_b11_u6_u4_multi_dbg_attribution_isolation(env, tmp_path, monkeypatch):
    """④ 多 .dbg 归属隔离：two.dbg 无产出不得误认 one.dbg.DEC，且保留后者。"""
    uploader, local_root, log_root = env
    event = _event_dir(local_root, with_dbg=False)
    (event / "one.dbg").write_bytes(b"one")
    (event / "two.dbg").write_bytes(b"two")

    def builder(work: Path, dbg: Path):
        if dbg.name == "one.dbg":
            dec = work / "one.dbg.DEC"
            dec.mkdir(parents=True, exist_ok=True)
            (dec / EXP_MAIN_NAME).write_bytes(DEC_BODY)
            return _FakeExtractProc(0)
        return _FakeExtractProc(255)

    tool = _install_u6_fake_extract(monkeypatch, tmp_path, rc=255, builder=builder)

    one_main = EvidenceUploader._unpack_dbg_in_place(event / "one.dbg", tool)
    assert one_main.is_file()
    assert (event / "one.dbg.DEC" / EXP_MAIN_NAME).read_bytes() == DEC_BODY

    with pytest.raises(EvidenceLayoutError):
        EvidenceUploader._unpack_dbg_in_place(event / "two.dbg", tool)
    assert (event / "one.dbg.DEC" / EXP_MAIN_NAME).is_file()
    assert not (event / "two.dbg.DEC").exists()

    # 外层发布：one 成功、two 降级后 dest 仍完整保留 one.dbg.DEC
    event2 = _event_dir(local_root / "pub", with_dbg=False)
    (event2 / "one.dbg").write_bytes(b"one")
    (event2 / "two.dbg").write_bytes(b"two")
    before2 = dir_sha256_and_size(event2)
    calls = _capture_posts(monkeypatch)
    uploader._upload(_task(event2))
    dest = log_root / f"evidence/42_{FOLDER}/{SERIAL}/{EVENT}"
    assert dest.is_dir()
    mains = list_dec_exp_mains(dest)
    assert len(mains) == 1
    assert mains[0].parent.name == "one.dbg.DEC"
    assert dir_sha256_and_size(event2) == before2
    assert calls[-1]["state"] == "VERIFIED"
    assert (event / "one.dbg.DEC" / EXP_MAIN_NAME).is_file()
    assert not (event / "two.dbg.DEC").exists()


def test_b11_u6_u4_chmod_000_fail_closed_and_cleanup_no_follow_symlink(
    env, tmp_path, monkeypatch,
):
    """⑤ 无权限 .DEC（chmod 000）→ EvidenceLayoutError；清理不跟随 symlink 改外部。"""
    if os.geteuid() == 0:
        pytest.skip("root 下 chmod 000 不触发 PermissionError")

    uploader, local_root, log_root = env
    event = _event_dir(local_root)
    before = dir_sha256_and_size(event)
    external = tmp_path / "external_target"
    external.mkdir()
    (external / "keep.txt").write_text("keep-me\n", encoding="utf-8")
    external_mode_before = external.stat().st_mode

    locked_dirs: list[Path] = []

    def builder(work: Path, dbg: Path):
        dec = work / f"{dbg.name}.DEC"
        dec.mkdir(parents=True, exist_ok=True)
        nested = dec / "nested_locked"
        nested.mkdir()
        (nested / EXP_MAIN_NAME).write_bytes(DEC_BODY)
        # 半成品树内植入指向外部的 symlink，验证清理边界
        (dec / "escape.link").symlink_to(external)
        os.chmod(nested, 0o000)
        os.chmod(dec, 0o000)
        locked_dirs.extend([nested, dec])
        return _FakeExtractProc(255)

    tool = _install_u6_fake_extract(monkeypatch, tmp_path, rc=255, builder=builder)
    try:
        with pytest.raises(EvidenceLayoutError):
            EvidenceUploader._unpack_dbg_in_place(event / "main.dbg", tool)
        assert not (event / "main.dbg.DEC").exists()
        assert external.is_dir()
        assert (external / "keep.txt").read_text(encoding="utf-8") == "keep-me\n"
        assert external.stat().st_mode == external_mode_before

        calls = _capture_posts(monkeypatch)
        uploader._upload(_task(event))
        dest = log_root / f"evidence/42_{FOLDER}/{SERIAL}/{EVENT}"
        assert dest.is_dir()
        assert list_dec_exp_mains(dest) == []
        assert not (dest / "main.dbg.DEC").exists()
        assert dir_sha256_and_size(event) == before
        assert calls[-1]["state"] == "VERIFIED"
    finally:
        for path in locked_dirs:
            try:
                os.chmod(path, 0o700)
            except OSError:
                pass

    # 独立探针：safe_rmtree_no_follow 不跟随 symlink 删外部 / 改外部权限
    probe = tmp_path / "probe_tree"
    probe.mkdir()
    locked = probe / "locked.DEC"
    locked.mkdir()
    (locked / "escape.link").symlink_to(external)
    os.chmod(locked, 0o000)
    try:
        safe_rmtree_no_follow(probe)
        assert not probe.exists()
        assert external.is_dir()
        assert (external / "keep.txt").read_text(encoding="utf-8") == "keep-me\n"
        assert external.stat().st_mode == external_mode_before
    finally:
        if locked.exists():
            os.chmod(locked, 0o700)
        if probe.exists():
            shutil.rmtree(probe, ignore_errors=True)
