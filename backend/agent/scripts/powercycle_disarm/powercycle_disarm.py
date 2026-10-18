# -*- coding: utf-8 -*-
"""开关机解除 / 探针（#3730 / #3723 §3.6 / ADR-0061 D2）。

v1.0.4：§3.6.1 第 7 条 —— 脚本不内置已验证类别清单，正式值一律按未验证规则；
evidence 增加权限模型与 AutoTestTool APK SHA-256。§3.6.2 —— 进度戳 sleep
从上一次周期原点起算，单次轮询 adb 超时受 15s 周期与剩余截止约束。
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
from typing import Any, Callable

from _lib import (
    adb_shell,
    autotesttool_apk_sha256,
    device_online,
    device_serial,
    get_prefs_xml,
    is_root,
    output_result,
    param_or_env,
    params,
    permission_model,
    progress_stamp,
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

# §3.6.2：同步轮询打戳；相邻 PROGRESS 最多 15s；不扩 660s 外墙。
_WAIT_POLL_INTERVAL_S = 15
_WAIT_MAX_ITERATIONS = 10_000  # 假时钟测试独立迭代上界


def _call_online(online_fn: Callable[..., bool], timeout: int) -> bool:
    """兼容 ``online_fn(timeout)`` 与旧式无参替身。"""
    try:
        return bool(online_fn(timeout))
    except TypeError:
        return bool(online_fn())


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

    #3723 §3.6.2 / v1.7.1：monotonic 总截止；每次真实 device/boot 轮询完成后
    递增 PROGRESS；相邻戳按真实时间最多 15s（含 adb 耗时）。
    单次轮询全部 adb（含 get-state）超时之和受「距上一次打戳的 15s 窗口」与
    剩余截止约束。sleep 从上一次打戳起算，并预留下一轮完整 15s adb 预算
    （因此可 sleep 量为 0），避免「先睡满 15s 再跑慢 get-state」撑破间隔。
    同步轮询无后台线程；超时退出不再打戳。
    """
    timeout_s = max(0, int(timeout_s or 0))
    deadline = clock() + timeout_s
    online_fn = online_fn or (lambda t: device_online(timeout=t))
    boot_prop_fn = boot_prop_fn or (
        lambda t: adb_shell("getprop sys.boot_completed", timeout=t).strip()
    )
    progress_fn = progress_fn or progress_stamp
    seq = 0
    last_stamp_at: float | None = None

    for _ in range(_WAIT_MAX_ITERATIONS):
        now = clock()
        remaining = deadline - now
        if remaining <= 0:
            return False

        if last_stamp_at is None:
            window_left = min(float(_WAIT_POLL_INTERVAL_S), remaining)
            window_origin = now
        else:
            window_left = min(
                _WAIT_POLL_INTERVAL_S - (now - last_stamp_at),
                remaining,
            )
            window_origin = last_stamp_at

        if window_left < 1:
            return False

        online = False
        booted = ""
        err_text = ""
        try:
            budget_int = max(1, int(window_left))
            if budget_int >= 2:
                state_timeout = budget_int - 1
                boot_timeout_cap = budget_int - state_timeout
            else:
                state_timeout = 1
                boot_timeout_cap = 0

            online = _call_online(online_fn, state_timeout)
            if online and boot_timeout_cap >= 1:
                wall_left = min(
                    _WAIT_POLL_INTERVAL_S - (clock() - window_origin)
                    if last_stamp_at is not None
                    else (window_left - (clock() - now)),
                    deadline - clock(),
                )
                boot_timeout = min(boot_timeout_cap, max(0, int(wall_left)))
                if boot_timeout >= 1:
                    booted = str(boot_prop_fn(boot_timeout) or "").strip()
        except Exception as exc:  # noqa: BLE001 — 轮询诊断入戳，不中断截止
            err_text = str(exc)[:200]
            online = False
            booted = ""

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
        last_stamp_at = clock()

        if online and booted == "1":
            return True

        if deadline - clock() <= 0:
            return False
        # 预留下一轮完整 15s adb 预算 ⇒ 可 sleep 量 = 0（仍调用 sleeper 便于测试）
        sleeper(0)
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
    """root 优先回读 prefs，返回 evidence + 归一化 readback 状态。

    readback（原始诊断，可与正式六值分离）:
      - clear: 可读且 running=false（无足迹）
      - footprint: 可读且 running=true 或 auto_resume=true
      - readback_failed: 读不到 / kind 非 ok
    """
    evidence: dict[str, Any] = {"root": bool(is_root())}
    if is_root():
        kind, xml = _root_read_prefs()
        evidence["kind"] = kind
        evidence["prefs_len"] = len(xml or "")
        if kind != "ok":
            return {
                "readback": "readback_failed",
                "running": None,
                "auto_resume": None,
                "evidence": evidence,
            }
    else:
        xml = get_prefs_xml()
        evidence["kind"] = "ok" if xml else "non-root-empty"
        evidence["prefs_len"] = len(xml or "")
        if not xml:
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
    """原始 readback → 正式六值（v1.7.1 §3.6.1 第 7 条：一律未验证规则）。

    - footprint → failed
    - clear + disarm 已完成 → executed_unverified（不得 confirmed）
    - clear + probe / 动作未完成 → failed
    - 原始 readback_failed：
        * disarm 已完成 → executed_unverified（保留原始诊断；不得正式 readback_failed）
        * 动作未完成 / probe → failed
    """
    if readback == "footprint":
        return _RESULT_FAILED
    if readback == "clear":
        if mode == "disarm" and action_completed:
            return _RESULT_EXECUTED_UNVERIFIED
        return _RESULT_FAILED
    # readback == "readback_failed"（及其它不可信）
    if mode == "disarm" and action_completed:
        return _RESULT_EXECUTED_UNVERIFIED
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
    """离线 / 等待失败等：动作未完成，正式结果不得借未验证规则报成已执行。"""
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
            **_device_identity_evidence(),
        },
        "mode": mode,
    }


def _run(cfg: dict) -> dict:
    mode = str(param_or_env(cfg, "mode", "STP_POWER_CYCLE_DISARM_MODE", _DEFAULT_MODE)).strip().lower()
    if mode not in ("probe", "disarm"):
        raise RuntimeError(f"mode 必须是 probe 或 disarm，收到: {mode!r}")

    wait_online = int(str(param_or_env(
        cfg, "wait_device_online_seconds", "STP_POWER_CYCLE_WAIT_ONLINE_SECONDS",
        _DEFAULT_WAIT_ONLINE,
    )) or _DEFAULT_WAIT_ONLINE)

    serial = device_serial()
    progress_stamp({"phase": "powercycle_disarm", "mode": mode, "serial": serial})

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
        force = str(param_or_env(cfg, "force_stop", "STP_POWER_CYCLE_FORCE_STOP", "true")).lower() == "true"
        try:
            stop_task(force=force)
        except Exception as exc:  # noqa: BLE001
            # 解除动作失败仍尝试回读；未取得 clear 时保留失败事实（不得冒称已执行）
            progress_stamp({"phase": "stop_task_error", "error": str(exc)[:200]})
            stop_error = str(exc)
        else:
            stop_error = ""
        time.sleep(1)
    else:
        # probe：设备需可达才能读
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
        stop_error = ""

    platform, model = _resolve_category(cfg, can_detect=True)
    identity = _device_identity_evidence()

    snap = _read_prefs_evidence()
    readback = str(snap["readback"])

    if stop_error and readback == "clear":
        # 停服务报错但回读已 clear：仍以回读为准（解除幂等）
        result = _map_result(
            "clear", mode=mode, action_completed=True,
        )
    elif stop_error:
        # 动作失败且未取得 clear：依 D1 保留失败事实
        result = _RESULT_FAILED
    else:
        result = _map_result(
            readback,
            mode=mode,
            action_completed=(mode == "disarm"),
        )

    # §3.6.1.7：任何输入下正式值不得为 confirmed / readback_failed
    if result in (_RESULT_CONFIRMED, _RESULT_READBACK_FAILED):
        result = (
            _RESULT_EXECUTED_UNVERIFIED
            if mode == "disarm" and not stop_error
            else _RESULT_FAILED
        )

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
