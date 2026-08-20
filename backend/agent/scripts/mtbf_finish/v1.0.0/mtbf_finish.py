# -*- coding: utf-8 -*-
"""MTBF 停止 + 结果收取（teardown 阶段，ADR-0030 D6 P0 / P0 设计 §3.5）。

移植自 stability_MTBF-Test/scripts/stop.ps1 + lib.ps1（Stop-MtbfTask）。

流程：
1. 停任务：auto_resume=false（防看门狗/开机续跑）→ action.stop 优雅停止 → force-stop 兜底
2. 拉取 /sdcard/results/realresult（最新运行目录）
3. 解析 TESTS-RealResult-TestPoints.xml → 摘要 metrics（join 键 = testpoint name）
4. 逐条结果写 {STP_AEE_NFS_ROOT}/mtbf/{project}/results/{run_dir}.json（P2 test_case_result 数据源）
5. stdout JSON 只带摘要（规避 step_trace 64KiB 截断）

STP_STEP_PARAMS:
{
    "project": "legacy",
    "force_stop": true          // 优雅停止失败时强制杀（对应 stop.bat -Force）
}

输出 (stdout): {"success": true/false, "metrics": {rounds 摘要...}, "detail_uri": "..."}
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

from _lib import (
    adb_shell,
    device_serial,
    output_result,
    params,
    parse_realresult,
    results_dir,
)

_OSM_PACKAGE = "com.ape.offlinescriptmanager"
_RESULTS_ROOT = "/sdcard/results"
_RESULT_XML = "TESTS-RealResult-TestPoints.xml"


def _set_auto_resume(enabled: bool) -> None:
    pref_dir = f"/data/data/{_OSM_PACKAGE}/shared_prefs"
    adb_shell(f"mkdir -p {pref_dir}", timeout=30)
    value = "true" if enabled else "false"
    content = (
        "<?xml version='1.0' encoding='utf-8' standalone='yes' ?>\n"
        "<map>\n"
        f'    <boolean name="auto_resume" value="{value}"/>\n'
        "</map>\n"
    ).encode("utf-8")
    with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name
    try:
        _push_file(Path(tmp_path), f"{pref_dir}/mtbf_runner.xml")
    finally:
        Path(tmp_path).unlink(missing_ok=True)
    adb_shell(f"chown system:system {pref_dir}/mtbf_runner.xml", timeout=30)
    adb_shell(f"chmod 660 {pref_dir}/mtbf_runner.xml", timeout=30)


def _push_file(local: Path, remote: str) -> None:
    from _lib import adb
    rc, _, err = adb("push", str(local), remote, timeout=120)
    if rc != 0:
        raise RuntimeError(f"push 失败 {local.name} -> {remote}: {err.strip()}")


def _service_alive() -> bool:
    out = adb_shell(f"dumpsys activity services {_OSM_PACKAGE}", timeout=30)
    return "RunTaskService" in out


def _stop_task(force: bool) -> None:
    _set_auto_resume(False)
    if _service_alive():
        adb_shell(
            f"am startservice -n {_OSM_PACKAGE}/{_OSM_PACKAGE}.view.RunTaskService "
            f"-a {_OSM_PACKAGE}.view.RunTaskService.action.stop",
            timeout=30,
        )
        time.sleep(3)
    if force or _service_alive():
        adb_shell(f"am force-stop {_OSM_PACKAGE}", timeout=30)
        time.sleep(1)
    if _service_alive():
        raise RuntimeError("停止 RunTaskService 失败（优雅停止 + force-stop 均未生效）")


def _latest_run_dir() -> str:
    out = adb_shell(f"ls {_RESULTS_ROOT}/realresult/", timeout=30).strip()
    names = [line for line in out.splitlines() if line.strip() and not line.startswith("total")]
    return names[-1] if names else ""


def _pull_results() -> tuple[str, Path]:
    """拉取 realresult 目录到本地临时目录，返回 (run_dir, 本地目录)。"""
    run_dir = _latest_run_dir()
    if not run_dir:
        raise RuntimeError("设备端没有结果目录（/sdcard/results/realresult 为空），任务可能未真正运行")
    local_dir = Path(tempfile.mkdtemp(prefix="mtbf-results-"))
    from _lib import adb
    rc, _, err = adb("pull", f"{_RESULTS_ROOT}/realresult/", str(local_dir), timeout=600)
    if rc != 0:
        raise RuntimeError(f"adb pull realresult 失败: {err.strip()}")
    return run_dir, local_dir / run_dir


def _run(cfg: dict) -> dict:
    project = cfg.get("project", "legacy")
    force = bool(cfg.get("force_stop", True))

    _stop_task(force=force)
    time.sleep(2)   # 等结果文件 close（writer 在任务停止时收尾）

    run_dir, local_run = _pull_results()
    xml_path = local_run / _RESULT_XML
    if not xml_path.is_file():
        raise RuntimeError(f"结果文件缺失: {xml_path}")

    parsed = parse_realresult(xml_path.read_bytes())
    metrics = {
        "run_dir": run_dir,
        "taskname": parsed["taskname"],
        "entries": parsed["entries"],
        "passed": parsed["passed"],
        "failed": parsed["failed"],
        "error": parsed["error"],
    }

    # 逐条结果写中心存储（P2 test_case_result 的数据源）
    detail_dir = results_dir(project)
    detail_dir.mkdir(parents=True, exist_ok=True)
    detail_file = detail_dir / f"{run_dir}.json"
    detail_file.write_text(
        json.dumps({"run_dir": run_dir, "metrics": metrics, "testpoints": parsed["testpoints"]}, ensure_ascii=False),
        encoding="utf-8",
    )
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
