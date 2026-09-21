# -*- coding: utf-8 -*-
"""Sleep 停止 + 结果收取（teardown 阶段，issue #462 P0a；G15 对齐 §3.1）。

v1.0.4（#2980）：**把 SIGTERM 路径也纳入临时目录回收**。v1.0.3 的 ``finally``
只覆盖脚本内部异常与 ``SystemExit``——但步骤墙钟到点时引擎是对**进程组**发
SIGTERM（再升 SIGKILL），CPython 默认不把 SIGTERM 转成异常，``finally`` 根本
不执行。本版本：main() 开头为 SIGTERM 装处理器（转 ``SystemExit(143)``，让既有
finally 照常回收；引擎 SIGTERM→SIGKILL 宽限 ~2s，回收的是本地目录，充裕）；
另在启动时兜底清扫 **≥24h 的陈旧孤儿目录**（SIGKILL 升级/断电仍会漏，形状判据
同 v1.0.3 回收：gettempdir 直接子项 ∧ 本族前缀，再叠加陈旧门槛——合法步骤墙钟
为分钟级（≤600s），24h 远大于任何并行兄弟的在飞窗口，不会误删活跃目录）。

v1.0.3（#2834 同形收口）：回收本次自建的临时结果目录。v1.0.0–v1.0.2 用
``tempfile.mkdtemp(prefix="sleep-results-")`` 拉结果后从不删除——与 ``gpu_finish``
同形状。登记 + ``main()`` 的 ``finally`` 统一回收；形状不符不删。

移植自 stability_Sleep-Test/scripts/stop.ps1 + lib.ps1（Stop-SleepTestTask）。
PC wake-watchdog 不移植（G15 决策：OEM 闹钟丢失场景记已知缺口，patrol 兜底）。

流程：
1. 停任务：prefs auto_resume=false+running=false → SLEEP_TEST_STOP 优雅停止 → force-stop 兜底
2. 拉取 sleep_test_result.txt（主路径 /sdcard/Android/data/.../files/SleepTest/，旧路径兜底）
3. 解析（parse_sleep_result）→ 摘要 metrics
4. 逐行结果写 {STP_AEE_NFS_ROOT}/sleep/{project}/results/{run_id}.json
   （run_id = 收尾时刻 sleep_YYYYmmdd_HHMMSS_<serial>，v1.0.1 加设备维度防并行碰撞）
5. stdout JSON 只带摘要（step_trace 64KiB 截断约束同 MTBF）

STP_STEP_PARAMS:
{
    "project": "legacy",
    "force_stop": true      // 优雅停止失败时强制杀（stop.bat -Force 语义）
}

输出 (stdout): {"success": true/false, "metrics": {...}, "detail_uri": "..."}
metrics: {run_id, cycles_done, expected_cycles, wake_failures, sleep_anomalies,
          final_status, result_bytes}
final_status: PASS | FAIL | INCOMPLETE（无 finished 行 = 测试未收尾）
"""
from __future__ import annotations

import json
import shutil
import signal
import sys
import tempfile
import time
from pathlib import Path

from _lib import (
    adb,
    adb_shell,
    device_serial,
    output_result,
    param_or_env,
    params,
    parse_size_from_ls,
    parse_sleep_result,
    results_dir,
    result_paths,
    stop_task,
)



#: 本族临时结果目录前缀——建/删必须同一字面量（#2834 同形）。
_PULL_TMP_PREFIX = "sleep-results-"

#: 本次执行自建的临时目录清单，由 ``main()`` 的 finally 回收。
_TEMP_RESULT_DIRS: list[Path] = []


def _mk_result_tmpdir() -> Path:
    """建本次执行的临时结果目录并登记（登记是回收的唯一途径）。"""
    path = Path(tempfile.mkdtemp(prefix=_PULL_TMP_PREFIX))
    _TEMP_RESULT_DIRS.append(path)
    return path


def _discard_result_tmpdirs() -> None:
    """回收本次自建的临时结果目录；形状不符就不删。"""
    tmp_root = Path(tempfile.gettempdir()).resolve()
    while _TEMP_RESULT_DIRS:
        path = _TEMP_RESULT_DIRS.pop()
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if resolved.parent != tmp_root or not resolved.name.startswith(_PULL_TMP_PREFIX):
            sys.stderr.write(
                f"sleep_finish: skip tmp cleanup (unexpected shape) {resolved}\n"
            )
            continue
        shutil.rmtree(resolved, ignore_errors=True)


#: #2980：孤儿目录兜底清扫的陈旧门槛。合法步骤墙钟为分钟级（≤600s），
#: 24h 是其 ×144——远大于任何并行兄弟的在飞窗口，只会命中真遗孤。
_STALE_TMPDIR_SECONDS = 24 * 3600


def _install_sigterm_guard() -> None:
    """#2980：SIGTERM 转 ``SystemExit``——墙钟到点时让 ``main()`` 的 finally
    照常回收临时目录（CPython 默认不把 SIGTERM 转成异常，finally 不执行）。

    引擎升级 SIGKILL 前有 ~2s grace（pipeline_engine ``_terminate_process_tree``），
    回收是本地 rmtree，时间充裕；SIGKILL 漏网的由 ``_sweep_stale_result_tmpdirs``
    在下次启动时兜底。
    """

    def _abort(signum, _frame):  # noqa: ANN001, ANN202
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, _abort)


def _sweep_stale_result_tmpdirs(max_age_seconds: float = _STALE_TMPDIR_SECONDS) -> None:
    """#2980 兜底：清 **≥24h 的陈旧孤儿目录**（SIGKILL 升级 / 断电遗留）。

    形状判据同 v1.0.3 回收（``gettempdir()`` 直接子项 ∧ 本族前缀），再叠加
    陈旧门槛——宁可多留一天，不可误删兄弟并行执行的活跃目录。
    """
    tmp_root = Path(tempfile.gettempdir()).resolve()
    cutoff = time.time() - max_age_seconds
    try:
        entries = sorted(tmp_root.iterdir())
    except OSError:      # 临时根不可读：放弃本轮兜底，不炸主流程
        return
    for path in entries:
        if not path.name.startswith(_PULL_TMP_PREFIX):
            continue
        try:
            if path.is_dir() and path.stat().st_mtime < cutoff:
                shutil.rmtree(path, ignore_errors=True)
        except OSError:  # stat/删除竞态（外部已清）：跳过
            continue


def _pull_result_file() -> Path:
    """拉取结果文件（主路径优先，旧路径兜底）；都不存在则报错。"""
    for path in result_paths():
        ls = adb_shell(f"ls -l {path}", timeout=30).strip()
        if parse_size_from_ls(ls) <= 0:
            continue
        local = _mk_result_tmpdir() / Path(path).name
        rc, _, err = adb("pull", path, str(local), timeout=120)
        if rc != 0:
            raise RuntimeError(f"adb pull 失败 {path}: {err.strip() or 'rc=%d' % rc}")
        if local.is_file():
            return local
    raise RuntimeError("设备端没有 sleep_test_result.txt（主路径与旧路径均不存在），任务可能未真正运行")


def _run(cfg: dict) -> dict:
    project = str(param_or_env(cfg, "project", "STP_SLEEP_PROJECT", "legacy"))
    force = str(param_or_env(cfg, "force_stop", "STP_SLEEP_FORCE_STOP", "true")).lower() == "true"

    stop_task(force=force)
    time.sleep(2)   # 等结果文件收尾（服务停止时 flush）

    local_file = _pull_result_file()
    parsed = parse_sleep_result(local_file.read_bytes())
    # v1.0.1：run_id 加设备维度——多设备并行同秒不再互相覆盖（验收发现⑨）
    run_id = f"sleep_{time.strftime('%Y%m%d_%H%M%S')}_{device_serial()}"
    final_status = parsed["final_status"] or "INCOMPLETE"
    metrics = {
        "run_id": run_id,
        "cycles_done": parsed["cycles_done"],
        "expected_cycles": parsed["expected_cycles"],
        "wake_failures": parsed["wake_failures"],
        "sleep_anomalies": parsed["sleep_anomalies"],
        "final_status": final_status,
        "result_bytes": local_file.stat().st_size,
    }

    # 逐行结果写中心存储（P2 test_case_result 数据源，mtbf results/ 同款）
    detail_dir = results_dir(project)
    detail_dir.mkdir(parents=True, exist_ok=True)
    detail_file = detail_dir / f"{run_id}.json"
    detail_file.write_text(
        json.dumps({"run_id": run_id, "metrics": metrics, "entries": parsed["entries"]}, ensure_ascii=False),
        encoding="utf-8",
    )
    return {"metrics": metrics, "detail_uri": str(detail_file)}


def main() -> None:
    cfg = params()
    _install_sigterm_guard()        # #2980：墙钟 SIGTERM 也要走 finally 回收
    _sweep_stale_result_tmpdirs()   # #2980：SIGKILL 漏网的孤儿下次启动兜底清
    try:
        try:
            result = _run(cfg)
        except Exception as exc:  # noqa: BLE001
            output_result(False, error_message=str(exc))
            sys.exit(1)
        output_result(True, **result)
    finally:
        # #2834：sys.exit(1) 的 SystemExit 同样走这里——失败路径才是累积主力。
        _discard_result_tmpdirs()


if __name__ == "__main__":
    main()
