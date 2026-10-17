# -*- coding: utf-8 -*-
"""开关机解除 / 探针（#3730 / #3723 §3.6 / ADR-0061 D2）。

v1.0.2：§3.6.1.3 —— 未验证 **probe**（含原始 clear）不得 ``success=true`` /
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
正式 ``disarm_result`` 依 v1.6 §3.6.1 / D2.5：已验证类别才可记正式
``readback_failed`` / ``confirmed``；未验证且 **disarm 已完成**（无可信读数或
原始 clear）→ ``executed_unverified``；动作失败/未完成不得冒称已执行；
未验证 **probe**（含原始 clear）→ ``failed`` / ``success=false``，不得当可信无足迹通过。
已验证设备类别见共享契约 ``ONBOARDED["power-cycle"]["verified_device_classes"]``。
"""
from __future__ import annotations

import sys
import time
from typing import Any

from _lib import (
    adb_shell,
    device_online,
    device_serial,
    get_prefs_xml,
    is_root,
    output_result,
    param_or_env,
    params,
    progress_stamp,
    stop_task,
    _root_read_prefs,
)

# 与 device_return 机器值对齐（脚本包不跨包 import 契约模块）
_RESULT_CONFIRMED = "confirmed"
_RESULT_EXECUTED_UNVERIFIED = "executed_unverified"
_RESULT_FAILED = "failed"
_RESULT_READBACK_FAILED = "readback_failed"

_DEFAULT_WAIT_ONLINE = 600
_DEFAULT_MODE = "disarm"

# 已验证设备类别（platform + model）。必须与
# ``device_return.ONBOARDED["power-cycle"]["verified_device_classes"]`` 保持一致。
# 脚本包在 tools_cache 隔离执行，不能运行时 import 契约模块。
# 本批真机验证 pending → 空清单；不得虚填（D2.5 / v1.6 §3.6.1）。
_VERIFIED_DEVICE_CLASSES: tuple[dict[str, str], ...] = ()


def _load_verified_classes() -> list[dict[str, str]]:
    return [dict(x) for x in _VERIFIED_DEVICE_CLASSES]


def _wait_device_online(timeout_s: int) -> bool:
    """轮询 get-state==device 且 boot_completed==1，最长 timeout_s 秒。"""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if device_online():
            booted = adb_shell("getprop sys.boot_completed", timeout=15).strip()
            if booted == "1":
                return True
        time.sleep(10)
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


def _category_verified(platform: str, model: str) -> bool:
    want_p = (platform or "").strip().upper()
    want_m = (model or "").strip()
    for item in _load_verified_classes():
        if item["platform"] == want_p and item["model"] == want_m:
            return True
    return False


def _resolve_category(cfg: dict, *, can_detect: bool) -> tuple[str, str, bool]:
    """解析 platform/model 与是否已验证；离线时仅用 cfg 覆盖，缺省视为未验证。"""
    platform = str(cfg.get("device_platform") or "").strip() or None
    model = str(cfg.get("device_model") or "").strip() or None
    if can_detect:
        if platform is None:
            platform = _detect_platform()
        if model is None:
            model = _detect_model()
    platform = platform or "UNKNOWN"
    model = model or ""
    return platform, model, _category_verified(platform, model)


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
    verified: bool,
    mode: str,
    action_completed: bool,
) -> str:
    """原始 readback → 正式六值（v1.6 §3.6.1 / ADR-0061 D2.5）。

    - footprint → failed
    - clear + 已验证 → confirmed
    - clear + 未验证 + disarm 已完成 → executed_unverified（不得 confirmed）
    - clear + 未验证 + probe / 动作未完成 → failed（§3.6.1.3 不通过）
    - 原始 readback_failed：
        * 已验证 → 正式 readback_failed
        * 未验证 + disarm 已完成 → executed_unverified（保留原始诊断）
        * 未验证 + 动作未完成 / probe → failed（不得冒称已执行或可信回读失败）
    """
    if readback == "footprint":
        return _RESULT_FAILED
    if readback == "clear":
        if verified:
            return _RESULT_CONFIRMED
        # §3.6.1.3：未验证 clear 仅在 disarm 已完成时报 executed_unverified；
        # probe 不得把 clear 当可信无足迹通过。
        if mode == "disarm" and action_completed:
            return _RESULT_EXECUTED_UNVERIFIED
        return _RESULT_FAILED
    # readback == "readback_failed"（及其它不可信）
    if verified:
        return _RESULT_READBACK_FAILED
    if mode == "disarm" and action_completed:
        return _RESULT_EXECUTED_UNVERIFIED
    return _RESULT_FAILED


def _early_unreadable(
    *,
    mode: str,
    serial: str,
    summary: str,
    evidence: dict[str, Any],
    verified: bool,
    platform: str,
    model: str,
) -> dict:
    """离线 / 等待失败等：动作未完成，正式结果不得借未验证规则报成已执行。"""
    formal = _RESULT_READBACK_FAILED if verified else _RESULT_FAILED
    return {
        "success": False,
        "disarm_result": formal,
        "readback": "readback_failed",
        "readback_summary": summary,
        "evidence": {
            **evidence,
            "platform": platform,
            "model": model,
            "verified_category": verified,
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
            platform, model, verified = _resolve_category(cfg, can_detect=False)
            return _early_unreadable(
                mode=mode,
                serial=serial,
                summary=f"设备 {serial} 在 {wait_online}s 内未上线，无法解除/回读",
                evidence={"wait_device_online_seconds": wait_online, "online": False},
                verified=verified,
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
            platform, model, verified = _resolve_category(cfg, can_detect=False)
            return _early_unreadable(
                mode=mode,
                serial=serial,
                summary=f"设备 {serial} 离线，探针无法回读",
                evidence={"online": False},
                verified=verified,
                platform=platform,
                model=model,
            )
        stop_error = ""

    platform, model, verified = _resolve_category(cfg, can_detect=True)

    snap = _read_prefs_evidence()
    readback = str(snap["readback"])

    if stop_error and readback == "clear":
        # 停服务报错但回读已 clear：仍以回读为准（解除幂等）
        result = _map_result(
            "clear", verified=verified, mode=mode, action_completed=True,
        )
    elif stop_error:
        # 动作失败且未取得 clear：依 D1 保留失败事实
        result = _RESULT_FAILED
    else:
        result = _map_result(
            readback,
            verified=verified,
            mode=mode,
            action_completed=(mode == "disarm"),
        )

    summary_parts = [
        f"mode={mode}",
        f"platform={platform}",
        f"model={model}",
        f"verified={verified}",
        f"readback={readback}",
    ]
    if stop_error:
        summary_parts.append(f"stop_error={stop_error[:120]}")
    summary = "; ".join(summary_parts)

    success = result in (_RESULT_CONFIRMED, _RESULT_EXECUTED_UNVERIFIED)
    return {
        "success": success,
        "disarm_result": result,
        "readback": readback,
        "readback_summary": summary,
        "evidence": {
            **(snap.get("evidence") or {}),
            "platform": platform,
            "model": model,
            "verified_category": verified,
            "running": snap.get("running"),
            "auto_resume": snap.get("auto_resume"),
            "stop_error": stop_error or None,
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
