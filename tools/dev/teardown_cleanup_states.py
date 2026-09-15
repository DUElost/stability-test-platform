#!/usr/bin/env python3
"""设备端清理三态验证夹具（#2162）。

对 teardown 类脚本的「清理 + 回读验证」实现跑三种受控场景，确认行为正确：

  ① 删除成功        —— 目标消失且 step 成功；
  ② 残留转红        —— 目标被重建 → 必须转红（报「仍存在」）；
  ③ 探测不可用转红  —— 回读命令本身失败（真实 adb rc≠0）→ 必须转红（报「无法确认」）。

设计要点（来自 #894 / #2146 的真机教训）
--------------------------------------
- **② 用设备端无间隔紧凑循环重建目标**：`while :; do touch f; sleep 0.05; done` 这类带
  间隔的循环会与探测形成**时序空档**，产生假阴性（真机验证时 A2/B2 各失败过一次）。
- **每个用例跑前置自检**（循环存活 / 目标确实在场 / 探测确实能读到）：自检不过报
  **夹具错误**（exit 2），不得记成用例失败——否则会把夹具问题误读成实现缺陷。
- **③ 用真实 adb**：把探测调用指向不存在的 serial → adb 真实返回 rc≠0。不改被测代码、
  不 stub 探测输出（stub 只能验证测试自己的想象）。
- **安全闸**：设备上有 monkey / aim 相关进程时默认拒跑（`--force` 越过，用于明确知道
  该设备可破坏的场合）；跑完必清循环与测试文件。

用法
----
    # 在 agent 宿主机上（本机有 adb，且脚本已部署到 --script-root）
    python3 tools/dev/teardown_cleanup_states.py --serial <SERIAL> --script monkey_teardown
    python3 tools/dev/teardown_cleanup_states.py --serial <SERIAL> --script gpu_finish --version 1.0.5 --json

    # 从控制面经 ansible 下发到目标宿主机执行
    ansible -i ~/hosts.ini <host> -m copy \
      -a 'src=tools/dev/teardown_cleanup_states.py dest=/tmp/ mode=0644'
    ansible -i ~/hosts.ini <host> -m shell \
      -a 'python3 /tmp/teardown_cleanup_states.py --serial <SERIAL> --script monkey_teardown'

退出码：``0`` 全部通过；``1`` 有用例失败（实现行为不符）；``2`` 夹具错误（前置自检/环境不满足）。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

DEFAULT_SCRIPT_ROOT = "/opt/stability-test-agent/agent/scripts"
LOOP_PATTERN = "while :; do touc[h]"          # bracket 技巧：pkill/pgrep 不匹配自身命令行
SAFETY_PATTERNS = ("com.android.commands.monkey", "/data/local/tmp/aimwd", "MonkeyTest.sh")
BOGUS_SERIAL = "NOSUCH_SERIAL0"  # 故意含下划线：非真机 serial 形态，过 public 仓泄漏门禁

MONKEY_PATHS = [
    "/data/local/tmp/MonkeyTest.sh", "/data/local/tmp/offlinemonkey.sh",
    "/data/local/tmp/aim", "/data/local/tmp/aimwd", "/data/local/tmp/aim.jar",
    "/data/local/tmp/monkey.apk", "/data/local/tmp/arm64-v8a",
    "/data/local/tmp/armeabi-v7a", "/sdcard/blacklist.txt",
]
GPU_PATH = "/sdcard/Auto/gpu_stress_loop.sh"


@dataclass(frozen=True)
class Spec:
    """一个脚本的清理验证规格。"""
    name: str
    entry: str                 # "main" = 跑完整脚本入口；其它 = 模块内函数名
    params: dict               # 传给 STP_STEP_PARAMS（main 入口）
    paths: list[str]           # 清理目标清单
    residue_path: str          # ② 重建哪一个（挑成本最低的）
    probe_match: str           # ③ 探测命令前缀（用于把该次调用换成不存在 serial）
    red_markers: dict          # 状态 → 期望在错误信息/异常里出现的子串


SCRIPT_SPECS: dict[str, Spec] = {
    "monkey_teardown": Spec(
        name="monkey_teardown",
        entry="main",
        params={"pull_paths": [], "clear_aee": False, "cleanup": True},
        paths=MONKEY_PATHS,
        residue_path="/sdcard/blacklist.txt",
        probe_match="for p in",
        red_markers={"residue": "cleanup 后仍存在", "probe": "无法确认删除结果"},
    ),
    "gpu_finish": Spec(
        name="gpu_finish",
        entry="_cleanup_device_script",
        params={},
        paths=[GPU_PATH],
        residue_path=GPU_PATH,
        probe_match="[ -e",
        red_markers={"residue": "仍存在", "probe": "清理验证不可用"},
    ),
}


# ── 设备侧封装 ───────────────────────────────────────────────────────────────

class Device:
    """adb 薄封装：全部走真实设备，除 ③ 显式换 serial 外不做任何模拟。"""

    def __init__(self, serial: str, *, adb_bin: str = "adb", timeout: int = 30) -> None:
        self.serial = serial
        self.adb_bin = adb_bin
        self.timeout = timeout
        self.calls: list[list[str]] = []

    def _run(self, args: Sequence[str], *, timeout: int | None = None) -> tuple[int, str, str]:
        cmd = [self.adb_bin, "-s", self.serial, *args]
        self.calls.append(list(cmd))
        try:
            p = subprocess.run(cmd, capture_output=True, text=True,
                               timeout=timeout or self.timeout)
        except (subprocess.TimeoutExpired, OSError) as exc:
            return 1, "", str(exc)
        return p.returncode, p.stdout or "", p.stderr or ""

    def shell(self, command: str, *, timeout: int | None = None) -> tuple[int, str, str]:
        rc, out, err = self._run(["shell", command], timeout=timeout)
        return rc, out.strip(), err.strip()

    def connected(self) -> bool:
        rc, out, _ = self._run(["get-state"])
        return rc == 0 and out.strip() == "device"

    def exists(self, path: str) -> bool:
        _, out, _ = self.shell(f"[ -e {path} ] && echo YES || echo NO")
        return out == "YES"

    def touch(self, path: str) -> None:
        self.shell(f"mkdir -p $(dirname {path}); touch {path}")

    def remove(self, path: str) -> None:
        self.shell(f"rm -f {path}")

    def start_recreate_loop(self, path: str) -> None:
        # 无 sleep：保证探测时刻目标必然在场（消除时序空档）
        self.shell(f"nohup sh -c 'while :; do touch {path}; done' >/dev/null 2>&1 &", timeout=10)

    def loop_alive(self) -> bool:
        _, out, _ = self.shell(f"pgrep -f '{LOOP_PATTERN}' | head -1")
        return bool(out.strip())

    def stop_recreate_loop(self) -> None:
        self.shell(f"pkill -f '{LOOP_PATTERN}' 2>/dev/null", timeout=10)

    def busy_conflicts(self) -> list[str]:
        """设备上是否有 monkey/aim 相关进程（安全闸）。"""
        _, out, _ = self.shell("ps -ef")
        hits = []
        for line in out.splitlines():
            if any(pat in line for pat in SAFETY_PATTERNS) and "pgrep" not in line:
                hits.append(line.strip()[:120])
        return hits


# ── 被测脚本加载与执行 ───────────────────────────────────────────────────────

def pick_version_dir(script_root: Path, name: str, version: str | None) -> Path:
    """返回 <root>/<name>/v<version>；version 省略时取版本号最大的目录。"""
    base = script_root / name
    if not base.is_dir():
        raise FixtureError(f"脚本目录不存在：{base}")
    if version:
        target = base / f"v{version}"
        if not target.is_dir():
            raise FixtureError(f"版本目录不存在：{target}")
        return target
    dirs = [d for d in base.iterdir() if d.is_dir() and re.fullmatch(r"v\d+(\.\d+)*", d.name)]
    if not dirs:
        raise FixtureError(f"{base} 下没有版本目录")
    def key(p: Path) -> tuple[int, ...]:
        return tuple(int(x) for x in p.name[1:].split("."))
    return sorted(dirs, key=key)[-1]


class FixtureError(RuntimeError):
    """夹具自身的问题（环境/前置自检），不得记成用例失败。"""


def load_script_module(version_dir: Path, name: str) -> Any:
    """按路径加载被测脚本（清理辅助模块缓存，避免跨脚本串库）。"""
    entry = version_dir / f"{name}.py"
    if not entry.is_file():
        raise FixtureError(f"入口脚本不存在：{entry}")
    for helper in ("_adb", "_lib"):
        sys.modules.pop(helper, None)
    sys.path.insert(0, str(version_dir))
    spec = importlib.util.spec_from_file_location(f"{name}_under_test", entry)
    if spec is None or spec.loader is None:
        raise FixtureError(f"无法加载：{entry}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@dataclass
class Outcome:
    """一次清理调用的观测结果。"""
    success: bool | None = None       # main 入口：output_result 的 success
    error_message: str = ""           # main 入口：error_message
    metrics: dict = field(default_factory=dict)
    raised: str = ""                  # 函数入口：异常文本

    @property
    def failed(self) -> bool:
        return self.success is False or bool(self.raised)


def run_cleanup(mod: Any, spec: Spec, serial: str, *, log_dir: Path,
                probe_serial_swap: bool = False) -> Outcome:
    """执行一次清理（main 入口或函数入口），③ 态可在探测调用上换 serial。"""
    out = Outcome()
    if probe_serial_swap:
        _install_probe_serial_swap(mod, spec)

    if spec.entry == "main":
        os.environ["STP_DEVICE_SERIAL"] = serial
        os.environ["STP_LOG_DIR"] = str(log_dir)
        os.environ["STP_STEP_PARAMS"] = json.dumps(spec.params)

        def capture(success: bool, **kw: Any) -> None:
            out.success = success
            out.error_message = str(kw.get("error_message") or "")
            out.metrics = dict(kw.get("metrics") or {})

        mod.output_result = capture
        mod.main()
    else:
        os.environ["STP_DEVICE_SERIAL"] = serial
        func = getattr(mod, spec.entry, None)
        if func is None:
            raise FixtureError(f"{spec.name} 没有入口 {spec.entry}()")
        try:
            func()
        except Exception as exc:  # noqa: BLE001 —— 被测实现以异常表达失败
            out.raised = f"{type(exc).__name__}: {exc}"
    return out


def _install_probe_serial_swap(mod: Any, spec: Spec) -> None:
    """把 ③ 的「探测那一跳」换成不存在的 serial（真实 adb、真实 rc≠0）。"""
    if spec.entry == "main":
        real = mod.adb_shell_quiet

        def wrapper(cmd: str, timeout: int = 10):
            if cmd.startswith(spec.probe_match):
                old = os.environ.get("STP_DEVICE_SERIAL")
                os.environ["STP_DEVICE_SERIAL"] = BOGUS_SERIAL
                try:
                    return real(cmd, timeout=timeout)
                finally:
                    if old is not None:
                        os.environ["STP_DEVICE_SERIAL"] = old
            return real(cmd, timeout=timeout)

        mod.adb_shell_quiet = wrapper
    else:
        lib = sys.modules.get("_lib")
        real_adb, real_serial = lib.adb, lib.device_serial

        def adb_wrapper(*args: Any, **kw: Any):
            cmd = args[1] if len(args) > 1 else ""
            if spec.probe_match in cmd:
                lib.device_serial = lambda: BOGUS_SERIAL
                try:
                    return real_adb(*args, **kw)
                finally:
                    lib.device_serial = real_serial
            return real_adb(*args, **kw)

        mod.adb = adb_wrapper


# ── 三个用例 ────────────────────────────────────────────────────────────────

@dataclass
class CaseResult:
    case: str
    ok: bool
    detail: dict


def case_delete_ok(dev: Device, mod: Any, spec: Spec, log_dir: Path) -> CaseResult:
    """① 删除成功：预置目标 → 清理 → 目标消失且成功。"""
    for p in spec.paths:
        dev.touch(p)
    missing = [p for p in spec.paths if not dev.exists(p)]
    if missing:
        raise FixtureError(f"① 预置失败，以下路径未创建：{missing}")

    out = run_cleanup(mod, spec, dev.serial, log_dir=log_dir)
    left = [p for p in spec.paths if dev.exists(p)]
    ok = (not out.failed) and not left
    if spec.entry == "main" and out.success is True:
        ok = ok and out.metrics.get("cleanup_remaining_count", 0) == 0
    return CaseResult("① 删除成功", ok, {
        "success": out.success, "raised": out.raised, "metrics": out.metrics,
        "still_present": left,
    })


def case_residue_red(dev: Device, mod: Any, spec: Spec, log_dir: Path) -> CaseResult:
    """② 残留转红：紧凑循环重建目标 → 清理必须转红并报「仍存在」。"""
    dev.touch(spec.residue_path)
    dev.start_recreate_loop(spec.residue_path)
    try:
        # 前置自检：循环存活 + 删掉后确实会被重建（否则是夹具问题，不是实现问题）
        if not dev.loop_alive():
            raise FixtureError("② 前置自检失败：重建循环未存活")
        time.sleep(0.4)
        dev.remove(spec.residue_path)
        time.sleep(0.3)
        if not dev.exists(spec.residue_path):
            raise FixtureError("② 前置自检失败：删除后目标未被重建（竞态未消除）")

        out = run_cleanup(mod, spec, dev.serial, log_dir=log_dir)
    finally:
        dev.stop_recreate_loop()
        time.sleep(0.2)
        dev.remove(spec.residue_path)

    marker = spec.red_markers["residue"]
    text = out.raised or out.error_message
    ok = out.failed and marker in text
    return CaseResult("② 残留转红", ok, {
        "success": out.success, "raised": out.raised, "error_message": out.error_message,
        "metrics": out.metrics, "expected_marker": marker,
    })


def case_probe_unavailable_red(dev: Device, mod: Any, spec: Spec, log_dir: Path) -> CaseResult:
    """③ 探测不可用转红：真实 adb + 探测那一跳换不存在 serial（真实 rc≠0）。"""
    for p in spec.paths:
        dev.touch(p)

    out = run_cleanup(mod, spec, dev.serial, log_dir=log_dir, probe_serial_swap=True)
    for p in spec.paths:
        dev.remove(p)

    marker = spec.red_markers["probe"]
    text = out.raised or out.error_message
    ok = out.failed and marker in text
    return CaseResult("③ 探测不可用转红", ok, {
        "success": out.success, "raised": out.raised, "error_message": out.error_message,
        "expected_marker": marker,
        "note": f"探测那一跳使用真实 adb + serial={BOGUS_SERIAL}（真实 rc≠0）",
    })


CASES: dict[str, Callable[..., CaseResult]] = {
    "delete_ok": case_delete_ok,
    "residue_red": case_residue_red,
    "probe_unavailable_red": case_probe_unavailable_red,
}


# ── 主流程 ──────────────────────────────────────────────────────────────────

def run_fixture(*, serial: str, script: str, script_root: Path, version: str | None,
                cases: list[str], force: bool, log: Callable[[str], None]) -> dict:
    spec = SCRIPT_SPECS.get(script)
    if spec is None:
        raise FixtureError(f"未登记的脚本：{script}（可选：{sorted(SCRIPT_SPECS)}）")

    dev = Device(serial)
    if not dev.connected():
        raise FixtureError(f"设备 {serial} 不是 device 状态（adb get-state 失败）")

    conflicts = dev.busy_conflicts()
    if conflicts and not force:
        raise FixtureError(
            "设备上有 monkey/aim 相关进程，拒绝在其上做破坏性清理验证（--force 可越过）："
            + "; ".join(conflicts[:3])
        )

    version_dir = pick_version_dir(script_root, script, version)
    log(f"script={script} version={version_dir.name} serial={serial}")

    results: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="tsv-logs-") as log_dir:
        for case_name in cases:
            mod = load_script_module(version_dir, script)   # 每个用例重新加载（干净模块态）
            try:
                res = CASES[case_name](dev, mod, spec, Path(log_dir))
                results.append(res.__dict__)
                log(f"[{'PASS' if res.ok else 'FAIL'}] {res.case}: "
                    f"{json.dumps(res.detail, ensure_ascii=False)[:300]}")
            finally:
                dev.stop_recreate_loop()
    passed = sum(1 for r in results if r["ok"])
    return {
        "script": script, "version": version_dir.name, "serial": serial,
        "passed": passed, "total": len(results), "results": results,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="设备端清理三态验证夹具（#2162）")
    parser.add_argument("--serial", required=True, help="目标设备 serial（破坏性：默认只在该设备上操作）")
    parser.add_argument("--script", required=True, choices=sorted(SCRIPT_SPECS),
                        help="被测脚本名")
    parser.add_argument("--version", help="脚本版本（省略=取目录里最大的版本）")
    parser.add_argument("--script-root", default=DEFAULT_SCRIPT_ROOT,
                        help=f"脚本根目录（默认 {DEFAULT_SCRIPT_ROOT}）")
    parser.add_argument("--case", default="all",
                        help="逗号分隔的用例名（delete_ok,residue_red,probe_unavailable_red）或 all")
    parser.add_argument("--force", action="store_true",
                        help="设备上有在跑的 monkey/aim 进程时仍执行（危险）")
    parser.add_argument("--json", action="store_true", help="stdout 输出 JSON 结果")
    args = parser.parse_args(argv)

    cases = list(CASES) if args.case == "all" else [c.strip() for c in args.case.split(",") if c.strip()]
    unknown = [c for c in cases if c not in CASES]
    if unknown:
        print(f"未知用例：{unknown}", file=sys.stderr)
        return 2

    try:
        summary = run_fixture(
            serial=args.serial, script=args.script, script_root=Path(args.script_root),
            version=args.version, cases=cases, force=args.force,
            log=lambda m: print(m, file=sys.stderr if args.json else sys.stdout),
        )
    except FixtureError as exc:
        print(f"夹具错误：{exc}", file=sys.stderr)
        if args.json:
            print(json.dumps({"error": str(exc), "kind": "fixture"}, ensure_ascii=False))
        return 2

    if args.json:
        print(json.dumps(summary, ensure_ascii=False))
    else:
        print(f"\nSUMMARY: {summary['passed']}/{summary['total']} passed")
    return 0 if summary["passed"] == summary["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
