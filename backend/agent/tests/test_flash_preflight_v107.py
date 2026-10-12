"""flash_preflight v1.0.7（535 / MLD_NX2）：SP Flash Tool V6 代际两面。

守什么：
- `flashtool-v6` 是**硬门**：V6 工具根在位却找不到 `SPFlashToolV6`，必须现在判
  失败——工具包由引擎整包核验注入，到得了这一步说明包已就位，入口不在=布局错，
  留到刷机窗口才发现等于白烧一次设备时间；
- 一个 V6 根都没有时判「无从检查」（`skipped`，不失败）：包未发布/未拉到时本步
  根本不会启动（派发前 exit 2），把它伪装成主机问题是假信号；
- `qt-libs-v6` 只能是 **warning-only**：这组包只有跑 v6 固件才需要，做成硬门会
  让 552-only 主机被无关包名阻断；真正跑 v6 时 `flash_firmware` 的 shared-libs
  预检会硬失败；
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


def test_v6_libs_missing_is_warning_only(monkeypatch, capsys, tmp_path) -> None:
    payload = _run(monkeypatch, capsys, tmp_path, missing_v6=True)
    item = _item(payload, "qt-libs-v6")
    assert item["ok"] is True
    assert payload["success"] is True, payload
    warn = next(w for w in payload["metrics"]["warnings"] if "qt-libs" in w or "libgl1" in w)
    assert "libgl1" in warn and "agent_flash_prereq_packages_v6" in warn
    # V5 那组仍是硬门：本例里 V5 包齐全，qt-libs 未受影响
    assert _item(payload, "qt-libs")["ok"] is True


def test_non_dpkg_system_is_not_reported_as_ready(monkeypatch, capsys, tmp_path) -> None:
    """非 dpkg 系统只能说「无法判定」，不得写成「V6 运行库齐备」（与 qt-libs 同判据）。"""
    _prepare_env(monkeypatch, tmp_path)
    real = pf._check_dpkg_installed
    monkeypatch.setattr(pf, "_check_dpkg_installed",
                        lambda pkg: None if pkg in pf._V6_PACKAGES else real(pkg))
    pf.main()
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    item = _item(payload, "qt-libs-v6")
    assert item["ok"] is True
    assert item["detail"].startswith("non-dpkg system, skipped:")
    assert "齐备" not in item["detail"]
    assert payload["success"] is True, payload


def test_v5_hard_items_untouched(monkeypatch, capsys, tmp_path) -> None:
    """回归面：V5 的 flashtool 缺失仍然判失败，与 v1.0.6 同语义。"""
    _prepare_env(monkeypatch, tmp_path)
    monkeypatch.setattr(pf, "_locate_flashtool", lambda: None)
    pf.main()
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload["success"] is False
    assert "flashtool:" in payload["error_message"]
