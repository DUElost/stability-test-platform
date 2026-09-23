# -*- coding: utf-8 -*-
"""MTBF 停止 + 结果收取（teardown 阶段，ADR-0030 D6 P0 / P0 设计 §3.5）。

移植自 stability_MTBF-Test/scripts/stop.ps1 + lib.ps1（Stop-MtbfTask）。

v1.6.0（#810，R08）：run_dir 以 setup 绑定为准（无绑定回退最新）——只 pull
绑定目录，避免残留目录把历史轮 entries 混入归档；归档成功后清除绑定。

v1.5.0 相对 v1.4.0（中心结果路径具备跨设备唯一性，#1030）：
  - `run_dir` 是设备本地毫秒时间戳，非全局唯一（设计即如此）。v1.4.0 的
    结果文件名 `{run_dir}.json` 没有设备/Job 维度：同项目两台设备产生同名
    run_dir 时写向同一文件，互相覆盖；写入也是直接 write_text，消费方
    （case_result_ingest 按 detail_uri 读）可能读到写了一半的 JSON。
  - 文件名加入稳定身份 `{run_dir}__job{STP_JOB_ID}__{STP_DEVICE_SERIAL}.json`
    （缺失的维度省略，特殊字符归一化为 `_`）：不同设备天然隔离，同一 Job
    重跑仍覆盖同一路径（幂等，不产垃圾）。
  - 改为同目录临时文件 + `os.replace` 原子发布；写失败不留隐藏临时文件。
  - 消费方只读 detail_uri（不按文件名约定扫目录），路径变更对其透明。

流程：
1. 停任务：auto_resume=false（防看门狗/开机续跑）→ action.stop 优雅停止 → force-stop 兜底
2. 拉取 /sdcard/results/realresult（最新运行目录）
3. 解析 TESTS-RealResult-TestPoints.xml → 摘要 metrics（join 键 = testpoint name）
4. 逐条结果写 {STP_AEE_NFS_ROOT}/mtbf/{project}/results/{run_dir}__job{job}__{serial}.json
   （P2 test_case_result 数据源，v1.5.0 起路径带稳定身份）
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
import os
import re
import sys
import tempfile
import time
from pathlib import Path

from _lib import (
    adb,
    adb_shell,
    clear_run_dir,
    device_serial,
    load_run_dir,
    output_result,
    param_or_env,
    params,
    parse_realresult,
    results_dir,
    sha256_file,
    suite_dir,
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


def _bound_run_dir(project: str) -> str:
    """#810：优先 setup 绑定的 run_dir；无绑定才回退最新（兼容旧 setup/手动跑）。"""
    bound = load_run_dir(project)
    if bound:
        return bound
    return _latest_run_dir()


def _pull_results(project: str) -> tuple[str, Path]:
    """拉取 realresult 目录到本地临时目录，返回 (run_dir, 本地 XML 目录)。

    adb pull 目录语义：远端末级目录名会保留在本地（<local>/realresult/{run_dir}/）。
    v1.2.0 误以 ``local_dir / run_dir`` 定位 → 冒烟 #217 teardown 实测
    「结果文件缺失」；此处修正并留 adb 版本差异兜底。

    v1.6.0（#810）：run_dir 以 setup 绑定为准（无绑定回退最新）——避免残留目录
    把历史轮数据混入本次归档。
    """
    run_dir = _bound_run_dir(project)
    if not run_dir:
        raise RuntimeError("设备端没有结果目录（/sdcard/results/realresult 为空），任务可能未真正运行")
    local_dir = Path(tempfile.mkdtemp(prefix="mtbf-results-"))
    rc, _, err = adb("pull", f"{_RESULTS_ROOT}/realresult/{run_dir}/", str(local_dir), timeout=600)
    if rc != 0:
        # 兜底：个别自检/权限场景按整目录拉（保留旧行为）
        rc2, _, err2 = adb("pull", f"{_RESULTS_ROOT}/realresult/", str(local_dir), timeout=600)
        if rc2 != 0:
            raise RuntimeError(f"adb pull realresult 失败: {(err or err2).strip()}")
    xml_dir = local_dir / run_dir
    if not xml_dir.is_dir():
        alt = local_dir / "realresult" / run_dir
        xml_dir = alt if alt.is_dir() else xml_dir
    return run_dir, xml_dir


def _result_stem(run_dir: str) -> str:
    """#1030：结果文件名加稳定身份 —— run_dir 不是全局唯一键。

    Why: run_dir 是设备本地毫秒时间戳（设计如此），同项目两台设备完全可能
         产生同名目录；v1.4.0 直接用 `{run_dir}.json` → 后写者覆盖先写者，
         先完成那台的结果永久丢失。
    How to apply: 追加 job_id 与 device_serial（缺失的维度省略）；同一 Job 重跑
                 仍得到同一名字（幂等覆盖，不产垃圾）；非文件名安全字符归一。
    """
    parts = [run_dir]
    job_id = (os.environ.get("STP_JOB_ID") or "").strip()
    if job_id:
        parts.append(f"job{job_id}")
    serial = (os.environ.get("STP_DEVICE_SERIAL") or "").strip()
    if serial:
        parts.append(serial)
    return "__".join(re.sub(r"[^A-Za-z0-9._-]", "_", p) for p in parts)


def _write_json_atomic(path: Path, payload: dict) -> None:
    """#1030：同目录临时文件 + os.replace 发布，消费方不会读到写了一半的 JSON。

    写失败时清掉临时文件 —— 隐藏的 .tmp-* 留在 results 目录里会污染人工排查。
    """
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8",
        )
        os.replace(tmp, path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def _run(cfg: dict) -> dict:
    project = str(param_or_env(cfg, "project", "STP_MTBF_PROJECT", "legacy"))
    force = str(param_or_env(cfg, "force_stop", "STP_MTBF_FORCE_STOP", "true")).lower() == "true"

    _stop_task(force=force)
    time.sleep(2)   # 等结果文件 close（writer 在任务停止时收尾）

    run_dir, local_run = _pull_results(project)
    xml_path = local_run / _RESULT_XML
    if not xml_path.is_file():
        raise RuntimeError(f"结果文件缺失: {xml_path}")

    parsed = parse_realresult(xml_path.read_bytes())
    # suite_sha256：与 setup 同源（NFS 未 patch 的 runtask.xml）——结果 JSON 与
    # init step_trace 的 suite_sha256 形成闭环（P2 test_case_result 关联键）。
    suite_sha = sha256_file(suite_dir(project) / "runtask.xml")
    metrics = {
        "run_dir": run_dir,
        "suite_sha256": suite_sha,
        "taskname": parsed["taskname"],
        "entries": parsed["entries"],
        "passed": parsed["passed"],
        "failed": parsed["failed"],
        "error": parsed["error"],
    }

    # 逐条结果写中心存储（P2 test_case_result 的数据源）
    detail_dir = results_dir(project)
    detail_dir.mkdir(parents=True, exist_ok=True)
    detail_file = detail_dir / f"{_result_stem(run_dir)}.json"
    _write_json_atomic(
        detail_file,
        {"run_dir": run_dir, "metrics": metrics, "testpoints": parsed["testpoints"]},
    )
    # #810：本次运行已归档，清掉 run_dir 绑定，避免下一轮 Job 误用残留绑定。
    clear_run_dir()
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
