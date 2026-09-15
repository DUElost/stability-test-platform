# -*- coding: utf-8 -*-
"""GPU 停止 + 结果收取（teardown 阶段，issue #462 P0c；G15 对齐 §3.3）。

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


def _pull_result_log() -> Path:
    """拉取 test_log.txt 到本地临时目录；不存在则报错。"""
    if result_log_bytes() <= 0:
        raise RuntimeError(f"设备端没有 {_RESULT_LOG}，任务可能未真正运行")
    local = Path(tempfile.mkdtemp(prefix="gpu-results-")) / "test_log.txt"
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
    try:
        result = _run(cfg)
    except Exception as exc:  # noqa: BLE001
        output_result(False, error_message=str(exc))
        sys.exit(1)
    output_result(True, **result)


if __name__ == "__main__":
    main()
