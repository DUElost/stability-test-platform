# -*- coding: utf-8 -*-
"""开关机解除 / 探针（#3730 / #3723 §3.6 / ADR-0061 D2）。

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

读不到时必须为 ``readback_failed``，不得报无足迹（D1.6 / §3.6.1）。
已验证设备类别见共享契约 ``ONBOARDED["power-cycle"]["verified_device_classes"]``；
未在清单内的类别即使回读 clear 也只能报 ``executed_unverified``。
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
# 本批真机验证 pending → 空清单；clear 回读只能报 executed_unverified（D2.5）。
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


def _read_prefs_evidence() -> dict[str, Any]:
    """root 优先回读 prefs，返回 evidence + 归一化 readback 状态。

    readback:
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


def _map_result(readback: str, *, verified: bool) -> str:
    if readback == "readback_failed":
        return _RESULT_READBACK_FAILED
    if readback == "footprint":
        return _RESULT_FAILED
    # clear
    if verified:
        return _RESULT_CONFIRMED
    return _RESULT_EXECUTED_UNVERIFIED


def _run(cfg: dict) -> dict:
    mode = str(param_or_env(cfg, "mode", "STP_POWER_CYCLE_DISARM_MODE", _DEFAULT_MODE)).strip().lower()
    if mode not in ("probe", "disarm"):
        raise RuntimeError(f"mode 必须是 probe 或 disarm，收到: {mode!r}")

    wait_online = int(str(param_or_env(
        cfg, "wait_device_online_seconds", "STP_POWER_CYCLE_WAIT_ONLINE_SECONDS",
        _DEFAULT_WAIT_ONLINE,
    )) or _DEFAULT_WAIT_ONLINE)

    platform = str(cfg.get("device_platform") or "").strip() or None
    model = str(cfg.get("device_model") or "").strip() or None

    serial = device_serial()
    progress_stamp({"phase": "powercycle_disarm", "mode": mode, "serial": serial})

    if mode == "disarm":
        if not _wait_device_online(wait_online):
            # 等不到设备 = 本次无法回读，也未证明无足迹
            return {
                "success": False,
                "disarm_result": _RESULT_READBACK_FAILED,
                "readback": "readback_failed",
                "readback_summary": f"设备 {serial} 在 {wait_online}s 内未上线，无法解除/回读",
                "evidence": {"wait_device_online_seconds": wait_online, "online": False},
                "mode": mode,
            }
        force = str(param_or_env(cfg, "force_stop", "STP_POWER_CYCLE_FORCE_STOP", "true")).lower() == "true"
        try:
            stop_task(force=force)
        except Exception as exc:  # noqa: BLE001
            # 解除动作失败仍尝试回读，由回读决定最终六值；读不到则 readback_failed
            progress_stamp({"phase": "stop_task_error", "error": str(exc)[:200]})
            stop_error = str(exc)
        else:
            stop_error = ""
        time.sleep(1)
    else:
        # probe：设备需可达才能读；不可达 → readback_failed
        if not device_online():
            return {
                "success": False,
                "disarm_result": _RESULT_READBACK_FAILED,
                "readback": "readback_failed",
                "readback_summary": f"设备 {serial} 离线，探针无法回读",
                "evidence": {"online": False},
                "mode": mode,
            }
        stop_error = ""

    if platform is None:
        platform = _detect_platform()
    if model is None:
        model = _detect_model()
    verified = _category_verified(platform, model)

    snap = _read_prefs_evidence()
    readback = str(snap["readback"])
    result = _map_result(readback, verified=verified)
    if stop_error and readback == "clear":
        # 停服务报错但回读已 clear：仍以回读为准（解除幂等）
        pass
    elif stop_error and readback == "footprint":
        result = _RESULT_FAILED
    elif stop_error and readback == "readback_failed":
        result = _RESULT_READBACK_FAILED

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
