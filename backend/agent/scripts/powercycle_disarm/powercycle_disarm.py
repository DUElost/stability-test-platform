# -*- coding: utf-8 -*-
"""开关机解除 / 探针（#3730 / #3723 §3.6 / ADR-0061 D2）。

v1.0.8（#3723 §3.6.6 v1.7.8）：服务状态按返回码分 alive/stopped/unknown；
STOP、force-stop 与最后一次状态查询的返回码任一不满足即不写并报 failed；
evidence 记录各步返回码。
v1.0.7（#3723 §3.6.6 v1.7.7）：读取先看 rc=0 且含 <map 根元素；root 空文件
删除后重建最小 prefs；错误字样只在 rc!=0 或无 prefs 内容时用于分类。
v1.0.6（#3723 §3.6.6）：先 STOP/force-stop 再写停止标志，同时验证
running/auto_resume；非 root 按 run-as 输出分为 not_installed/absent/
denied/transient；未安装不写设备；prefs 不存在写最小 prefs；读取被拒或
未知不整写。
v1.0.5：§3.6.2 v1.7.2–v1.7.3 —— 轮询间隔 ≥10s、单次 adb 超时之和 ≤5s；
上线后每次 adb 结束打 PROGRESS。§3.6.1.7 v1.7.3–v1.7.4 —— permission_model
按 id -u 记 root/non-root/unknown；离线/开机未完成不取证；probe 读
sys.boot_completed，evidence 记 boot_completed。
v1.0.4：§3.6.1 第 7 条 —— 脚本不内置已验证类别清单，正式值一律按未验证规则；
evidence 增加权限模型与 AutoTestTool APK SHA-256。
v1.0.3：§3.6.1.3 —— 未验证 **probe**（含原始 clear）不得 ``success=true`` /
``executed_unverified`` 当通过；已完成 **disarm** 的未验证结果仍为
``executed_unverified``。
v1.0.1：按 #3723 v1.6 §3.6.1 区分类别语义——未验证类别不得输出正式
``confirmed`` / ``readback_failed``；解除已完成但无可信回读 → ``executed_unverified``。
v1.0.0：与结果收取拆分——本脚本只负责足迹解除与回读，不拉结果文件。

参数（STP_STEP_PARAMS）::

    {
      "mode": "probe" | "disarm",          # 默认 disarm
      "wait_device_online_seconds": 600,   # disarm 等待设备上线上限
      "device_platform": null,             # 可选覆盖；缺省现场探测
      "device_model": null                 # 可选覆盖；缺省 getprop ro.product.model
    }

``disarm``：等待上线 → 写 ``auto_resume=false`` / ``running=false`` → 停服务
→ root 优先回读。``probe``：只回读，不写 prefs、不停服务。

stdout JSON（引擎解析 ``disarm_result`` / ``readback``）::

    {
      "success": true/false,
      "disarm_result": "<六值之一>",
      "readback": "clear" | "footprint" | "readback_failed",
      "readback_summary": "...",
      "evidence": {...},
      "mode": "probe"|"disarm"
    }

原始 ``readback`` / evidence 可读失败时仍记 ``readback_failed``（诊断）。
正式 ``disarm_result`` 依 v1.7.1 §3.6.1 第 7 条：脚本不据类别输出
``confirmed`` / ``readback_failed``；一律按「类别未验证」规则。
可信组合登记与升级归 U8 共享契约（``ONBOARDED["power-cycle"]["verified_device_classes"]``），
不在本探针包内。
"""
from __future__ import annotations

import sys
import time
from typing import Any

from _lib import (
    StopNotConfirmed,
    adb_shell,
    autotesttool_apk_sha256,
    device_online,
    device_serial,
    enable_exec_progress,
    get_prefs_xml,
    is_root,
    output_result,
    param_or_env,
    params,
    permission_model,
    progress_stamp,
    read_prefs_classified,
    stop_task,
    _root_read_prefs,
)

# 与 device_return 机器值对齐（脚本包不跨包 import 契约模块）
_RESULT_CONFIRMED = "confirmed"  # 保留常量供对照；本脚本正式输出不会使用
_RESULT_EXECUTED_UNVERIFIED = "executed_unverified"
_RESULT_FAILED = "failed"
_RESULT_READBACK_FAILED = "readback_failed"  # 仅作原始 readback 诊断值

_DEFAULT_WAIT_ONLINE = 600
_DEFAULT_MODE = "disarm"

# §3.6.2（v1.7.2）：相邻两次轮询开始间隔 ≥10s；单次轮询 adb 超时之和 ≤5s。
_POLL_PERIOD_S = 10
_STATE_TIMEOUT_S = 2
_BOOT_TIMEOUT_S = 3


def _wait_device_online(
    timeout_s: int,
    *,
    clock=time.monotonic,
    sleeper=time.sleep,
    online_fn=None,
    boot_prop_fn=None,
    progress_fn=None,
) -> bool:
    """轮询 get-state==device 且 boot_completed==1，最长 timeout_s 秒。

    #3723 §3.6.2 / v1.7.2：monotonic 总截止；相邻轮询开始间隔 ≥10s；
    单次轮询 get-state 2s + getprop 3s（之和 ≤5s，并受剩余截止约束）；
    每次真实 device/boot 轮询完成后递增 PROGRESS；相邻戳按真实时间最多 15s。
    sleep 到 min(本轮开始+10s, 截止)。同步轮询无后台线程；超时退出不再打戳。
    迭代上界 ``timeout//10+2`` 仅作假时钟安全网，真实等待不得在截止前结束。
    """
    timeout_s = max(0, int(timeout_s or 0))
    deadline = clock() + timeout_s
    online_fn = online_fn or (lambda t: device_online(timeout=t))
    boot_prop_fn = boot_prop_fn or (
        lambda t: adb_shell("getprop sys.boot_completed", timeout=t).strip()
    )
    progress_fn = progress_fn or progress_stamp
    seq = 0
    # 安全网：真实时钟下每轮至少 10s，截止前到不了这个上界。
    for _ in range(timeout_s // _POLL_PERIOD_S + 2):
        poll_start = clock()
        state_timeout = min(_STATE_TIMEOUT_S, int(deadline - poll_start))
        if state_timeout < 1:  # 剩余不足 1s：不再调 adb，睡到截止
            remaining = deadline - clock()
            if remaining > 0:
                sleeper(remaining)
            return False
        online, booted, err_text = False, "", ""
        try:
            online = bool(online_fn(state_timeout))
            if online:
                boot_timeout = min(_BOOT_TIMEOUT_S, int(deadline - clock()))
                if boot_timeout >= 1:
                    booted = str(boot_prop_fn(boot_timeout) or "").strip()
        except Exception as exc:  # noqa: BLE001 — 诊断入戳，不中断截止
            err_text = str(exc)[:200]
            online, booted = False, ""
        seq += 1
        payload: dict[str, Any] = {
            "phase": "wait_device_online",
            "seq": seq,
            "online": online,
            "boot_completed": booted,
            "remaining_s": max(0, int(deadline - clock())),
        }
        if err_text:
            payload["error"] = err_text
        progress_fn(payload)
        if online and booted == "1":
            return True
        delay = min(poll_start + _POLL_PERIOD_S, deadline) - clock()
        if delay > 0:
            sleeper(delay)
    return False


def _detect_platform() -> str:
    """现场探测 device.platform（MTK / UNISOC / QCOM / UNKNOWN 词表）。"""
    soc = adb_shell("getprop ro.soc.manufacturer", timeout=15).strip().lower()
    if "mediatek" in soc or soc == "mtk":
        return "MTK"
    if "unisoc" in soc or "spreadtrum" in soc:
        return "UNISOC"
    if "qualcomm" in soc or soc == "qti":
        return "QCOM"
    board = adb_shell("getprop ro.board.platform", timeout=15).strip().lower()
    if board.startswith("mt"):
        return "MTK"
    if board.startswith(("ums", "sc", "sp")):
        return "UNISOC"
    if board.startswith(("msm", "sdm", "sm", "qcom")):
        return "QCOM"
    return "UNKNOWN"


def _detect_model() -> str:
    return adb_shell("getprop ro.product.model", timeout=15).strip()


def _resolve_category(cfg: dict, *, can_detect: bool) -> tuple[str, str]:
    """解析 platform/model；离线时仅用 cfg 覆盖。不做已验证类别判定（§3.6.1.7）。"""
    platform = str(cfg.get("device_platform") or "").strip() or None
    model = str(cfg.get("device_model") or "").strip() or None
    if can_detect:
        if platform is None:
            platform = _detect_platform()
        if model is None:
            model = _detect_model()
    return platform or "UNKNOWN", model or ""


def _read_prefs_evidence() -> dict[str, Any]:
    """读取 prefs 并按 §3.6.6 第 2、3 条给出原始 readback 与 evidence。

    kind: ok / not_installed / absent / denied / empty / transient。
    readback: footprint / clear / absent / not_installed / readback_failed。
    """
    kind, xml, installed = read_prefs_classified()
    evidence: dict[str, Any] = {
        "root": bool(is_root()),
        "kind": kind,
        "autotesttool_installed": installed,
        "prefs_len": len(xml or ""),
    }
    if kind == "not_installed":
        return {
            "readback": "not_installed",
            "running": None,
            "auto_resume": None,
            "evidence": evidence,
        }
    if kind == "absent":
        return {
            "readback": "absent",
            "running": None,
            "auto_resume": None,
            "evidence": evidence,
        }
    if kind != "ok":
        # 被拒 / 未知 / 超时 / 空文件：原始读取失败，不据此判无足迹
        return {
            "readback": "readback_failed",
            "running": None,
            "auto_resume": None,
            "evidence": evidence,
        }

    running_true = 'name="running" value="true"' in xml
    running_false = 'name="running" value="false"' in xml
    auto_true = 'name="auto_resume" value="true"' in xml
    evidence["running_token"] = (
        "true" if running_true else ("false" if running_false else "absent")
    )
    evidence["auto_resume_token"] = "true" if auto_true else "other"

    if running_true or auto_true:
        return {
            "readback": "footprint",
            "running": running_true,
            "auto_resume": auto_true,
            "evidence": evidence,
        }
    if running_false:
        return {
            "readback": "clear",
            "running": False,
            "auto_resume": auto_true,
            "evidence": evidence,
        }
    # 可读但缺少 running 字段——不当作无足迹
    return {
        "readback": "readback_failed",
        "running": None,
        "auto_resume": auto_true,
        "evidence": evidence,
    }


def _map_result(
    readback: str,
    *,
    mode: str,
    action_completed: bool,
) -> str:
    """原始 readback → 正式六值（#3723 §3.6.6 第 3、4、5 条）。

    - clear / absent / not_installed + disarm 已完成 → executed_unverified；
    - footprint → failed；
    - readback_failed（被拒/未知/超时/缺 running）→ failed，即使 disarm
      已经停过服务也不得升级为 executed_unverified（§3.6.6 第 5 条）。
    - probe 一律不得通过（可信组合待 U8 登记）。
    """
    if readback in ("clear", "absent", "not_installed"):
        if mode == "disarm" and action_completed:
            return _RESULT_EXECUTED_UNVERIFIED
        return _RESULT_FAILED
    return _RESULT_FAILED


def _device_identity_evidence() -> dict[str, Any]:
    """权限模型 + AutoTestTool APK SHA（§3.6.1.7）；失败时保留空/None。"""
    try:
        model = permission_model()
    except Exception:  # noqa: BLE001
        model = "unknown"
    try:
        apk_sha = autotesttool_apk_sha256(timeout=30)
    except Exception:  # noqa: BLE001
        apk_sha = None
    return {
        "permission_model": model,
        "autotesttool_apk_sha256": apk_sha,
    }


def _early_unreadable(
    *,
    mode: str,
    serial: str,
    summary: str,
    evidence: dict[str, Any],
    platform: str,
    model: str,
) -> dict:
    """离线 / 等待失败 / 开机未完成：动作未完成；不得再 adb 取证（§3.6.1.7）。"""
    return {
        "success": False,
        "disarm_result": _RESULT_FAILED,
        "readback": "readback_failed",
        "readback_summary": summary,
        "evidence": {
            **evidence,
            "platform": platform,
            "model": model,
            "verified_category": False,
            "permission_model": "unknown",
            "autotesttool_installed": None,
            "autotesttool_apk_sha256": None,
        },
        "mode": mode,
    }


def _run(cfg: dict) -> dict:
    mode = str(
        param_or_env(cfg, "mode", "STP_POWER_CYCLE_DISARM_MODE", _DEFAULT_MODE)
    ).strip().lower()
    if mode not in ("probe", "disarm"):
        raise RuntimeError(f"mode 必须是 probe 或 disarm，收到: {mode!r}")

    wait_online = int(
        str(
            param_or_env(
                cfg,
                "wait_device_online_seconds",
                "STP_POWER_CYCLE_WAIT_ONLINE_SECONDS",
                _DEFAULT_WAIT_ONLINE,
            )
        )
        or _DEFAULT_WAIT_ONLINE
    )

    serial = device_serial()
    progress_stamp({"phase": "powercycle_disarm", "mode": mode, "serial": serial})

    boot_completed: str | None = None
    stop_error = ""
    stop_steps: dict[str, Any] | None = None
    action_completed = False
    if mode == "disarm":
        if not _wait_device_online(wait_online):
            platform, model = _resolve_category(cfg, can_detect=False)
            return _early_unreadable(
                mode=mode,
                serial=serial,
                summary=f"设备 {serial} 在 {wait_online}s 内未上线，无法解除/回读",
                evidence={"wait_device_online_seconds": wait_online, "online": False},
                platform=platform,
                model=model,
            )
        # 等待成功即 boot_completed==1；开启上线后打戳
        boot_completed = "1"
        enable_exec_progress()
        force = (
            str(param_or_env(cfg, "force_stop", "STP_POWER_CYCLE_FORCE_STOP", "true"))
            .lower()
            == "true"
        )
        # 先只读分类，再决定是否需要停服务/写标志（§3.6.6 第 4、5 条）。
        pre = _read_prefs_evidence()
        pre_evidence = pre.get("evidence") or {}
        kind = str(pre_evidence.get("kind") or "")
        installed = pre_evidence.get("autotesttool_installed")
        if installed is None and kind == "ok":
            # 兼容旧测试桩；真实读取路径总会给出 true/false/null。
            installed = True
        if kind == "not_installed" or installed is False:
            # 没有可停止的服务和可写的 prefs：不写设备，视为解除完成。
            action_completed = True
        else:
            try:
                stop_steps = stop_task(force=force, kind=kind, installed=installed)
                action_completed = True
            except StopNotConfirmed as exc:
                # §3.6.6 第 1 条 v1.7.8：查询/STOP/force-stop 任一失败即未确认
                # 停止；本分支发生在任何写 prefs 之前，evidence 保留各步返回码。
                stop_steps = dict(exc.steps)
                progress_stamp({"phase": "stop_task_error", "error": str(exc)[:200]})
                stop_error = str(exc)
            except Exception as exc:  # noqa: BLE001
                # 解除动作失败（读取被拒 / 无法确认安装）仍尝试回读，
                # 但不得把未完成动作报成已执行。
                progress_stamp({"phase": "stop_task_error", "error": str(exc)[:200]})
                stop_error = str(exc)
            time.sleep(1)
    else:
        # probe：在线检查 + boot_completed（§3.6.1.7 v1.7.4）
        if not device_online():
            platform, model = _resolve_category(cfg, can_detect=False)
            return _early_unreadable(
                mode=mode,
                serial=serial,
                summary=f"设备 {serial} 离线，探针无法回读",
                evidence={"online": False},
                platform=platform,
                model=model,
            )
        boot_completed = adb_shell("getprop sys.boot_completed", timeout=3).strip()
        if boot_completed != "1":
            platform, model = _resolve_category(cfg, can_detect=False)
            return _early_unreadable(
                mode=mode,
                serial=serial,
                summary=f"设备 {serial} 开机未完成（boot_completed={boot_completed!r}），探针不读 prefs",
                evidence={"online": True, "boot_completed": boot_completed},
                platform=platform,
                model=model,
            )
        enable_exec_progress()
        stop_error = ""

    platform, model = _resolve_category(cfg, can_detect=True)
    identity = _device_identity_evidence()

    snap = _read_prefs_evidence()
    readback = str(snap["readback"])

    if stop_error:
        # 服务未停 / 读取被拒 / 无法确认安装：保留失败事实。
        result = _RESULT_FAILED
    else:
        result = _map_result(
            readback,
            mode=mode,
            action_completed=action_completed,
        )

    # §3.6.1.7：任何输入下正式值不得为 confirmed / readback_failed
    if result in (_RESULT_CONFIRMED, _RESULT_READBACK_FAILED):
        result = _RESULT_FAILED

    summary_parts = [
        f"mode={mode}",
        f"platform={platform}",
        f"model={model}",
        f"permission_model={identity.get('permission_model')}",
        f"readback={readback}",
    ]
    if stop_error:
        summary_parts.append(f"stop_error={stop_error[:120]}")
    summary = "; ".join(summary_parts)

    success = result == _RESULT_EXECUTED_UNVERIFIED
    return {
        "success": success,
        "disarm_result": result,
        "readback": readback,
        "readback_summary": summary,
        "evidence": {
            **(snap.get("evidence") or {}),
            "platform": platform,
            "model": model,
            "verified_category": False,
            "running": snap.get("running"),
            "auto_resume": snap.get("auto_resume"),
            "stop_error": stop_error or None,
            "stop_steps": stop_steps,
            "boot_completed": boot_completed,
            **identity,
        },
        "mode": mode,
    }


def main() -> None:
    cfg = params()
    try:
        result = _run(cfg)
    except Exception as exc:  # noqa: BLE001
        output_result(
            False,
            disarm_result=_RESULT_FAILED,
            readback="readback_failed",
            readback_summary=str(exc),
            error_message=str(exc),
            mode=str((cfg or {}).get("mode") or _DEFAULT_MODE),
        )
        sys.exit(1)
    output_result(bool(result.get("success")), **{k: v for k, v in result.items() if k != "success"})
    if not result.get("success"):
        sys.exit(1)


if __name__ == "__main__":
    main()
