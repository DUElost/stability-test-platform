"""Tests for AEE db_history incremental processor (D1)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from backend.agent.aee.db_history import (
    parse_db_history_line,
    parse_vendor_db_history_line,
    state_key,
)
from backend.agent.aee.folder_name import get_aee_log_folder_name
from backend.agent.aee.processor import ProcessConfig, process_device_logs
from backend.agent.aee.paths import resolve_device_output_dir
from backend.agent.aee.timestamp import format_timestamp_for_filename, parse_timestamp


class _MemStore:
    def __init__(self) -> None:
        self._data: dict[str, str] = {}

    def get_state(self, key: str, default: str = "") -> str:
        return self._data.get(key, default)

    def set_state(self, key: str, value: str) -> None:
        self._data[key] = value


class _FakeAdbPull:
    def __init__(self, serial: str, history_by_path: dict[str, str]) -> None:
        self.serial = serial
        self.history_by_path = history_by_path
        self.pulled: list[tuple[str, str]] = []

    def shell(self, serial: str, cmd: str, timeout: int = 30):
        if "getprop" in cmd:
            props = {
                "ro.product.name": "X6851-OP",
                "ro.build.display.id": "X6851-OP-16.3.0.022(SU_0401)",
                "ro.build.version.incremental": "0401",
                "ro.build.version.release": "16",
            }
            for key, val in props.items():
                if key in cmd:
                    return type("R", (), {"stdout": val})()
        if cmd.startswith("cat "):
            remote = cmd[4:].replace("/db_history", "")
            body = self.history_by_path.get(remote, "")
            return type("R", (), {"stdout": body})()
        return type("R", (), {"stdout": ""})()


def test_aee_reconciler_imports_when_only_agent_package_is_deployed(tmp_path):
    """Hot-update 只部署 backend/agent 时,AEE reconciler 仍必须能独立 import。"""
    deployed_parent = Path(__file__).resolve().parents[2]
    code = "\n".join([
        "import importlib.abc",
        "import sys",
        "class _BlockBackendCoreAeeMetadata(importlib.abc.MetaPathFinder):",
        "    def find_spec(self, fullname, path=None, target=None):",
        "        if fullname == 'backend.core.aee_metadata':",
        "            raise ModuleNotFoundError('blocked backend.core.aee_metadata')",
        "        return None",
        "sys.meta_path.insert(0, _BlockBackendCoreAeeMetadata())",
        "import agent.aee.db_history",
        "import agent.aee.processor",
        "import agent.aee.reconciler",
        "assert 'backend.core.aee_metadata' not in sys.modules",
    ])
    env = os.environ.copy()
    env["PYTHONPATH"] = str(deployed_parent)
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        timeout=10,
    )

    assert proc.returncode == 0, proc.stderr


def test_parse_db_history_line():
    line = "/data/aee_exp/db.01,CRASH,pkg,_,_,_,_,_,com.example.app,2026-05-27 10:15:22.123"
    parsed = parse_db_history_line(line)
    assert parsed is not None
    assert parsed["db_path"] == "/data/aee_exp/db.01"
    assert parsed["pkg_name"] == "com.example.app"
    assert parsed["event_type"] == "CRASH"


@pytest.mark.parametrize(
    ("raw_event_type", "expected_event_type", "expected_subtype"),
    [
        ("Java (JE)", "CRASH", "JE"),
        ("Native (NE)", "CRASH", "NE"),
        ("SIGSEGV", "CRASH", "NE"),
        ("ANR", "ANR", "ANR"),
    ],
)
def test_parse_db_history_line_normalizes_real_device_event_types(
    raw_event_type,
    expected_event_type,
    expected_subtype,
):
    line = (
        f"/data/aee_exp/db.01,{raw_event_type},pkg,_,_,_,_,_,"
        "com.example.app,2026-05-27 10:15:22.123"
    )
    parsed = parse_db_history_line(line)
    assert parsed is not None
    assert parsed["raw_event_type"] == raw_event_type
    assert parsed["event_type"] == expected_event_type
    assert parsed["event_subtype"] == expected_subtype


def test_parse_vendor_db_history_line_filters_invalid():
    assert parse_vendor_db_history_line("androidboot.bootreason=xxx") is None
    line = "/data/vendor/aee_exp/db.02,CRASH,pkg,_,_,_,_,_,vendor.app,2026-05-27 11:00:00"
    parsed = parse_vendor_db_history_line(line)
    assert parsed is not None
    assert parsed["db_path"].startswith("/data/vendor/aee_exp/")


def test_parse_vendor_db_history_line_preserves_vendor_subtype():
    line = (
        "/data/vendor/aee_exp/db.03,System API Dump,pkg,_,_,_,_,_,"
        "vendor.app,2026-05-27 11:05:00"
    )
    parsed = parse_vendor_db_history_line(line)
    assert parsed is not None
    assert parsed["raw_event_type"] == "System API Dump"
    assert parsed["event_type"] == "CRASH"
    assert parsed["event_subtype"] == "System API Dump"


def test_get_aee_log_folder_name():
    def _getprop(name: str, timeout: int = 10) -> str:
        return {
            "ro.product.name": "X6851-OP",
            "ro.build.display.id": "X6851-OP-16.3.0.022(SU_0401)",
            "ro.build.version.incremental": "0401",
            "ro.build.version.release": "16",
        }.get(name, "")

    name = get_aee_log_folder_name(getprop=_getprop, run_date_stamp="0527")
    assert name is not None
    assert "X6851-OP" in name
    assert name.endswith("_0527_MonkeyAEEinfo")


def test_process_device_logs_incremental(tmp_path, monkeypatch):
    monkeypatch.setenv("STP_AEE_NFS_ROOT", str(tmp_path))
    store = _MemStore()
    line = "/data/aee_exp/db.01,CRASH,pkg,_,_,_,_,_,com.app,2026-05-27 10:15:22.123"
    history = line + "\n"

    def shell_fn(cmd: str, timeout: int):
        if "getprop" in cmd:
            props = {
                "ro.product.name": "X6851-OP",
                "ro.build.display.id": "X6851-OP-16.3.0.022(SU_0401)",
                "ro.build.version.incremental": "0401",
                "ro.build.version.release": "16",
            }
            for key, val in props.items():
                if key in cmd:
                    return val
        if "cat /data/aee_exp/db_history" in cmd:
            return history
        if "cat /data/vendor/aee_exp/db_history" in cmd:
            return ""
        return ""

    pulled: list[str] = []

    def pull_fn(remote: str, local: str, timeout: int) -> bool:
        pulled.append(remote)
        Path(local).mkdir(parents=True, exist_ok=True)
        # T0.5-1 strict verify 要求至少含一个 .dbg 关键文件
        (Path(local) / "main.dbg").write_text("ok", encoding="utf-8")
        return True

    from backend.agent.aee import processor as proc_mod

    monkeypatch.setattr(proc_mod, "make_adb_shell_fn", lambda serial, adb_path: lambda cmd, t: shell_fn(cmd, t))
    monkeypatch.setattr(proc_mod, "make_adb_pull_fn", lambda serial, adb_path: pull_fn)
    monkeypatch.setattr(proc_mod, "export_correlated_mobilelogs", lambda **kw: {"matched": 0, "pulled": 0})
    monkeypatch.setattr(proc_mod, "export_bugreport_for_timestamp", lambda **kw: True)

    cfg = ProcessConfig(export_mobilelog=False, export_bugreport=False)
    r1 = process_device_logs(serial="dev1", job_id=42, state_store=store, config=cfg)
    assert r1.pulled == 1
    assert len(pulled) == 1

    r2 = process_device_logs(serial="dev1", job_id=42, state_store=store, config=cfg)
    assert r2.pulled == 0
    assert r2.skipped_known >= 0
    assert len(pulled) == 1

    key = state_key("dev1", "aee_exp")
    saved = json.loads(store.get_state(key))
    assert line in saved


def test_format_timestamp_for_filename():
    ts = "2026-05-27 10:15:22.456"
    assert format_timestamp_for_filename(ts).startswith("2026_0527_101522_456")
    assert parse_timestamp(ts) is not None


def test_process_logs_strict_verify_rejects_dir_without_dbg(tmp_path, monkeypatch):
    """T0.5-1 P0-#1: pull 后目录无 .dbg 文件 → strict verify 失败 → pull_verify_failed 错误。"""
    monkeypatch.setenv("STP_AEE_NFS_ROOT", str(tmp_path))
    store = _MemStore()
    line = "/data/aee_exp/db.99,CRASH,pkg,_,_,_,_,_,com.bad,2026-05-27 10:15:22.123"

    def shell_fn(cmd: str, timeout: int):
        if "getprop" in cmd:
            props = {
                "ro.product.name": "X6851-OP",
                "ro.build.display.id": "X6851-OP-16.3.0.022(SU_0401)",
                "ro.build.version.incremental": "0401",
                "ro.build.version.release": "16",
            }
            for key, val in props.items():
                if key in cmd:
                    return val
        if "cat /data/aee_exp/db_history" in cmd:
            return line + "\n"
        if "cat /data/vendor/aee_exp/db_history" in cmd:
            return ""
        return ""

    def pull_fn(remote: str, local: str, timeout: int) -> bool:
        # 只写非 .dbg 文件 → strict verify 缺少关键文件
        Path(local).mkdir(parents=True, exist_ok=True)
        (Path(local) / "noise.txt").write_text("garbage", encoding="utf-8")
        return True

    from backend.agent.aee import processor as proc_mod

    monkeypatch.setattr(proc_mod, "make_adb_shell_fn", lambda serial, adb_path: lambda cmd, t: shell_fn(cmd, t))
    monkeypatch.setattr(proc_mod, "make_adb_pull_fn", lambda serial, adb_path: pull_fn)
    monkeypatch.setattr(proc_mod, "export_correlated_mobilelogs", lambda **kw: {"matched": 0, "pulled": 0})
    monkeypatch.setattr(proc_mod, "export_bugreport_for_timestamp", lambda **kw: True)

    cfg = ProcessConfig(export_mobilelog=False, export_bugreport=False)
    r = process_device_logs(serial="dev_strict", job_id=77, state_store=store, config=cfg)
    assert r.pulled == 0
    assert any(e.startswith("pull_verify_failed:") for e in r.errors), r.errors
    # 失败目录应清理掉
    nfs_root = tmp_path
    for db_dir in nfs_root.rglob("*db.99*"):
        assert not db_dir.exists() or not any(db_dir.iterdir()), "失败 pull 应清理目录"


def test_process_logs_mobilelog_uses_stp_subdir_default(tmp_path, monkeypatch):
    """ADR-0025 D3: mobilelog 落在事件目录(local_target_dir)内的 mobilelog/ 子目录。"""
    monkeypatch.setenv("STP_AEE_NFS_ROOT", str(tmp_path))
    monkeypatch.delenv("STP_WATCHER_AEE_SUBDIR_LAYOUT", raising=False)
    store = _MemStore()
    line = "/data/aee_exp/db.01,CRASH,pkg,_,_,_,_,_,com.app,2026-05-27 10:15:22.123"

    def shell_fn(cmd: str, timeout: int):
        if "getprop" in cmd:
            props = {
                "ro.product.name": "X6851-OP",
                "ro.build.display.id": "X6851-OP-16.3.0.022(SU_0401)",
                "ro.build.version.incremental": "0401",
                "ro.build.version.release": "16",
            }
            for key, val in props.items():
                if key in cmd:
                    return val
        if "cat /data/aee_exp/db_history" in cmd:
            return line + "\n"
        if "cat /data/vendor/aee_exp/db_history" in cmd:
            return ""
        return ""

    def pull_fn(remote: str, local: str, timeout: int) -> bool:
        Path(local).mkdir(parents=True, exist_ok=True)
        (Path(local) / "main.dbg").write_text("data", encoding="utf-8")
        return True

    captured: dict[str, Path] = {}

    def fake_mobilelog(**kw):
        captured["output_dir"] = kw["output_dir"]
        # 模拟 mobilelog.py 默认行为:写到 resolve_mobilelog_subdir() 子目录
        from backend.agent.aee.mobilelog import _resolve_mobilelog_subdir

        target = kw["output_dir"] / _resolve_mobilelog_subdir()
        target.mkdir(parents=True, exist_ok=True)
        (target / "main_log_dummy").write_text("x", encoding="utf-8")
        return {"matched": 1, "pulled": 1}

    from backend.agent.aee import processor as proc_mod

    monkeypatch.setattr(proc_mod, "make_adb_shell_fn", lambda serial, adb_path: lambda cmd, t: shell_fn(cmd, t))
    monkeypatch.setattr(proc_mod, "make_adb_pull_fn", lambda serial, adb_path: pull_fn)
    monkeypatch.setattr(proc_mod, "export_correlated_mobilelogs", fake_mobilelog)
    monkeypatch.setattr(proc_mod, "export_bugreport_for_timestamp", lambda **kw: True)

    cfg = ProcessConfig(export_mobilelog=True, export_bugreport=False)
    r = process_device_logs(serial="dev_sd", job_id=88, state_store=store, config=cfg)
    assert r.pulled == 1
    assert "output_dir" in captured
    # ADR-0018 2026-06-18: output_dir 应为事件目录(local_target_dir),非设备级 base_output_dir
    # 事件目录名格式: {ts}_{db_path_basename},如 2026_0527_101522_123_db.01
    assert captured["output_dir"].name.startswith("2026_"), \
        f"output_dir 应为事件目录,实际: {captured['output_dir']}"
    assert "db.01" in captured["output_dir"].name, \
        f"事件目录应含 db_path basename,实际: {captured['output_dir'].name}"
    from backend.agent.aee.mobilelog import _resolve_mobilelog_subdir

    subdir = _resolve_mobilelog_subdir()
    landed = captured["output_dir"] / subdir
    assert landed.is_dir(), f"应写入事件目录内 {subdir}/,实际: {list(captured['output_dir'].iterdir())}"


def test_process_logs_mobilelog_subdir_stp_fallback(tmp_path, monkeypatch):
    """T0.5-2 D3: default (stp) 时 mobilelog/ 布局；correlated 逃生口回退旧布局。"""
    monkeypatch.delenv("STP_WATCHER_AEE_SUBDIR_LAYOUT", raising=False)
    from backend.agent.aee.mobilelog import _resolve_mobilelog_subdir

    assert _resolve_mobilelog_subdir() == "mobilelog"

    monkeypatch.setenv("STP_WATCHER_AEE_SUBDIR_LAYOUT", "correlated")
    assert _resolve_mobilelog_subdir() == "correlated_mobilelogs"


# ----------------------------------------------------------------------
# M0/PR #2 — on_new_entry 回调
# ----------------------------------------------------------------------


def _setup_pdl_stubs(monkeypatch, history_line: str):
    """共享桩:伪造 shell/pull/mobilelog/bugreport,使 process_device_logs 走通成功 pull 分支。"""

    def shell_fn(cmd: str, timeout: int):
        if "getprop" in cmd:
            props = {
                "ro.product.name": "X6851-OP",
                "ro.build.display.id": "X6851-OP-16.3.0.022(SU_0401)",
                "ro.build.version.incremental": "0401",
                "ro.build.version.release": "16",
            }
            for key, val in props.items():
                if key in cmd:
                    return val
        if "cat /data/aee_exp/db_history" in cmd:
            return history_line + "\n"
        if "cat /data/vendor/aee_exp/db_history" in cmd:
            return ""
        return ""

    def pull_fn(remote: str, local: str, timeout: int) -> bool:
        Path(local).mkdir(parents=True, exist_ok=True)
        (Path(local) / "main.dbg").write_text("ok", encoding="utf-8")
        return True

    from backend.agent.aee import processor as proc_mod

    monkeypatch.setattr(proc_mod, "make_adb_shell_fn", lambda serial, adb_path: lambda cmd, t: shell_fn(cmd, t))
    monkeypatch.setattr(proc_mod, "make_adb_pull_fn", lambda serial, adb_path: pull_fn)
    monkeypatch.setattr(proc_mod, "export_correlated_mobilelogs", lambda **kw: {"matched": 0, "pulled": 0})
    monkeypatch.setattr(proc_mod, "export_bugreport_for_timestamp", lambda **kw: True)


def test_process_device_logs_on_new_entry_called(tmp_path, monkeypatch):
    """on_new_entry 回调:pull 成功时被调用一次,payload 字段完整;第二次同 line 不再触发。"""
    monkeypatch.setenv("STP_AEE_NFS_ROOT", str(tmp_path))
    store = _MemStore()
    line = "/data/aee_exp/db.42,Java (JE),pkg,_,_,_,_,_,com.example.app,2026-05-28 10:15:22.123"
    _setup_pdl_stubs(monkeypatch, line)

    captured: list[dict] = []

    def on_new(payload: dict) -> None:
        captured.append(payload)

    cfg = ProcessConfig(export_mobilelog=False, export_bugreport=False)
    r1 = process_device_logs(
        serial="dev_cb", job_id=42,
        state_store=store, config=cfg, on_new_entry=on_new,
    )
    assert r1.pulled == 1
    assert len(captured) == 1
    payload = captured[0]
    assert payload["line"] == line
    assert payload["aee_type"] == "aee_exp"
    assert payload["parsed"]["db_path"] == "/data/aee_exp/db.42"
    assert payload["parsed"]["pkg_name"] == "com.example.app"
    assert payload["parsed"]["timestamp"] == "2026-05-28 10:15:22.123"
    assert payload["parsed"]["event_type"] == "CRASH"
    assert payload["parsed"]["raw_event_type"] == "Java (JE)"
    assert payload["parsed"]["event_subtype"] == "JE"
    assert isinstance(payload["output_subdir"], Path)
    assert payload["output_subdir"].is_dir()
    # output_subdir 应位于 aee_type 子目录下
    assert payload["output_subdir"].parent.name == "aee_exp"

    # 第二次:line 已 processed → on_new_entry 不再触发
    r2 = process_device_logs(
        serial="dev_cb", job_id=42,
        state_store=store, config=cfg, on_new_entry=on_new,
    )
    assert r2.pulled == 0
    assert len(captured) == 1, "已处理的 line 不应再次触发回调"


def test_process_device_logs_on_new_entry_exception_swallowed(tmp_path, monkeypatch):
    """on_new_entry 抛异常时:主流程不崩,pulled 依旧 +1,line 被标记 processed。"""
    monkeypatch.setenv("STP_AEE_NFS_ROOT", str(tmp_path))
    store = _MemStore()
    line = "/data/aee_exp/db.77,CRASH,pkg,_,_,_,_,_,com.boom.app,2026-05-28 10:15:22.123"
    _setup_pdl_stubs(monkeypatch, line)

    call_count = {"n": 0}

    def on_new_explode(payload: dict) -> None:
        call_count["n"] += 1
        raise RuntimeError("intentional callback failure")

    cfg = ProcessConfig(export_mobilelog=False, export_bugreport=False)
    r = process_device_logs(
        serial="dev_boom", job_id=77,
        state_store=store, config=cfg, on_new_entry=on_new_explode,
    )
    # 回调抛错被吞 → 主流程继续:pulled 仍 +1、line 入 processed
    assert r.pulled == 1
    assert call_count["n"] == 1
    assert line in r.new_timestamps or len(r.new_timestamps) == 1
    key = state_key("dev_boom", "aee_exp")
    saved = json.loads(store.get_state(key))
    assert line in saved, "回调失败不应阻止 line 被标记为 processed"


def test_process_device_logs_prioritizes_newer_entries_when_backlog_exists(tmp_path, monkeypatch):
    """backlog 存在时应优先处理更新的 db_history 条目,避免 fresh AEE 排到最后。"""
    monkeypatch.setenv("STP_AEE_NFS_ROOT", str(tmp_path))
    store = _MemStore()
    lines = [
        "/data/aee_exp/db.01,CRASH,pkg,_,_,_,_,_,com.old,2026-05-28 10:00:00.000",
        "/data/aee_exp/db.02,CRASH,pkg,_,_,_,_,_,com.mid,2026-05-28 10:05:00.000",
        "/data/aee_exp/db.03,CRASH,pkg,_,_,_,_,_,com.new,2026-05-28 10:10:00.000",
    ]
    _setup_pdl_stubs(monkeypatch, "\n".join(lines))

    seen_paths: list[str] = []

    def on_new(payload: dict) -> None:
        seen_paths.append(payload["parsed"]["db_path"])

    cfg = ProcessConfig(export_mobilelog=False, export_bugreport=False)
    r = process_device_logs(
        serial="dev_fresh_first",
        job_id=89,
        state_store=store,
        config=cfg,
        on_new_entry=on_new,
    )

    assert r.pulled == 3
    assert seen_paths == [
        "/data/aee_exp/db.03",
        "/data/aee_exp/db.02",
        "/data/aee_exp/db.01",
    ]


def test_process_device_logs_emits_when_local_aee_dir_already_exists(tmp_path, monkeypatch):
    """目录已落盘但当前 run 未 processed 时,仍应回调并纳入当前 run。"""
    monkeypatch.setenv("STP_AEE_NFS_ROOT", str(tmp_path))
    store = _MemStore()
    line = "/data/aee_exp/db.55,CRASH,pkg,_,_,_,_,_,com.reuse.app,2026-06-01 19:20:00.123"
    _setup_pdl_stubs(monkeypatch, line)

    folder_name = get_aee_log_folder_name(
        getprop=lambda name, timeout=10: {
            "ro.product.name": "X6851-OP",
            "ro.build.display.id": "X6851-OP-16.3.0.022(SU_0401)",
            "ro.build.version.incremental": "0401",
            "ro.build.version.release": "16",
        }.get(name, ""),
        run_date_stamp="0601",
    )
    assert folder_name is not None
    existing_dir = resolve_device_output_dir(
        nfs_root=tmp_path,
        folder_name=folder_name,
        serial="dev_existing_dir",
    ) / "aee_exp" / f"{format_timestamp_for_filename('2026-06-01 19:20:00.123')}_db.55"
    existing_dir.mkdir(parents=True, exist_ok=True)
    (existing_dir / "main.dbg").write_text("ok", encoding="utf-8")

    captured: list[dict] = []
    cfg = ProcessConfig(export_mobilelog=False, export_bugreport=False)
    r = process_device_logs(
        serial="dev_existing_dir",
        job_id=90,
        state_store=store,
        config=cfg,
        run_date_stamp="0601",
        on_new_entry=captured.append,
    )

    assert r.pulled == 1
    assert captured and captured[0]["parsed"]["db_path"] == "/data/aee_exp/db.55"
    saved = json.loads(store.get_state(state_key("dev_existing_dir", "aee_exp"), "[]"))
    assert line in saved


def test_process_device_logs_enriches_from_local_exp_main(tmp_path, monkeypatch):
    """本地 AEE 目录已有 __exp_main.txt 时,应回填 subtype 和 package。"""
    monkeypatch.setenv("STP_AEE_NFS_ROOT", str(tmp_path))
    store = _MemStore()
    line = "/data/aee_exp/db.66,CRASH,pkg,_,_,_,_,_,unknown,2026-06-01 20:20:00.123"
    _setup_pdl_stubs(monkeypatch, line)

    folder_name = get_aee_log_folder_name(
        getprop=lambda name, timeout=10: {
            "ro.product.name": "X6851-OP",
            "ro.build.display.id": "X6851-OP-16.3.0.022(SU_0401)",
            "ro.build.version.incremental": "0401",
            "ro.build.version.release": "16",
        }.get(name, ""),
        run_date_stamp="0601",
    )
    assert folder_name is not None
    existing_dir = resolve_device_output_dir(
        nfs_root=tmp_path,
        folder_name=folder_name,
        serial="dev_existing_meta",
    ) / "aee_exp" / f"{format_timestamp_for_filename('2026-06-01 20:20:00.123')}_db.66"
    existing_dir.mkdir(parents=True, exist_ok=True)
    (existing_dir / "main.dbg").write_text("ok", encoding="utf-8")
    (existing_dir / "__exp_main.txt").write_text(
        "\n".join([
            "Build Info: 'foo'",
            "Exception Class: Java (JE)",
            "Current Executing Process:",
            "com.android.systemui",
            "Package: com.android.settings",
        ]),
        encoding="utf-8",
    )

    captured: list[dict] = []
    cfg = ProcessConfig(export_mobilelog=False, export_bugreport=False)
    r = process_device_logs(
        serial="dev_existing_meta",
        job_id=91,
        state_store=store,
        config=cfg,
        run_date_stamp="0601",
        on_new_entry=captured.append,
    )

    assert r.pulled == 1
    assert captured
    assert captured[0]["parsed"]["event_subtype"] == "JE"
    assert captured[0]["parsed"]["pkg_name"] == "com.android.settings"


def test_process_device_logs_persists_processed_before_side_effects(tmp_path, monkeypatch):
    """执行 mobilelog 副作用前,processed/pending 状态应已落盘。"""
    monkeypatch.setenv("STP_AEE_NFS_ROOT", str(tmp_path))
    store = _MemStore()
    line = "/data/aee_exp/db.88,CRASH,pkg,_,_,_,_,_,com.persist.app,2026-05-28 10:15:22.123"
    _setup_pdl_stubs(monkeypatch, line)

    from backend.agent.aee import processor as proc_mod

    observed: dict[str, object] = {}

    def fake_mobilelog(**kw):
        observed["saved"] = json.loads(
            store.get_state(state_key("dev_persist", "aee_exp"), "[]")
        )
        observed["pending"] = json.loads(
            store.get_state("watcher:aee:dev_persist:aee_exp:pending_pull", "{}")
        )
        return {"matched": 0, "pulled": 0}

    monkeypatch.setattr(proc_mod, "export_correlated_mobilelogs", fake_mobilelog)

    cfg = ProcessConfig(export_mobilelog=True, export_bugreport=False)
    r = process_device_logs(
        serial="dev_persist",
        job_id=88,
        state_store=store,
        config=cfg,
    )
    assert r.pulled == 1
    assert line in observed["saved"]
    assert observed["pending"] == {}
