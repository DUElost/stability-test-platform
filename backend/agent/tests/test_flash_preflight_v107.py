"""flash_preflight v1.0.7（535 / MLD_NX2）：SP Flash Tool V6 代际两面。

守什么：
- `flashtool-v6` 是**硬门**：V6 工具根在位却找不到 `SPFlashToolV6`，必须现在判
  失败——工具包由引擎整包核验注入，到得了这一步说明包已就位，入口不在=布局错，
  留到刷机窗口才发现等于白烧一次设备时间；
- 一个 V6 根都没有时判「无从检查」（`skipped`，不失败）：包未发布/未拉到时本步
  根本不会启动（派发前 exit 2），把它伪装成主机问题是假信号；
- `v6-shared-libs` 只能是 **warning-only**，且判据必须是 **ldd 实测**而不是 dpkg 包名：
  没装过的包名在 dpkg 库里查不到（返回「无法判定」），按名义判定会把「缺库」报成
  「齐备」——2026-10-12 在 `.83` 实跑正是这样暴露的（那台机器缺 `libGL.so.1`，
  `SPFlashToolV6` 连 `-h` 都起不来）；
- 做成硬门会
- 既有六项的条目名与判据不变（在用 552 刷机计划的 step_trace 消费面不变）。
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path


_TREE = Path(__file__).resolve().parents[1] / "scripts" / "flash_preflight"

_spec = importlib.util.spec_from_file_location("flash_preflight_v107", _TREE / "flash_preflight.py")
pf = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(pf)


class _P:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


class _Recorder:
    """dpkg-query 按包名判定：`missing_v6` 时只有 V6 那组算缺。"""

    def __init__(self, *, missing_v6: bool = False):
        self.missing_v6 = missing_v6
        self.calls: "list[list[str]]" = []

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        if argv[:1] == ["dpkg-query"]:
            name = argv[-1]
            if self.missing_v6 and name in pf._V6_PACKAGES:
                return _P(stdout="deinstall ok config-files", returncode=1)
            return _P(stdout="install ok installed", returncode=0)
        return _P()


def _prepare_env(monkeypatch, tmp_path, *, missing_v6: bool = False) -> _Recorder:
    rec = _Recorder(missing_v6=missing_v6)
    monkeypatch.setattr(pf, "subprocess_run", rec)
    monkeypatch.delenv("STP_FLASH_TOOL_V6_DIR", raising=False)
    v5 = tmp_path / "flashtool"
    v5.mkdir()
    exe = v5 / "flash_tool"
    exe.write_text("#!/bin/sh\n", encoding="utf-8")
    exe.chmod(0o755)
    monkeypatch.setattr(pf, "_locate_flashtool", lambda: str(exe))
    monkeypatch.setattr(pf, "_udev_rule_ok", lambda rd: True)
    monkeypatch.setattr(pf, "_udev_rule_form", lambda rd="": "0660")
    monkeypatch.setattr(pf, "_priv_capable", lambda sub: True)
    monkeypatch.setattr(pf, "_priv_wrapper_present", lambda: True)
    monkeypatch.setattr(pf, "_user_dialout_persistent", lambda user: True)
    monkeypatch.setattr(pf, "_user_in_dialout", lambda: True)
    fw = tmp_path / "firmware" / "MLD"
    fw.mkdir(parents=True, exist_ok=True)
    (fw / "latest.json").write_text('{"version": "9.9.9.9"}', encoding="utf-8")
    monkeypatch.setenv("STP_NFS_ROOT", str(tmp_path))
    monkeypatch.setenv("STP_STEP_PARAMS", "{}")
    return rec


def _run(monkeypatch, capsys, tmp_path, *, missing_v6: bool = False) -> dict:
    _prepare_env(monkeypatch, tmp_path, missing_v6=missing_v6)
    pf.main()
    return json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def _item(payload, name):
    return next(i for i in payload["metrics"]["items"] if i["check"] == name)


def _v6_root(tmp_path) -> Path:
    """在位且入口可执行的 V6 工具根。"""
    root = tmp_path / "cache/flashtool_v6/1.2444.00.100"
    root.mkdir(parents=True)
    exe = root / "SPFlashToolV6"
    exe.write_text("#!/bin/sh\n", encoding="utf-8")
    exe.chmod(0o755)
    return root


# ── 解析面 ───────────────────────────────────────────────────────────


def test_v6_candidates_priority_and_layout(tmp_path: Path, monkeypatch) -> None:
    env_dir = tmp_path / "cache/flashtool_v6/1.2444.00.100"
    env_dir.mkdir(parents=True)
    monkeypatch.setenv("STP_FLASH_TOOL_V6_DIR", str(env_dir))
    monkeypatch.setenv("STP_FLASH_TOOL_DIR", str(tmp_path / "cache/flashtool/1.2444.00.100"))
    cands = pf._flashtool_v6_candidates()
    assert cands[0] == str(env_dir)
    v5_root = tmp_path / "cache/flashtool/1.2444.00.100"
    assert str(v5_root / "SP_Flash_Tool_V6") in cands
    # selector 原样解压布局：V5 根的兄弟目录
    assert str(v5_root.parent / "SP_Flash_Tool_V6") in cands


def test_locate_returns_none_none_when_no_root(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("STP_FLASH_TOOL_V6_DIR", str(tmp_path / "absent"))
    monkeypatch.setenv("STP_FLASH_TOOL_DIR", str(tmp_path / "absent-v5"))
    assert pf._locate_flashtool_v6() == (None, None)


def test_locate_finds_exe_in_flat_and_selector_roots(tmp_path: Path, monkeypatch) -> None:
    flat = tmp_path / "flat"
    flat.mkdir()
    exe = flat / "SPFlashToolV6"
    exe.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setenv("STP_FLASH_TOOL_V6_DIR", str(flat))
    monkeypatch.setenv("STP_FLASH_TOOL_DIR", "")
    assert pf._locate_flashtool_v6() == (str(exe), str(flat))


# ── main 判定面 ──────────────────────────────────────────────────────


def test_missing_v6_root_is_skipped_not_failed(monkeypatch, capsys, tmp_path) -> None:
    payload = _run(monkeypatch, capsys, tmp_path)
    item = _item(payload, "flashtool-v6")
    assert item["ok"] is True and item.get("skipped") is True
    assert payload["success"] is True, payload
    assert not any("SPFlashToolV6" in w for w in payload["metrics"]["warnings"])


def test_existing_root_without_exe_fails(monkeypatch, capsys, tmp_path) -> None:
    root = tmp_path / "cache/flashtool_v6/1.2444.00.100"
    root.mkdir(parents=True)
    (root / "lib").mkdir()  # 包解压出来了，入口名不对
    _prepare_env(monkeypatch, tmp_path)
    monkeypatch.setenv("STP_FLASH_TOOL_V6_DIR", str(root))
    pf.main()
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload["success"] is False
    assert "flashtool-v6" in payload["error_message"]
    assert "SPFlashToolV6 not found under" in payload["error_message"]
    assert str(root) in payload["error_message"]


def test_v6_exe_without_exec_bit_is_chmodded(monkeypatch, capsys, tmp_path) -> None:
    root = tmp_path / "cache/flashtool_v6/1.2444.00.100"
    root.mkdir(parents=True)
    exe = root / "SPFlashToolV6"
    exe.write_text("#!/bin/sh\n", encoding="utf-8")
    exe.chmod(0o644)
    _prepare_env(monkeypatch, tmp_path)
    monkeypatch.setenv("STP_FLASH_TOOL_V6_DIR", str(root))
    pf.main()
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    item = _item(payload, "flashtool-v6")
    assert payload["success"] is True, payload
    assert item["ok"] is True and item["fixed"] is True
    assert exe.stat().st_mode & 0o111


def test_v6_missing_libs_is_warning_only_with_pkg_hint(monkeypatch, capsys, tmp_path) -> None:
    """ldd 报缺库 → warning + 指到应装的包名；整步仍判过（跑 v5 不需要这些库）。"""
    root = _v6_root(tmp_path)
    _prepare_env(monkeypatch, tmp_path)
    monkeypatch.setenv("STP_FLASH_TOOL_V6_DIR", str(root))
    monkeypatch.setattr(pf, "_ldd_missing_libs",
                        lambda exe: ["libGL.so.1", "libwhatnot.so.9"])
    pf.main()
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    item = _item(payload, "v6-shared-libs")
    assert item["ok"] is True and payload["success"] is True, payload
    warn = next(w for w in payload["metrics"]["warnings"] if "missing libs" in w)
    assert "libGL.so.1" in warn and "libgl1" in warn
    assert "libwhatnot.so.9" in warn and "未知 soname" in warn
    assert _item(payload, "qt-libs")["ok"] is True   # V5 那组仍是硬门，未被牵连


def test_dpkg_blindness_never_becomes_false_ready(monkeypatch, capsys, tmp_path) -> None:
    """dpkg 对未装过的包名只能返回「无法判定」——不得据此写「齐备」。

    回归 2026-10-12 `.83` 实跑：那台 host 没装 libGL 面，`qt-libs` 口径的
    dpkg 判定会把 V6 报成绿。判据换成 ldd 后，「无入口可 ldd」与「ldd 无缺库」
    是两条不同的 detail，绝不混同。
    """
    _prepare_env(monkeypatch, tmp_path)
    real = pf._check_dpkg_installed
    monkeypatch.setattr(pf, "_check_dpkg_installed",
                        lambda pkg: None if pkg in pf._V6_PACKAGES else real(pkg))
    monkeypatch.delenv("STP_FLASH_TOOL_V6_DIR", raising=False)
    pf.main()
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    item = _item(payload, "v6-shared-libs")
    assert item["ok"] is True
    assert "ldd 无缺库" not in item["detail"]      # 没实测过就不许说齐备
    assert "无从 ldd" in item["detail"]
    assert not any("libgl1" in w for w in payload["metrics"]["warnings"])


def test_ldd_helper_uses_tool_dir_library_path(tmp_path, monkeypatch) -> None:
    """ldd 必须带「包根 + 包内 lib」的 LD_LIBRARY_PATH，否则会把工具自带 Qt 报成缺库。"""
    seen: "list[dict]" = []

    class _P:
        def __init__(self):
            self.stdout = "\tlibQt5Core.so.5 => /opt/x/libQt5Core.so.5 (0x1)\n" \
                          "\tlibGL.so.1 => not found\n"
            self.returncode = 0

    monkeypatch.setattr(pf.platform, "system", lambda: "Linux")
    monkeypatch.setattr(pf.shutil, "which", lambda _n: "/usr/bin/ldd")

    def spy(argv, **kwargs):
        seen.append((list(argv), kwargs.get("env") or {}))
        return _P()

    monkeypatch.setattr(pf.subprocess, "run", spy)
    exe = tmp_path / "SPFlashToolV6"
    exe.write_text("#!/bin/sh\n", encoding="utf-8")
    assert pf._ldd_missing_libs(str(exe)) == ["libGL.so.1"]
    argv, env = seen[0]
    assert argv[0] == "ldd" and argv[1] == str(exe)
    assert env["LD_LIBRARY_PATH"].startswith(f"{tmp_path}:{tmp_path}/lib")


def test_ldd_helper_says_nothing_when_probe_impossible(tmp_path, monkeypatch) -> None:
    """非 Linux / 没有 ldd / 超时 → 空表：无法判断不等于缺失（也不得判失败）。"""
    monkeypatch.setattr(pf.platform, "system", lambda: "Windows")
    assert pf._ldd_missing_libs("C:\\x.exe") == []
    monkeypatch.setattr(pf.platform, "system", lambda: "Linux")
    monkeypatch.setattr(pf.shutil, "which", lambda _n: None)
    assert pf._ldd_missing_libs("/nope") == []


def test_v6_libs_present_reports_ldd_result(monkeypatch, capsys, tmp_path) -> None:
    root = _v6_root(tmp_path)
    _prepare_env(monkeypatch, tmp_path)
    monkeypatch.setenv("STP_FLASH_TOOL_V6_DIR", str(root))
    monkeypatch.setattr(pf, "_ldd_missing_libs", lambda exe: [])
    pf.main()
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    item = _item(payload, "v6-shared-libs")
    assert item["ok"] is True and item["detail"].startswith("ldd 无缺库")


def test_v5_hard_items_untouched(monkeypatch, capsys, tmp_path) -> None:
    """回归面：V5 的 flashtool 缺失仍然判失败，与 v1.0.6 同语义。"""
    _prepare_env(monkeypatch, tmp_path)
    monkeypatch.setattr(pf, "_locate_flashtool", lambda: None)
    pf.main()
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload["success"] is False
    assert "flashtool:" in payload["error_message"]
