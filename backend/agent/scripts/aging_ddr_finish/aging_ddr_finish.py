# -*- coding: utf-8 -*-
"""FacAging (com.ape.aging) Aging DDR 结果收取（teardown 阶段）。

只负责收取测试证据与产出结构化摘要（足迹解除由 ``aging_ddr_disarm`` 负责）：
1. 等待设备在线且 ``sys.boot_completed==1``（支持重启窗口重试）；
2. 读取 ``aging_pipeline_session`` / ``ddr_test_session`` / ``ddr_test_report`` /
   ``ddr_test_params`` 与 ``/cache/ddr_test_flag``；
3. 拉取最新 ``ddr_report_*.csv``（优先读取 ``ddr_test_report.records`` 中的 ``EXPORT:``
   路径，否则枚举 ``exports/`` 目录最新文件）；
4. 归档详情 JSON（及 CSV 副本）至 ``{STP_AEE_NFS_ROOT}/aging-ddr/{project}/results/``；
5. ``main()`` 挂 SIGTERM 处理器并在 ``finally`` 中清理本地临时目录，启动时清扫 ≥24h 陈旧目录。

STP_STEP_PARAMS:
{
    "project": "default",
    "collect_attempts": 3,
    "collect_retry_wait_seconds": 120
}

输出 (stdout): {"success": true/false, "metrics": {...}, "detail_uri": "...", "csv_uri": "..."}
"""
from __future__ import annotations

import json
import re
import shutil
import signal
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from _lib import (
    EXPORTS_DIR,
    adb,
    adb_shell,
    device_online,
    device_serial,
    output_result,
    param_or_env,
    params,
    progress_stamp,
    read_datastore_file,
    results_dir,
)

_PULL_TMP_PREFIX = "aging-ddr-results-"
_TEMP_RESULT_DIRS: list[Path] = []
_STALE_TMPDIR_SECONDS = 24 * 3600

_DEFAULT_COLLECT_ATTEMPTS = 3
_DEFAULT_COLLECT_RETRY_WAIT = 120

_EXPORT_RE = re.compile(r"EXPORT:\s*(/\S+\.csv)")


def _mk_result_tmpdir() -> Path:
    path = Path(tempfile.mkdtemp(prefix=_PULL_TMP_PREFIX))
    _TEMP_RESULT_DIRS.append(path)
    return path


def _discard_result_tmpdirs() -> None:
    tmp_root = Path(tempfile.gettempdir()).resolve()
    while _TEMP_RESULT_DIRS:
        path = _TEMP_RESULT_DIRS.pop()
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if resolved.parent != tmp_root or not resolved.name.startswith(_PULL_TMP_PREFIX):
            sys.stderr.write(
                f"aging_ddr_finish: skip tmp cleanup (unexpected shape) {resolved}\n"
            )
            continue
        shutil.rmtree(resolved, ignore_errors=True)


def _install_sigterm_guard() -> None:
    def _abort(signum, _frame):  # noqa: ANN001, ANN202
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, _abort)


def _sweep_stale_result_tmpdirs(max_age_seconds: float = _STALE_TMPDIR_SECONDS) -> None:
    tmp_root = Path(tempfile.gettempdir()).resolve()
    cutoff = time.time() - max_age_seconds
    try:
        entries = sorted(tmp_root.iterdir())
    except OSError:
        return
    for path in entries:
        if not path.name.startswith(_PULL_TMP_PREFIX):
            continue
        try:
            if path.is_dir() and path.stat().st_mtime < cutoff:
                shutil.rmtree(path, ignore_errors=True)
        except OSError:
            continue


def _wait_device_online(timeout_s: int) -> bool:
    deadline = time.monotonic() + max(0, int(timeout_s))
    seq = 0
    while time.monotonic() < deadline:
        seq += 1
        online = device_online(timeout=10)
        booted = ""
        if online:
            booted = adb_shell("getprop sys.boot_completed", timeout=10).strip()
            if booted == "1":
                return True
        progress_stamp(
            {
                "phase": "wait_device_online",
                "seq": seq,
                "online": online,
                "boot_completed": booted,
            }
        )
        time.sleep(min(10.0, max(0.0, deadline - time.monotonic())))
    return False


def _find_latest_remote_csv(report_records: str) -> str | None:
    matches = _EXPORT_RE.findall(report_records or "")
    if matches:
        return matches[-1]
    rc, out, _ = adb("shell", f"ls -1t {EXPORTS_DIR}/ddr_report_*.csv 2>/dev/null", timeout=15)
    if rc != 0:
        return None
    for line in out.splitlines():
        cand = line.strip()
        if cand.endswith(".csv") and "No such file" not in cand:
            return cand
    return None


def _pull_csv(remote_csv: str) -> Path | None:
    tmp_dir = _mk_result_tmpdir()
    local_csv = tmp_dir / Path(remote_csv).name
    rc, _, err = adb("pull", remote_csv, str(local_csv), timeout=60)
    if rc != 0 or not local_csv.is_file():
        sys.stderr.write(f"aging_ddr_finish: pull csv failed {remote_csv}: {err.strip()}\n")
        return None
    return local_csv


def _collect_once() -> dict[str, Any]:
    pipe_kind, pipe = read_datastore_file("aging_pipeline_session.preferences_pb", timeout=15)
    ddr_kind, ddr = read_datastore_file("ddr_test_session.preferences_pb", timeout=15)
    rep_kind, rep = read_datastore_file("ddr_test_report.preferences_pb", timeout=15)
    params_kind, ddr_params = read_datastore_file("ddr_test_params.preferences_pb", timeout=15)

    if (
        pipe_kind == "transient"
        and ddr_kind == "transient"
        and rep_kind == "transient"
        and not device_online(timeout=5)
    ):
        raise RuntimeError("设备在收取结果中途离线，等待重试")

    if pipe_kind == "absent" and ddr_kind == "absent" and rep_kind == "absent":
        raise RuntimeError("设备端无任何 Aging DDR DataStore 文件，任务可能未真正运行")

    flag_rc, flag_out, _ = adb("shell", "cat /cache/ddr_test_flag 2>/dev/null", timeout=10)
    ddr_test_flag = flag_out.strip() if flag_rc == 0 else ""

    records = str(rep.get("records") or "")
    remote_csv = _find_latest_remote_csv(records)
    local_csv = _pull_csv(remote_csv) if remote_csv else None

    return {
        "pipe_kind": pipe_kind,
        "pipe": pipe,
        "ddr_kind": ddr_kind,
        "ddr": ddr,
        "rep_kind": rep_kind,
        "rep": rep,
        "params_kind": params_kind,
        "ddr_params": ddr_params,
        "ddr_test_flag": ddr_test_flag,
        "remote_csv": remote_csv or "",
        "local_csv": local_csv,
    }


def _determine_final_status(pipe: dict[str, Any], rep: dict[str, Any]) -> str:
    pipe_status = str(pipe.get("status") or "").strip().upper()
    stage_status = str(pipe.get("stage_status") or "").strip()
    report_result = str(rep.get("result") or "").strip().upper()
    if pipe_status == "FAILED" or "DDR:DONE_FAIL" in stage_status or report_result == "FAIL":
        return "FAIL"
    if pipe_status == "COMPLETED" or "DDR:DONE_PASS" in stage_status or report_result == "PASS":
        return "PASS"
    return "INCOMPLETE"


def _collect_with_retry(wait_s: int, attempts: int) -> dict[str, Any]:
    last_exc: RuntimeError | None = None
    tried = 0
    for idx in range(attempts):
        progress_stamp({"phase": "collect_attempt", "attempt": idx + 1, "max_attempts": attempts})
        if not _wait_device_online(wait_s):
            last_exc = RuntimeError(f"等待设备上线超时（{wait_s}s）")
            break
        tried += 1
        try:
            return _collect_once()
        except RuntimeError as exc:
            last_exc = exc
    raise RuntimeError(
        f"收取 Aging DDR 结果失败（尝试 {tried}/{attempts} 次，单次等待 {wait_s}s）：{last_exc}"
    ) from last_exc


def _run(cfg: dict[str, Any]) -> dict[str, Any]:
    project = str(param_or_env(cfg, "project", "STP_AGING_DDR_PROJECT", "default")).strip() or "default"
    collect_attempts = max(
        1,
        int(
            str(
                param_or_env(
                    cfg,
                    "collect_attempts",
                    "STP_AGING_DDR_COLLECT_ATTEMPTS",
                    _DEFAULT_COLLECT_ATTEMPTS,
                )
            )
            or _DEFAULT_COLLECT_ATTEMPTS
        ),
    )
    collect_wait = max(
        5,
        int(
            str(
                param_or_env(
                    cfg,
                    "collect_retry_wait_seconds",
                    "STP_AGING_DDR_COLLECT_RETRY_WAIT_SECONDS",
                    _DEFAULT_COLLECT_RETRY_WAIT,
                )
            )
            or _DEFAULT_COLLECT_RETRY_WAIT
        ),
    )

    serial = device_serial()
    collected = _collect_with_retry(collect_wait, collect_attempts)
    pipe = collected["pipe"]
    ddr = collected["ddr"]
    rep = collected["rep"]
    local_csv: Path | None = collected["local_csv"]

    run_id = f"aging_ddr_{time.strftime('%Y%m%d_%H%M%S')}_{serial}"
    final_status = _determine_final_status(pipe, rep)
    csv_bytes = local_csv.stat().st_size if local_csv and local_csv.is_file() else 0

    metrics: dict[str, Any] = {
        "run_id": run_id,
        "session_id": pipe.get("session_id") or ddr.get("session_id") or "",
        "final_status": final_status,
        "pipeline_status": pipe.get("status", ""),
        "stage_status": pipe.get("stage_status", ""),
        "report_result": rep.get("result", ""),
        "last_error": pipe.get("last_error", ""),
        "current_module_pass": ddr.get("current_module_pass"),
        "module_pass_count": ddr.get("module_pass_count"),
        "current_reboot_count": ddr.get("current_reboot_count"),
        "ddr_test_flag": collected["ddr_test_flag"],
        "csv_filename": local_csv.name if local_csv else "",
        "csv_bytes": csv_bytes,
    }

    out: dict[str, Any] = {"metrics": metrics}
    detail_dir = results_dir(project)
    if detail_dir is not None:
        detail_dir.mkdir(parents=True, exist_ok=True)
        detail_file = detail_dir / f"{run_id}.json"
        detail_payload = {
            "run_id": run_id,
            "serial": serial,
            "metrics": metrics,
            "pipeline_session": pipe,
            "ddr_session": ddr,
            "ddr_params": collected["ddr_params"],
            "report_records": rep.get("records", ""),
            "remote_csv": collected["remote_csv"],
            "ddr_test_flag": collected["ddr_test_flag"],
        }
        detail_file.write_text(
            json.dumps(detail_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        out["detail_uri"] = str(detail_file)
        if local_csv and local_csv.is_file():
            csv_dest = detail_dir / f"{run_id}.csv"
            shutil.copyfile(local_csv, csv_dest)
            out["csv_uri"] = str(csv_dest)
    return out


def main() -> None:
    cfg = params()
    _install_sigterm_guard()
    _sweep_stale_result_tmpdirs()
    try:
        try:
            result = _run(cfg)
        except Exception as exc:  # noqa: BLE001 — 脚本顶层统一输出结构化错误
            output_result(False, error_message=str(exc))
            sys.exit(1)
        output_result(True, **result)
    finally:
        _discard_result_tmpdirs()


if __name__ == "__main__":
    main()
