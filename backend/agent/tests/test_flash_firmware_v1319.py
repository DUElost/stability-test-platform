"""flash_firmware v1.3.19（535 / MLD_NX2）：SP Flash Tool **V6** 代际。

守什么（判据全部来自 2026-10-12 对本机 flashtool@1.2444.00.100 包内两代二进制的
`-h` 实测 + 现场 V6 成功日志）：

- 代际是闭集：`tool_generation` 未知取值必须 fail-fast，**不得**回落 v5——
  回落会拼出一条工具直接报错的命令行，把失败点后移到刷机窗口里；
- v6 的入口物只有 flash.xml：manifest 必须给 `flash_xml_file`，v5 必须给
  scatter+DA，两者不得互相顶替；
- v6 命令行是 `-c … -f … -b …`，不得带 `-d/-s`；**v5 命令行与 1.3.18 逐字一致**
  （在用 552 刷机计划零行为变化）；
- 指纹路由下一代际只认 manifest.json，计划参数不得改它（代际属于固件包）；
- 入口解析按代际选二进制，不得把 V5 根里的 `flash_tool` 当成 V6 入口；
- 现场 V6 日志必须被既有 PASS 判据接住，百分比与阶段戳不失灵。
"""

from __future__ import annotations

import importlib.util
import json
import os
import textwrap
from pathlib import Path

import pytest

_SCRIPT_DIR = Path(__file__).resolve().parents[2] / "agent" / "scripts" / "flash_firmware"

_spec = importlib.util.spec_from_file_location(
    "flash_firmware_v1319", _SCRIPT_DIR / "flash_firmware.py"
)
ff = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(ff)


# ── fixtures ─────────────────────────────────────────────────────────

_V6_MANIFEST = {
    "family": "MLD",
    "version": "P535AA_EVT_16.0_NX2_USERROOT_260928V9",
    "version_prop": "ro.build.version.incremental",
    "tool_generation": "v6",
    "flash_xml_file": "download_agent/flash.xml",
    "models": ["MLD_NX2", "MLD-NX2"],
}

_V5_MANIFEST = {
    "family": "MLD",
    "version": "V552AA-HONOR-LX2-16-260804V71",
    "version_prop": "ro.build.version.incremental",
    "scatter_file": "MT6768_Android_scatter.txt",
    "da_file": "DA_A16_V552AA_BR.bin",
    "models": ["MLD_LX2", "MLD-LX2"],
}


def _v6_firmware_dir(root: Path, version: str = "P535AA_EVT_16.0_NX2_USERROOT_260928V9",
                     manifest: dict | None = None) -> Path:
    d = root / version
    (d / "download_agent").mkdir(parents=True, exist_ok=True)
    (d / "download_agent" / "flash.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8" ?>\n<flash-mode>\n'
        "  <dagent>DA_BR.bin</dagent>\n"
        "  <scatter>../MT6835_Android_scatter.xml</scatter>\n</flash-mode>\n",
        encoding="utf-8",
    )
    (d / "MT6835_Android_scatter.xml").write_text("<root/>\n", encoding="utf-8")
    (d / "download_agent" / "DA_BR.bin").write_text("DA", encoding="utf-8")
    payload = dict(_V6_MANIFEST if manifest is None else manifest)
    payload["version"] = version
    (d / "manifest.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return d


def _v5_firmware_dir(root: Path) -> Path:
    d = root / _V5_MANIFEST["version"]
    d.mkdir(parents=True, exist_ok=True)
    (d / _V5_MANIFEST["scatter_file"]).write_text("scatter", encoding="utf-8")
    (d / _V5_MANIFEST["da_file"]).write_text("DA", encoding="utf-8")
    (d / "manifest.json").write_text(json.dumps(_V5_MANIFEST), encoding="utf-8")
    return d


def _tool_root(tmp_path: Path, *, v5: bool = True, v6: bool = True,
               subdir: str | None = None) -> Path:
    """造工具根：subdir 非空时按 selector 布局（根下再分 SP_Flash_Tool_V{5,6}）。"""
    root = tmp_path / ("selector-root" if subdir else "flat-root")
    if v5:
        base = root / subdir if subdir else root
        base.mkdir(parents=True, exist_ok=True)
        (base / "flash_tool").write_text("#!/bin/sh\n", encoding="utf-8")
        (base / "flash_tool").chmod(0o755)
    if v6:
        base = root / "SP_Flash_Tool_V6"
        base.mkdir(parents=True, exist_ok=True)
        (base / "SPFlashToolV6").write_text("#!/bin/sh\n", encoding="utf-8")
        (base / "SPFlashToolV6").chmod(0o755)
    return root


# ── 1. 代际判据：闭集、缺省 v5、不互相顶替 ────────────────────────────


@pytest.mark.parametrize("raw", ["v7", "V6.1", "flash_tool_v6", "auto", "6"])
def test_unknown_generation_fails_fast_not_fallback(raw: str) -> None:
    generation, err = ff._manifest_generation({"tool_generation": raw})
    assert generation is None
    assert "not supported" in err and "v5, v6" in err
    # 不得静默回落：错误必须来自代际判据本身，而不是「当成 v5 后缺 scatter」
    assert "scatter_file" not in err


def test_missing_generation_defaults_to_v5_for_legacy_manifests() -> None:
    """552 在用的 manifest 没有 tool_generation 键——缺省必须仍是 v5。"""
    generation, err = ff._manifest_generation({"scatter_file": "s.txt", "da_file": "d.bin"})
    assert (generation, err) == ("v5", None)


def test_param_generation_override_is_also_a_closed_set() -> None:
    generation, err = ff._resolve_generation({"tool_generation": "v6"}, None)
    assert (generation, err) == ("v6", None)
    generation, err = ff._resolve_generation({"tool_generation": "v9"}, {"tool_generation": "v6"})
    assert generation is None and "param tool_generation" in err


def test_manifest_v6_requires_flash_xml_file(tmp_path: Path) -> None:
    d = _v6_firmware_dir(tmp_path)
    manifest = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    manifest.pop("flash_xml_file")
    (d / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    data, err = ff._load_manifest(str(d))
    assert data is None
    assert "must define flash_xml_file for tool_generation v6" in err


def test_manifest_v5_still_requires_scatter_and_da(tmp_path: Path) -> None:
    """v6 的放宽不得顺带松掉 v5 的既有判据（1.3.18 行为逐字保留）。"""
    d = tmp_path / "V5-broken"
    d.mkdir()
    (d / "manifest.json").write_text(json.dumps({"family": "MLD"}), encoding="utf-8")
    data, err = ff._load_manifest(str(d))
    assert data is None
    assert "must define scatter_file and da_file" in err


# ── 2. 路由：显式目录与指纹路由两分支 ─────────────────────────────────


def test_explicit_dir_v6_route(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("STP_NFS_ROOT", raising=False)
    d = _v6_firmware_dir(tmp_path)
    route, err = ff._resolve_explicit_dir({}, str(d))
    assert err is None
    assert route["tool_generation"] == "v6"
    assert route["flash_xml_file"] == str(d / "download_agent" / "flash.xml")
    # v6 不再有 DA/scatter 的命令行拼接面
    assert route["da_file"] is None and route["scatter_file"] is None


def test_explicit_dir_v5_route_unchanged(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("STP_NFS_ROOT", raising=False)
    d = _v5_firmware_dir(tmp_path)
    route, err = ff._resolve_explicit_dir({}, str(d))
    assert err is None
    assert route["tool_generation"] == "v5"
    assert route["da_file"] == str(d / _V5_MANIFEST["da_file"])
    assert route["scatter_file"] == str(d / _V5_MANIFEST["scatter_file"])
    assert route["flash_xml_file"] is None


def test_explicit_dir_param_can_supply_flash_xml_without_manifest(tmp_path: Path, monkeypatch) -> None:
    """现场先拿到没有 manifest 的 V6 固件包时，显式目录模式可用参数补入口物。"""
    monkeypatch.delenv("STP_NFS_ROOT", raising=False)
    d = tmp_path / "raw_pkg"
    (d / "download_agent").mkdir(parents=True)
    (d / "download_agent" / "flash.xml").write_text("<flash-mode/>\n", encoding="utf-8")
    route, err = ff._resolve_explicit_dir(
        {"tool_generation": "v6", "flash_xml_file": "download_agent/flash.xml"}, str(d))
    assert err is None
    assert route["tool_generation"] == "v6"
    assert route["flash_xml_file"].endswith("download_agent/flash.xml")


def test_explicit_dir_v6_without_entry_fails(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("STP_NFS_ROOT", raising=False)
    d = tmp_path / "raw_pkg2"
    d.mkdir()
    route, err = ff._resolve_explicit_dir({"tool_generation": "v6"}, str(d))
    assert route is None
    assert "flash_xml_file is required" in err


@pytest.fixture
def nfs_root(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "firmware"
    root.mkdir()
    monkeypatch.setenv("STP_NFS_ROOT", str(tmp_path))
    monkeypatch.delenv("STP_FLASH_TOOL_V6_DIR", raising=False)
    monkeypatch.delenv("STP_FLASH_TOOL_DIR", raising=False)
    monkeypatch.delenv("STP_FLASH_FIRMWARE_VERSION", raising=False)
    return root


def test_fingerprint_route_nx2_to_v6(nfs_root: Path, monkeypatch) -> None:
    d = _v6_firmware_dir(nfs_root / "MLD")
    (nfs_root / "MLD" / "latest.json").write_text(json.dumps({
        "versions": {"MLD_LX2": _V5_MANIFEST["version"], "MLD_NX2": d.name},
    }), encoding="utf-8")
    monkeypatch.setattr(
        ff, "_adb_getprop",
        lambda prop, adb, serial: "MLD-NX2" if prop == "ro.product.model" else None)
    route, err = ff._resolve_by_fingerprint({}, "0000NX2622000670", "adb")
    assert err is None, err
    assert route["model"] == "MLD-NX2" and route["family"] == "MLD"
    assert route["tool_generation"] == "v6"
    assert route["flash_xml_file"] == str(d / "download_agent" / "flash.xml")


def test_fingerprint_generation_is_manifest_only(nfs_root: Path, monkeypatch) -> None:
    """计划参数不得在指纹路由里改代际（代际属于固件包，不由计划分化）。"""
    d = _v6_firmware_dir(nfs_root / "MLD")
    (nfs_root / "MLD" / "latest.json").write_text(
        json.dumps({"versions": {"MLD_NX2": d.name}}), encoding="utf-8")
    monkeypatch.setattr(
        ff, "_adb_getprop",
        lambda prop, adb, serial: "MLD_NX2" if prop == "ro.product.model" else None)
    route, err = ff._resolve_by_fingerprint({"tool_generation": "v5"}, "S", "adb")
    assert err is None, err
    assert route["tool_generation"] == "v6"


def test_unknown_model_fails_with_known_route_set(nfs_root: Path, monkeypatch) -> None:
    assert ff._MODEL_FAMILY_ROUTES["MLD_NX2"] == "MLD"
    assert ff._MODEL_FAMILY_ROUTES["MLD-NX2"] == "MLD"
    (nfs_root / "MLD").mkdir(exist_ok=True)
    (nfs_root / "MLD" / "latest.json").write_text('{"version": "x"}', encoding="utf-8")
    monkeypatch.setattr(
        ff, "_adb_getprop",
        lambda prop, adb, serial: "MLD_ZZ9" if prop == "ro.product.model" else None)
    route, err = ff._resolve_by_fingerprint({}, "S", "adb")
    assert route is None
    assert "no firmware family route for model MLD_ZZ9" in err
    assert "MLD_NX2" in err  # fail-fast 必须列出已知集合


# ── 3. 入口解析与命令行拼接 ───────────────────────────────────────────


def test_pick_exe_never_crosses_generations(tmp_path: Path) -> None:
    flat = _tool_root(tmp_path)
    assert Path(ff._pick_flash_tool_exe(str(flat), "v5")).name == "flash_tool"
    assert Path(ff._pick_flash_tool_exe(str(flat), "v6")).name == "SPFlashToolV6"
    sel = _tool_root(tmp_path / "sel", subdir="SP_Flash_Tool_V5")
    assert Path(ff._pick_flash_tool_exe(str(sel), "v5")).name == "flash_tool"
    assert Path(ff._pick_flash_tool_exe(str(sel), "v6")).name == "SPFlashToolV6"


def test_pick_exe_v6_refuses_v5_only_root(tmp_path: Path) -> None:
    v5_only = tmp_path / "v5only"
    v5_only.mkdir()
    (v5_only / "flash_tool").write_text("#!/bin/sh\n", encoding="utf-8")
    assert ff._pick_flash_tool_exe(str(v5_only), "v6") is None


def test_pick_exe_v5_default_keeps_1_3_18_shape(tmp_path: Path) -> None:
    """默认代际 = 旧调用形态：`_pick_flash_tool_exe(dir)` 仍按 V5 找。"""
    flat = _tool_root(tmp_path / "flat2")
    assert Path(ff._pick_flash_tool_exe(str(flat))).name == "flash_tool"


def test_locate_v6_dir_priority(tmp_path: Path, monkeypatch) -> None:
    env_root = _tool_root(tmp_path / "byenv")
    param_root = _tool_root(tmp_path / "byparam")
    monkeypatch.setenv("STP_FLASH_TOOL_V6_DIR", str(env_root))
    assert ff._locate_flash_tool_v6_dir(str(param_root)) == str(param_root)
    assert ff._locate_flash_tool_v6_dir(None) == str(env_root)


def test_locate_v6_dir_falls_back_to_selector_sibling(tmp_path: Path, monkeypatch) -> None:
    """无注入无参数时，selector 原样解压布局（V5 根的兄弟目录）可用。"""
    monkeypatch.delenv("STP_FLASH_TOOL_V6_DIR", raising=False)
    sel = _tool_root(tmp_path / "sel2", subdir="SP_Flash_Tool_V5")
    v5_root = sel / "SP_Flash_Tool_V5"
    assert ff._locate_flash_tool_v6_dir(None, str(v5_root)) == str(sel / "SP_Flash_Tool_V6")


def test_build_argv_v6_and_v5() -> None:
    v6 = ff._build_flash_argv(
        "/tools/SPFlashToolV6", "v6", "firmware-upgrade", "auto",
        flash_xml_file="/fw/P535AA/download_agent/flash.xml")
    assert v6 == ["/tools/SPFlashToolV6", "-c", "firmware-upgrade",
                  "-f", "/fw/P535AA/download_agent/flash.xml", "-b", "auto"]
    assert "-d" not in v6 and "-s" not in v6

    v5 = ff._build_flash_argv(
        "/tools/flash_tool", "v5", "firmware-upgrade", "auto",
        da_file="/fw/DA.bin", scatter_file="/fw/scatter.txt")
    # 与 1.3.18 的唯一形态逐字一致
    assert v5 == ["/tools/flash_tool", "-c", "firmware-upgrade",
                  "-d", "/fw/DA.bin", "-s", "/fw/scatter.txt", "-b", "auto"]


@pytest.mark.parametrize(
    ("generation", "kwargs"),
    [
        ("v6", {"da_file": "/fw/DA.bin", "scatter_file": "/fw/s.txt"}),
        ("v5", {"flash_xml_file": "/fw/download_agent/flash.xml"}),
        ("v5", {"da_file": "/fw/DA.bin"}),
    ],
)
def test_build_argv_refuses_wrong_entry_artifact(generation: str, kwargs: dict) -> None:
    with pytest.raises(ValueError):
        ff._build_flash_argv("/tools/exe", generation, "firmware-upgrade", "auto", **kwargs)


# ── 4. main 的 fail-closed 接线 ───────────────────────────────────────


def _run_main(monkeypatch, capsys, params: dict, **env) -> dict:
    monkeypatch.setenv("STP_STEP_PARAMS", json.dumps(params))
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(ff, "_adb_getprop", lambda prop, adb, serial: None)
    ff.main()
    out = capsys.readouterr().out.strip().splitlines()
    return json.loads(out[-1])


def test_main_fails_when_flash_xml_missing(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.delenv("STP_NFS_ROOT", raising=False)
    d = _v6_firmware_dir(tmp_path)
    (d / "download_agent" / "flash.xml").unlink()
    payload = _run_main(monkeypatch, capsys, {"firmware_dir": str(d)}, STP_DEVICE_SERIAL="")
    assert payload["success"] is False
    assert "flash_xml_file not found" in payload["error_message"]


def test_main_fails_when_v6_tool_root_missing(tmp_path: Path, monkeypatch, capsys) -> None:
    """工具根缺失的错误必须指名 V6 键——指到 flash_tool_dir 会把运维引向错目录。"""
    monkeypatch.delenv("STP_NFS_ROOT", raising=False)
    d = _v6_firmware_dir(tmp_path)
    payload = _run_main(
        monkeypatch, capsys, {"firmware_dir": str(d)},
        STP_DEVICE_SERIAL="", STP_FLASH_TOOL_V6_DIR=str(tmp_path / "nope"))
    assert payload["success"] is False
    assert payload["error_message"].startswith("flash_tool_v6_dir not found")


def test_main_metrics_route_carries_generation(tmp_path: Path, monkeypatch, capsys) -> None:
    """route/metrics 里必须能审计到代际与入口物——step_trace 是唯一现场留痕面。"""
    monkeypatch.delenv("STP_NFS_ROOT", raising=False)
    d = _v6_firmware_dir(tmp_path)
    payload = _run_main(
        monkeypatch, capsys, {"firmware_dir": str(d)},
        STP_DEVICE_SERIAL="", STP_FLASH_TOOL_V6_DIR=str(tmp_path / "nope"))
    assert payload["metrics"]["route"]["tool_generation"] == "v6"
    assert payload["metrics"]["route"]["flash_xml_file"].endswith("download_agent/flash.xml")


# ── 5. 现场 V6 日志：判据与打戳 ───────────────────────────────────────

#: 真机日志节选（同代工具整包升级成功，260928V9）。
_V6_LOG = textwrap.dedent("""\
    XML schema validation passed!
    feature-options node in flash.xml is not exist, skip parsing.
    SLA_Challenge_Cmd API cannot be found in SLA_Challenge.dll
    Parsing BROM SLA feature options from flash.xml.
    scan device START, timeout set as 120000 ms.
    com port clue: COM://.
    scan device succeed, symbolic_link: COM://...MediaTek PreLoader USB VCOM (Android) (COM93)
    connect brom START, connection options: 115200, auth file: ,    cert file: .
    connect brom successed.
    CMD:GET-HW-INFO command execute successed.
    enter DA mode START, flash DA mode: FLASH-MODE-DA, flash XML: D:/WebTool/P535AA_EVT_16.0_NX2_USERROOT_260928V9_FLASH/download_agent/flash.xml
    enter 1st DA mode successed.
    DA DUI Authentication enabled status: Disabled.
    Warning: DA DUI Authentication feature Disabled, skip it!
    13% of DA data has been sent(about 64.00K)
    96% of DA data has been sent(about 448.00K)
    enter 2nd DA mode successed.
    DA SLA enabled status: Disabled.
    CMD:EMMC-CONTROL command execute successed.
    100% of [scatter-file] data has been sent (about 108.68K)
    7% of [md1img_a [6/26]] data has been sent (about 6.00M)
    100% of [md1img_a [6/26]] data has been sent (about 77.64M)
    1% of [super [25/26]] data has been sent (about 48.00M)
    100% of [super [25/26]] data has been sent (about 4.67G)
    100% of [userdata [26/26]] data has been sent (about 9.26M)
    CMD:FLASH-UPDATE command execute successed.
    CMD:REBOOT command execute successed.
    All command exec done!
""")


@pytest.fixture
def fake_v6_tool(tmp_path: Path) -> Path:
    exe = tmp_path / "SPFlashToolV6"
    body = "import sys\nsys.stdout.write(" + repr(_V6_LOG) + ")\nsys.stdout.flush()\n"
    exe.write_text("#!/usr/bin/env python3\n" + body, encoding="utf-8")
    exe.chmod(0o755)
    return exe


def test_caught_brom_matches_both_generation_wordings() -> None:
    """`caught_brom` 两代措辞都得认（v6 真机日志是 `connect brom successed.`）。"""
    assert ff._output_caught_brom(_V6_LOG) is True          # 真机 V6 日志
    assert ff._output_caught_brom("BROM connected\nDownload started") is True
    assert ff._output_caught_brom("scan device com port failed, error code: -1") is False


def test_v6_log_passes_verdict() -> None:
    ok, reason = ff._scan_output_for_verdict(_V6_LOG, "")
    assert ok is True, reason


def test_v6_fail_log_still_fails() -> None:
    ok, reason = ff._scan_output_for_verdict("connect brom successed.\nS_FT_DOWNLOAD_FAIL\n", "")
    assert ok is False and "S_FT_DOWNLOAD_FAIL" in reason


def test_v6_log_stages_and_percent_stamps(fake_v6_tool, capsys) -> None:
    seq: list[int] = [0]
    stages: list[str] = []
    stdout, rc = ff._run_flash_tool_with_progress(
        [str(fake_v6_tool)], cwd=str(fake_v6_tool.parent), env=dict(os.environ),
        timeout=60,
        on_stage=lambda tok: (stages.append(tok), ff._emit_progress(seq, stage=tok)),
        on_percent=lambda pct: ff._emit_progress(seq, percent=pct),
    )
    assert rc == 0 and "All command exec done!" in stdout
    for expected in ("SCAN DEVICE", "CONNECT BROM", "DOWNLOAD", "DA MODE", "FLASH-UPDATE"):
        assert expected in stages, stages
    stamps = [
        json.loads(line[len("PROGRESS "):])
        for line in capsys.readouterr().err.splitlines() if line.startswith("PROGRESS ")
    ]
    percent_stamps = [s for s in stamps if "percent" in s]
    assert percent_stamps, "百分比戳全部丢失 = 停滞判据失去活信号"
    assert percent_stamps[-1]["percent"] == 100
    assert max(s["seq"] for s in stamps) == len(stamps)  # seq 单调且连续
