# -*- coding: utf-8 -*-
"""PowerCycle 轮询（patrol 阶段，issue #462 P0b；G15 对齐 §3.2）。

v1.0.13（#4222 问题 1，2026-11-28）：
- 开机卡死（bootanimation deadlock / SWT）早停：当 ``device_online()`` 为真但
  ``service_alive()`` 为假时，按 ``boot_check_interval_seconds``（默认 120s，0=每轮）
  抽检 ``getprop sys.boot_completed``；若连续超过 ``boot_hang_timeout_seconds``
  （默认 600s）未就绪，立即返回 ``patrol_fail=True`` 与结构化诊断摘要；
- 打破 ``result_bytes == 0`` 与 boot 转换（``not was_online``）的永久豁免：
  新增 ``zero_result_timeout_seconds``（默认 900s，0=关闭）；超时仍无结果写入
  且服务未存活或零进展时不再清零 ``dead_streak``，达到 ``dead_grace_cycles`` 即判死；
- 持续 ADB 离线有界检测：新增 ``max_offline_seconds``（默认 1800s，0=关闭），
  对 ``boot_hang_since`` / ``zero_result_since`` / ``offline_since`` 均以时间锚持久化，
  bootloop 交替掉线重连也不被清零。

v1.0.11（NX3 开关机专项，2026-11-12）：
- 判死不再豁免 cycles_done==0：NX3 应用读不到 prefs 时 current_count 恒 0，
  结果文件只有旧行、服务已死，旧逻辑每轮清零 dead_streak ⇒ 永远 PATROL_SLEEP。
  文件有内容且 mtime 停滞时按 grace 判死；首轮基线仍由 result_bytes==0 /
  not was_online 兜住。
- push_prefs_xml（_lib）按应用 data 目录 context 对齐 shared_prefs
  目录与 prefs 文件的 SELinux 标签，避免巡检收取窗口 resume 时重新写入
  不可读的 prefs。

v1.0.10（#3463 §9 G1-pc-2，2026-09-27）：prefs 读路径 kind 区分收口（F1「六族内
所有 prefs 读路径」，#3471 复核 B1/B3 退回项）。**收取窗口步骤语义不变**（§9.1
裁定：失败记 `collect_error`、下周期重试、补偿 resume、`success` 仍为 true）：
- `get_prefs_xml`（_lib）port root 优先形态（setup 1.2.3+ 同构）——巡检进度读
  与窗口 pause/resume 链路在 root 下不再恒空；
- `set_prefs(reset_count=false)`（_lib）：kind 区分，读不到不以 0 整写；
- `resume_task` / `start_task`（_lib）：prefs 不可读（非 `ok`）时 raise，交给
  窗口既有的补偿/重试语义承接。

v1.0.9（#3463 G1，2026-09-27）：F1 + F2 双收口（batch B1，方案见 #3463 §3 G1）。
- F1 port powercycle_setup 参照：`_root_read_prefs` 单调用同源证据 +
  收紧后的 `repair_prefs_ownership`（只有「存在 + 读成功 + 内容空」才删——
  旧拷贝「run-as 读空 + 可 root ⇒ rm」对 platform 签名 shared-uid 包每轮删
  健康 prefs）；`set_stop_flags` 只认 `absent` 才整写最小 map，
  `transient`/`denied` 保留文件并 raise（pause 路径步骤转红可重试）；
- F2 `_lib.adb()` 改 bytes 采集 + `decode_device_output` 宽容 UTF-8（port 自
  gpu_setup v1.2.3 参照，#3069 形态）。

v1.0.8（#813，R08-F04）：收取窗口 pause→resume 原子化——pause 前先落
``paused_by_collect`` 意图标记；收取/恢复异常时在 except 分支补偿 resume，
窗口外周期入口也检查该标记补做恢复（进程被杀场景）。补偿成功则本轮不判死并
清零停滞账；补偿失败保留标记继续重试、且照常判死（不掩盖真故障）。

v1.0.7（#1028，R08-F10）：与 mtbf_check v1.4.0 同型——巡检状态写入``job_id``（STP_JOB_ID），新 Job 不继承上一 Job 的 seq / 收取窗口标记（collecting_done_for_window / last_collected）/ last_online。

v1.0.6（综合验收方案 A）：**定时收取窗口**——``collect_window_start``
（"HH:MM"，**东八区业务时间**）+ ``collect_window_minutes``（10/30/60）Per-Plan。
判死策略定稿（最终验收遗留）：**文件停滞判定**——判死 = 结果文件 mtime 停止
增长（服务死且无进展），替代「服务死 N 周期」；偶发启动失败（自愈场景）
在下轮 reboot 文件增长时清零，不再误判。
可配（步骤 params 自由键）；窗口内暂停任务 → 等设备稳定在线 → 收取结果写中心
存储 → 恢复续跑，PROGRESS 报 ``phase=collecting``。另修复验收发现⑥：设备
offline→online 转换（boot 窗口）不累计 dead_streak。

每周期（patrol_interval_seconds，建议 300）执行一次：
1. 存活判定：PowerCycleService 在跑？——**设备离线（重启周期中）不判服务死亡**
   （reboot 模式固有现象；持续离线由平台心跳 UNKNOWN/恢复链路兜底）；
   设备在线但服务死亡连续 dead_grace_cycles（默认 2）周期 → 判死（boot 转换除外）。
2. **完成检测（v1.0.1，同 sleep_check 冒烟发现）**：结果文件含 ``finished result=`` 行
   即测试自然收尾（服务完成 test_times 后自停）——优先报完成，不判死。
3. **定时收取窗口（v1.0.2）**：见上。
4. 进度采集：prefs current_count/test_times（run-as 读，权威续跑计数）；
   prefs 读不到 → 设备端 grep -c 'cycle ' 兜底（结果文件行数）。
5. PROGRESS 打戳（#115：stderr，配 stall_seconds 使用）。
6. stdout JSON 摘要。

跨周期状态（连续死亡计数）落在 Agent 本机临时文件，键 = 设备序列号。

STP_STEP_PARAMS:
{
    "project": "legacy",
    "expected_cycles": 100,      // 可选注入（P1 suite 绑定后自动填充）；0=用 prefs test_times
    "dead_grace_cycles": 2,
    "collect_window_start": "00:00",   // 定时收取窗口起点（Agent 本地时间；空=不启用）
    "collect_window_minutes": 30,      // 窗口时长（10/30/60）
    "boot_check_interval_seconds": 120,  // 服务未存活时抽检 sys.boot_completed 的最小间隔（0=每轮）
    "boot_hang_timeout_seconds": 600,    // ADB 在线但 boot_completed!=1 超过该秒数判开机卡死（0=关闭）
    "zero_result_timeout_seconds": 900,  // 服务未存活且结果文件持续为空/零进展超时（0=关闭）
    "max_offline_seconds": 1800          // 连续 ADB 离线超过该秒数发 patrol_fail（0=关闭）
}

输出 (stdout): {"success": true/false, "progress": {...}}
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from _lib import (
    adb_path,
    adb_shell,
    collect_powercycle_result,
    device_online,
    device_serial,
    get_prefs_xml,
    get_r30_prefs_xml,
    output_result,
    param_or_env,
    params,
    parse_size_from_ls,
    pause_task,
    pause_task_r30,
    progress_stamp,
    resolve_apk_flavor,
    result_paths,
    resume_task,
    resume_task_r30,
    service_alive,
)


def _state_file() -> Path:
    return Path(tempfile.gettempdir()) / f"powercycle_check_{device_serial()}.json"


def _load_state() -> dict:
    try:
        return json.loads(_state_file().read_text())
    except (OSError, ValueError):
        return {}


def _save_state(state: dict) -> None:
    try:
        _state_file().write_text(json.dumps(state))
    except OSError:
        pass


def _compensate_pending_resume(state: dict, *, apk_flavor: str = "stp") -> bool:
    """#813：把「已暂停但未恢复」的自动测试任务恢复续跑。

    返回 True 表示当前已无待恢复状态（恢复成功、或服务本就在跑）。
    恢复失败时保留 ``paused_by_collect`` 标记与 ``resume_error``，由后续周期
    继续补偿；成功才清标记。
    """
    if not state.get("paused_by_collect"):
        return True
    alive_now = service_alive() if apk_flavor == "stp" else service_alive(apk_flavor)
    if alive_now:
        # 任务已在跑（恢复已生效/暂停未真正落下）——只清标记
        state["paused_by_collect"] = False
        state.pop("resume_error", None)
        _save_state(state)
        return True
    try:
        if apk_flavor == "r30":
            resume_task_r30()
        else:
            resume_task()
    except Exception as exc:  # noqa: BLE001 — 补偿失败不中断巡检，下周期重试
        state["resume_error"] = str(exc)
        _save_state(state)
        return False
    state["paused_by_collect"] = False
    state.pop("resume_error", None)
    _save_state(state)
    return True


def _read_prefs_progress(apk_flavor: str = "stp") -> tuple[int, int] | None:
    """prefs 进度读取（stp: current_count/test_times；r30: restarted_times/Num）；读不到返回 None。"""
    if apk_flavor == "r30":
        xml = get_r30_prefs_xml()
        if not xml:
            return None
        cur = re.search(r'name="restarted_times" value="(\d+)"', xml)
        if not cur:
            return None
        total = re.search(r'name="Num" value="(\d+)"', xml)
        return int(cur.group(1)), int(total.group(1)) if total else 0
    xml = get_prefs_xml()
    if not xml:
        return None
    cur = re.search(r'name="current_count" value="(\d+)"', xml)
    if not cur:
        return None
    total = re.search(r'name="test_times" value="(\d+)"', xml)
    return int(cur.group(1)), int(total.group(1)) if total else 0


def _grep_cycle_count(apk_flavor: str = "stp") -> int:
    """兜底：结果文件循环计数（stp: 'cycle '；r30: 'time implement result'）。"""
    paths = result_paths() if apk_flavor == "stp" else result_paths(apk_flavor)
    pattern = "time implement result" if apk_flavor == "r30" else "cycle "
    for path in paths:
        rc, out, _ = _adb_grep(path) if apk_flavor == "stp" else _adb_grep(path, pattern=pattern)
        if rc == 0 and out.strip():
            try:
                return int(out.strip().splitlines()[-1])
            except ValueError:
                pass
    return 0


def _result_bytes(apk_flavor: str = "stp") -> int:
    paths = result_paths() if apk_flavor == "stp" else result_paths(apk_flavor)
    for path in paths:
        ls = adb_shell(f"ls -l {path}", timeout=30).strip()
        size = parse_size_from_ls(ls)
        if size > 0:
            return size
    return 0


def _result_mtime(apk_flavor: str = "stp") -> int:
    """结果文件 mtime（epoch 秒，stat -c %Y）；不可读返回 0。"""
    paths = result_paths() if apk_flavor == "stp" else result_paths(apk_flavor)
    for path in paths:
        out = adb_shell(f"stat -c %Y {path}", timeout=30).strip()
        if out.isdigit():
            return int(out)
    return 0


def _adb_grep(path: str, *, pattern: str = "cycle ") -> tuple[int, str, str]:
    try:
        result = subprocess.run(
            [adb_path(), "-s", device_serial(), "shell", f"grep -c '{pattern}' {path} 2>/dev/null"],
            capture_output=True, text=True, timeout=30,
        )
        return result.returncode, result.stdout or "", result.stderr or ""
    except subprocess.TimeoutExpired:
        return -1, "", "timeout"


def _run_finished(apk_flavor: str = "stp") -> bool:
    """结果文件是否已有自然收尾标记（stp: ``finished result=``；r30: ``Test result`` + ``Total :``）。"""
    paths = result_paths() if apk_flavor == "stp" else result_paths(apk_flavor)
    for path in paths:
        try:
            result = subprocess.run(
                [adb_path(), "-s", device_serial(), "shell", f"cat {path}"],
                capture_output=True, text=True, timeout=30,
            )
        except subprocess.TimeoutExpired:
            continue
        text = result.stdout or ""
        if apk_flavor == "r30":
            if "Test result" in text and "Total :" in text:
                return True
        elif "finished result=" in text:
            return True
    return False


def _in_collect_window(cfg: dict, now=None) -> bool:
    """定时收取窗口判定（方法 A，验收方案确认）。

    ``collect_window_start`` = "HH:MM"（**Agent 主机本地时间**——设备时钟
    严重偏斜，不能读设备时间）；``collect_window_minutes`` = 10/30/60。
    窗口跨天（如 23:50 + 30min → 次日 00:20 前）正确覆盖。未配置 start
    （空）→ 恒 False（不启用）。
    """
    start = str(param_or_env(cfg, "collect_window_start", "STP_POWER_CYCLE_COLLECT_WINDOW_START", ""))
    if not start:
        return False
    minutes = max(1, int(str(param_or_env(
        cfg, "collect_window_minutes", "STP_POWER_CYCLE_COLLECT_WINDOW_MINUTES", "30",
    )) or 30))
    try:
        hh, mm = (int(x) for x in start.split(":"))
        if not (0 <= hh <= 23 and 0 <= mm <= 59):
            return False
    except ValueError:
        return False
    # v1.0.3：窗口按**东八区（UTC+8）**判定——collect_window_start 是业务时区
    # （北京时间）配置；主机时区各异（实测 -67 为 PDT -07:00），主机本地
    # 时间判定会让「凌晨 0 点」在不同主机上错位 15 小时。
    now = now or datetime.now(timezone(timedelta(hours=8)))
    start_today = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if start_today <= now:
        return now < start_today + timedelta(minutes=minutes)
    # 起点在明天：昨天窗口可能跨到今天凌晨
    return now < (start_today - timedelta(days=1)) + timedelta(minutes=minutes)


def _wait_online_short(timeout_s: int) -> bool:
    """窗口收取前的短等待：设备稳定在线（收取必须在非重启窗口执行）。"""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if device_online():
            return True
        time.sleep(5)
    return False


def _boot_completed() -> bool:
    """检查 Android 系统是否完成启动（getprop sys.boot_completed == '1'）。"""
    try:
        result = subprocess.run(
            [adb_path(), "-s", device_serial(), "shell", "getprop", "sys.boot_completed"],
            capture_output=True, text=True, timeout=15,
        )
        return result.returncode == 0 and (result.stdout or "").strip() == "1"
    except subprocess.TimeoutExpired:
        return False


def _int_param(cfg: dict, key: str, env_key: str, default: int) -> int:
    raw = str(param_or_env(cfg, key, env_key, str(default)) or str(default)).strip()
    try:
        return max(0, int(raw))
    except ValueError:
        return default


def _run(cfg: dict) -> dict:
    grace = max(1, int(str(param_or_env(cfg, "dead_grace_cycles", "STP_POWER_CYCLE_DEAD_GRACE_CYCLES", "2")) or 2))
    injected = int(str(cfg.get("expected_cycles") or 0) or 0)
    project = str(param_or_env(cfg, "project", "STP_POWER_CYCLE_PROJECT", "legacy"))
    apk_flavor = resolve_apk_flavor(cfg)
    boot_check_interval = _int_param(cfg, "boot_check_interval_seconds", "STP_POWER_CYCLE_BOOT_CHECK_INTERVAL_SECONDS", 120)
    boot_hang_timeout = _int_param(cfg, "boot_hang_timeout_seconds", "STP_POWER_CYCLE_BOOT_HANG_TIMEOUT_SECONDS", 600)
    zero_result_timeout = _int_param(cfg, "zero_result_timeout_seconds", "STP_POWER_CYCLE_ZERO_RESULT_TIMEOUT_SECONDS", 900)
    max_offline = _int_param(cfg, "max_offline_seconds", "STP_POWER_CYCLE_MAX_OFFLINE_SECONDS", 1800)

    state = _load_state()
    # #1028（R08-F10）：巡检状态是「单次运行」语义——新 Job 不继承上一 Job 的
    # dead_streak / 周期序号 / 收取窗口标记，否则新 Job 首个周期就背上旧账、
    # 提前耗尽宽限。job 身份来自 Agent 注入的 STP_JOB_ID；身份缺失（手动跑）
    # 时与残留旧键不等同样重置一次，此后维持既有语义。
    job_id = os.environ.get("STP_JOB_ID", "").strip()
    if state.get("job_id") != job_id:
        state = {"job_id": job_id}
        _save_state(state)

    now = time.time()

    if not device_online():
        # 重启周期中设备离线属正常，短暂离线不判死；但持续离线超过
        # max_offline_seconds（默认 1800s）发 patrol_fail 避免长时间黑洞（#4222）。
        state["last_online"] = False
        offline_since = state.get("offline_since")
        if not isinstance(offline_since, (int, float)) or offline_since <= 0:
            offline_since = now
            state["offline_since"] = offline_since
        offline_elapsed = int(max(0.0, now - float(offline_since)))
        payload = {
            "seq": int(state.get("seq", 0)) + 1,
            "step": "powercycle_check",
            "cycles_done": 0,
            "expected_cycles": injected,
            "result_bytes": 0,
            "device_online": False,
            "service_alive": False,
            "offline_elapsed_seconds": offline_elapsed,
        }
        state["seq"] = payload["seq"]
        _save_state(state)
        progress_stamp(payload)
        if max_offline > 0 and offline_elapsed >= max_offline:
            return {
                "success": False,
                "patrol_fail": True,
                "error_message": (
                    f"设备连续 ADB 离线 {offline_elapsed}s（超过阈值 {max_offline}s），"
                    "开关机压测异常（可能死机/掉卡/USB断连）"
                ),
                "progress": payload,
            }
        return {"success": True, "progress": payload}

    state["offline_since"] = None
    was_online = bool(state.get("last_online", True))

    if _in_collect_window(cfg):
        # 定时收取窗口（方法 A）：暂停 → 等设备稳定 → 收取 → 恢复续跑。
        # 窗口期间任务暂停中，服务死亡属预期——不判死。
        if not state.get("collecting_done_for_window"):
            # #813：先落「暂停意图」再执行 pause——收取/恢复中途异常或进程被杀
            # 时，窗口外周期可凭该标记做补偿性 resume，避免任务永久暂停后因
            # mtime 停滞被误判服务死亡（整 Plan 误 FAILED）。
            state["paused_by_collect"] = True
            _save_state(state)
            try:
                if apk_flavor == "r30":
                    pause_task_r30()
                else:
                    pause_task()
                if not _wait_online_short(timeout_s=120):
                    raise RuntimeError("收取窗口内设备 120s 未上线，跳过本轮收取（下个周期重试）")
                collected = (
                    collect_powercycle_result(project, apk_flavor="r30")
                    if apk_flavor == "r30"
                    else collect_powercycle_result(project)
                )
                if apk_flavor == "r30":
                    resume_task_r30()
                else:
                    resume_task()
                state["paused_by_collect"] = False
                state.pop("resume_error", None)
                state["last_collected"] = collected["run_id"]
                state["collecting_done_for_window"] = True
                state.pop("collect_error", None)
            except Exception as exc:  # noqa: BLE001 — 收取失败不判死，下周期重试
                state["collect_error"] = str(exc)
                # 补偿：pause 已执行而 resume 未确认时必须尝试恢复；失败则保留
                # 标记，由窗口外周期继续补偿。
                _compensate_pending_resume(state, apk_flavor=apk_flavor)
        payload = {
            "seq": int(state.get("seq", 0)) + 1,
            "step": "powercycle_check",
            "phase": "collecting",
            "cycles_done": 0,
            "expected_cycles": injected,
            "result_bytes": 0,
            "device_online": True,
            "service_alive": False,
            "last_collected_run_id": state.get("last_collected"),
            "collect_error": state.get("collect_error"),
        }
        state["seq"] = payload["seq"]
        _save_state(state)
        progress_stamp(payload)
        return {"success": True, "progress": payload}

    # 窗口外：#813 先补偿「被暂停但未恢复」（上一窗口异常/进程被杀遗留）。
    # 恢复成功则本轮不判死（服务启动需要时间），并清掉暂停期累积的停滞账；
    # 补偿失败保留标记继续尝试，且照常进入判死评估（不掩盖真故障）。
    if state.get("paused_by_collect"):
        if _compensate_pending_resume(state, apk_flavor=apk_flavor):
            payload = {
                "seq": int(state.get("seq", 0)) + 1,
                "step": "powercycle_check",
                "phase": "resume_recovered",
                "cycles_done": 0,
                "expected_cycles": injected,
                "result_bytes": 0,
                "device_online": True,
                "service_alive": service_alive() if apk_flavor == "stp" else service_alive(apk_flavor),
                "resume_error": state.get("resume_error"),
            }
            state["dead_streak"] = 0
            state["last_mtime"] = 0
            state["seq"] = payload["seq"]
            _save_state(state)
            progress_stamp(payload)
            return {"success": True, "progress": payload}

    # 窗口外：清窗口状态
    state["collecting_done_for_window"] = False
    state.pop("collect_error", None)

    alive = service_alive() if apk_flavor == "stp" else service_alive(apk_flavor)
    prefs = _read_prefs_progress() if apk_flavor == "stp" else _read_prefs_progress(apk_flavor)
    cycles_done = prefs[0] if prefs else (_grep_cycle_count() if apk_flavor == "stp" else _grep_cycle_count(apk_flavor))
    expected = injected or (prefs[1] if prefs else 0)
    result_bytes = _result_bytes() if apk_flavor == "stp" else _result_bytes(apk_flavor)

    if _run_finished() if apk_flavor == "stp" else _run_finished(apk_flavor):
        # 自然收尾：报完成（成败由 teardown 解析 final_status 定）
        state["boot_hang_since"] = None
        state["zero_result_since"] = None
        payload = {
            "seq": int(state.get("seq", 0)) + 1,
            "step": "powercycle_check",
            "cycles_done": cycles_done,
            "expected_cycles": expected,
            "result_bytes": result_bytes,
            "device_online": True,
            "service_alive": alive,
            "run_finished": True,
        }
        state["seq"] = payload["seq"]
        state["last_online"] = True
        _save_state(state)
        progress_stamp(payload)
        return {"success": True, "progress": payload}

    # #4222 问题 1a：仅在 service_alive()==False 时按窗口抽检 sys.boot_completed，
    # 避免对健康运行态主机/设备产生额外 ADB 并发压力。
    boot_completed_now: bool | None = None
    if alive:
        state["boot_hang_since"] = None
        boot_completed_now = True
    elif boot_hang_timeout > 0:
        last_boot_check = float(state.get("last_boot_check_at") or 0.0)
        hang_since_raw = state.get("boot_hang_since")
        hang_due = (
            isinstance(hang_since_raw, (int, float))
            and hang_since_raw > 0
            and (now - float(hang_since_raw)) >= boot_hang_timeout
        )
        if last_boot_check <= 0 or (now - last_boot_check) >= boot_check_interval or hang_due:
            boot_completed_now = _boot_completed()
            state["last_boot_check_at"] = now
            if boot_completed_now:
                state["boot_hang_since"] = None
            elif not isinstance(hang_since_raw, (int, float)) or hang_since_raw <= 0:
                state["boot_hang_since"] = now

    boot_hang_since = state.get("boot_hang_since")
    if (
        not alive
        and boot_hang_timeout > 0
        and isinstance(boot_hang_since, (int, float))
        and boot_hang_since > 0
    ):
        boot_hang_elapsed = int(max(0.0, now - float(boot_hang_since)))
        if boot_hang_elapsed >= boot_hang_timeout:
            state["last_online"] = True
            payload = {
                "seq": int(state.get("seq", 0)) + 1,
                "step": "powercycle_check",
                "cycles_done": cycles_done,
                "expected_cycles": expected,
                "result_bytes": result_bytes,
                "device_online": True,
                "service_alive": False,
                "boot_completed": False,
                "boot_hang_elapsed_seconds": boot_hang_elapsed,
                "failure_reason": "boot_hang_timeout",
            }
            state["seq"] = payload["seq"]
            _save_state(state)
            progress_stamp(payload)
            return {
                "success": False,
                "patrol_fail": True,
                "error_message": (
                    f"设备 ADB 在线但 sys.boot_completed 连续 {boot_hang_elapsed}s 未就绪"
                    f"（超过阈值 {boot_hang_timeout}s），疑似开机卡死/bootanimation deadlock"
                ),
                "progress": payload,
            }

    # ⑥ 修复（v1.0.6 定稿）+ #4222 问题 1b（有界零进展突破永久豁免）：
    mtime = _result_mtime() if apk_flavor == "stp" else _result_mtime(apk_flavor)
    prev_cycles = int(state.get("last_cycles_done", 0) or 0)
    prev_bytes = int(state.get("last_result_bytes", 0) or 0)
    made_progress = (
        alive
        or cycles_done > prev_cycles
        or result_bytes > prev_bytes
        or (mtime > 0 and state.get("last_mtime") not in (None, 0) and mtime != state.get("last_mtime"))
    )
    if made_progress:
        state["zero_result_since"] = None
    elif result_bytes == 0:
        if not isinstance(state.get("zero_result_since"), (int, float)) or state["zero_result_since"] <= 0:
            state["zero_result_since"] = now

    zero_result_since = state.get("zero_result_since")
    zero_result_elapsed = (
        int(max(0.0, now - float(zero_result_since)))
        if isinstance(zero_result_since, (int, float)) and zero_result_since > 0
        else 0
    )
    zero_result_timed_out = (
        not alive
        and result_bytes == 0
        and zero_result_timeout > 0
        and zero_result_elapsed >= zero_result_timeout
    )

    if alive:
        state["dead_streak"] = 0
    elif zero_result_timed_out:
        state["dead_streak"] = int(state.get("dead_streak", 0)) + 1
    elif result_bytes == 0 or not was_online:
        state["dead_streak"] = 0
    elif mtime > 0 and mtime == state.get("last_mtime"):
        state["dead_streak"] = int(state.get("dead_streak", 0)) + 1
    else:
        state["dead_streak"] = 0
    state["last_mtime"] = mtime
    state["last_cycles_done"] = cycles_done
    state["last_result_bytes"] = result_bytes
    state["last_online"] = True
    _save_state(state)

    if state["dead_streak"] >= grace:
        svc_name = "RebootService" if apk_flavor == "r30" else "PowerCycleService"
        detail = (
            f"（结果文件持续为空 {zero_result_elapsed}s，无重启进展）"
            if zero_result_timed_out
            else ""
        )
        return {
            "success": False,
            "error_message": f"{svc_name} 连续 {state['dead_streak']} 个周期未存活{detail}",
            "progress": {
                "cycles_done": cycles_done,
                "expected_cycles": expected,
                "result_bytes": result_bytes,
                "device_online": True,
                "service_alive": False,
                **({"zero_result_elapsed_seconds": zero_result_elapsed} if zero_result_timed_out else {}),
            },
        }

    payload = {
        "seq": int(state.get("seq", 0)) + 1,
        "step": "powercycle_check",
        "cycles_done": cycles_done,
        "expected_cycles": expected,
        "result_bytes": result_bytes,
        "device_online": True,
        "service_alive": alive,
    }
    state["seq"] = payload["seq"]
    _save_state(state)
    progress_stamp(payload)
    return {"success": True, "progress": payload}


def main() -> None:
    cfg = params()
    try:
        result = _run(cfg)
    except Exception as exc:  # noqa: BLE001
        output_result(False, error_message=str(exc))
        sys.exit(1)
    success = bool(result.pop("success"))
    output_result(success, **result)


if __name__ == "__main__":
    main()
