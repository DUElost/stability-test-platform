"""#3202 / #3223 守卫：禁止「只把 time.sleep 换成 no-op、不推进时钟」的用例写法。

来历：2026-09-23 13:42 控制面宿主整机卡死（SwapFree 见底 658 MiB、只能人工按电源）。元凶是
`test_powercycle_scripts.py` 的用例把 `mod.time.sleep` 换成 `lambda s: None` 却没同时替换
`mod.time.time`，而被测 `install_apk` 的等待环靠**真时钟**判 deadline（`wait_system_ready`：
`while True` + `sleep(min(_ATT_READY_POLL_SECONDS=5, remaining))`，默认 ready=60s、budget=90s）
⇒ 每圈耗时≈0、退出条件只能等真墙钟走完 ⇒ 60–90 秒忙等；adb 桩又每次 `calls.append(...)`
永不回收 ⇒ 内存按圈单调增长，实测 ≈150 MB/s。

为什么不能靠 CI 兜：修复前该用例在 #3208 的 `pr-agent-tests` 里是**绿的**（5m50s）——峰值 ~9 GiB
在空闲 runner 上能扛，在只剩 3–8 GiB 余量的生产控制面宿主上不能。门禁绿 ≠ 宿主安全。

判据粒度是**函数**不是文件：`test_powercycle_scripts.py` 本来就有 `_patch_advancing_clock`，
按文件判会把真凶正好放过（写这条时实测到的假阴性）。按 #2639 纪律自证锚点在、判据有牙。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SUITE = REPO_ROOT / "backend" / "agent" / "tests"

NOOP_SLEEP = re.compile(r"""setattr\(\s*[\w.]+\.time\s*,\s*["']sleep["']\s*,\s*lambda[^:]*:\s*None\s*\)""")
ADVANCES_CLOCK = re.compile(r"""_patch_advancing_clock\(|setattr\(\s*[\w.]+\.time\s*,\s*["']time["']""")

#: 存量豁免（函数粒度）。值 = 2026-09-23 在同一棵 main 树上、MemoryMax=1600M +
#: MemorySwapMax=0 的 cgroup 顶内**逐文件实测**结果。
#: 读法（重要）：它只证明「今天这条路径没走进墙钟等待环」，**不是结构安全**。
#: 新增一处即红；换掉一处必须同条删除登记，否则也红 —— 登记不缩短即为未完成。
#: 存量收敛与账 1（给等待环加迭代上界）在 #3223。
_ADVANCING_CLOCK_DEBT: dict[str, str] = {
    "test_check_device_scripts.py::_patch_v102": "6 passed/1.03s/峰值0.04GiB",
    "test_clear_recents_v105.py::test_invalid_dump_path_fails_step_without_touching_device": "15 passed/0.04s/峰值0.04GiB",
    "test_device_script_misc_fixes.py::_prep_trigger": "13 passed/0.05s/峰值0.04GiB",
    "test_device_script_misc_fixes.py::test_connect_wifi_quotes_credentials_and_verifies": "13 passed/0.05s/峰值0.04GiB",
    "test_device_script_misc_fixes.py::test_connect_wifi_rc_failure_reports_error": "13 passed/0.05s/峰值0.04GiB",
    "test_device_script_misc_fixes.py::test_connect_wifi_v102_attempts_connect_when_prefix_ssid_connected": "13 passed/0.05s/峰值0.04GiB",
    "test_device_script_misc_fixes.py::test_oobe_skip_wait_adbd_ready_polls_until_device": "13 passed/0.05s/峰值0.04GiB",
    "test_device_script_misc_fixes.py::test_oobe_skip_wait_adbd_ready_times_out": "13 passed/0.05s/峰值0.04GiB",
    "test_ensure_root_scripts.py::_patch": "8 passed/2.04s/峰值0.04GiB",
    "test_ensure_root_scripts.py::_patch_v102": "8 passed/2.04s/峰值0.04GiB",
    "test_flash_firmware.py::TestRebootIntoFlashMode.test_reboot_nonzero_captured": "25 passed/0.04s/峰值0.03GiB",
    "test_gpu_scripts.py::TestInstallApkStableV121.test_transient_failure_recovers": "25 passed/0.06s/峰值0.03GiB",
    "test_monkey_watchdog_chain_809.py::_common_patches": "31 passed/1.16s/峰值0.04GiB",
    "test_monkey_watchdog_chain_809.py::_run_launch": "31 passed/1.16s/峰值0.04GiB",
    "test_monkey_watchdog_chain_809.py::test_restart_false_on_shell_rc_nonzero": "31 passed/1.16s/峰值0.04GiB",
    "test_monkey_watchdog_chain_809.py::test_restart_false_when_aimwd_never_seen": "31 passed/1.16s/峰值0.04GiB",
    "test_monkey_watchdog_chain_809.py::test_restart_false_when_monkeytest_sh_never_seen": "31 passed/1.16s/峰值0.04GiB",
    "test_monkey_watchdog_chain_809.py::test_restart_true_only_when_both_watchdogs_seen": "31 passed/1.16s/峰值0.04GiB",
    "test_monkey_watchdog_chain_809.py::test_wait_ps_process_command_carries_watchdog_exclusion": "31 passed/1.16s/峰值0.04GiB",
    "test_monkey_watchdog_chain_809.py::test_wait_ps_process_sees_process_after_retries": "31 passed/1.16s/峰值0.04GiB",
    "test_monkey_watchdog_chain_809.py::test_wait_ps_process_times_out_when_absent": "31 passed/1.16s/峰值0.04GiB",
    "test_mtbf_scripts.py::TestEnsureAdbRoot.test_root_denied_fails_fast_with_build_diagnostics": "21 passed/0.06s/峰值0.04GiB",
    "test_mtbf_scripts.py::TestEnsureAdbRoot.test_root_ok": "21 passed/0.06s/峰值0.04GiB",
    "test_mtbf_scripts.py::TestEnsureAdbRoot.test_root_retries_through_adbd_restart_window": "21 passed/0.06s/峰值0.04GiB",
    "test_sleep_scripts.py::TestFinish.test_run_incomplete_marked": "37 passed/0.08s/峰值0.03GiB",
    "test_sleep_scripts.py::TestFinish.test_run_writes_detail_json": "37 passed/0.08s/峰值0.03GiB",
    "test_sleep_scripts.py::TestFinishV101RunId.test_run_id_has_serial": "37 passed/0.08s/峰值0.03GiB",
    "test_sleep_scripts.py::TestInstallApkV103.test_transient_failure_recovers": "37 passed/0.08s/峰值0.03GiB",
    "test_teardown_cleanup_894.py::_prep_gf": "13 passed/0.05s/峰值0.03GiB",
    "test_teardown_cleanup_894.py::_prep_mt": "13 passed/0.05s/峰值0.03GiB",
    "test_teardown_cleanup_894.py::test_gf_v105_run_marks_verified_and_writes_detail": "13 passed/0.05s/峰值0.03GiB",
    "test_teardown_rc_guards.py::_prep_mt": "8 passed/0.04s/峰值0.03GiB",
    "test_teardown_rc_guards.py::_prep_sa": "8 passed/0.04s/峰值0.03GiB",
}


def offending_functions(path: Path) -> list[str]:
    """该文件里「把 sleep 换成 no-op、同函数却没推进时钟」的函数限定名。"""
    src = path.read_text(encoding="utf-8", errors="replace")
    found: list[str] = []

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                seg = ast.get_source_segment(src, child) or ""
                if NOOP_SLEEP.search(seg) and not ADVANCES_CLOCK.search(seg):
                    found.append(prefix + child.name)
                walk(child, prefix + child.name + ".")
            elif isinstance(child, ast.ClassDef):
                walk(child, prefix + child.name + ".")
            else:
                walk(child, prefix)

    walk(ast.parse(src), "")
    return found


def current_debt() -> set[str]:
    return {
        f"{f.name}::{name}"
        for f in sorted(SUITE.glob("test_*.py"))
        for name in offending_functions(f)
    }


def test_anchor_is_present_before_judging_shape() -> None:
    """先证锚点在：扫面非空、且被扫对象里确有 sleep stub —— 否则本判据在空转。"""
    assert SUITE.is_dir(), f"{SUITE} 不在了：扫面要跟着改，别让它静默失效"
    files = sorted(SUITE.glob("test_*.py"))
    assert len(files) > 100, f"扫面只剩 {len(files)} 个文件：目录或命名变了，判据已退化"
    scanned = "".join(f.read_text(encoding="utf-8", errors="replace") for f in files)
    assert ".time, " in scanned and '"sleep"' in scanned, "扫面里已无任何 sleep stub：请把判据退役，别留恒绿测试"
    assert current_debt() or _ADVANCING_CLOCK_DEBT, "扫面与登记同时为空：判据已空转"


def test_no_new_noop_sleep_stub_without_advancing_clock() -> None:
    grown = sorted(current_debt() - set(_ADVANCING_CLOCK_DEBT))
    stale = sorted(set(_ADVANCING_CLOCK_DEBT) - current_debt())
    fix = "处置：同函数内改用 _patch_advancing_clock(monkeypatch, mod)（sleep 推进假时钟，循环才收敛）。"
    assert not grown, (
        "这些用例把 time.sleep 换成 no-op 却没有推进时钟：被测等待环若靠真 time.time() 判 "
        f"deadline，就会变成 60–90 秒忙等并按圈积累内存（#3202 实测 ≈150 MB/s，曾冻结控制面宿主）。"
        f"\n{fix}\n" + "\n".join(f"  - {name}" for name in grown)
    )
    assert not stale, (
        "这些豁免已不再成立（说明已修好），请把登记条目一起删掉——登记不缩短即为未完成：\n"
        + "\n".join(f"  - {name}（{reason}）" for name, reason in sorted(_ADVANCING_CLOCK_DEBT.items()) if name in stale)
    )


def test_debt_register_carries_measured_evidence() -> None:
    """反空转：每条豁免必须带实测字样，不接受"应该没事"式理由。"""
    assert current_debt() <= set(_ADVANCING_CLOCK_DEBT), "登记漏了现存条目"
    empty = sorted(k for k, v in _ADVANCING_CLOCK_DEBT.items() if "passed" not in v or "峰值" not in v)
    assert not empty, "这些豁免缺少实测证据（passed/耗时/峰值）：\n" + "\n".join(f"  - {k}" for k in empty)


def test_guard_is_discriminative(tmp_path: Path) -> None:
    """自证有牙：坏形态必被抓，三种安全形态必放过。"""
    cases = {
        "bad.py": 'class TestOne:\n    def test_spins(self, v103, monkeypatch):\n'
                  '        monkeypatch.setattr(v103.time, "sleep", lambda s: None)\n        v103.install_apk(p)\n',
        "good_helper.py": 'class TestOne:\n    def test_ok(self, v103, monkeypatch):\n'
                          '        _patch_advancing_clock(monkeypatch, v103)\n        v103.install_apk(p)\n',
        "good_stub_time.py": 'class TestOne:\n    def test_ok2(self, mod, monkeypatch):\n'
                             '        monkeypatch.setattr(mod.time, "sleep", lambda s: None)\n'
                             '        monkeypatch.setattr(mod.time, "time", lambda: 1000.0)\n',
        "good_recording.py": 'class TestOne:\n    def test_ok3(self, mod, monkeypatch):\n'
                             '        sleeps = []\n'
                             '        monkeypatch.setattr(mod.time, "sleep", lambda s: sleeps.append(s))\n',
    }
    for name, text in cases.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    assert offending_functions(tmp_path / "bad.py") == ["TestOne.test_spins"], "坏形态没被抓到：判据退化"
    for name in ("good_helper.py", "good_stub_time.py", "good_recording.py"):
        assert offending_functions(tmp_path / name) == [], f"安全形态被误判：{name}"
