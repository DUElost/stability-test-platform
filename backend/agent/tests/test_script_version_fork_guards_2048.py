# -*- coding: utf-8 -*-
"""#2048 守卫：脚本新版本不得把已修缺陷带回（对**最新**版本目录断言）。

背景（2026-09-13 窗口）：`#1690` 批量补 PROGRESS 打戳时新发的几个版本目录，
是从**比当时最新版更旧**的版本拷贝的，把已修缺陷带回主线：

- `gpu_setup/v1.1.0` 抄自 v1.0.9，丢掉 `#755` 的 `_retry_uninstall_pkgs_for_apk`
  （重试安装前只卸「当前失败 APK」对应包）——`scripts-debug.apk` 失败会误卸
  刚装成功的 Antutu 包；
- `monkey_launch/v5.1.0` 抄自 v5.0.1，丢掉 `#1711` 的 aimwd 独立 `max_wait`
  窗口——`MonkeyTest.sh` 迟到时 aimwd 预算已耗尽，双看门狗被误判成 aimwd 未运行。

既有用例只钉住**当时**的版本目录（且 `_load_gpu_lib_v110` 名字与实际加载的
v1.0.10 不符），新版本一出现就无人覆盖。本文件改为**动态解析最新版本目录**：
只要再出现「新版本从旧基线拷贝」，这里立刻红。

版本目录不可变（ADR-0020/0039）：本文件只读，不改任何既有版本。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _version_key(name: str) -> tuple[int, ...]:
    return tuple(int(part) for part in name[1:].split("."))


def _latest_version_dir(script: str) -> Path:
    """最新版本目录（按数字段排序，不是字典序——字典序会把 v1.0.9 排到 v1.0.10 后）。"""
    base = _SCRIPTS / script
    dirs = [
        p
        for p in base.iterdir()
        if p.is_dir() and p.name.startswith("v") and all(part.isdigit() for part in p.name[1:].split("."))
    ]
    assert dirs, f"{script} 下没有版本目录：{base}"
    return max(dirs, key=lambda p: _version_key(p.name))


def _load_module(name: str, path: Path):
    """按文件路径加载（脚本目录内以 `from _lib import …` / `from _adb import …` 互相引用）。"""
    sys.path.insert(0, str(path.parent))
    try:
        sys.modules.pop("_lib", None)
        sys.modules.pop("_adb", None)
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec and spec.loader, f"cannot load {path}"
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.modules.pop("_lib", None)
        sys.modules.pop("_adb", None)
        sys.path.remove(str(path.parent))


# ─────────────────── gpu_setup：最新版本必须保留 #755 ───────────────────


def _fake_adb_factory(lib, calls: list, *, fail_first_install: bool = True):
    """push 成功、首次 pm install 失败、重试成功；记录全部 pm uninstall。"""
    state = {"n": 0}

    def fake_adb(*args, timeout=30):
        calls.append(args)
        if args[0] == "push":
            return 0, "1 file pushed", ""
        if args[0] == "shell" and args[1].startswith("pm install"):
            state["n"] += 1
            if fail_first_install and state["n"] == 1:
                return 1, "Failure [INSTALL_FAILED]", ""
            return 0, "Success", ""
        return 0, "", ""

    return fake_adb


def test_latest_gpu_setup_retains_755_retry_uninstall_scope(monkeypatch, tmp_path):
    """#2048：最新 gpu_setup 重试前**只**卸当前失败 APK 对应包（#755）。"""
    version_dir = _latest_version_dir("gpu_setup")
    lib = _load_module("gpu_lib_latest", version_dir / "_lib.py")

    assert hasattr(lib, "_retry_uninstall_pkgs_for_apk"), (
        f"{version_dir.name} 丢了 #755 的 _retry_uninstall_pkgs_for_apk"
        "——新版本是否从旧基线拷贝？见 #2048。"
    )

    calls: list = []
    monkeypatch.setattr(lib, "adb", _fake_adb_factory(lib, calls))

    apk = tmp_path / "scripts-debug.apk"
    apk.write_bytes(b"apk")
    rc, out = lib._install_apk_stable(apk)

    assert rc == 0 and "Success" in out
    uninstalls = [c[1] for c in calls if c[0] == "shell" and c[1].startswith("pm uninstall")]
    assert uninstalls == [f"pm uninstall {lib._HOST_PKG}"], (
        f"{version_dir.name} 的重试卸载范围不是「只卸当前失败 APK 对应包」：{uninstalls}"
        "（#755：宿主 APK 失败时不得误卸 Antutu）"
    )


def test_latest_gpu_setup_retry_uninstall_map_consistent_with_behavior():
    """#2048：最新 gpu_setup 的 APK→包 映射与 `_install_apk_stable` 行为一致。

    映射表随版本拷贝最易丢失/写错（v1.1.0 连函数一起丢），故对**最新**目录再钉一次；
    逐版本的行为断言在 `test_gpu_power_sleep_resources.py` 内参数化。
    """
    lib = _load_module("gpu_lib_latest_map", _latest_version_dir("gpu_setup") / "_lib.py")

    assert lib._retry_uninstall_pkgs_for_apk(Path("scripts-debug.apk")) == (lib._HOST_PKG,)
    assert lib._retry_uninstall_pkgs_for_apk(Path("Antutu_3D_Lite_10.2.9.apk")) == (
        lib._ANTUTU_LITE_PKG,
    )
    assert lib._retry_uninstall_pkgs_for_apk(Path("unknown.apk")) == ()


# ─────────────────── monkey_launch：最新版本必须有独立 aimwd 窗口 ───────────────────


def _run_launch_timed(monkeypatch, mod, *, max_wait: int = 15):
    """模拟真实时钟：MonkeyTest.sh 在窗口末段才出现，aimwd 需要**第二次** poll。

    共用 deadline 时（#1711 缺陷形态）第一个循环会吃光整个预算，aimwd 循环
    立即退出 → 判「aimwd 未运行」。
    """
    captured: dict = {}
    clock = {"t": 1000.0}
    t0 = clock["t"]
    aimwd_calls = 0

    monkeypatch.setattr(mod, "device_serial", lambda: "S")
    monkeypatch.setattr(mod, "params", lambda: {"max_wait_seconds": max_wait})
    monkeypatch.setattr(mod, "_shell", lambda serial, cmd, timeout=30: (0, ""))
    monkeypatch.setattr(mod.time, "time", lambda: clock["t"])
    monkeypatch.setattr(
        mod.time, "sleep", lambda secs: clock.__setitem__("t", clock["t"] + secs)
    )

    def fake_ps(serial, pattern, timeout=10):
        if pattern == "MonkeyTest.sh":
            return clock["t"] >= t0 + max_wait - 1  # 窗口末段才可见
        if pattern == "MonkeyWatchdog":
            nonlocal aimwd_calls
            aimwd_calls += 1
            return aimwd_calls >= 2  # 需要第二次 poll
        return False

    monkeypatch.setattr(mod, "_ps_grep", fake_ps)

    def fake_output_result(success, error_message=None, metrics=None):
        captured.update(
            {"success": success, "error_message": error_message, "metrics": metrics or {}}
        )

    monkeypatch.setattr(mod, "output_result", fake_output_result)

    exited = False
    try:
        mod.main()
    except SystemExit:
        exited = True
    return captured, exited


def test_latest_monkey_launch_has_independent_aimwd_window(monkeypatch):
    """#2048：最新 monkey_launch 的 aimwd post-check 必须有独立 max_wait 窗口（#1711）。"""
    version_dir = _latest_version_dir("monkey_launch")
    mod = _load_module("monkey_launch_latest", version_dir / "monkey_launch.py")

    out, exited = _run_launch_timed(monkeypatch, mod)

    assert out.get("success") is True, (
        f"{version_dir.name} 在「MonkeyTest.sh 迟到」场景下判失败："
        f"{out.get('error_message')!r}——aimwd 的 max_wait 窗口是否与 sh 共用？见 #2048/#1711。"
    )
    assert exited is False


@pytest.mark.parametrize("version", ["5.0.2", "5.2.0"])
def test_monkey_launch_repaired_versions_pass_late_sh_scenario(monkeypatch, version):
    """#1711/#2048：两个「已修复」版本都必须通过迟到场景（v5.0.2 与 v5.2.0）。"""
    mod = _load_module(
        f"monkey_launch_{version.replace('.', '_')}",
        _SCRIPTS / "monkey_launch" / f"v{version}" / "monkey_launch.py",
    )
    out, exited = _run_launch_timed(monkeypatch, mod)
    assert out.get("success") is True and exited is False
