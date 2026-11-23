"""B10 U4（#4086 §4）：三大刷机族接入 Agent L1 固件缓存必测反例。

覆盖：
1. MTK ensure 发生在 ``_acquire_host_lock`` 之前、命令行指向本地缓存、metrics 字段齐全
2. Unisoc Fastboot / BSL 分别按 profile 指向本地目录，并打 wait/copy PROGRESS 戳
3. 旧 Agent（导入失败 / API 版本不匹配）在任何设备访问与取锁前返回
   ``agent_firmware_cache_unavailable``
4. BSL：无 ``/var/tmp/stp_flash`` 写入、无锁内拷贝；未封包在取锁/状态变更前
   ``firmware_unsealed_not_supported``；已封包放行
5. ``pipeline_engine.py`` 零新增 ``script_name`` 字面量分支（相对 origin/main）
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator, List

import pytest

from backend.agent.firmware_cache import FirmwareCacheError

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "scripts"
_MTK = _SCRIPTS / "flash_firmware" / "flash_firmware.py"
_UNISOC = _SCRIPTS / "flash_firmware_unisoc" / "flash_firmware_unisoc.py"
_BSL = _SCRIPTS / "flash_firmware_unisoc_bsl" / "flash_firmware_unisoc_bsl.py"
_REPO = Path(__file__).resolve().parents[3]


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fc_metrics_keys() -> set[str]:
    return {
        "hit",
        "elapsed_seconds",
        "tier",
        "sealed",
        "cache_key",
        "fallback_reason",
        "permit_degraded",
    }


@contextmanager
def _tracking_ensure(
    source_dir,
    profile,
    *,
    progress_callback=None,
    events: List[Any] | None = None,
    local_dir: Path | None = None,
    sealed: bool = True,
    raise_code: str | None = None,
    **kwargs,
) -> Iterator[SimpleNamespace]:
    if events is not None:
        events.append(("ensure_enter", profile, str(source_dir)))
    if progress_callback is not None:
        progress_callback(stage="firmware-stage-wait", wait_for="permit")
        progress_callback(stage="firmware-stage-copy", copied_bytes=1)
    if raise_code:
        raise FirmwareCacheError(raise_code, raise_code)
    src = Path(source_dir)
    loc = Path(local_dir) if local_dir is not None else src
    yield SimpleNamespace(
        local_dir=loc,
        cache_key=f"u4_{profile}",
        sealed=sealed,
        content_digest="sha256:" + ("cd" * 32),
        profile=profile,
        tier="primary",
        hit=False,
        total_bytes=42,
        fallback_reason=None,
        permit_degraded=False,
    )
    if events is not None:
        events.append(("ensure_exit", profile))


# ── 3. 旧 Agent / API 版本交错 ───────────────────────────────────────────


@pytest.mark.parametrize(
    "script_path,mod_name,via_run",
    [
        (_MTK, "flash_firmware_b10_u4_mtk", False),
        (_UNISOC, "flash_firmware_b10_u4_unisoc", True),
        (_BSL, "flash_firmware_b10_u4_bsl", True),
    ],
)
@pytest.mark.parametrize("mode", ["import_fail", "version_mismatch"])
def test_u4_old_agent_fails_before_device_or_lock(
    script_path, mod_name, via_run, mode, monkeypatch, tmp_path, capsys
):
    mod = _load(script_path, mod_name)
    touches: list[str] = []

    def boom_load():
        if mode == "import_fail":
            if via_run:
                # unisoc returns 4-tuple; bsl/mtk 3-tuple — detect by name
                if "unisoc" in mod_name and "bsl" not in mod_name:
                    return None, None, None, "agent_firmware_cache_unavailable: import failed"
                return None, None, "agent_firmware_cache_unavailable: import failed"
            return None, None, "agent_firmware_cache_unavailable: import failed"
        # version mismatch path: return err string
        if "unisoc" in mod_name and "bsl" not in mod_name:
            return None, None, None, "agent_firmware_cache_unavailable: API version 0 != 1"
        return None, None, "agent_firmware_cache_unavailable: API version 0 != 1"

    monkeypatch.setattr(mod, "_load_firmware_cache_api", boom_load)

    if hasattr(mod, "getprop"):
        orig_getprop = mod.getprop

        def tracked_getprop(*a, **k):
            touches.append("getprop")
            return orig_getprop(*a, **k)

        monkeypatch.setattr(mod, "getprop", tracked_getprop)

    if hasattr(mod, "_adb_getprop"):
        monkeypatch.setattr(
            mod,
            "_adb_getprop",
            lambda *a, **k: touches.append("adb_getprop") or None,
        )
    if hasattr(mod, "run_adb"):
        monkeypatch.setattr(
            mod,
            "run_adb",
            lambda *a, **k: touches.append("run_adb") or (0, "", ""),
        )
    if hasattr(mod, "_acquire_host_lock"):
        monkeypatch.setattr(
            mod,
            "_acquire_host_lock",
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("host lock must not run")),
        )
    if hasattr(mod, "acquire_host_lock"):
        monkeypatch.setattr(
            mod,
            "acquire_host_lock",
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("host lock must not run")),
        )

    monkeypatch.setenv("STP_DEVICE_SERIAL", "SN-U4")
    monkeypatch.setenv("STP_STEP_PARAMS", json.dumps({"firmware_dir": str(tmp_path / "fw")}))

    if via_run:
        with pytest.raises(Exception) as ei:
            mod.run({"firmware_dir": str(tmp_path / "fw")}, "SN-U4", mod.Progress(), [])
        assert "agent_firmware_cache_unavailable" in str(ei.value)
    else:
        buf = io.StringIO()
        monkeypatch.setattr(sys, "stdout", buf)
        try:
            mod.main()
        finally:
            monkeypatch.setattr(sys, "stdout", sys.__stdout__)
        payload = json.loads(buf.getvalue().strip().splitlines()[-1])
        assert payload["success"] is False
        assert payload["error_message"] == "agent_firmware_cache_unavailable"

    assert touches == []


# ── 1. MTK：ensure 先于锁、命令行指向本地缓存、metrics 齐全 ─────────────


def test_u4_mtk_ensure_before_lock_and_metrics(monkeypatch, tmp_path, capsys):
    from backend.agent.tests import test_flash_firmware_v140 as v140

    ff = v140.ff
    events: list[Any] = []
    local_root = tmp_path / "local_fw"
    # Harness builds V6 tree under tmp_path; copy structure into local_root via remap target
    h = v140.Harness(monkeypatch, tmp_path, {})
    source = Path(json.loads(os.environ["STP_STEP_PARAMS"])["firmware_dir"])
    # Build a distinct local cache tree with same relative files
    import shutil

    if local_root.exists():
        shutil.rmtree(local_root)
    shutil.copytree(source, local_root)

    def load_ok():
        def ensure(source_dir, profile, **kw):
            assert profile == "mtk"
            return _tracking_ensure(
                source_dir,
                profile,
                events=events,
                local_dir=local_root,
                **kw,
            )

        return ensure, FirmwareCacheError, None

    monkeypatch.setattr(ff, "_load_firmware_cache_api", load_ok)

    lock_calls: list[str] = []

    def acquire_tracked(*a, **k):
        lock_calls.append("lock")
        assert any(e[0] == "ensure_enter" for e in events), "ensure must precede lock"
        h.locked.append(True)
        return 7

    monkeypatch.setattr(ff, "_acquire_host_lock", acquire_tracked)
    payload = h.run(monkeypatch, capsys)
    assert payload["success"] is True, payload
    assert events[0][0] == "ensure_enter"
    assert lock_calls == ["lock"]
    fc = payload["metrics"]["firmware_cache"]
    assert _fc_metrics_keys() <= set(fc)
    argv = payload["metrics"]["command_argv"]
    joined = " ".join(str(x) for x in argv)
    assert str(local_root) in joined
    assert str(source) not in joined or str(local_root) in joined


# ── 2. Unisoc Fastboot / BSL profiles + PROGRESS stamps ──────────────────


def test_u4_unisoc_fastboot_profile_and_progress(monkeypatch, tmp_path):
    mod = _load(_UNISOC, "flash_firmware_b10_u4_unisoc_prog")
    fw = tmp_path / "fw"
    fw.mkdir()
    (fw / "manifest.json").write_text(
        json.dumps(
            {
                "version": "V1",
                "family": "A57",
                "version_prop": "ro.build.sw_internal_version",
                "plan": "images/plan.json",
            }
        ),
        encoding="utf-8",
    )
    images = fw / "images"
    images.mkdir()
    (images / "plan.json").write_text(
        json.dumps({"entries": [{"id": "BOOT", "file": "boot.img", "partitions": ["boot_a"]}]}),
        encoding="utf-8",
    )
    (images / "boot.img").write_bytes(b"boot")

    # §4 U4 #2：local_dir 必须与源目录可区分，否则旁路 flash_partitions(fw_dir) 会静绿
    local_root = tmp_path / "cache_local_unisoc"
    local_root.mkdir()
    assert local_root.resolve() != fw.resolve()

    stamps: list[dict] = []
    events: list[Any] = []
    order: list[str] = []
    flash_dirs: list[str] = []
    spl_dirs: list[str] = []

    class P:
        def stamp(self, **fields):
            stamps.append(fields)

    def load_ok():
        def ensure(source_dir, profile, **kw):
            assert profile == "unisoc_fastboot"
            assert Path(source_dir).resolve() == fw.resolve()
            order.append("ensure")
            return _tracking_ensure(
                source_dir, profile, events=events, local_dir=local_root, **kw
            )

        return ensure, FirmwareCacheError, 1, None

    monkeypatch.setattr(mod, "_load_firmware_cache_api", load_ok)
    monkeypatch.setattr(mod, "resolve_fastboot", lambda: "/bin/fastboot")
    fake_dev = SimpleNamespace(run=lambda *x, **y: SimpleNamespace(stdout="0", returncode=0))
    monkeypatch.setattr(mod, "probe_device", lambda *a, **k: ("adb", fake_dev, None))
    monkeypatch.setattr(mod, "ensure_root", lambda *a, **k: "0")
    monkeypatch.setattr(
        mod,
        "resolve_firmware",
        lambda *a, **k: (str(fw), json.loads((fw / "manifest.json").read_text())),
    )
    monkeypatch.setattr(mod, "load_plan", lambda *a, **k: [{"id": "BOOT", "file": "boot.img", "partitions": ["boot_a"]}])
    monkeypatch.setattr(mod, "getprop", lambda *a, **k: "other")
    monkeypatch.setattr(mod, "acquire_host_lock", lambda progress: order.append("lock") or 3)

    def fake_spl(fw_dir, *a, **k):
        spl_dirs.append(fw_dir)
        return {"mode": "never", "action": "skip"}

    def fake_flash(fw_dir, *a, **k):
        flash_dirs.append(fw_dir)
        return {"partitions": 0, "flash_seconds": 0.1, "fw": fw_dir}

    monkeypatch.setattr(mod, "spl_decision", fake_spl)
    monkeypatch.setattr(mod, "write_spl", lambda *a, **k: {"mode": "never", "action": "skip"})
    monkeypatch.setattr(mod, "flash_partitions", fake_flash)
    monkeypatch.setattr(mod, "wipe", lambda *a, **k: {"erases": []})
    monkeypatch.setattr(mod, "reboot_out_of_fastboot", lambda *a, **k: None)
    monkeypatch.setattr(mod, "wait_boot", lambda *a, **k: {"boot_completed": True})
    monkeypatch.setattr(mod, "boot_slot", lambda *a, **k: "a")
    monkeypatch.setattr(mod, "wait_fastboot", lambda *a, **k: SimpleNamespace())
    monkeypatch.setattr(mod, "adb", lambda *a, **k: fake_dev)
    from backend.agent.tests.test_flash_firmware_unisoc import _patch_advancing_clock

    _patch_advancing_clock(monkeypatch, mod)

    res = mod.run(
        {
            "firmware_dir": str(fw),
            "wipe": False,
            "verify_version": False,
            "flash_spl": "never",
            "force": True,
        },
        "SN",
        P(),
        [],
    )
    assert order == ["ensure", "lock"]
    assert flash_dirs, "flash_partitions must be invoked"
    assert Path(flash_dirs[0]).resolve() == local_root.resolve()
    assert Path(flash_dirs[0]).resolve() != fw.resolve()
    assert spl_dirs and Path(spl_dirs[0]).resolve() == local_root.resolve()
    assert Path(res["metrics"]["firmware_dir"]).resolve() == local_root.resolve()
    assert Path(res["metrics"]["source_firmware_dir"]).resolve() == fw.resolve()
    assert _fc_metrics_keys() <= set(res["metrics"]["firmware_cache"])
    stages = [s.get("stage") for s in stamps]
    assert "firmware-stage-wait" in stages
    assert "firmware-stage-copy" in stages
    assert events[0][0] == "ensure_enter"


def test_u4_bsl_profile_points_at_images_and_trimmed_super(monkeypatch, tmp_path):
    mod = _load(_BSL, "flash_firmware_b10_u4_bsl_prog")
    from backend.agent.tests.test_flash_firmware_unisoc_bsl import (
        _create_firmware_dir,
        _install_fake_adb,
        _make_fake_sysfs,
        _patch_advancing_clock,
    )

    ver_dir = _create_firmware_dir(tmp_path / "fw", family="P525AA", version="v36", models=["MLY-LX2"])
    local_root = tmp_path / "cache_local"
    import shutil

    shutil.copytree(ver_dir, local_root)
    sysfs = _make_fake_sysfs(tmp_path)
    clock = _patch_advancing_clock(monkeypatch, mod)
    adb_state = _install_fake_adb(
        monkeypatch,
        mod,
        props={
            "ro.product.model": "MLY-LX2",
            "ro.product.device": "MLY",
            "ro.product.name": "MLY-LX2",
            "ro.build.type": "userdebug",
            "ro.build.version.incremental": "1",
            "ro.build.sw_internal_version": "old",
        },
        post_props={
            "ro.product.model": "MLY-LX2",
            "ro.product.device": "MLY",
            "ro.product.name": "MLY-LX2",
            "ro.build.type": "userdebug",
            "ro.build.version.incremental": "1",
            "ro.build.sw_internal_version": "v36",
        },
        clock=clock,
    )
    monkeypatch.setattr(mod, "_SYSFS_USB_BASE", str(sysfs))
    monkeypatch.setattr(mod, "resolve_adb", lambda: "adb")
    monkeypatch.setattr(mod, "resolve_spd_dump", lambda route=None: "/usr/bin/spd_dump")
    lock_file = tmp_path / "lock"
    seen_cmd: list[list[str]] = []
    stamps: list[dict] = []

    class P:
        def stamp(self, **fields):
            stamps.append(fields)

    def load_ok():
        def ensure(source_dir, profile, **kw):
            assert profile == "unisoc_bsl"
            assert Path(source_dir) == ver_dir or Path(source_dir).resolve() == ver_dir.resolve()
            return _tracking_ensure(source_dir, profile, local_dir=local_root, **kw)

        return ensure, FirmwareCacheError, None

    monkeypatch.setattr(mod, "_load_firmware_cache_api", load_ok)
    monkeypatch.setattr(
        mod,
        "acquire_host_lock",
        lambda progress, timeout_seconds=7200, lock_path=None: os.open(
            lock_file, os.O_CREAT | os.O_RDWR, 0o666
        ),
    )

    def fake_spd(cmd, *a, **k):
        seen_cmd.append(list(cmd))
        adb_state["rebooted"] = True
        adb_state["nonce"] = ""
        clock["now"] += 20
        return 1.0

    monkeypatch.setattr(mod, "run_spd_dump", fake_spd)
    monkeypatch.setattr(mod, "wait_boot_completed", lambda *a, **k: 1.0)

    res = mod.run({"firmware_dir": str(ver_dir), "verify_root": False}, "SN1", P(), [])
    assert res["metrics"]["firmware_dir"] == str(local_root / "images")
    assert _fc_metrics_keys() <= set(res["metrics"]["firmware_cache"])
    joined = " ".join(seen_cmd[0])
    assert str(local_root / "images" / "bsl" / "super_trimmed.img") in joined
    stages = [s.get("stage") for s in stamps]
    assert "firmware-stage-wait" in stages
    assert "firmware-stage-copy" in stages


# ── 4. BSL 无私有缓存路径；未封包 fail-closed；已封包放行 ────────────────


def test_u4_bsl_no_stp_flash_writes_and_unsealed_before_lock(monkeypatch, tmp_path):
    mod = _load(_BSL, "flash_firmware_b10_u4_bsl_unsealed")

    from backend.agent.tests.test_flash_firmware_unisoc_bsl import (
        _create_firmware_dir,
        _install_fake_adb,
        _make_fake_sysfs,
    )

    # same family, two version dirs: sealed (has staging) vs unsealed
    fw_root = tmp_path / "firmware"
    sealed = _create_firmware_dir(fw_root, family="P525AA", version="sealed_v1", models=["MLY-LX2"])
    unsealed = _create_firmware_dir(fw_root, family="P525AA", version="unsealed_v1", models=["MLY-LX2"])
    (sealed / "staging_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "profiles": {
                    "unisoc_bsl": {
                        "content_digest": "sha256:" + ("11" * 32),
                        "total_bytes": 1,
                        "files": [],
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    sysfs = _make_fake_sysfs(tmp_path)
    _install_fake_adb(
        monkeypatch,
        mod,
        props={
            "ro.product.model": "MLY-LX2",
            "ro.product.device": "MLY",
            "ro.product.name": "MLY-LX2",
            "ro.build.type": "userdebug",
            "ro.build.version.incremental": "x",
        },
    )
    monkeypatch.setattr(mod, "_SYSFS_USB_BASE", str(sysfs))
    monkeypatch.setattr(mod, "resolve_adb", lambda: "adb")
    monkeypatch.setattr(mod, "resolve_spd_dump", lambda route=None: "/usr/bin/spd_dump")
    monkeypatch.setenv("STP_FLASH_FIRMWARE_ROOT", str(fw_root))

    lock_hits: list[str] = []
    monkeypatch.setattr(
        mod,
        "acquire_host_lock",
        lambda *a, **k: lock_hits.append("lock") or 1,
    )
    state_changes: list[str] = []
    monkeypatch.setattr(
        mod,
        "run_spd_dump",
        lambda *a, **k: state_changes.append("spd") or 1.0,
    )

    # Route unsealed via explicit firmware_dir
    def load_unsealed():
        def ensure(source_dir, profile, **kw):
            raise FirmwareCacheError(
                "firmware_unsealed_not_supported",
                "firmware_unsealed_not_supported",
            )

        return ensure, FirmwareCacheError, None

    monkeypatch.setattr(mod, "_load_firmware_cache_api", load_unsealed)
    with pytest.raises(Exception) as ei:
        mod.run({"firmware_dir": str(unsealed), "verify_root": False}, "SN1", mod.Progress(), [])
    assert "firmware_unsealed_not_supported" in str(ei.value)
    assert lock_hits == []
    assert state_changes == []

    # 已封包：ensure 放行后才会触达主机锁（完整刷写路径由 profile 测例覆盖）
    def load_sealed():
        def ensure(source_dir, profile, **kw):
            return _tracking_ensure(source_dir, profile, local_dir=sealed, sealed=True, **kw)

        return ensure, FirmwareCacheError, None

    monkeypatch.setattr(mod, "_load_firmware_cache_api", load_sealed)
    monkeypatch.setattr(
        mod,
        "acquire_host_lock",
        lambda *a, **k: lock_hits.append("lock") or os.open(
            tmp_path / "lock2", os.O_CREAT | os.O_RDWR, 0o666
        ),
    )
    monkeypatch.setattr(mod, "run_spd_dump", lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("stop-after-lock")
    ))
    with pytest.raises(RuntimeError, match="stop-after-lock"):
        mod.run({"firmware_dir": str(sealed), "verify_root": False}, "SN1", mod.Progress(), [])
    assert "lock" in lock_hits


def test_u4_bsl_family_latest_routes_sealed_vs_unsealed_by_identity(monkeypatch, tmp_path):
    """#4086 §4 U4 #4：同一 family + latest.json，两种只读 getprop 身份分别路由到已/未封包目录。"""
    import shutil

    mod = _load(_BSL, "flash_firmware_b10_u4_bsl_family_latest")
    from backend.agent.tests.test_flash_firmware_unisoc_bsl import (
        _create_firmware_dir,
        _install_fake_adb,
        _make_fake_sysfs,
        _patch_advancing_clock,
    )

    fw_root = tmp_path / "firmware"
    sealed = _create_firmware_dir(
        fw_root, family="P525AA", version="sealed_v1", models=["MLY-LX2"]
    )
    unsealed = _create_firmware_dir(
        fw_root, family="P525AA", version="unsealed_v1", models=["MLY-LX2-CA"]
    )
    (sealed / "staging_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "profiles": {
                    "unisoc_bsl": {
                        "content_digest": "sha256:" + ("22" * 32),
                        "total_bytes": 1,
                        "files": [],
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    (fw_root / "P525AA" / "latest.json").write_text(
        json.dumps(
            {
                "versions": {
                    "MLY-LX2": "sealed_v1",
                    "MLY-LX2-CA": "unsealed_v1",
                }
            }
        ),
        encoding="utf-8",
    )

    local_sealed = tmp_path / "cache_local_sealed"
    shutil.copytree(sealed, local_sealed)
    assert local_sealed.resolve() != sealed.resolve()

    sysfs = _make_fake_sysfs(tmp_path)
    clock = _patch_advancing_clock(monkeypatch, mod)
    monkeypatch.setattr(mod, "_SYSFS_USB_BASE", str(sysfs))
    monkeypatch.setattr(mod, "resolve_adb", lambda: "adb")
    monkeypatch.setattr(mod, "resolve_spd_dump", lambda route=None: "/usr/bin/spd_dump")
    monkeypatch.setenv("STP_FLASH_FIRMWARE_ROOT", str(fw_root))
    monkeypatch.delenv("STP_FLASH_FIRMWARE_VERSION", raising=False)

    ensure_sources: list[str] = []
    lock_hits: list[str] = []
    state_changes: list[str] = []
    adb_calls: list[list[str]] = []
    stp_flash_writes: list[str] = []

    def load_by_source():
        def ensure(source_dir, profile, **kw):
            assert profile == "unisoc_bsl"
            src = Path(source_dir).resolve()
            ensure_sources.append(str(src))
            if src == unsealed.resolve():
                raise FirmwareCacheError(
                    "firmware_unsealed_not_supported",
                    "firmware_unsealed_not_supported",
                )
            if src == sealed.resolve():
                return _tracking_ensure(
                    source_dir, profile, local_dir=local_sealed, sealed=True, **kw
                )
            raise AssertionError(f"unexpected ensure source_dir={src}")

        return ensure, FirmwareCacheError, None

    monkeypatch.setattr(mod, "_load_firmware_cache_api", load_by_source)
    monkeypatch.setattr(
        mod,
        "acquire_host_lock",
        lambda *a, **k: lock_hits.append("lock")
        or os.open(tmp_path / f"lock-{len(lock_hits)}", os.O_CREAT | os.O_RDWR, 0o666),
    )

    def fake_spd(cmd, *a, **k):
        state_changes.append("spd")
        joined = " ".join(str(x) for x in cmd)
        if "/var/tmp/stp_flash" in joined:
            stp_flash_writes.append(joined)
        raise RuntimeError("stop-after-lock")

    monkeypatch.setattr(mod, "run_spd_dump", fake_spd)

    family_args = {
        "family": "P525AA",
        "verify_root": False,
        "skip_battery": True,
        "require_port_binding": False,
    }

    # 未封包身份：latest 路由到 unsealed_v1 → 取锁 / 状态变更前 fail-closed
    _install_fake_adb(
        monkeypatch,
        mod,
        props={
            "ro.product.model": "MLY-LX2-CA",
            "ro.product.device": "MLY",
            "ro.product.name": "MLY-LX2-CA",
            "ro.build.type": "userdebug",
            "ro.build.version.incremental": "x",
        },
        adb_calls=adb_calls,
        clock=clock,
    )
    with pytest.raises(Exception) as ei_unsealed:
        mod.run(family_args, "SN1", mod.Progress(), [])
    assert "firmware_unsealed_not_supported" in str(ei_unsealed.value)
    assert ensure_sources and Path(ensure_sources[-1]).resolve() == unsealed.resolve()
    assert lock_hits == []
    assert state_changes == []
    assert ["reboot", "autodloader"] not in adb_calls

    # 已封包身份：latest 路由到 sealed_v1 → 走本地缓存，零 /var/tmp/stp_flash 写入
    ensure_sources.clear()
    adb_calls.clear()
    _install_fake_adb(
        monkeypatch,
        mod,
        props={
            "ro.product.model": "MLY-LX2",
            "ro.product.device": "MLY",
            "ro.product.name": "MLY-LX2",
            "ro.build.type": "userdebug",
            "ro.build.version.incremental": "x",
            "ro.build.sw_internal_version": "old",
        },
        post_props={
            "ro.product.model": "MLY-LX2",
            "ro.product.device": "MLY",
            "ro.product.name": "MLY-LX2",
            "ro.build.type": "userdebug",
            "ro.build.version.incremental": "x",
            "ro.build.sw_internal_version": "sealed_v1",
        },
        adb_calls=adb_calls,
        clock=clock,
    )
    with pytest.raises(RuntimeError, match="stop-after-lock"):
        mod.run(family_args, "SN1", mod.Progress(), [])
    assert ensure_sources and Path(ensure_sources[-1]).resolve() == sealed.resolve()
    assert "lock" in lock_hits
    assert state_changes == ["spd"]
    assert stp_flash_writes == []
    assert not any("/var/tmp/stp_flash" in " ".join(c) for c in adb_calls)


def test_u4_bsl_source_has_no_var_tmp_stp_flash_writes():
    from tools.dev.source_anchor import SourceGuard

    # 正锚点：L1 缓存接入仍在本文件；否定断言防私有缓存/锁内拷贝回潮（#2639）。
    guard = (
        SourceGuard.of_repo_path("backend/agent/scripts/flash_firmware_unisoc_bsl/flash_firmware_unisoc_bsl.py")
        .anchored('profile="unisoc_bsl"')
        .anchored("firmware_unsealed_not_supported")
    )
    guard.assert_absent(
        "def prepare_local_cache",
        why="#4086 U4 / ADR-0064 D4：BSL 私有 prepare_local_cache 已退役，不得回潮",
    )
    guard.assert_absent(
        "_copy_if_changed",
        why="#4086 U4：锁内/私有 _copy_if_changed 已删除，不得回潮",
    )
    guard.assert_absent(
        '_DEFAULT_CACHE_DIR = "/var/tmp/stp_flash"',
        why="#4086 U4：不得恢复 /var/tmp/stp_flash 默认缓存目录常量",
    )
    guard.assert_absent(
        '"/var/tmp/stp_flash"',
        why="#4086 U4：不得在代码字面量中写入 /var/tmp/stp_flash 路径（changelog 无引号形式除外）",
    )


# ── 5. pipeline_engine 零新增 script_name 分支 ───────────────────────────


def test_u4_pipeline_engine_no_new_script_name_branches():
    from tools.dev.source_anchor import SourceGuard

    # 相对合入前：不得新增三刷机族字面量特判（U2 已保证双路径 PYTHONPATH，无 script_name 分支）
    guard = SourceGuard.of_repo_path("backend/agent/pipeline_engine.py").anchored(
        "class PipelineEngine:"
    )
    for name in ("flash_firmware_unisoc_bsl", "flash_firmware_unisoc", "flash_firmware"):
        guard.assert_absent(
            f'script_name == "{name}"',
            why="#4086 §6 / #3779：不得向 pipeline_engine 新增刷机族 script_name 特判",
        )
        guard.assert_absent(
            f"script_name == '{name}'",
            why="#4086 §6 / #3779：不得向 pipeline_engine 新增刷机族 script_name 特判",
        )


def test_u4_capabilities_declare_firmware_cache_v1():
    for family in ("flash_firmware", "flash_firmware_unisoc", "flash_firmware_unisoc_bsl"):
        data = json.loads((_SCRIPTS / family / "capabilities.json").read_text(encoding="utf-8"))
        assert "firmware_cache_v1" in data.get("requires_agent_capabilities", [])
