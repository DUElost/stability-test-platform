"""B10 U2：Agent L1 固件缓存基座 / 能力门控 / Settings 必测反例（#4086 §4）。

全部落在临时目录；不触碰生产盘/生产库。
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from backend.agent.contracts.capabilities import FIRMWARE_CACHE_V1
from backend.agent.contracts.firmware_manifest import (
    PROFILE_MTK,
    PROFILE_UNISOC_BSL,
    PROFILE_UNISOC_FASTBOOT,
    compute_content_digest,
    dumps_staging_manifest,
)
from backend.agent.firmware_cache import (
    FIRMWARE_CACHE_API_VERSION,
    CODE_INSUFFICIENT_SPACE,
    CODE_PROFILE_NOT_FOUND,
    CODE_UNSEALED_NOT_SUPPORTED,
    FirmwareCacheError,
    FirmwareCacheManager,
    ensure_firmware,
    resolve_cache_identity,
    resolve_cache_roots,
)
from backend.agent.script_packages import inject_required_tools, verify_package
from backend.agent.settings import (
    get_firmware_cache_settings,
    reset_agent_settings_caches,
)
from backend.agent.tool_requirements import (
    RequirementError,
    parse_agent_capability_requirements,
)
from backend.agent.transfer_permit_client import TransferPermitHold

_REPO = Path(__file__).resolve().parents[3]


def _settings(**overrides):
    base = dict(
        stp_firmware_cache_dir="",
        stp_firmware_cache_fallback_dir="",
        stp_firmware_cache_reserve_gb=0.0,
        stp_firmware_cache_max_gb=60.0,
        stp_firmware_cache_max_pct=90.0,
        stp_firmware_cache_ttl_days=14.0,
        stp_firmware_cache_cleanup_interval_seconds=21600.0,
        stp_firmware_transfer_permit_wait_timeout_seconds=3600.0,
        stp_firmware_transfer_unreachable_timeout_seconds=300.0,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _fake_permit():
    client = MagicMock()
    client.acquire_permit.return_value = TransferPermitHold(
        lease_token="t",
        host_id="h",
        pool="firmware_nfs",
        permit_degraded=False,
        _client=None,
    )
    return client


def _write_file(path: Path, data: bytes) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return {
        "relpath": path.name if path.parent == path.parent else str(path),
        "size": len(data),
        "sha256": __import__("hashlib").sha256(data).hexdigest(),
    }


def _sealed_source(root: Path, profiles: dict[str, list[tuple[str, bytes]]]) -> Path:
    """构造带 staging_manifest.json 的源目录。"""
    profile_objs: dict = {}
    for name, files in profiles.items():
        entries = []
        for rel, data in files:
            abs_path = root / rel
            abs_path.parent.mkdir(parents=True, exist_ok=True)
            abs_path.write_bytes(data)
            entries.append(
                {
                    "relpath": rel.replace("\\", "/"),
                    "size": len(data),
                    "sha256": __import__("hashlib").sha256(data).hexdigest(),
                }
            )
        ordered = sorted(entries, key=lambda e: e["relpath"])
        profile_objs[name] = {
            "content_digest": compute_content_digest(ordered),
            "total_bytes": sum(e["size"] for e in ordered),
            "files": ordered,
        }
    manifest = {"schema_version": 1, "profiles": profile_objs}
    (root / "staging_manifest.json").write_text(
        dumps_staging_manifest(manifest), encoding="utf-8"
    )
    return root


def _disk(total=100.0, used=10.0, avail=90.0, writable=10.0):
    return {
        "total_gb": total,
        "used_gb": used,
        "free_gb": avail,
        "avail_gb": avail,
        "usage_percent": used / total * 100.0,
        "writable_percent": writable,
    }


# ── 1. 导入口径与能力门控 ─────────────────────────────────────────────────────


def test_dual_pythonpath_imports_agent_and_config(tmp_path):
    """env -i 无预设 PYTHONPATH：经 pipeline_engine 同源双路径使 agent.* 与 config 同可用。"""
    import sys

    import backend.agent.pipeline_engine as pipeline_engine

    # 与 pipeline_engine 启动子进程处同一构造（ADR-0064 D2），不手拼仓库相对路径。
    agent_dir = Path(pipeline_engine.__file__).resolve().parent
    agent_parent = str(agent_dir.parent)
    dual_prefix = f"{agent_dir}{os.pathsep}{agent_parent}"
    env_probe: dict[str, str] = {}
    env_probe["PYTHONPATH"] = dual_prefix  # 等同引擎：无既有 PYTHONPATH 时只写双路径前缀
    assert env_probe["PYTHONPATH"].split(os.pathsep)[:2] == [
        str(agent_dir),
        agent_parent,
    ]

    script = (
        "from agent.firmware_cache import FIRMWARE_CACHE_API_VERSION as v\n"
        "from config import AGENT_DIR\n"
        "print(v)\n"
        "print(AGENT_DIR)\n"
    )
    # 仅注入 PATH/HOME/PYTHONPATH——模拟无 systemd 预设的干净子进程。
    # PATH 字面量（不读 os.environ）：避免 env-inventory 把 PATH 登记为新读取名。
    # 解释器取当前 pytest 进程（sys.executable），不硬编码 .venv 路径。
    proc = subprocess.run(
        [
            "env",
            "-i",
            "PATH=/usr/bin:/bin",
            f"HOME={tmp_path}",
            f"PYTHONPATH={env_probe['PYTHONPATH']}",
            sys.executable,
            "-c",
            script,
        ],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    lines = proc.stdout.strip().splitlines()
    assert lines[0] == str(FIRMWARE_CACHE_API_VERSION)


def test_requires_agent_capabilities_blocks_verify_and_inject(tmp_path, monkeypatch):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "flash.py").write_text("print(1)\n", encoding="utf-8")
    (pkg / "capabilities.json").write_text(
        json.dumps(
            {
                "capabilities": [],
                "requires_agent_capabilities": [FIRMWARE_CACHE_V1, "missing_cap_x"],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(RequirementError):
        parse_agent_capability_requirements(
            {"requires_agent_capabilities": "firmware_cache_v1"}
        )
    assert parse_agent_capability_requirements(
        {"requires_agent_capabilities": [FIRMWARE_CACHE_V1]}
    ) == [FIRMWARE_CACHE_V1]

    # verify_package：无 package_sha 时仍应执行能力门控——但 verify 在无 sha 时早退。
    # 直接测 inject_required_tools（步骤启动前路径）。
    err = inject_required_tools(pkg, {})
    assert err is not None
    assert "required_agent_capability_missing" in err
    assert "missing_cap_x" in err

    # 仅声明已有能力 → 通过
    (pkg / "capabilities.json").write_text(
        json.dumps({"requires_agent_capabilities": [FIRMWARE_CACHE_V1]}),
        encoding="utf-8",
    )
    assert inject_required_tools(pkg, {}) is None

    # verify_package 带 sha 路径：用假 ensure 跳过拉包，仍跑能力校验
    monkeypatch.setattr(
        "backend.agent.script_packages.tool_cache._packages_root",
        lambda _e: tmp_path / "packages",
    )
    monkeypatch.setattr(
        "backend.agent.script_packages.tool_cache._cache_root",
        lambda _e: tmp_path / "cache",
    )
    monkeypatch.setattr(
        "backend.agent.script_packages.tool_cache.ensure_package",
        lambda *a, **k: pkg,
    )
    (pkg / "capabilities.json").write_text(
        json.dumps({"requires_agent_capabilities": ["nope_cap"]}),
        encoding="utf-8",
    )
    ok, err = verify_package(
        {
            "name": "flash_firmware",
            "version": "1.0.0",
            "nfs_path": "flash.py",
            "package_sha256": "abc",
        },
        env={},
    )
    assert ok is False
    assert err and "required_agent_capability_missing" in err


# ── 2. 拓扑与 profile × 封包状态 ───────────────────────────────────────────────


def test_topology_and_profile_matrix(tmp_path, monkeypatch):
    primary = tmp_path / "ssd" / "firmware_cache"
    secondary = tmp_path / "hdd" / "stp_firmware_cache"
    aee = tmp_path / "hdd" / "aee_events"
    primary.mkdir(parents=True)
    secondary.mkdir(parents=True)
    aee.mkdir(parents=True)

    # 伪造不同 st_dev
    monkeypatch.setattr(
        "backend.agent.firmware_cache._st_dev",
        lambda p: 1 if "ssd" in str(p) else 2,
    )
    monkeypatch.setattr(
        "backend.agent.firmware_cache._mount_fstype_for_path",
        lambda _p: "ext4",
    )
    monkeypatch.setattr(
        "backend.agent.firmware_cache.get_aee_local_root",
        lambda: aee,
    )
    monkeypatch.setattr(
        "backend.agent.firmware_cache.is_ssd_fallback_root",
        lambda _p: False,
    )
    monkeypatch.setattr(
        "backend.agent.firmware_cache.get_disk_usage",
        lambda _p: _disk(),
    )

    cfg = _settings(
        stp_firmware_cache_dir=str(primary),
        stp_firmware_cache_fallback_dir=str(secondary),
    )
    p, s = resolve_cache_roots(settings=cfg)
    assert p == primary
    assert s == secondary

    # 同 st_dev → 跳过二级
    monkeypatch.setattr("backend.agent.firmware_cache._st_dev", lambda _p: 9)
    p2, s2 = resolve_cache_roots(settings=cfg)
    assert p2 == primary
    assert s2 is None

    # 拒绝 nfs
    monkeypatch.setattr(
        "backend.agent.firmware_cache._mount_fstype_for_path",
        lambda _p: "nfs4",
    )
    with pytest.raises(FirmwareCacheError) as ei:
        resolve_cache_roots(
            settings=_settings(stp_firmware_cache_dir=str(tmp_path / "nfs_root"))
        )
    assert ei.value.code == "firmware_cache_root_rejected"

    monkeypatch.setattr(
        "backend.agent.firmware_cache._mount_fstype_for_path",
        lambda _p: "ext4",
    )
    monkeypatch.setattr("backend.agent.firmware_cache._st_dev", lambda _p: 1)

    # 已封包缺 profile
    src = _sealed_source(
        tmp_path / "fw_sealed",
        {PROFILE_UNISOC_FASTBOOT: [("images/x.img", b"fastboot-bytes")]},
    )
    with pytest.raises(FirmwareCacheError) as ei:
        resolve_cache_identity(src, PROFILE_UNISOC_BSL)
    assert ei.value.code == CODE_PROFILE_NOT_FOUND

    # 未封包 mtk / unisoc_fastboot
    unsealed = tmp_path / "fw_unsealed"
    (unsealed / "a.bin").parent.mkdir(parents=True, exist_ok=True)
    (unsealed / "a.bin").write_bytes(b"abc")
    key_m, sealed_m, *_ = resolve_cache_identity(unsealed, PROFILE_MTK)
    assert sealed_m is False
    assert "_unsealed_" in key_m
    key_f, sealed_f, *_ = resolve_cache_identity(unsealed, PROFILE_UNISOC_FASTBOOT)
    assert sealed_f is False
    assert key_m == key_f  # 未封包整目录键不含 profile

    with pytest.raises(FirmwareCacheError) as ei:
        resolve_cache_identity(unsealed, PROFILE_UNISOC_BSL)
    assert ei.value.code == CODE_UNSEALED_NOT_SUPPORTED


# ── 3. 命中零读源大镜像 + 双 profile 隔离 ─────────────────────────────────────


def test_sealed_hit_skips_source_image_open_and_profiles_isolated(
    tmp_path, monkeypatch
):
    primary = tmp_path / "cache"
    primary.mkdir()
    monkeypatch.setattr(
        "backend.agent.firmware_cache._mount_fstype_for_path", lambda _p: "ext4"
    )
    monkeypatch.setattr(
        "backend.agent.firmware_cache.get_aee_local_root",
        lambda: tmp_path / "aee",
    )
    (tmp_path / "aee").mkdir()
    monkeypatch.setattr(
        "backend.agent.firmware_cache.get_disk_usage", lambda _p: _disk()
    )
    monkeypatch.setattr(
        "backend.agent.firmware_cache._st_dev", lambda _p: 1
    )

    big = b"B" * 1024
    src = _sealed_source(
        tmp_path / "P525AA",
        {
            PROFILE_UNISOC_FASTBOOT: [("images/raw/super.img", big)],
            PROFILE_UNISOC_BSL: [("images/bsl/super_trimmed.img", b"trim")],
        },
    )
    cfg = _settings(stp_firmware_cache_dir=str(primary))
    permit = _fake_permit()

    with ensure_firmware(
        src,
        PROFILE_UNISOC_FASTBOOT,
        permit_client=permit,
        settings=cfg,
    ) as r1:
        assert r1.hit is False
        assert r1.sealed is True
        fastboot_key = r1.cache_key

    with ensure_firmware(
        src,
        PROFILE_UNISOC_BSL,
        permit_client=permit,
        settings=cfg,
    ) as r2:
        assert r2.cache_key != fastboot_key

    opened_source_images: list[str] = []
    real_open = open

    def tracking_open(file, *args, **kwargs):
        path = str(file)
        if str(src) in path and path.endswith(".img"):
            opened_source_images.append(path)
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr("builtins.open", tracking_open)
    with ensure_firmware(
        src,
        PROFILE_UNISOC_FASTBOOT,
        permit_client=permit,
        settings=cfg,
    ) as hit:
        assert hit.hit is True
        assert (hit.local_dir / "images" / "raw" / "super.img").is_file()
    assert opened_source_images == []


# ── 4. 并发 / 降级复核 / 进度回调 ─────────────────────────────────────────────


def test_concurrent_same_firmware_copies_once(tmp_path, monkeypatch):
    primary = tmp_path / "cache"
    primary.mkdir()
    monkeypatch.setattr(
        "backend.agent.firmware_cache._mount_fstype_for_path", lambda _p: "ext4"
    )
    monkeypatch.setattr(
        "backend.agent.firmware_cache.get_aee_local_root",
        lambda: tmp_path / "aee",
    )
    (tmp_path / "aee").mkdir()
    monkeypatch.setattr(
        "backend.agent.firmware_cache.get_disk_usage", lambda _p: _disk()
    )
    monkeypatch.setattr("backend.agent.firmware_cache._st_dev", lambda _p: 1)

    src = _sealed_source(
        tmp_path / "fw",
        {PROFILE_MTK: [("scatter.xml", b"scatter"), ("img.bin", b"payload")]},
    )
    cfg = _settings(stp_firmware_cache_dir=str(primary))
    copy_count = {"n": 0}
    real_copy = __import__(
        "backend.agent.firmware_cache", fromlist=["_copy_files_streaming"]
    )._copy_files_streaming

    def counting_copy(*args, **kwargs):
        copy_count["n"] += 1
        time.sleep(0.05)
        return real_copy(*args, **kwargs)

    monkeypatch.setattr(
        "backend.agent.firmware_cache._copy_files_streaming", counting_copy
    )
    barrier = threading.Barrier(2)
    results = []

    def worker():
        barrier.wait()
        with ensure_firmware(
            src, PROFILE_MTK, permit_client=_fake_permit(), settings=cfg
        ) as r:
            results.append(r.cache_key)
            time.sleep(0.05)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert copy_count["n"] == 1
    assert len(results) == 2
    assert results[0] == results[1]


def test_host_transfer_lock_serializes_different_firmware(tmp_path, monkeypatch):
    primary = tmp_path / "cache"
    primary.mkdir()
    monkeypatch.setattr(
        "backend.agent.firmware_cache._mount_fstype_for_path", lambda _p: "ext4"
    )
    monkeypatch.setattr(
        "backend.agent.firmware_cache.get_aee_local_root",
        lambda: tmp_path / "aee",
    )
    (tmp_path / "aee").mkdir()
    monkeypatch.setattr(
        "backend.agent.firmware_cache.get_disk_usage", lambda _p: _disk()
    )
    monkeypatch.setattr("backend.agent.firmware_cache._st_dev", lambda _p: 1)

    src_a = _sealed_source(tmp_path / "A", {PROFILE_MTK: [("a.bin", b"aaa")]})
    src_b = _sealed_source(tmp_path / "B", {PROFILE_MTK: [("b.bin", b"bbb")]})
    cfg = _settings(stp_firmware_cache_dir=str(primary))
    timeline: list[str] = []
    lock = threading.Lock()
    real_copy = __import__(
        "backend.agent.firmware_cache", fromlist=["_copy_files_streaming"]
    )._copy_files_streaming

    def slow_copy(*args, **kwargs):
        with lock:
            timeline.append("copy-start")
        time.sleep(0.08)
        out = real_copy(*args, **kwargs)
        with lock:
            timeline.append("copy-end")
        return out

    monkeypatch.setattr(
        "backend.agent.firmware_cache._copy_files_streaming", slow_copy
    )
    barrier = threading.Barrier(2)

    def worker(src):
        barrier.wait()
        with ensure_firmware(
            src, PROFILE_MTK, permit_client=_fake_permit(), settings=cfg
        ):
            pass

    threads = [
        threading.Thread(target=worker, args=(src_a,)),
        threading.Thread(target=worker, args=(src_b,)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)
    # 串行：不应出现 copy-start, copy-start, copy-end, copy-end
    assert timeline == ["copy-start", "copy-end", "copy-start", "copy-end"]


def test_lock_downgrade_reentry_and_progress_callback(tmp_path, monkeypatch):
    primary = tmp_path / "cache"
    primary.mkdir()
    monkeypatch.setattr(
        "backend.agent.firmware_cache._mount_fstype_for_path", lambda _p: "ext4"
    )
    monkeypatch.setattr(
        "backend.agent.firmware_cache.get_aee_local_root",
        lambda: tmp_path / "aee",
    )
    (tmp_path / "aee").mkdir()
    monkeypatch.setattr(
        "backend.agent.firmware_cache.get_disk_usage", lambda _p: _disk()
    )
    monkeypatch.setattr("backend.agent.firmware_cache._st_dev", lambda _p: 1)

    src = _sealed_source(
        tmp_path / "fw", {PROFILE_MTK: [("x.bin", b"payload-data")]}
    )
    cfg = _settings(stp_firmware_cache_dir=str(primary))
    stages: list[str] = []

    def on_progress(**kwargs):
        stages.append(kwargs.get("stage", ""))

    # 先正常写入
    with ensure_firmware(
        src,
        PROFILE_MTK,
        permit_client=_fake_permit(),
        settings=cfg,
        progress_callback=on_progress,
    ) as r:
        entry = r.local_dir
        assert r.hit is False
    assert "firmware-stage-copy" in stages

    # 模拟降级窗口：第一次 SH 复核失败（临时移走 ready），随后恢复并重入备货
    ready = entry / ".stp-fw-ready.json"
    hidden = entry / ".stp-fw-ready.json.hidden"
    calls = {"n": 0}
    real_verify = __import__(
        "backend.agent.firmware_cache", fromlist=["_verify_ready_under_sh"]
    )._verify_ready_under_sh

    def flaky_verify(entry_dir, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1 and ready.is_file():
            ready.rename(hidden)
            ok = real_verify(entry_dir, **kwargs)
            hidden.rename(ready)
            return ok
        return real_verify(entry_dir, **kwargs)

    monkeypatch.setattr(
        "backend.agent.firmware_cache._verify_ready_under_sh", flaky_verify
    )
    with ensure_firmware(
        src, PROFILE_MTK, permit_client=_fake_permit(), settings=cfg
    ) as r2:
        assert r2.local_dir.exists()
    assert calls["n"] >= 2


# ── 5. Pin 保护 ───────────────────────────────────────────────────────────────


def test_pin_protects_from_lru_and_ttl(tmp_path, monkeypatch):
    """跨进程 pin：刷机子进程持 LOCK_SH 时，Agent 清理线程不得删该条目。"""
    import fcntl
    import multiprocessing as mp

    primary = tmp_path / "cache"
    primary.mkdir()
    monkeypatch.setattr(
        "backend.agent.firmware_cache._mount_fstype_for_path", lambda _p: "ext4"
    )
    monkeypatch.setattr(
        "backend.agent.firmware_cache.get_aee_local_root",
        lambda: tmp_path / "aee",
    )
    (tmp_path / "aee").mkdir()
    monkeypatch.setattr(
        "backend.agent.firmware_cache.get_disk_usage", lambda _p: _disk()
    )
    monkeypatch.setattr("backend.agent.firmware_cache._st_dev", lambda _p: 1)
    cfg = _settings(
        stp_firmware_cache_dir=str(primary),
        stp_firmware_cache_ttl_days=0.0,  # 立即过期
    )
    monkeypatch.setattr(
        "backend.agent.firmware_cache.get_firmware_cache_settings",
        lambda: cfg,
    )

    src_old = _sealed_source(tmp_path / "old", {PROFILE_MTK: [("o.bin", b"old")]})
    src_pin = _sealed_source(tmp_path / "pin", {PROFILE_MTK: [("p.bin", b"pin")]})

    with ensure_firmware(
        src_old, PROFILE_MTK, permit_client=_fake_permit(), settings=cfg
    ) as old_r:
        old_dir = old_r.local_dir
    with ensure_firmware(
        src_pin, PROFILE_MTK, permit_client=_fake_permit(), settings=cfg
    ) as pin_r:
        pin_dir = pin_r.local_dir
    os.utime(old_dir / ".stp-fw-ready.json", (1, 1))

    ready = mp.Event()
    done = mp.Event()

    def _hold_sh(lock_path: str, ready_evt: mp.Event, done_evt: mp.Event) -> None:
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_SH)
            ready_evt.set()
            done_evt.wait(timeout=30)
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(fd)

    lock_path = str(pin_dir / ".entry.lock")
    holder = mp.Process(target=_hold_sh, args=(lock_path, ready, done))
    holder.start()
    assert ready.wait(timeout=5)
    try:
        FirmwareCacheManager.instance().configure().run_cleanup_once()
        assert pin_dir.exists()
        assert not old_dir.exists()
    finally:
        done.set()
        holder.join(timeout=5)
        if holder.is_alive():
            holder.terminate()


# ── 6. 崩溃残留 staging 清扫 ──────────────────────────────────────────────────


def test_cleanup_sweeps_orphaned_staging(tmp_path, monkeypatch):
    primary = tmp_path / "cache"
    staging = primary / ".staging"
    staging.mkdir(parents=True)
    orphan = staging / "key.tmp.999999"
    orphan.mkdir()
    (orphan / "junk").write_bytes(b"x")
    monkeypatch.setattr(
        "backend.agent.firmware_cache._mount_fstype_for_path", lambda _p: "ext4"
    )
    monkeypatch.setattr(
        "backend.agent.firmware_cache.get_aee_local_root",
        lambda: tmp_path / "aee",
    )
    (tmp_path / "aee").mkdir()
    monkeypatch.setattr(
        "backend.agent.firmware_cache.get_disk_usage", lambda _p: _disk()
    )
    monkeypatch.setattr(
        "backend.agent.firmware_cache.resolve_cache_roots",
        lambda **_k: (primary, None),
    )
    FirmwareCacheManager.instance().configure().run_cleanup_once()
    assert not orphan.exists()


# ── 7. 空间耗尽 ───────────────────────────────────────────────────────────────


def test_insufficient_space_fail_closed_and_fallback(tmp_path, monkeypatch):
    primary = tmp_path / "ssd"
    secondary = tmp_path / "hdd"
    primary.mkdir()
    secondary.mkdir()
    monkeypatch.setattr(
        "backend.agent.firmware_cache._mount_fstype_for_path", lambda _p: "ext4"
    )
    monkeypatch.setattr(
        "backend.agent.firmware_cache.get_aee_local_root",
        lambda: tmp_path / "aee",
    )
    (tmp_path / "aee").mkdir()
    monkeypatch.setattr(
        "backend.agent.firmware_cache._st_dev",
        lambda p: 1 if "ssd" in str(p) else 2,
    )
    monkeypatch.setattr(
        "backend.agent.firmware_cache.get_disk_usage",
        lambda _p: _disk(total=100.0, used=95.0, avail=1.0, writable=95.0),
    )
    src = _sealed_source(
        tmp_path / "fw", {PROFILE_MTK: [("big.bin", b"0123456789" * 100)]}
    )
    cfg = _settings(
        stp_firmware_cache_dir=str(primary),
        stp_firmware_cache_fallback_dir=str(secondary),
        stp_firmware_cache_reserve_gb=20.0,
        stp_firmware_cache_max_gb=1.0,
        stp_firmware_cache_max_pct=1.0,
    )
    with pytest.raises(FirmwareCacheError) as ei:
        with ensure_firmware(
            src,
            PROFILE_MTK,
            permit_client=_fake_permit(),
            settings=cfg,
            max_retries=1,
        ):
            pass
    assert ei.value.code == CODE_INSUFFICIENT_SPACE

    with ensure_firmware(
        src,
        PROFILE_MTK,
        permit_client=_fake_permit(),
        settings=cfg,
        allow_nfs_direct_fallback=True,
        max_retries=1,
    ) as r:
        assert r.tier == "nfs_direct"
        assert r.fallback_reason == CODE_INSUFFICIENT_SPACE
        assert r.local_dir == src


# ── 8. Settings 热重载 ────────────────────────────────────────────────────────


def test_firmware_cache_settings_hot_reload(monkeypatch):
    reset_agent_settings_caches()
    monkeypatch.setenv("STP_FIRMWARE_CACHE_RESERVE_GB", "20")
    reset_agent_settings_caches()
    assert get_firmware_cache_settings().stp_firmware_cache_reserve_gb == 20.0
    monkeypatch.setenv("STP_FIRMWARE_CACHE_RESERVE_GB", "25")
    # 未清缓存 → 旧值
    assert get_firmware_cache_settings().stp_firmware_cache_reserve_gb == 20.0
    reset_agent_settings_caches()
    assert get_firmware_cache_settings().stp_firmware_cache_reserve_gb == 25.0
    monkeypatch.delenv("STP_FIRMWARE_CACHE_RESERVE_GB", raising=False)
    reset_agent_settings_caches()
