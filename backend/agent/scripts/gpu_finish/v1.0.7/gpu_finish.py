# -*- coding: utf-8 -*-
"""GPU 停止 + 结果收取（teardown 阶段，issue #462 P0c；G15 对齐 §3.3）。

v1.0.7（#2980）：**把 SIGTERM 路径也纳入临时目录回收**。v1.0.6 的 ``finally``
只覆盖脚本内部异常与 ``SystemExit``——但步骤墙钟到点时引擎是对**进程组**发
SIGTERM（再升 SIGKILL），CPython 默认不把 SIGTERM 转成异常，``finally`` 根本
不执行（实测退出码 -15、无 finally 标记）。本版本：main() 开头为 SIGTERM 装
处理器（转 ``SystemExit(143)``，让既有 finally 照常回收；引擎 SIGTERM→SIGKILL
宽限 ~2s，回收的是本地目录，充裕）；另在启动时兜底清扫 **≥24h 的陈旧孤儿目录**
（SIGKILL 升级/断电仍会漏，形状判据同 v1.0.6：gettempdir 直接子项 ∧ 本族前缀，
再叠加陈旧门槛——合法步骤墙钟 ≤600s，24h 远大于任何并行兄弟的在飞窗口，
不会误删别人的活跃目录）。

v1.0.6（#2834）：**回收本次自建的临时结果目录**。v1.0.0–v1.0.5 用
``tempfile.mkdtemp(prefix="gpu-results-")`` 拉回 test_log.txt 后从不删除——宿主
``/tmp``（tmpfs）随 GPU 窗口单调累积，实测 .68 打满 100%（1.8G/1.8G）并直接把该机
agent 热更新撞成 ENOSPC 失败（``hot_update_remote_failed``），.64/.67 各钉住 3.3G/3.6G
内存。v1.0.6 把临时目录登记进模块级清单，由 ``main()`` 的 ``finally`` 统一回收：
**成功与失败两条路径都清**（失败窗才是真正反复累积的那一支——一次 ENOSPC/离线让
``_run`` 抛错时，目录原本永久留下）。回收带**形状判据**：只删「临时目录根下、带本族
前缀」的那一层，形状不符就跳过并往 stderr 记一行——脚本手里有 ``shutil`` 权限，少这道
判据时一次变量名写错就会 rmtree 到别人的目录（跨进程数据破坏比留几个临时目录严重一个
量级）。

v1.0.5（#2146）：清理验证补「探测不可用」态——v1.0.4 用 ``adb_shell``（只取
stdout、丢 rc），设备离线/超时/重启窗口下探测返回空串会被判成「没有 REMAINS」
而静默假绿。现改用 ``adb()`` 取 rc：rm rc≠0 / 探测 rc≠0 / 输出既无 REMAINS
也无 CLEAN → 一律 raise（三态：删除成功 / 残留转红 / 探测不可用转红）。

v1.0.4（#894）：结果落盘后删除设备端循环脚本 /sdcard/Auto/gpu_stress_loop.sh
并回读验证（清理完整化——链式衔接不留平台自产脚本）。test_log.txt 保留：
原始日志只 pull 到本机临时目录、平台存储只有摘要 JSON，删除即不可追溯。

移植自 stability_GPU-Test/stop.bat（force-stop 4 包 + pkill instrument；bat 注释：
压测跑在 Antutu 进程内，只停框架）。

流程：
1. 停任务：force-stop 4 包 + pkill 循环脚本/instrument
2. 拉取 /sdcard/Auto/test_log.txt（instrument stdout + 平台标记行原文）
3. 解析（parse_gpu_log）→ 摘要 metrics（标记行为准；instrument 输出原文备查）
4. 摘要 JSON 写 {STP_AEE_NFS_ROOT}/gpu/{project}/results/{run_id}.json
   （run_id = 收尾时刻 gpu_YYYYmmdd_HHMMSS_<serial>，v1.0.2 加设备维度防并行碰撞）
5. stdout JSON 只带摘要（step_trace 64KiB 截断约束同 MTBF）
6. 删除设备端循环脚本并回读验证（v1.0.4；test_log.txt 保留）

STP_STEP_PARAMS:
{
    "project": "legacy",
    "cleanup": true    (default true；删设备端循环脚本 + 回读验证)
}

输出 (stdout): {"success": true/false, "metrics": {...}, "detail_uri": "..."}
metrics: {run_id, rounds_done, expected_rounds, failed_rounds, end_rc, final_status, log_bytes}
final_status: COMPLETED（有 GPU_RUN_END）| INCOMPLETE（无 END 标记）
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
    _DEVICE_SCRIPT,
    _RESULT_LOG,
    adb,
    device_serial,
    output_result,
    param_or_env,
    params,
    parse_gpu_log,
    result_log_bytes,
    results_dir,
    stop_stress,
)


#: 本族临时结果目录的前缀——建它和删它必须用同一个字面量（否则形状判据永远不匹配，
#: 退化回静默泄漏；由 tests/test_gpu_finish_tmp_cleanup_guard_2834.py 钉住）。
_PULL_TMP_PREFIX = "gpu-results-"

#: 本次执行自建的临时目录清单，由 ``main()`` 的 finally 回收（#2834）。
_TEMP_RESULT_DIRS: list[Path] = []


def _mk_result_tmpdir() -> Path:
    """建本次执行的临时结果目录并登记（#2834：登记是回收的唯一途径）。"""
    path = Path(tempfile.mkdtemp(prefix=_PULL_TMP_PREFIX))
    _TEMP_RESULT_DIRS.append(path)
    return path


def _discard_result_tmpdirs() -> None:
    """回收本次自建的临时结果目录；**形状不符就不删**。

    只认「``gettempdir()`` 直接子目录 + 以 ``_PULL_TMP_PREFIX`` 开头」这一种路径。
    判据失配时跳过并往 stderr 记一行（引擎 reader B 丢弃非 PROGRESS 的 stderr 行，
    不影响 stdout 的 JSON 结果契约）——宁可留一个目录，不可删错一个目录。
    """
    tmp_root = Path(tempfile.gettempdir()).resolve()
    while _TEMP_RESULT_DIRS:
        path = _TEMP_RESULT_DIRS.pop()
        try:
            resolved = path.resolve()
        except OSError:      # 目录已被外部删掉/不可 stat：没什么可回收的，也不算失败
            continue
        if resolved.parent != tmp_root or not resolved.name.startswith(_PULL_TMP_PREFIX):
            sys.stderr.write(
                f"gpu_finish: skip tmp cleanup (unexpected shape) {resolved}\n"
            )
            continue
        shutil.rmtree(resolved, ignore_errors=True)


#: #2980：孤儿目录兜底清扫的陈旧门槛。合法步骤墙钟上限 600s（gpu.json），
#: 24h 是它的 ×144——远大于任何并行兄弟的在飞窗口，只会命中真遗孤。
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

    形状判据同 v1.0.6 回收（``gettempdir()`` 直接子项 ∧ 本族前缀），再叠加
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


def _pull_result_log() -> Path:
    """拉取 test_log.txt 到本地临时目录；不存在则报错。"""
    if result_log_bytes() <= 0:
        raise RuntimeError(f"设备端没有 {_RESULT_LOG}，任务可能未真正运行")
    local = _mk_result_tmpdir() / "test_log.txt"
    rc, _, err = adb("pull", _RESULT_LOG, str(local), timeout=300)
    if rc != 0 or not local.is_file():
        raise RuntimeError(f"adb pull {_RESULT_LOG} 失败: {err.strip() or 'rc=%d' % rc}")
    return local


def _cleanup_device_script() -> None:
    """删除设备端循环脚本并回读验证（三态：删除成功 / 残留 / 探测不可用）。

    只删平台自产循环脚本；test_log.txt 保留（原始日志未进中心存储，
    删除即丢证——链式衔接的残留风险来自脚本，不是日志文件）。

    v1.0.5（#2146）：改用 ``adb()`` 取 rc——探测不可用（离线/超时/重启窗口）
    必须 raise，不能把「读不到」当成「干净」（v1.0.4 的静默假绿）。
    """
    rc, _out, err = adb("shell", f"rm -f {_DEVICE_SCRIPT}", timeout=30)
    if rc != 0:
        raise RuntimeError(
            f"设备端脚本清理命令失败：rm -f {_DEVICE_SCRIPT} rc={rc} {err.strip()[:120]}"
        )

    rc, out, err = adb(
        "shell", f"[ -e {_DEVICE_SCRIPT} ] && echo REMAINS || echo CLEAN", timeout=30
    )
    if rc != 0:
        raise RuntimeError(
            f"清理验证不可用：rc={rc}，无法确认 {_DEVICE_SCRIPT} 是否已删除"
            f"（{err.strip()[:120]}）"
        )
    if "REMAINS" in out:
        raise RuntimeError(f"设备端脚本清理失败：{_DEVICE_SCRIPT} 仍存在（#894）")
    if "CLEAN" not in out:
        raise RuntimeError(
            f"清理验证输出异常：{out.strip()[:80]!r}（无法确认已删除）"
        )


def _run(cfg: dict) -> dict:
    project = str(param_or_env(cfg, "project", "STP_GPU_PROJECT", "legacy"))

    stop_stress()
    time.sleep(2)   # 等 instrument 退出、log 收尾

    local_file = _pull_result_log()
    parsed = parse_gpu_log(local_file.read_text(encoding="utf-8", errors="replace"))
    # v1.0.2：run_id 加设备维度——多设备并行同秒不再互相覆盖（验收发现⑨）
    run_id = f"gpu_{time.strftime('%Y%m%d_%H%M%S')}_{device_serial()}"
    # v1.0.3：JUnit FAILURES（rc=0 假成功）单独标记
    if parsed["end_rc"] is None:
        final_status = "INCOMPLETE"
    elif parsed.get("junit_failed_rounds", 0) > 0 or parsed["failed_rounds"] > 0:
        final_status = "TEST_FAILED"
    else:
        final_status = "COMPLETED"
    metrics = {
        "run_id": run_id,
        "test_id": parsed["test_id"],
        "rounds_done": parsed["rounds_done"],
        "expected_rounds": parsed["expected_rounds"],
        "failed_rounds": parsed["failed_rounds"],
        "end_rc": parsed["end_rc"],
        "final_status": final_status,
        "junit_failed_rounds": parsed.get("junit_failed_rounds", 0),
        "log_bytes": local_file.stat().st_size,
    }

    detail_dir = results_dir(project)
    detail_dir.mkdir(parents=True, exist_ok=True)
    detail_file = detail_dir / f"{run_id}.json"
    detail_file.write_text(
        json.dumps({"run_id": run_id, "metrics": metrics, "rounds": parsed["rounds"]}, ensure_ascii=False),
        encoding="utf-8",
    )

    # v1.0.4：结果（NFS 摘要）落盘后再清设备端循环脚本——清理失败不丢本次结果
    if cfg.get("cleanup", True):
        _cleanup_device_script()
        metrics["cleanup_verified"] = True

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
        # #2834：sys.exit(1) 抛的 SystemExit 同样走这里——失败路径留下的临时目录
        # 正是这次打满 /tmp 的主力（GPU 窗失败率越高，累积越快）。
        _discard_result_tmpdirs()


if __name__ == "__main__":
    main()
