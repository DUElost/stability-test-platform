"""B10 U2：Agent L1 固件缓存基座必测反例（#4086 §4 + 复核 M1–M8）。"""

from __future__ import annotations

import fcntl
import json
import os
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
    FirmwareManifestError,
    compute_content_digest,
    dumps_staging_manifest,
)
from backend.agent.firmware_cache import (
    CODE_COPY_IO,
    CODE_DOWNGRADE_RACE,
    CODE_ENUM_FAILED,
    CODE_INSUFFICIENT_SPACE,
    CODE_PATH_ESCAPE,
    CODE_PROFILE_NOT_FOUND,
    CODE_UNSEALED_NOT_SUPPORTED,
    FIRMWARE_CACHE_API_VERSION,
    FirmwareCacheError,
    FirmwareCacheManager,
    READY_MARKER,
    _entry_lock_path,
    _safe_rmtree,
    _sweep_staging,
    ensure_firmware,
    host_transfer_lock_path,
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


def _sealed_source(root: Path, profiles: dict[str, list[tuple[str, bytes]]]) -> Path:
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


def _mount_ok(monkeypatch):
    monkeypatch.setattr(
        "backend.agent.firmware_cache._mount_fstype_for_path", lambda _p: "ext4"
    )
    monkeypatch.setattr(
        "backend.agent.firmware_cache._probe_fstype", lambda _p: "ext4"
    )


# ── 1 / M8：真实引擎双路径 PYTHONPATH ─────────────────────────────────────────


def test_dual_pythonpath_imports_agent_and_config(tmp_path, monkeypatch):
    """M8：经真实 ``_run_script_action`` 启动干净子进程，双导入均可用。"""
    from types import SimpleNamespace as NS

    from backend.agent.pipeline_engine import PipelineEngine

    script = tmp_path / "probe_imports.py"
    script.write_text(
        "\n".join(
            [
                "from agent.firmware_cache import FIRMWARE_CACHE_API_VERSION as v",
                "from config import AGENT_DIR",
                "import json",
                "print(json.dumps({'v': v, 'agent_dir': str(AGENT_DIR)}))",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("PYTHONPATH", raising=False)

    class _Reg:
        def resolve(self, name, version):
            return NS(
                script_id=1,
                name=name,
                version=version,
                script_type="python",
                nfs_path=str(script),
                content_sha256="c" * 64,
                package_sha256=None,
            )

    import backend.agent.script_packages as sp

    monkeypatch.setattr(
        "backend.agent.pipeline_engine.resolve_script_path",
        lambda entry: sp.ResolvedScript(
            path=entry.nfs_path, cwd=str(Path(entry.nfs_path).parent)
        ),
    )
    monkeypatch.setattr(
        "backend.agent.pipeline_engine.inject_required_tools",
        lambda *_a, **_k: None,
    )

    engine = PipelineEngine(
        adb=NS(adb_path="adb"),
        serial="S1",
        run_id=1,
        script_registry=_Reg(),
    )
    ctx = NS(
        serial="S1",
        adb_path="adb",
        log_dir=str(tmp_path / "logs"),
        params={},
        nfs_root="",
        job_id=1,
        phase="init",
    )
    (tmp_path / "logs").mkdir()
    result = engine._run_script_action(
        ctx,
        {
            "step_id": "probe",
            "action": "script:probe_imports",
            "version": "1.0.0",
            "params": {},
            "timeout_seconds": 10,
        },
    )
    assert result.success, result.error_message
    # 再跑一次：脚本打印 PYTHONPATH；引擎日志名用 action 名（非 step_id）。
    import json as _json

    import backend.agent.pipeline_engine as pe

    agent_dir = Path(pe.__file__).resolve().parent
    script.write_text(
        "import os, json\n"
        "from agent.firmware_cache import FIRMWARE_CACHE_API_VERSION as v\n"
        "from config import AGENT_DIR\n"
        "print(json.dumps({'v': int(v), 'pp': os.environ.get('PYTHONPATH','')}))\n",
        encoding="utf-8",
    )
    result2 = engine._run_script_action(
        ctx,
        {
            "step_id": "probe",
            "action": "script:probe_imports",
            "version": "1.0.0",
            "params": {},
            "timeout_seconds": 10,
        },
    )
    assert result2.success, result2.error_message
    out_log = tmp_path / "logs" / "init_probe_imports.out.log"
    payload = _json.loads(out_log.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert payload["v"] == FIRMWARE_CACHE_API_VERSION
    pp_parts = payload["pp"].split(os.pathsep)
    assert pp_parts[0] == str(agent_dir)
    assert pp_parts[1] == str(agent_dir.parent)


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

    err = inject_required_tools(pkg, {})
    assert err is not None
    assert "required_agent_capability_missing" in err
    assert "missing_cap_x" in err

    (pkg / "capabilities.json").write_text(
        json.dumps({"requires_agent_capabilities": [FIRMWARE_CACHE_V1]}),
        encoding="utf-8",
    )
    assert inject_required_tools(pkg, {}) is None

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


# ── 2 / M5：拓扑与拒绝顺序 ────────────────────────────────────────────────────


def test_topology_and_profile_matrix(tmp_path, monkeypatch):
    primary = tmp_path / "ssd" / "firmware_cache"
    secondary = tmp_path / "hdd" / "stp_firmware_cache"
    aee = tmp_path / "hdd" / "aee_events"
    aee.mkdir(parents=True)

    monkeypatch.setattr(
        "backend.agent.firmware_cache._st_dev",
        lambda p: 1 if "ssd" in str(p) else 2,
    )
    _mount_ok(monkeypatch)
    monkeypatch.setattr(
        "backend.agent.firmware_cache.get_aee_local_root", lambda: aee
    )
    monkeypatch.setattr(
        "backend.agent.firmware_cache.is_ssd_fallback_root", lambda _p: False
    )
    monkeypatch.setattr(
        "backend.agent.firmware_cache.get_disk_usage", lambda _p: _disk()
    )

    cfg = _settings(
        stp_firmware_cache_dir=str(primary),
        stp_firmware_cache_fallback_dir=str(secondary),
    )
    p, s = resolve_cache_roots(settings=cfg)
    assert p == primary
    assert s == secondary

    monkeypatch.setattr("backend.agent.firmware_cache._st_dev", lambda _p: 9)
    p2, s2 = resolve_cache_roots(settings=cfg)
    assert p2 == primary and s2 is None

    # M5：tmpfs 拒绝且拒绝前零目录创建
    nfs_root = tmp_path / "never_create_nfs"
    assert not nfs_root.exists()
    monkeypatch.setattr(
        "backend.agent.firmware_cache._probe_fstype", lambda _p: "tmpfs"
    )
    monkeypatch.setattr(
        "backend.agent.firmware_cache._mount_fstype_for_path", lambda _p: "tmpfs"
    )
    with pytest.raises(FirmwareCacheError) as ei:
        resolve_cache_roots(
            settings=_settings(stp_firmware_cache_dir=str(nfs_root))
        )
    assert ei.value.code == "firmware_cache_root_rejected"
    assert not nfs_root.exists()

    _mount_ok(monkeypatch)
    monkeypatch.setattr("backend.agent.firmware_cache._st_dev", lambda _p: 1)

    # 二级 st_dev=None / 不可用 → 跳过二级，不阻断一级
    sec = tmp_path / "sec_denied"
    cfg2 = _settings(
        stp_firmware_cache_dir=str(tmp_path / "pri_ok"),
        stp_firmware_cache_fallback_dir=str(sec),
    )
    monkeypatch.setattr(
        "backend.agent.firmware_cache._st_dev",
        lambda p: None if "sec_denied" in str(p) else 1,
    )
    p3, s3 = resolve_cache_roots(settings=cfg2)
    assert p3.exists() and s3 is None

    # 二级 st_dev=None → 跳过
    monkeypatch.setattr(
        "backend.agent.firmware_cache._st_dev",
        lambda p: 1 if "pri" in str(p) or "pri_ok" in str(p) else None,
    )
    cfg3 = _settings(
        stp_firmware_cache_dir=str(tmp_path / "pri2"),
        stp_firmware_cache_fallback_dir=str(tmp_path / "sec2"),
    )
    p4, s4 = resolve_cache_roots(settings=cfg3)
    assert p4.exists() and s4 is None

    _mount_ok(monkeypatch)
    monkeypatch.setattr("backend.agent.firmware_cache._st_dev", lambda _p: 1)

    src = _sealed_source(
        tmp_path / "fw_sealed",
        {PROFILE_UNISOC_FASTBOOT: [("images/x.img", b"fastboot-bytes")]},
    )
    with pytest.raises(FirmwareCacheError) as ei:
        resolve_cache_identity(src, PROFILE_UNISOC_BSL)
    assert ei.value.code == CODE_PROFILE_NOT_FOUND

    unsealed = tmp_path / "fw_unsealed"
    unsealed.mkdir()
    (unsealed / "a.bin").write_bytes(b"abc")
    key_m, sealed_m, *_ = resolve_cache_identity(unsealed, PROFILE_MTK)
    assert sealed_m is False and "_unsealed_" in key_m
    key_f, sealed_f, *_ = resolve_cache_identity(unsealed, PROFILE_UNISOC_FASTBOOT)
    assert sealed_f is False and key_m == key_f
    with pytest.raises(FirmwareCacheError) as ei:
        resolve_cache_identity(unsealed, PROFILE_UNISOC_BSL)
    assert ei.value.code == CODE_UNSEALED_NOT_SUPPORTED


# ── 3：命中零读源 + 双 profile ────────────────────────────────────────────────


def test_sealed_hit_skips_source_image_open_and_profiles_isolated(
    tmp_path, monkeypatch
):
    primary = tmp_path / "cache"
    _mount_ok(monkeypatch)
    monkeypatch.setattr(
        "backend.agent.firmware_cache.get_aee_local_root",
        lambda: tmp_path / "aee",
    )
    (tmp_path / "aee").mkdir()
    monkeypatch.setattr(
        "backend.agent.firmware_cache.get_disk_usage", lambda _p: _disk()
    )
    monkeypatch.setattr("backend.agent.firmware_cache._st_dev", lambda _p: 1)

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
        src, PROFILE_UNISOC_FASTBOOT, permit_client=permit, settings=cfg
    ) as r1:
        assert r1.hit is False and r1.sealed is True
        fastboot_key = r1.cache_key

    with ensure_firmware(
        src, PROFILE_UNISOC_BSL, permit_client=permit, settings=cfg
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
        src, PROFILE_UNISOC_FASTBOOT, permit_client=permit, settings=cfg
    ) as hit:
        assert hit.hit is True
    assert opened_source_images == []


# ── 4 / M2 / M7：并发、降级重入、等待打戳 ─────────────────────────────────────


def test_concurrent_same_firmware_copies_once(tmp_path, monkeypatch):
    primary = tmp_path / "cache"
    _mount_ok(monkeypatch)
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
    import backend.agent.firmware_cache as fc

    real_copy = fc._copy_files_streaming

    def counting_copy(*args, **kwargs):
        copy_count["n"] += 1
        time.sleep(0.05)
        return real_copy(*args, **kwargs)

    monkeypatch.setattr(fc, "_copy_files_streaming", counting_copy)
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
    assert len(results) == 2 and results[0] == results[1]


def test_host_transfer_lock_serializes_different_firmware(tmp_path, monkeypatch):
    primary = tmp_path / "cache"
    _mount_ok(monkeypatch)
    monkeypatch.setattr(
        "backend.agent.firmware_cache.get_aee_local_root",
        lambda: tmp_path / "aee",
    )
    (tmp_path / "aee").mkdir()
    monkeypatch.setattr(
        "backend.agent.firmware_cache.get_disk_usage", lambda _p: _disk()
    )
    monkeypatch.setattr("backend.agent.firmware_cache._st_dev", lambda _p: 1)
    monkeypatch.setenv("STP_AGENT_INSTALL_DIR", str(tmp_path / "install"))
    (tmp_path / "install").mkdir()

    src_a = _sealed_source(tmp_path / "A", {PROFILE_MTK: [("a.bin", b"aaa")]})
    src_b = _sealed_source(tmp_path / "B", {PROFILE_MTK: [("b.bin", b"bbb")]})
    cfg = _settings(stp_firmware_cache_dir=str(primary))
    timeline: list[str] = []
    lock = threading.Lock()
    import backend.agent.firmware_cache as fc

    real_copy = fc._copy_files_streaming

    def slow_copy(*args, **kwargs):
        with lock:
            timeline.append("copy-start")
        time.sleep(0.08)
        out = real_copy(*args, **kwargs)
        with lock:
            timeline.append("copy-end")
        return out

    monkeypatch.setattr(fc, "_copy_files_streaming", slow_copy)
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
    assert timeline == ["copy-start", "copy-end", "copy-start", "copy-end"]


def test_cold_pull_downgrade_race_reenters(tmp_path, monkeypatch):
    """M2：冷拉首次降级窗口移走 ready → 自动第二轮重入；取消复核则翻红。"""
    primary = tmp_path / "cache"
    _mount_ok(monkeypatch)
    monkeypatch.setattr(
        "backend.agent.firmware_cache.get_aee_local_root",
        lambda: tmp_path / "aee",
    )
    (tmp_path / "aee").mkdir()
    monkeypatch.setattr(
        "backend.agent.firmware_cache.get_disk_usage", lambda _p: _disk()
    )
    monkeypatch.setattr("backend.agent.firmware_cache._st_dev", lambda _p: 1)
    monkeypatch.setenv("STP_AGENT_INSTALL_DIR", str(tmp_path / "install"))
    (tmp_path / "install").mkdir()

    src = _sealed_source(
        tmp_path / "fw", {PROFILE_MTK: [("x.bin", b"payload-data")]}
    )
    cfg = _settings(stp_firmware_cache_dir=str(primary))
    import backend.agent.firmware_cache as fc

    calls = {"n": 0}
    real_verify = fc._verify_ready_under_sh

    def flaky_after_cold_pull(entry_dir, **kwargs):
        # 仅在冷拉降级后的第一次持 SH 复核失败（此时 entry 刚 replace 完成）
        calls["n"] += 1
        ready = entry_dir / ".stp-fw-ready.json"
        # 第二次调用起：第一次是 EX 下双检（通常无 ready），之后冷拉完成的 SH 复核
        if calls["n"] == 2 and ready.is_file():
            hidden = entry_dir / ".stp-fw-ready.json.hidden"
            ready.rename(hidden)
            ok = real_verify(entry_dir, **kwargs)
            # 不立刻恢复——迫使 retryable 重入；重入拷贝前再放回由下一轮 populate 重写
            # 为让第二轮能成功，在返回后短暂恢复
            def _restore():
                time.sleep(0.05)
                if hidden.exists() and not ready.exists():
                    hidden.rename(ready)

            threading.Thread(target=_restore, daemon=True).start()
            return ok
        return real_verify(entry_dir, **kwargs)

    monkeypatch.setattr(fc, "_verify_ready_under_sh", flaky_after_cold_pull)
    with ensure_firmware(
        src, PROFILE_MTK, permit_client=_fake_permit(), settings=cfg, max_retries=3
    ) as r:
        assert r.local_dir.exists()
    assert calls["n"] >= 2


def test_lock_wait_emits_periodic_progress(tmp_path, monkeypatch):
    """M7：阻塞等待 entry/host 锁期间多次 firmware-stage-wait 回调。"""
    primary = tmp_path / "cache"
    _mount_ok(monkeypatch)
    monkeypatch.setattr(
        "backend.agent.firmware_cache.get_aee_local_root",
        lambda: tmp_path / "aee",
    )
    (tmp_path / "aee").mkdir()
    monkeypatch.setattr(
        "backend.agent.firmware_cache.get_disk_usage", lambda _p: _disk()
    )
    monkeypatch.setattr("backend.agent.firmware_cache._st_dev", lambda _p: 1)
    monkeypatch.setenv("STP_AGENT_INSTALL_DIR", str(tmp_path / "install"))
    (tmp_path / "install").mkdir()
    monkeypatch.setattr(
        "backend.agent.firmware_cache._LOCK_WAIT_POLL_SECONDS", 0.05
    )

    src = _sealed_source(tmp_path / "fw", {PROFILE_MTK: [("a.bin", b"aa")]})
    cfg = _settings(stp_firmware_cache_dir=str(primary))
    # 预占 host-transfer 锁约 0.4s
    hpath = host_transfer_lock_path()
    hpath.parent.mkdir(parents=True, exist_ok=True)
    hfd = os.open(str(hpath), os.O_RDWR | os.O_CREAT, 0o644)
    fcntl.flock(hfd, fcntl.LOCK_EX)
    stages: list[str] = []

    def on_progress(**kwargs):
        stages.append(kwargs.get("stage", ""))

    def releaser():
        time.sleep(0.35)
        fcntl.flock(hfd, fcntl.LOCK_UN)
        os.close(hfd)

    threading.Thread(target=releaser, daemon=True).start()
    with ensure_firmware(
        src,
        PROFILE_MTK,
        permit_client=_fake_permit(),
        settings=cfg,
        progress_callback=on_progress,
    ):
        pass
    wait_count = stages.count("firmware-stage-wait")
    assert wait_count >= 2, stages


def test_host_transfer_serial_across_hot_swapped_roots(tmp_path, monkeypatch):
    """M7：热切一级缓存根后，两传输仍经主机级 host-transfer 锁串行。"""
    _mount_ok(monkeypatch)
    monkeypatch.setattr(
        "backend.agent.firmware_cache.get_aee_local_root",
        lambda: tmp_path / "aee",
    )
    (tmp_path / "aee").mkdir()
    monkeypatch.setattr(
        "backend.agent.firmware_cache.get_disk_usage", lambda _p: _disk()
    )
    monkeypatch.setattr("backend.agent.firmware_cache._st_dev", lambda _p: 1)
    monkeypatch.setenv("STP_AGENT_INSTALL_DIR", str(tmp_path / "install"))
    (tmp_path / "install").mkdir()

    root_a = tmp_path / "cache_a"
    root_b = tmp_path / "cache_b"
    src_a = _sealed_source(tmp_path / "A", {PROFILE_MTK: [("a.bin", b"aaa")]})
    src_b = _sealed_source(tmp_path / "B", {PROFILE_MTK: [("b.bin", b"bbb")]})
    timeline: list[str] = []
    lock = threading.Lock()
    import backend.agent.firmware_cache as fc

    real_copy = fc._copy_files_streaming

    def slow_copy(*args, **kwargs):
        with lock:
            timeline.append("copy-start")
        time.sleep(0.1)
        out = real_copy(*args, **kwargs)
        with lock:
            timeline.append("copy-end")
        return out

    monkeypatch.setattr(fc, "_copy_files_streaming", slow_copy)
    barrier = threading.Barrier(2)

    def worker(src, root):
        barrier.wait()
        with ensure_firmware(
            src,
            PROFILE_MTK,
            permit_client=_fake_permit(),
            settings=_settings(stp_firmware_cache_dir=str(root)),
        ):
            pass

    threads = [
        threading.Thread(target=worker, args=(src_a, root_a)),
        threading.Thread(target=worker, args=(src_b, root_b)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)
    assert timeline == ["copy-start", "copy-end", "copy-start", "copy-end"]


# ── 5 / M1：真实冷拉 pin ──────────────────────────────────────────────────────


def test_real_cold_pull_pin_blocks_ttl_and_lru(tmp_path, monkeypatch):
    """M1：真实 ensure 持 SH 时 TTL/LRU 不得删；绕过 LRU 的 NB 判定必须翻红。"""
    primary = tmp_path / "cache"
    _mount_ok(monkeypatch)
    monkeypatch.setattr(
        "backend.agent.firmware_cache.get_aee_local_root",
        lambda: tmp_path / "aee",
    )
    (tmp_path / "aee").mkdir()
    monkeypatch.setattr(
        "backend.agent.firmware_cache.get_disk_usage", lambda _p: _disk()
    )
    monkeypatch.setattr("backend.agent.firmware_cache._st_dev", lambda _p: 1)
    monkeypatch.setenv("STP_AGENT_INSTALL_DIR", str(tmp_path / "install"))
    (tmp_path / "install").mkdir()

    cfg = _settings(
        stp_firmware_cache_dir=str(primary),
        stp_firmware_cache_ttl_days=0.0,
    )
    monkeypatch.setattr(
        "backend.agent.firmware_cache.get_firmware_cache_settings", lambda: cfg
    )

    src_old = _sealed_source(tmp_path / "old", {PROFILE_MTK: [("o.bin", b"old")]})
    src_pin = _sealed_source(tmp_path / "pin", {PROFILE_MTK: [("p.bin", b"pin")]})

    with ensure_firmware(
        src_old, PROFILE_MTK, permit_client=_fake_permit(), settings=cfg
    ) as old_r:
        old_dir = old_r.local_dir
    os.utime(old_dir / ".stp-fw-ready.json", (1, 1))

    with ensure_firmware(
        src_pin, PROFILE_MTK, permit_client=_fake_permit(), settings=cfg
    ) as pin_r:
        pin_dir = pin_r.local_dir
        # 持有真实冷拉上下文的 LOCK_SH（稳定 .locks inode）
        FirmwareCacheManager.instance().configure().run_cleanup_once()
        assert pin_dir.exists()
        assert not old_dir.exists()

        # 写入 LRU 也应跳过被 pin 条目
        import backend.agent.firmware_cache as fc

        before = list(pin_dir.iterdir())
        fc._evict_one_unlocked_entry(primary, protect_keys=set())
        assert pin_dir.exists()
        assert list(pin_dir.iterdir()) == before


def test_lru_nb_bypass_must_fail_when_pinned(tmp_path, monkeypatch):
    """区分度：绕过写入 LRU 的 LOCK_EX|NB 时，pin 测试必须翻红。"""
    primary = tmp_path / "cache"
    _mount_ok(monkeypatch)
    monkeypatch.setattr(
        "backend.agent.firmware_cache.get_aee_local_root",
        lambda: tmp_path / "aee",
    )
    (tmp_path / "aee").mkdir()
    monkeypatch.setattr(
        "backend.agent.firmware_cache.get_disk_usage", lambda _p: _disk()
    )
    monkeypatch.setattr("backend.agent.firmware_cache._st_dev", lambda _p: 1)
    monkeypatch.setenv("STP_AGENT_INSTALL_DIR", str(tmp_path / "install"))
    (tmp_path / "install").mkdir()
    cfg = _settings(stp_firmware_cache_dir=str(primary))
    src = _sealed_source(tmp_path / "pin", {PROFILE_MTK: [("p.bin", b"pin")]})

    with ensure_firmware(
        src, PROFILE_MTK, permit_client=_fake_permit(), settings=cfg
    ) as pin_r:
        pin_dir = pin_r.local_dir
        import backend.agent.firmware_cache as fc

        monkeypatch.setattr(fc, "_try_flock", lambda *_a, **_k: True)
        # 绕过 NB 后应能「误删」——断言：若实现仍跳过则本区分度用例无意义；
        # 这里验证绕过后果：删除成功 → pin 消失（证明 NB 是必要守卫）。
        deleted = fc._evict_one_unlocked_entry(primary, protect_keys=set())
        assert deleted is True
        assert not pin_dir.exists()


# ── 6 / M3：staging 保护与越界 ────────────────────────────────────────────────


def test_cleanup_sweeps_orphaned_staging(tmp_path, monkeypatch):
    primary = tmp_path / "cache"
    staging = primary / ".staging"
    staging.mkdir(parents=True)
    (primary / ".locks").mkdir(parents=True)
    orphan = staging / "key.tmp.999999"
    orphan.mkdir()
    (orphan / "junk").write_bytes(b"x")
    _mount_ok(monkeypatch)
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


def test_active_staging_not_swept_while_writer_holds_lock(tmp_path, monkeypatch):
    """M3：写者持条目锁停在拷贝后/ready 前，清理不得删其 staging。"""
    primary = tmp_path / "cache"
    primary.mkdir()
    (primary / ".locks").mkdir()
    (primary / ".staging").mkdir()
    key = "active_key"
    staging = primary / ".staging" / f"{key}.tmp.12345"
    staging.mkdir()
    (staging / "part.bin").write_bytes(b"partial")
    lock_path = _entry_lock_path(primary, key)
    fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o644)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        _sweep_staging(primary)
        assert staging.exists()
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def test_staging_symlink_escape_blocked(tmp_path):
    """M3：软链指向缓存根外 → 删除被拦截。"""
    primary = tmp_path / "cache"
    outside = tmp_path / "outside"
    outside.mkdir()
    victim = outside / "secret"
    victim.write_bytes(b"x")
    staging = primary / ".staging"
    staging.mkdir(parents=True)
    link = staging / "evil.tmp.1"
    link.symlink_to(outside)
    with pytest.raises(FirmwareCacheError) as ei:
        _safe_rmtree(link, primary, expected_key="evil")
    assert ei.value.code == CODE_PATH_ESCAPE
    assert victim.exists()


def test_safe_rmtree_rejects_root_itself(tmp_path):
    """M3：目标解析到缓存根本身 → 拦截。"""
    primary = tmp_path / "cache"
    primary.mkdir()
    (primary / ".staging").mkdir()
    with pytest.raises(FirmwareCacheError) as ei:
        _safe_rmtree(primary, primary, expected_key="cache")
    assert ei.value.code == CODE_PATH_ESCAPE


def test_safe_rmtree_rejects_other_entry_key(tmp_path):
    """M3：持锁 key=A 不得删除字面指向条目 B。"""
    primary = tmp_path / "cache"
    primary.mkdir()
    entry_b = primary / "key_b"
    entry_b.mkdir()
    (entry_b / "f.bin").write_bytes(b"keep")
    with pytest.raises(FirmwareCacheError) as ei:
        _safe_rmtree(entry_b, primary, expected_key="key_a")
    assert ei.value.code == CODE_PATH_ESCAPE
    assert entry_b.exists()
    assert (entry_b / "f.bin").read_bytes() == b"keep"


def test_safe_rmtree_rejects_staging_ancestor_alias_to_other_entry(
    tmp_path, monkeypatch
):
    """M3：``.staging`` 软链指向同根另一条目 → 拒绝且不实际删除。"""
    primary = tmp_path / "cache"
    primary.mkdir()
    pinned = primary / "pinned_other"
    pinned.mkdir()
    victim = pinned / "keep.bin"
    victim.write_bytes(b"pin")
    staging_link = primary / ".staging"
    staging_link.symlink_to(pinned)
    fake_child = staging_link / "key_a.tmp.1"
    fake_child.mkdir()
    (fake_child / "partial").write_bytes(b"x")

    deleted: list[str] = []
    import backend.agent.firmware_cache as fc

    def no_rmtree(path, *a, **k):
        deleted.append(str(path))
        raise AssertionError(f"rmtree must not run: {path}")

    monkeypatch.setattr(fc.shutil, "rmtree", no_rmtree)
    with pytest.raises(FirmwareCacheError) as ei:
        _safe_rmtree(fake_child, primary, expected_key="key_a")
    assert ei.value.code == CODE_PATH_ESCAPE
    assert deleted == []
    assert victim.read_bytes() == b"pin"


def test_safe_rmtree_rejects_same_key_leaf_symlink(tmp_path, monkeypatch):
    """M3：同根同 key 末级软链目标 → 拒绝且不实际删除。"""
    primary = tmp_path / "cache"
    primary.mkdir()
    (primary / ".staging").mkdir()
    real_entry = primary / "key_a"
    real_entry.mkdir()
    (real_entry / "payload.bin").write_bytes(b"live")
    leaf = primary / ".staging" / "key_a.tmp.9"
    leaf.symlink_to(real_entry)

    deleted: list[str] = []
    import backend.agent.firmware_cache as fc

    def no_rmtree(path, *a, **k):
        deleted.append(str(path))
        raise AssertionError(f"rmtree must not run: {path}")

    def no_unlink(self):
        deleted.append(str(self))
        raise AssertionError(f"unlink must not run: {self}")

    monkeypatch.setattr(fc.shutil, "rmtree", no_rmtree)
    monkeypatch.setattr(Path, "unlink", no_unlink)
    with pytest.raises(FirmwareCacheError) as ei:
        _safe_rmtree(leaf, primary, expected_key="key_a")
    assert ei.value.code == CODE_PATH_ESCAPE
    assert deleted == []
    assert (real_entry / "payload.bin").read_bytes() == b"live"


# ── 7 / M4：空间耗尽与串行区水位 ──────────────────────────────────────────────


def test_insufficient_space_fail_closed_and_fallback(tmp_path, monkeypatch):
    primary = tmp_path / "ssd"
    secondary = tmp_path / "hdd"
    _mount_ok(monkeypatch)
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
    monkeypatch.setenv("STP_AGENT_INSTALL_DIR", str(tmp_path / "install"))
    (tmp_path / "install").mkdir()
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


def test_serial_zone_rechecks_space_and_evicts_only_target_disk(
    tmp_path, monkeypatch
):
    """M4：真实 ensure 入口——预检通过后许可期间水位跌破 → 写前复核腾退主盘。

    去掉许可后水位复核时 e1 不会被删，本断言必红。
    """
    primary = tmp_path / "ssd"
    secondary = tmp_path / "hdd"
    primary.mkdir()
    secondary.mkdir()
    sec_entry = secondary / "keep_me"
    sec_entry.mkdir()
    (sec_entry / ".stp-fw-ready.json").write_text(
        json.dumps({"content_digest": "sha256:x", "files": []}), encoding="utf-8"
    )
    _mount_ok(monkeypatch)
    monkeypatch.setattr(
        "backend.agent.firmware_cache.get_aee_local_root",
        lambda: tmp_path / "aee",
    )
    (tmp_path / "aee").mkdir()
    monkeypatch.setattr(
        "backend.agent.firmware_cache._st_dev",
        lambda p: 1 if "ssd" in str(p) else 2,
    )
    monkeypatch.setenv("STP_AGENT_INSTALL_DIR", str(tmp_path / "install"))
    (tmp_path / "install").mkdir()

    state = {"avail": 50.0}

    def usage(path):
        if "ssd" in str(path):
            return _disk(
                total=100.0, used=95.0, avail=state["avail"], writable=95.0
            )
        return _disk(total=100.0, used=10.0, avail=90.0, writable=10.0)

    monkeypatch.setattr("backend.agent.firmware_cache.get_disk_usage", usage)

    e1 = primary / "e1"
    e1.mkdir()
    (e1 / ".stp-fw-ready.json").write_text("{}", encoding="utf-8")
    os.utime(e1 / ".stp-fw-ready.json", (1, 1))

    import backend.agent.firmware_cache as fc

    real_rmtree = fc._safe_rmtree

    def tracking_rmtree(target, cache_root, *, expected_key):
        real_rmtree(target, cache_root, expected_key=expected_key)
        if "ssd" in str(cache_root) and Path(target).name == "e1":
            state["avail"] = 50.0

    monkeypatch.setattr(fc, "_safe_rmtree", tracking_rmtree)

    permit = MagicMock()

    def acquire_permit(**_kw):
        state["avail"] = 5.0  # 跌破 reserve_gb=20
        return TransferPermitHold(
            lease_token="t",
            host_id="h",
            pool="firmware_nfs",
            permit_degraded=False,
            _client=None,
        )

    permit.acquire_permit.side_effect = acquire_permit

    src = _sealed_source(tmp_path / "fw", {PROFILE_MTK: [("a.bin", b"payload")]})
    cfg = _settings(
        stp_firmware_cache_dir=str(primary),
        stp_firmware_cache_fallback_dir=str(secondary),
        stp_firmware_cache_reserve_gb=20.0,
        stp_firmware_cache_max_gb=60.0,
        stp_firmware_cache_max_pct=90.0,
    )
    with ensure_firmware(
        src, PROFILE_MTK, permit_client=permit, settings=cfg, max_retries=1
    ) as r:
        assert r.tier == "primary"
        assert (r.local_dir / "a.bin").is_file()
    assert not e1.exists(), "post-permit recheck must evict primary e1"
    assert sec_entry.exists(), "secondary entry must not be touched"


def test_secondary_locks_permission_error_skips_to_primary(tmp_path, monkeypatch):
    """M5：二级 .locks PermissionError → 跳过二级，一级冷拉仍成功。"""
    primary = tmp_path / "ssd"
    secondary = tmp_path / "hdd"
    secondary.mkdir()
    (secondary / ".locks").write_text("blocked", encoding="utf-8")
    _mount_ok(monkeypatch)
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
        "backend.agent.firmware_cache.get_disk_usage", lambda _p: _disk()
    )
    monkeypatch.setenv("STP_AGENT_INSTALL_DIR", str(tmp_path / "install"))
    (tmp_path / "install").mkdir()
    src = _sealed_source(tmp_path / "fw", {PROFILE_MTK: [("a.bin", b"ok")]})
    cfg = _settings(
        stp_firmware_cache_dir=str(primary),
        stp_firmware_cache_fallback_dir=str(secondary),
    )
    _p, s = resolve_cache_roots(settings=cfg)
    assert s is None
    with ensure_firmware(
        src, PROFILE_MTK, permit_client=_fake_permit(), settings=cfg
    ) as r:
        assert r.tier == "primary"
        assert r.hit is False


# ── 6 / M6：错误语义与完整性 ──────────────────────────────────────────────────


def test_copy_io_error_not_nfs_fallback(tmp_path, monkeypatch):
    """M6：拷贝 EIO 不得在 allow_nfs_direct_fallback 下降级。"""
    primary = tmp_path / "cache"
    _mount_ok(monkeypatch)
    monkeypatch.setattr(
        "backend.agent.firmware_cache.get_aee_local_root",
        lambda: tmp_path / "aee",
    )
    (tmp_path / "aee").mkdir()
    monkeypatch.setattr(
        "backend.agent.firmware_cache.get_disk_usage", lambda _p: _disk()
    )
    monkeypatch.setattr("backend.agent.firmware_cache._st_dev", lambda _p: 1)
    monkeypatch.setenv("STP_AGENT_INSTALL_DIR", str(tmp_path / "install"))
    (tmp_path / "install").mkdir()
    src = _sealed_source(tmp_path / "fw", {PROFILE_MTK: [("a.bin", b"data")]})
    cfg = _settings(stp_firmware_cache_dir=str(primary))

    def boom(*_a, **_k):
        raise FirmwareCacheError(CODE_COPY_IO, "EIO simulated")

    monkeypatch.setattr(
        "backend.agent.firmware_cache._copy_files_streaming", boom
    )
    with pytest.raises(FirmwareCacheError) as ei:
        with ensure_firmware(
            src,
            PROFILE_MTK,
            permit_client=_fake_permit(),
            settings=cfg,
            allow_nfs_direct_fallback=True,
            max_retries=1,
        ):
            pass
    assert ei.value.code == CODE_COPY_IO


def test_mixed_primary_space_secondary_ready_eio_not_nfs_fallback(
    tmp_path, monkeypatch
):
    """M6-2：主盘空间不足 + 二级写 ready 报 EIO → 不得误判 insufficient/NFS。"""
    import errno

    primary = tmp_path / "ssd"
    secondary = tmp_path / "hdd"
    _mount_ok(monkeypatch)
    monkeypatch.setattr(
        "backend.agent.firmware_cache.get_aee_local_root",
        lambda: tmp_path / "aee",
    )
    (tmp_path / "aee").mkdir()
    monkeypatch.setattr(
        "backend.agent.firmware_cache._st_dev",
        lambda p: 1 if "ssd" in str(p) else 2,
    )

    def usage(path):
        if "ssd" in str(path):
            return _disk(total=100.0, used=95.0, avail=1.0, writable=95.0)
        return _disk(total=100.0, used=10.0, avail=90.0, writable=10.0)

    monkeypatch.setattr("backend.agent.firmware_cache.get_disk_usage", usage)
    monkeypatch.setenv("STP_AGENT_INSTALL_DIR", str(tmp_path / "install"))
    (tmp_path / "install").mkdir()
    src = _sealed_source(tmp_path / "fw", {PROFILE_MTK: [("a.bin", b"data")]})
    cfg = _settings(
        stp_firmware_cache_dir=str(primary),
        stp_firmware_cache_fallback_dir=str(secondary),
        stp_firmware_cache_reserve_gb=20.0,
        stp_firmware_cache_max_gb=60.0,
        stp_firmware_cache_max_pct=90.0,
    )

    def boom_ready(*_a, **_k):
        raise OSError(errno.EIO, "ready write failed")

    monkeypatch.setattr(
        "backend.agent.firmware_cache._write_ready", boom_ready
    )
    with pytest.raises(FirmwareCacheError) as ei:
        with ensure_firmware(
            src,
            PROFILE_MTK,
            permit_client=_fake_permit(),
            settings=cfg,
            allow_nfs_direct_fallback=True,
            max_retries=1,
        ):
            pass
    assert ei.value.code == CODE_COPY_IO


def test_sealed_hit_requires_authoritative_files(tmp_path, monkeypatch):
    """M6：ready digest 正确但缺权威清单文件 → 不得判 hit。"""
    primary = tmp_path / "cache"
    _mount_ok(monkeypatch)
    monkeypatch.setattr(
        "backend.agent.firmware_cache.get_aee_local_root",
        lambda: tmp_path / "aee",
    )
    (tmp_path / "aee").mkdir()
    monkeypatch.setattr(
        "backend.agent.firmware_cache.get_disk_usage", lambda _p: _disk()
    )
    monkeypatch.setattr("backend.agent.firmware_cache._st_dev", lambda _p: 1)
    monkeypatch.setenv("STP_AGENT_INSTALL_DIR", str(tmp_path / "install"))
    (tmp_path / "install").mkdir()
    src = _sealed_source(
        tmp_path / "fw",
        {PROFILE_MTK: [("a.bin", b"aaa"), ("b.bin", b"bbb")]},
    )
    cfg = _settings(stp_firmware_cache_dir=str(primary))
    with ensure_firmware(
        src, PROFILE_MTK, permit_client=_fake_permit(), settings=cfg
    ) as r:
        entry = r.local_dir
        digest = r.content_digest
    # 破坏本地：删掉权威清单中的 b.bin，但保留 ready 的 digest 字符串
    (entry / "b.bin").unlink()
    ready = json.loads((entry / ".stp-fw-ready.json").read_text(encoding="utf-8"))
    assert ready["content_digest"] == digest
    # 再次 ensure：应重新冷拉（hit=False）或至少不接受残缺 hit
    with ensure_firmware(
        src, PROFILE_MTK, permit_client=_fake_permit(), settings=cfg
    ) as r2:
        assert (r2.local_dir / "b.bin").is_file()


def test_sealed_hit_detects_same_size_one_byte_corruption(tmp_path, monkeypatch):
    """M6-1：尺寸未变但本地 1 字节损坏 → SHA 失配重拉；干净命中仍零读源镜像。"""
    primary = tmp_path / "cache"
    _mount_ok(monkeypatch)
    monkeypatch.setattr(
        "backend.agent.firmware_cache.get_aee_local_root",
        lambda: tmp_path / "aee",
    )
    (tmp_path / "aee").mkdir()
    monkeypatch.setattr(
        "backend.agent.firmware_cache.get_disk_usage", lambda _p: _disk()
    )
    monkeypatch.setattr("backend.agent.firmware_cache._st_dev", lambda _p: 1)
    monkeypatch.setenv("STP_AGENT_INSTALL_DIR", str(tmp_path / "install"))
    (tmp_path / "install").mkdir()

    payload = b"ABCD"
    src = _sealed_source(
        tmp_path / "fw", {PROFILE_MTK: [("images/raw/super.img", payload)]}
    )
    cfg = _settings(stp_firmware_cache_dir=str(primary))
    permit = _fake_permit()
    with ensure_firmware(
        src, PROFILE_MTK, permit_client=permit, settings=cfg
    ) as r1:
        entry = r1.local_dir
        assert r1.hit is False
        local_img = entry / "images" / "raw" / "super.img"
        assert local_img.read_bytes() == payload
        local_img.write_bytes(b"XBCD")

    with ensure_firmware(
        src, PROFILE_MTK, permit_client=permit, settings=cfg
    ) as r2:
        assert (
            r2.local_dir / "images" / "raw" / "super.img"
        ).read_bytes() == payload
        assert r2.hit is False

    opened_source_images: list[str] = []
    real_open = open

    def tracking_open(file, *args, **kwargs):
        path = str(file)
        if str(src) in path and path.endswith(".img"):
            opened_source_images.append(path)
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr("builtins.open", tracking_open)
    with ensure_firmware(
        src, PROFILE_MTK, permit_client=permit, settings=cfg
    ) as r3:
        assert r3.hit is True
    assert opened_source_images == []


def _prep_unsealed_cache_env(tmp_path, monkeypatch, cache: Path):
    _mount_ok(monkeypatch)
    monkeypatch.setattr(
        "backend.agent.firmware_cache.get_aee_local_root",
        lambda: tmp_path / "aee",
    )
    (tmp_path / "aee").mkdir(exist_ok=True)
    monkeypatch.setattr(
        "backend.agent.firmware_cache.get_disk_usage", lambda _p: _disk()
    )
    monkeypatch.setattr("backend.agent.firmware_cache._st_dev", lambda _p: 1)
    monkeypatch.setenv("STP_AGENT_INSTALL_DIR", str(tmp_path / "install"))
    (tmp_path / "install").mkdir(exist_ok=True)
    return _settings(stp_firmware_cache_dir=str(cache))


def test_unsealed_in_tree_file_symlink_is_cached(tmp_path, monkeypatch):
    """未封包根内文件软链（boot.img→base.img，目标大小≠链接路径长度）须完整交付。"""
    unsealed = tmp_path / "fw"
    unsealed.mkdir()
    # 目标文件远大于 "base.img" 路径字符串长度，避免等长巧合
    base_payload = b"BASE_PAYLOAD_" + (b"Z" * 64)
    (unsealed / "base.img").write_bytes(base_payload)
    (unsealed / "boot.img").symlink_to("base.img")
    assert (unsealed / "boot.img").is_symlink()
    assert len(base_payload) != len("base.img")

    cache = tmp_path / "cache"
    cfg = _prep_unsealed_cache_env(tmp_path, monkeypatch, cache)
    with ensure_firmware(
        unsealed, PROFILE_MTK, permit_client=_fake_permit(), settings=cfg
    ) as r:
        boot = r.local_dir / "boot.img"
        assert boot.is_file()
        assert not boot.is_symlink()
        assert boot.read_bytes() == base_payload
        assert (r.local_dir / READY_MARKER).is_file()


def test_unsealed_symlink_realpath_lstat_eio_fail_closed(tmp_path, monkeypatch):
    """软链 realpath(strict) 内部对链接本身 lstat 注入 EIO → ENUM_FAILED，零 ready。

    注入严格限定 ``basename == boot.img``（不跟链到 base.img），并断言命中恰好一次，
    以保证测的是 ``realpath(..., strict=True)`` 而非普通文件分支。
    """
    import errno

    unsealed = tmp_path / "fw"
    unsealed.mkdir()
    (unsealed / "base.img").write_bytes(b"BASE_PAYLOAD_" + (b"Z" * 64))
    boot = unsealed / "boot.img"
    boot.symlink_to("base.img")
    boot_link = str(boot)
    cache = tmp_path / "cache"
    cfg = _prep_unsealed_cache_env(tmp_path, monkeypatch, cache)

    real_lstat = os.lstat
    hits = {"n": 0}

    def boom_lstat(path, *args, **kwargs):
        # 只炸软链路径本身；base.img 等普通文件走原 lstat
        if os.path.basename(os.fspath(path)) == "boot.img" or os.fspath(
            path
        ) == boot_link:
            hits["n"] += 1
            raise OSError(errno.EIO, "lstat boom during realpath")
        return real_lstat(path, *args, **kwargs)

    monkeypatch.setattr(os, "lstat", boom_lstat)
    with pytest.raises(FirmwareCacheError) as ei:
        with ensure_firmware(
            unsealed,
            PROFILE_MTK,
            permit_client=_fake_permit(),
            settings=cfg,
            max_retries=1,
        ):
            pass
    assert ei.value.code == CODE_ENUM_FAILED
    assert hits["n"] == 1, hits
    assert list(cache.rglob(READY_MARKER)) == []


def test_unsealed_symlink_realpath_readlink_eio_fail_closed(tmp_path, monkeypatch):
    """软链 realpath(strict) 路径上 os.readlink 注入 EIO → ENUM_FAILED，零 ready。"""
    import errno

    unsealed = tmp_path / "fw"
    unsealed.mkdir()
    (unsealed / "base.img").write_bytes(b"BASE_PAYLOAD_" + (b"Z" * 64))
    boot = unsealed / "boot.img"
    boot.symlink_to("base.img")
    cache = tmp_path / "cache"
    cfg = _prep_unsealed_cache_env(tmp_path, monkeypatch, cache)

    real_readlink = os.readlink

    def boom_readlink(path, *args, **kwargs):
        if Path(path).name == "boot.img":
            raise OSError(errno.EIO, "readlink boom during realpath")
        return real_readlink(path, *args, **kwargs)

    monkeypatch.setattr(os, "readlink", boom_readlink)
    with pytest.raises(FirmwareCacheError) as ei:
        with ensure_firmware(
            unsealed,
            PROFILE_MTK,
            permit_client=_fake_permit(),
            settings=cfg,
            max_retries=1,
        ):
            pass
    assert ei.value.code == CODE_ENUM_FAILED
    assert list(cache.rglob(READY_MARKER)) == []


def test_unsealed_escape_and_dangling_symlink_fail_closed(tmp_path, monkeypatch):
    """未封包越界软链与悬空软链均显式失败，零 ready、不交付缺件目录。"""
    cache = tmp_path / "cache"
    cfg = _prep_unsealed_cache_env(tmp_path, monkeypatch, cache)

    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"secret-outside")
    escape_src = tmp_path / "fw_escape"
    escape_src.mkdir()
    (escape_src / "ok.bin").write_bytes(b"ok")
    (escape_src / "evil.img").symlink_to(outside)

    with pytest.raises(FirmwareCacheError) as ei:
        resolve_cache_identity(escape_src, PROFILE_MTK)
    assert ei.value.code == CODE_ENUM_FAILED
    with pytest.raises(FirmwareCacheError) as ei2:
        with ensure_firmware(
            escape_src,
            PROFILE_MTK,
            permit_client=_fake_permit(),
            settings=cfg,
            max_retries=1,
        ):
            pass
    assert ei2.value.code == CODE_ENUM_FAILED
    assert list(cache.rglob(READY_MARKER)) == []

    dangling_src = tmp_path / "fw_dangling"
    dangling_src.mkdir()
    (dangling_src / "ok.bin").write_bytes(b"ok")
    (dangling_src / "missing.img").symlink_to("no_such_target.img")

    with pytest.raises(FirmwareCacheError) as ei3:
        resolve_cache_identity(dangling_src, PROFILE_MTK)
    assert ei3.value.code == CODE_ENUM_FAILED
    with pytest.raises(FirmwareCacheError) as ei4:
        with ensure_firmware(
            dangling_src,
            PROFILE_MTK,
            permit_client=_fake_permit(),
            settings=cfg,
            max_retries=1,
        ):
            pass
    assert ei4.value.code == CODE_ENUM_FAILED
    assert list(cache.rglob(READY_MARKER)) == []


def test_unsealed_lstat_failure_is_explicit(tmp_path, monkeypatch):
    """M6：未封包枚举单文件 lstat 失败 → 显式失败。"""
    import errno

    unsealed = tmp_path / "fw"
    unsealed.mkdir()
    (unsealed / "a.bin").write_bytes(b"x")
    real_lstat = os.lstat

    def bad_lstat(path):
        if Path(path).name == "a.bin":
            raise OSError(errno.EIO, "boom")
        return real_lstat(path)

    monkeypatch.setattr(os, "lstat", bad_lstat)
    with pytest.raises(FirmwareCacheError) as ei:
        resolve_cache_identity(unsealed, PROFILE_MTK)
    assert ei.value.code == CODE_ENUM_FAILED


def test_unsealed_scandir_failure_is_explicit(tmp_path, monkeypatch):
    """M6-3：未封包子目录 scandir 失败 → 显式 ENUM_FAILED。"""
    unsealed = tmp_path / "fw"
    unsealed.mkdir()
    sub = unsealed / "subdir"
    sub.mkdir()
    (sub / "a.bin").write_bytes(b"x")
    real_scandir = os.scandir

    def boom_scandir(path):
        if Path(path).resolve() == sub.resolve():
            raise PermissionError("scandir denied")
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", boom_scandir)
    with pytest.raises(FirmwareCacheError) as ei:
        resolve_cache_identity(unsealed, PROFILE_MTK)
    assert ei.value.code == CODE_ENUM_FAILED


def test_unsealed_isdir_eio_fails_closed_no_partial_ready(tmp_path, monkeypatch):
    """#4086 v1.6：现存子目录 DirEntry.is_dir 注入 EIO → ENUM_FAILED，无 ready 产物。"""
    import errno

    unsealed = tmp_path / "fw"
    unsealed.mkdir()
    (unsealed / "scatter.txt").write_bytes(b"top")
    sub = unsealed / "images"
    sub.mkdir()
    (sub / "image.bin").write_bytes(b"img")

    cache = tmp_path / "cache"
    _mount_ok(monkeypatch)
    monkeypatch.setattr(
        "backend.agent.firmware_cache.get_aee_local_root",
        lambda: tmp_path / "aee",
    )
    (tmp_path / "aee").mkdir()
    monkeypatch.setattr(
        "backend.agent.firmware_cache.get_disk_usage", lambda _p: _disk()
    )
    monkeypatch.setattr("backend.agent.firmware_cache._st_dev", lambda _p: 1)
    monkeypatch.setenv("STP_AGENT_INSTALL_DIR", str(tmp_path / "install"))
    (tmp_path / "install").mkdir()

    real_scandir = os.scandir

    class _WrappedEntry:
        def __init__(self, entry):
            self._entry = entry
            self.name = entry.name
            self.path = entry.path

        def is_symlink(self):
            return self._entry.is_symlink()

        def is_dir(self, *, follow_symlinks=True):
            if Path(self.path).resolve() == sub.resolve():
                raise OSError(errno.EIO, "is_dir boom")
            return self._entry.is_dir(follow_symlinks=follow_symlinks)

        def is_file(self, *, follow_symlinks=True):
            return self._entry.is_file(follow_symlinks=follow_symlinks)

        def stat(self, *, follow_symlinks=True):
            return self._entry.stat(follow_symlinks=follow_symlinks)

    class _WrappedScan:
        def __init__(self, it):
            self._it = it

        def __enter__(self):
            self._it.__enter__()
            return self

        def __exit__(self, *a):
            return self._it.__exit__(*a)

        def __iter__(self):
            for entry in self._it:
                yield _WrappedEntry(entry)

    def wrap_scandir(path):
        return _WrappedScan(real_scandir(path))

    monkeypatch.setattr(os, "scandir", wrap_scandir)

    with pytest.raises(FirmwareCacheError) as ei:
        resolve_cache_identity(unsealed, PROFILE_MTK)
    assert ei.value.code == CODE_ENUM_FAILED

    # 经 ensure 入口亦不得发布 ready
    cfg = _settings(stp_firmware_cache_dir=str(cache))
    with pytest.raises(FirmwareCacheError) as ei2:
        with ensure_firmware(
            unsealed,
            PROFILE_MTK,
            permit_client=_fake_permit(),
            settings=cfg,
            max_retries=1,
        ):
            pass
    assert ei2.value.code == CODE_ENUM_FAILED
    assert list(cache.rglob(READY_MARKER)) == []


def test_primary_space_secondary_downgrade_race_not_nfs_fallback(
    tmp_path, monkeypatch
):
    """M6：主盘空间不足 + 二级 3 次降级复核耗尽 → 抛 downgrade_race，非 nfs_direct。"""
    primary = tmp_path / "ssd"
    secondary = tmp_path / "hdd"
    _mount_ok(monkeypatch)
    monkeypatch.setattr(
        "backend.agent.firmware_cache.get_aee_local_root",
        lambda: tmp_path / "aee",
    )
    (tmp_path / "aee").mkdir()
    monkeypatch.setattr(
        "backend.agent.firmware_cache._st_dev",
        lambda p: 1 if "ssd" in str(p) else 2,
    )

    def usage(path):
        if "ssd" in str(path):
            return _disk(total=100.0, used=95.0, avail=1.0, writable=95.0)
        return _disk(total=100.0, used=10.0, avail=90.0, writable=10.0)

    monkeypatch.setattr("backend.agent.firmware_cache.get_disk_usage", usage)
    monkeypatch.setenv("STP_AGENT_INSTALL_DIR", str(tmp_path / "install"))
    (tmp_path / "install").mkdir()
    src = _sealed_source(tmp_path / "fw", {PROFILE_MTK: [("a.bin", b"data")]})
    cfg = _settings(
        stp_firmware_cache_dir=str(primary),
        stp_firmware_cache_fallback_dir=str(secondary),
        stp_firmware_cache_reserve_gb=20.0,
        stp_firmware_cache_max_gb=60.0,
        stp_firmware_cache_max_pct=90.0,
    )

    import backend.agent.firmware_cache as fc

    real_verify = fc._verify_ready_under_sh
    # 仅在 populate 完成后的降级窗口失配：用计数区分 hit 探测
    state = {"populate_done": 0}
    real_populate = fc._populate_entry

    def counting_populate(*a, **k):
        out = real_populate(*a, **k)
        state["populate_done"] += 1
        return out

    def flaky_verify(entry_dir, **kwargs):
        if state["populate_done"] > 0:
            return False
        return real_verify(entry_dir, **kwargs)

    monkeypatch.setattr(fc, "_populate_entry", counting_populate)
    monkeypatch.setattr(fc, "_verify_ready_under_sh", flaky_verify)

    with pytest.raises(FirmwareCacheError) as ei:
        with ensure_firmware(
            src,
            PROFILE_MTK,
            permit_client=_fake_permit(),
            settings=cfg,
            allow_nfs_direct_fallback=True,
            max_retries=3,
        ):
            pass
    assert ei.value.code == CODE_DOWNGRADE_RACE
    assert state["populate_done"] >= 1


def test_downgrade_lock_sh_emits_wait_progress(tmp_path, monkeypatch):
    """M7：降级取 LOCK_SH 非阻塞轮询时，占用期间周期打 firmware-stage-wait。"""
    import backend.agent.firmware_cache as fc

    lock_path = tmp_path / "entry.lock"
    lock_path.write_bytes(b"")
    holder = os.open(str(lock_path), os.O_RDWR)
    fcntl.flock(holder, fcntl.LOCK_EX)
    waiter = os.open(str(lock_path), os.O_RDWR)
    stages: list[dict] = []

    def on_progress(**kwargs):
        stages.append(dict(kwargs))

    def releaser():
        time.sleep(0.25)
        fcntl.flock(holder, fcntl.LOCK_UN)
        os.close(holder)

    threading.Thread(target=releaser, daemon=True).start()
    fc._flock_wait(
        waiter,
        fcntl.LOCK_SH,
        progress_callback=on_progress,
        wait_for="entry-lock-sh-downgrade",
        poll_seconds=0.04,
    )
    os.close(waiter)
    wait_stamps = [
        s for s in stages if s.get("stage") == "firmware-stage-wait"
    ]
    assert len(wait_stamps) >= 2
    assert all(
        s.get("wait_for") == "entry-lock-sh-downgrade" for s in wait_stamps
    )


def test_sealed_manifest_rejects_other_profile_escape(tmp_path):
    """M6：共用目录另一 profile 的越界软链 → 整包拒绝。"""
    root = tmp_path / "shared"
    root.mkdir()
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"evil")
    (root / "images").mkdir()
    good = root / "images" / "ok.img"
    good.write_bytes(b"ok")
    bad_link = root / "images" / "escape.img"
    bad_link.symlink_to(outside)
    files_fb = [
        {
            "relpath": "images/ok.img",
            "size": 2,
            "sha256": __import__("hashlib").sha256(b"ok").hexdigest(),
        }
    ]
    files_bsl = [
        {
            "relpath": "images/escape.img",
            "size": 4,
            "sha256": "abcd",
        }
    ]
    manifest = {
        "schema_version": 1,
        "profiles": {
            PROFILE_UNISOC_FASTBOOT: {
                "content_digest": compute_content_digest(files_fb),
                "total_bytes": 2,
                "files": files_fb,
            },
            PROFILE_UNISOC_BSL: {
                "content_digest": compute_content_digest(files_bsl),
                "total_bytes": 4,
                "files": files_bsl,
            },
        },
    }
    (root / "staging_manifest.json").write_text(
        dumps_staging_manifest(manifest), encoding="utf-8"
    )
    with pytest.raises(FirmwareManifestError):
        # 请求 fastboot 时也应因另一 profile 越界而整包失败
        resolve_cache_identity(root, PROFILE_UNISOC_FASTBOOT)


# ── 8：Settings 热重载 ────────────────────────────────────────────────────────


def test_firmware_cache_settings_hot_reload(monkeypatch):
    reset_agent_settings_caches()
    monkeypatch.setenv("STP_FIRMWARE_CACHE_RESERVE_GB", "20")
    reset_agent_settings_caches()
    assert get_firmware_cache_settings().stp_firmware_cache_reserve_gb == 20.0
    monkeypatch.setenv("STP_FIRMWARE_CACHE_RESERVE_GB", "25")
    assert get_firmware_cache_settings().stp_firmware_cache_reserve_gb == 20.0
    reset_agent_settings_caches()
    assert get_firmware_cache_settings().stp_firmware_cache_reserve_gb == 25.0
    monkeypatch.delenv("STP_FIRMWARE_CACHE_RESERVE_GB", raising=False)
    reset_agent_settings_caches()
