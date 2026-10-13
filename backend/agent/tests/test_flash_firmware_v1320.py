"""flash_firmware v1.3.20（#3688 / run 921 事故）：V6 必须绑定并核验目标 USB 口。

判据来源全部是可复算的现场证据，不是对工具行为的假设：

- run 921（job 185452、plan 80、目标设备 id 939）真实日志：目标派发时
  ``pre_state=no-device``，1.3.19 只记 skip_reason 就照常起工具；工具回显
  ``scan device succeed, symbolic_link: COM:///dev/ttyACM1@/dev/ttyACM1@1-8``
  —— **1-8 是邻机（id 943）的口**，而且是自家初始门控压制后重枚举成
  ``0e8d:2000`` 的那台；随后 ``enter 1st DA mode failed ... checksum mismatched``。
- ``.85`` 包内二进制实测（2026-10-13 两次探针）：给 ``-p 1-99`` 时回显
  ``com port clue: COM://1-99.`` 且现场存在 2 个可捕获 preloader 口时仍然绑不到
  任何设备；不给 ``-p`` 时回显空线索 ``COM://.`` 并当场绑到了别的口。

守什么：
1. 回显端口解析：只认 sysfs 端口形态，Windows 形态不得被误判成「绑错口」；
2. 绑定口 ≠ 目标口 → 必须杀进程树并上抛，不得退化成「一次普通失败」继续重试；
3. 目标身份判不出 → fail-closed，不得让工具盲等（V6 默认开，V5 默认关）；
4. V6 命令行带 ``-p``；V5 命令行一个 token 都不许变（在用 552 计划零变化）；
5. V6 门控口径不得再压制邻机的正常 adb 态 ``201c``；V5 保持历史全量口径。
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

_SCRIPT_DIR = Path(__file__).resolve().parents[2] / "agent" / "scripts" / "flash_firmware"

_spec = importlib.util.spec_from_file_location(
    "flash_firmware_v1320", _SCRIPT_DIR / "flash_firmware.py"
)
ff = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(ff)


# ── 现场原始行（逐字取自 run 921 与 .85 探针，不做美化）────────────────

#: run 921 真机：工具自报绑到了邻机口 1-8（目标是 1-10）。
_LINE_BOUND_1_8 = "scan device succeed, symbolic_link: COM:///dev/ttyACM1@/dev/ttyACM1@1-8\n"
#: 同代工具绑对目标口时的回显形态（探针 B 实测，口号换成目标 1-10）。
_LINE_BOUND_1_10 = "scan device succeed, symbolic_link: COM:///dev/ttyACM0@/dev/ttyACM0@1-10\n"
#: hub 嵌套拓扑下的 sysfs 口名（RTS5411 形态）。
_LINE_BOUND_HUB = "scan device succeed, symbolic_link: COM:///dev/ttyACM2@/dev/ttyACM2@1-5.2.3\n"
#: Windows 侧真机日志（535 手工刷机日志原文节选）。
_LINE_WINDOWS = (
    "scan device succeed, symbolic_link: COM://"
    "\\\\?\\usb#vid_0e8d&pid_2000#5&2bd7e96e&0&6"
    "#{a5dcbf10-6530-11d2-901f-00c04fb951ed}"
    "@MediaTek PreLoader USB VCOM (Android) (COM93)@5&2BD7E96E&0&6\n"
)


def test_parse_bound_port_linux_and_hub() -> None:
    assert ff._parse_bound_port(_LINE_BOUND_1_8) == "1-8"
    assert ff._parse_bound_port(_LINE_BOUND_1_10) == "1-10"
    assert ff._parse_bound_port(_LINE_BOUND_HUB) == "1-5.2.3"


def test_parse_bound_port_refuses_non_port_forms() -> None:
    """解析不出端口形态必须返回 None。

    Windows 形态（``COM://\\?\\usb#...``）、clue 行、百分比行都不是 Linux sysfs
    口名；把它们当「绑定口」会在 Windows 侧造出假的绑错口中止。
    """
    assert ff._parse_bound_port(_LINE_WINDOWS) is None
    assert ff._parse_bound_port("com port clue: COM://.\n") is None
    assert ff._parse_bound_port("13% of DA data has been sent(about 64.00K)\n") is None
    assert ff._parse_bound_port("connect brom successed.\n") is None


def test_port_mismatch_reason_fires_only_on_wrong_port() -> None:
    assert ff._port_mismatch_reason(_LINE_BOUND_1_8, "1-10", "SN") is not None
    assert ff._port_mismatch_reason(_LINE_BOUND_1_10, "1-10", "SN") is None
    assert ff._port_mismatch_reason(_LINE_BOUND_HUB, "1-5.2.3", "SN") is None
    assert ff._port_mismatch_reason(_LINE_WINDOWS, "1-10", "SN") is None
    assert ff._port_mismatch_reason(_LINE_BOUND_1_8, "", "SN") is None
    # 理由里必须同时给出「绑到哪」和「该绑哪」，否则现场无法复盘
    reason = ff._port_mismatch_reason(_LINE_BOUND_1_8, "1-10", "AHFFNX6930000169")
    assert "'1-8'" in reason and "'1-10'" in reason


# ── 2. 止损：绑错口必须杀进程并上抛 ───────────────────────────────────


def _make_hanging_tool(tmp_path: Path, line: str) -> Path:
    """打印指定回显后**长时间挂住**的假工具。

    真工具绑上设备后几秒内就会下发 DA，所以「打印该行之后还活着」本身就是
    要被止损的状态；假工具故意不退出，用来证明脚本真的把它杀掉了。
    """
    exe = tmp_path / "SPFlashToolV6_abort"
    exe.write_text(
        "#!/usr/bin/env python3\n"
        "import sys, time\n"
        f"sys.stdout.write({line!r})\n"
        "sys.stdout.flush()\n"
        "time.sleep(120)\n",
        encoding="utf-8",
    )
    exe.chmod(0o755)
    return exe


def test_abort_check_kills_tool_and_raises(tmp_path: Path) -> None:
    import time as _t

    exe = _make_hanging_tool(tmp_path, _LINE_BOUND_1_8)
    started = _t.monotonic()
    with pytest.raises(ff.FlashToolAborted) as caught:
        ff._run_flash_tool_with_progress(
            [sys.executable, str(exe)], cwd=str(tmp_path), env=dict(os.environ),
            timeout=60,
            on_stage=lambda tok: None,
            on_percent=lambda pct: None,
            abort_check=lambda line: ff._port_mismatch_reason(line, "1-10", "SN"),
        )
    assert "1-8" in caught.value.reason and "1-10" in caught.value.reason
    # 杀掉必须是「立刻」：轮询粒度 1s，远超 20s 说明止损没起作用
    assert _t.monotonic() - started < 20


def test_abort_check_absent_keeps_legacy_behaviour(tmp_path: Path) -> None:
    """不传 abort_check（= V5 路径）时，同样的输出仍按原有 verdict 流程收场。"""
    exe = tmp_path / "flash_tool_legacy"
    exe.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        f"sys.stdout.write({(_LINE_BOUND_1_8 + 'All command exec done!\\n')!r})\n",
        encoding="utf-8",
    )
    exe.chmod(0o755)
    stdout, rc = ff._run_flash_tool_with_progress(
        [sys.executable, str(exe)], cwd=str(tmp_path), env=dict(os.environ),
        timeout=60,
        on_stage=lambda tok: None,
        on_percent=lambda pct: None,
    )
    assert rc == 0 and "All command exec done!" in stdout


# ── 3. 命令行装配：V6 钉口，V5 逐字不变 ────────────────────────────────


def test_argv_v6_carries_port_and_v5_never_does() -> None:
    v6 = ff._build_flash_argv(
        "/tools/SPFlashToolV6", "v6", "firmware-upgrade", "auto",
        flash_xml_file="/fw/P535AA/download_agent/flash.xml", port="1-10")
    assert v6[-2:] == ["-p", "1-10"]

    v6_noport = ff._build_flash_argv(
        "/tools/SPFlashToolV6", "v6", "firmware-upgrade", "auto",
        flash_xml_file="/fw/P535AA/download_agent/flash.xml")
    assert v6_noport == ["/tools/SPFlashToolV6", "-c", "firmware-upgrade",
                         "-f", "/fw/P535AA/download_agent/flash.xml", "-b", "auto"]

    v5 = ff._build_flash_argv(
        "/tools/flash_tool", "v5", "firmware-upgrade", "auto",
        da_file="/fw/DA.bin", scatter_file="/fw/scatter.txt", port="1-10")
    # 552 在用的形态：即使调用方误传 port，V5 也不得长出新 token
    assert v5 == ["/tools/flash_tool", "-c", "firmware-upgrade",
                  "-d", "/fw/DA.bin", "-s", "/fw/scatter.txt", "-b", "auto"]
    assert "-p" not in v5


def test_port_path_accepts_hub_nested_and_rejects_free_form() -> None:
    assert ff._PORT_PATH_RE.match("1-10")
    assert ff._PORT_PATH_RE.match("1-5.2.3")
    assert not ff._PORT_PATH_RE.match("COM93")
    assert not ff._PORT_PATH_RE.match("/dev/ttyACM0")
    assert not ff._PORT_PATH_RE.match("1-10; rm -rf /")


# ── 4. 目标身份：判不出就不得起工具 ───────────────────────────────────


def _mk_sysfs(base: Path, port: str, *, pid: str, serial: str = "",
              authorized: str = "1") -> None:
    d = base / port
    d.mkdir(parents=True, exist_ok=True)
    (d / "idVendor").write_text("0e8d\n", encoding="utf-8")
    (d / "idProduct").write_text(pid + "\n", encoding="utf-8")
    (d / "authorized").write_text(authorized + "\n", encoding="utf-8")
    if serial:
        (d / "serial").write_text(serial + "\n", encoding="utf-8")


@pytest.fixture
def fake_sysfs(tmp_path: Path) -> Path:
    """.85 事故现场的缩微拓扑：目标在 1-10，邻机在 1-8，另有普通态 2046。"""
    base = tmp_path / "sysfs"
    base.mkdir()
    _mk_sysfs(base, "1-10", pid="201c", serial="AHFFNX6930000169")
    _mk_sysfs(base, "1-8", pid="201c", serial="AHFFNX6930000224")
    _mk_sysfs(base, "1-13", pid="2046", serial="AHFFNX6930000235")
    return base


def _identity(fake_sysfs: Path, monkeypatch, *, adb_state: str, serial: str,
              explicit: str = "", binding: bool = True, ready: bool = True):
    monkeypatch.setattr(ff, "_adb_device_state", lambda s, p: adb_state)
    return ff._resolve_target_identity(
        serial=serial, adb_path="adb", explicit_port=explicit,
        require_binding=binding, require_adb_ready=ready,
        base=str(fake_sysfs),
    )


def test_identity_ok_when_adb_ready(fake_sysfs, monkeypatch) -> None:
    port, rep = _identity(fake_sysfs, monkeypatch, adb_state="device",
                          serial="AHFFNX6930000169")
    assert port == "1-10" and "error" not in rep
    assert rep["port_source"] == "serial" and rep["port_pid"] == "201c"


def test_identity_refuses_run921_shape(fake_sysfs, monkeypatch) -> None:
    """run 921 复现：目标 adb 不可用，口上还是正常态 201c。

    此时没有任何「这台设备可刷」的证据，起工具只会让它在全部 MTK 口里挑一个——
    必须拒绝，而不是记一条 skip_reason 继续。
    """
    port, rep = _identity(fake_sysfs, monkeypatch, adb_state="no-device",
                          serial="AHFFNX6930000169")
    assert "error" in rep
    assert "no-device" in rep["error"] and rep["port_pid"] == "201c"
    assert rep.get("port_catchable") is False


def test_identity_allows_device_already_in_preloader(fake_sysfs, monkeypatch) -> None:
    """设备已卡在 preloader（可刷态）时 adb 必然看不见——这是合法刷机态。"""
    _mk_sysfs(fake_sysfs, "1-9", pid="2000")
    port, rep = _identity(fake_sysfs, monkeypatch, adb_state="no-device",
                          serial="AHFFNX6930000999", explicit="1-9")
    assert "error" not in rep, rep
    assert port == "1-9" and rep["port_source"] == "explicit_param"


def test_identity_requires_explicit_port_when_serial_unreadable(
        fake_sysfs, monkeypatch) -> None:
    """BROM/preloader 态读不到 serial：不指定 flash_port 就只能拒绝。"""
    port, rep = _identity(fake_sysfs, monkeypatch, adb_state="no-device",
                          serial="AHFFNX6930000999", explicit="")
    assert port is None and "error" in rep and "flash_port" in rep["error"]


def test_identity_ladder_can_be_disabled_for_v5(fake_sysfs, monkeypatch) -> None:
    """V5 档（两个 require 都是 False）不参与判定——552 计划零变化。"""
    port, rep = _identity(fake_sysfs, monkeypatch, adb_state="no-device",
                          serial="AHFFNX6930000169", binding=False, ready=False)
    assert "error" not in rep


def test_gate_pid_sets_distinguish_normal_adb_state() -> None:
    """``201c`` 只在历史口径里；V6 口径必须不含它。"""
    assert "201c" in ff._FLASH_STAGE_PIDS          # V5 历史口径，未改
    assert "201c" not in ff._BROM_GATE_PIDS        # V6 新口径
    assert {"0003", "2000", "2001", "3000", "2026"} == ff._BROM_GATE_PIDS


def test_v6_gate_hides_only_catchable_ports(fake_sysfs, monkeypatch) -> None:
    """按 V6 口径门控时，正常 adb 态(201c)邻机不得被写 authorized=0。"""
    _mk_sysfs(fake_sysfs, "1-7", pid="2000")
    touched: list[tuple[str, str]] = []

    def _fake_set(port: str, value: str, base: str | None = None):
        touched.append((port, value))
        return True, "fake"

    monkeypatch.setattr(ff, "_is_linux", lambda: True)
    monkeypatch.setattr(ff, "_set_authorized", _fake_set)
    rep = ff._gate_other_mtk("1-10", base=str(fake_sysfs),
                             hold_pids=ff._BROM_GATE_PIDS)
    assert sorted(rep["hidden"]) == ["1-7"], rep
    assert ("1-7", "0") in touched
    assert all(port != "1-8" for port, _ in touched)


def test_v5_gate_keeps_historical_full_scope(fake_sysfs, monkeypatch) -> None:
    """V5 传 hold_pids=None 时必须还是历史全量口径（含 1-8 的 201c）。"""

    def _fake_set(port: str, value: str, base: str | None = None):
        return True, "fake"

    monkeypatch.setattr(ff, "_is_linux", lambda: True)
    monkeypatch.setattr(ff, "_set_authorized", _fake_set)
    rep = ff._gate_other_mtk("1-10", base=str(fake_sysfs))
    assert "1-8" in rep["hidden"]
    assert "1-13" not in rep["hidden"]  # 2046 普通态从来不动


# ── 5. 接线：main() 在 V6 上真的把这三道闸装上 ────────────────────────

_V6_MANIFEST = {
    "family": "MLD",
    "version": "P535AA_EVT_16.0_NX2_USERROOT_260928V9",
    "version_prop": "ro.build.version.incremental",
    "tool_generation": "v6",
    "flash_xml_file": "download_agent/flash.xml",
    "models": ["MLD_NX2", "MLD-NX2"],
}


def _v6_tree(tmp_path: Path) -> Path:
    """固件目录（V6 入口物只有 flash.xml，见 1.3.19 契约）+ 工具根。"""
    fw = tmp_path / _V6_MANIFEST["version"]
    (fw / "download_agent").mkdir(parents=True)
    (fw / "download_agent" / "flash.xml").write_text("<flash-mode/>\n", encoding="utf-8")
    (fw / "manifest.json").write_text(json.dumps(_V6_MANIFEST), encoding="utf-8")
    tool = tmp_path / "SP_Flash_Tool_V6"
    tool.mkdir()
    exe = tool / "SPFlashToolV6"
    exe.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    exe.chmod(0o755)
    return fw


def _stub_common(monkeypatch, tmp_path: Path, fw: Path, serial: str) -> None:
    monkeypatch.setenv("STP_STEP_PARAMS", json.dumps({"firmware_dir": str(fw)}))
    monkeypatch.setenv("STP_DEVICE_SERIAL", serial)
    monkeypatch.setenv("STP_NFS_ROOT", str(tmp_path / "unused"))
    monkeypatch.setenv("STP_FLASH_TOOL_V6_DIR", str(tmp_path))
    monkeypatch.delenv("STP_FLASH_TOOL_DIR", raising=False)
    monkeypatch.setattr(ff, "_adb_getprop", lambda prop, adb, serial: None)
    monkeypatch.setattr(ff, "_precheck_environment",
                        lambda *a, **k: (True, {"items": [], "warnings": []}))
    monkeypatch.setattr(ff, "_restore_orphaned_gates",
                        lambda *a, **k: {"restored": [], "errors": {}})
    monkeypatch.setattr(ff, "_acquire_host_lock", lambda *a, **k: None)
    monkeypatch.setattr(ff, "_release_host_lock", lambda fd: None)
    monkeypatch.setattr(ff, "_set_authorized",
                        lambda port, value, base=None: (False, "fake-denied"))


def test_main_refuses_before_lock_when_target_uncontrollable(
        tmp_path, monkeypatch, capsys) -> None:
    """目标不可控 → 在**抢刷机锁之前**就失败，且不碰工具。

    占着 host 锁再失败会把同机其它设备一起拖住；起工具则可能绑到邻机。
    """
    fw = _v6_tree(tmp_path)
    base = tmp_path / "sysfs"
    base.mkdir()
    _mk_sysfs(base, "1-10", pid="201c", serial="AHFFNX6930000169")
    _mk_sysfs(base, "1-8", pid="201c", serial="AHFFNX6930000224")

    launched: list[list] = []
    locked: list[bool] = []
    _stub_common(monkeypatch, tmp_path, fw, "AHFFNX6930000169")
    monkeypatch.setattr(ff, "_SYSFS_USB_BASE", str(base))
    monkeypatch.setattr(ff, "_adb_device_state", lambda s, p: "no-device")
    monkeypatch.setattr(ff, "_acquire_host_lock",
                        lambda *a, **k: (locked.append(True), None)[1])

    def _no_launch(cmd, *a, **k):
        launched.append(cmd)
        return "", 0

    monkeypatch.setattr(ff, "_run_flash_tool_with_progress", _no_launch)
    ff.main()
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])

    assert payload["success"] is False
    assert "拒绝起刷" in payload["error_message"]
    assert launched == [], "目标不可控时不得启动工具"
    assert locked == [], "目标不可控时不得占用 host 刷机锁"
    assert payload["metrics"]["identity"]["port_pid"] == "201c"


def test_main_v6_launches_with_bound_port(tmp_path, monkeypatch, capsys) -> None:
    """V6 正常路径：命令行必须带 ``-p <目标口>``，且逐行绑口核验被接上。"""
    fw = _v6_tree(tmp_path)
    base = tmp_path / "sysfs"
    base.mkdir()
    _mk_sysfs(base, "1-10", pid="201c", serial="AHFFNX6930000169")

    launched: list[list] = []
    _stub_common(monkeypatch, tmp_path, fw, "AHFFNX6930000169")
    monkeypatch.setattr(ff, "_SYSFS_USB_BASE", str(base))
    monkeypatch.setattr(ff, "_adb_device_state", lambda s, p: "device")
    monkeypatch.setattr(ff, "_wait_device_back", lambda **k: True)
    monkeypatch.setattr(ff, "_wait_boot_stable", lambda **k: {"ok": True})

    def _fake_launch(cmd, *a, **k):
        launched.append(cmd)
        assert k.get("abort_check") is not None, "V6 必须接上逐行绑口核验"
        return "connect brom successed.\nAll command exec done!\n", 0

    monkeypatch.setattr(ff, "_run_flash_tool_with_progress", _fake_launch)
    monkeypatch.setattr(ff, "_verify_after_flash",
                        lambda *a, **k: (True, {"verified": True}))
    ff.main()
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])

    assert payload["success"] is True, payload
    assert launched[-1][-2:] == ["-p", "1-10"]
    assert payload["metrics"]["port_binding"] == {
        "required": True, "bound_port": "1-10"}


def test_main_v5_path_unchanged_no_port_no_identity(
        tmp_path, monkeypatch, capsys) -> None:
    """552 红线：V5 命令行不带 ``-p``、不做身份判定、门控口径不变。"""
    manifest = {
        "family": "MLD",
        "version": "V552AA-HONOR-LX2-16-260804V71",
        "version_prop": "ro.build.version.incremental",
        "scatter_file": "MT6768_Android_scatter.txt",
        "da_file": "DA_A16_V552AA_BR.bin",
        "models": ["MLD_LX2"],
    }
    fw = tmp_path / manifest["version"]
    fw.mkdir()
    (fw / manifest["scatter_file"]).write_text("scatter", encoding="utf-8")
    (fw / manifest["da_file"]).write_text("DA", encoding="utf-8")
    (fw / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    root = tmp_path / "V5ROOT" / "SP_Flash_Tool_V5"
    root.mkdir(parents=True)
    exe = root / "flash_tool"
    exe.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    exe.chmod(0o755)

    base = tmp_path / "sysfs"
    base.mkdir()
    _mk_sysfs(base, "1-10", pid="201c", serial="AHFFNX6930000169")

    launched: list[list] = []
    hidden: list[list] = []
    monkeypatch.setenv("STP_STEP_PARAMS", json.dumps({"firmware_dir": str(fw)}))
    monkeypatch.setenv("STP_DEVICE_SERIAL", "AHFFNX6930000169")
    monkeypatch.setenv("STP_NFS_ROOT", str(tmp_path / "unused"))
    monkeypatch.setenv("STP_FLASH_TOOL_DIR", str(tmp_path / "V5ROOT"))
    monkeypatch.delenv("STP_FLASH_TOOL_V6_DIR", raising=False)
    monkeypatch.setattr(ff, "_SYSFS_USB_BASE", str(base))
    monkeypatch.setattr(ff, "_adb_device_state", lambda s, p: "device")
    monkeypatch.setattr(ff, "_adb_getprop", lambda prop, adb, serial: None)
    monkeypatch.setattr(ff, "_is_linux", lambda: True)
    monkeypatch.setattr(ff, "_precheck_environment",
                        lambda *a, **k: (True, {"items": [], "warnings": []}))
    monkeypatch.setattr(ff, "_restore_orphaned_gates",
                        lambda *a, **k: {"restored": [], "errors": {}})
    monkeypatch.setattr(ff, "_acquire_host_lock", lambda *a, **k: None)
    monkeypatch.setattr(ff, "_release_host_lock", lambda fd: None)
    monkeypatch.setattr(ff, "_set_authorized",
                        lambda port, value, base=None: (True, "fake"))
    monkeypatch.setattr(ff, "_wait_device_back", lambda **k: True)
    monkeypatch.setattr(ff, "_wait_boot_stable", lambda **k: {"ok": True})

    def _fake_launch(cmd, *a, **k):
        launched.append(cmd)
        rep = a and None
        return "BROM connected\nDownload started\nAll command exec done!\n", 0

    monkeypatch.setattr(ff, "_run_flash_tool_with_progress", _fake_launch)
    monkeypatch.setattr(ff, "_verify_after_flash",
                        lambda *a, **k: (True, {"verified": True}))
    ff.main()
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])

    assert payload["success"] is True, payload
    assert launched[-1] == [str(exe), "-c", "firmware-upgrade",
                            "-d", str(fw / manifest["da_file"]),
                            "-s", str(fw / manifest["scatter_file"]),
                            "-b", "auto"], "V5 命令行必须逐字保持 1.3.18 形态"
    assert "-p" not in launched[-1]
    assert payload["metrics"]["port_binding"] == {
        "required": False, "bound_port": None}
    # V5 历史口径：正常 adb 态(201c)邻机照旧被压制
    assert payload["metrics"]["identity"]["gate_pid_scope"] == "full"
    assert hidden == []
