# -*- coding: utf-8 -*-
"""FacAging (com.ape.aging) Aging DDR 专项共享库。

提供纯 ADB 协议下的 Jetpack DataStore (``PreferencesProto``) 编解码、
单调用同源 DataStore 读取（base64）、安全写入与设备预检辅助函数。
"""
from __future__ import annotations

import base64
import binascii
import json
import os
import re
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

PACKAGE = "com.ape.aging"
DATASTORE_DIR = f"/data/user/0/{PACKAGE}/files/datastore"
APP_FILES_DIR = f"/data/user/0/{PACKAGE}/files"
WORKSPACE_DDR_DIR = f"/storage/emulated/0/Android/data/{PACKAGE}/files/aging_workspace/ddr"
EXPORTS_DIR = f"/storage/emulated/0/Android/data/{PACKAGE}/files/exports"
BOOT_RECEIVER = f"{PACKAGE}/.boot.UnifiedBootReceiver"
DDR_SERVICE = f"{PACKAGE}/.stage.ddr.service.DdrTestService"
DDR_STOP_ACTION = f"{PACKAGE}.ddr.action.STOP"
WAKE_ACTION = f"{PACKAGE}.WAKE"
EXPECTED_BOOT_REASON = "reboot,aging_ddr_reboot_test"

# FacAging 1.0.3.3 assets/aging/ddr_params.conf 默认参数
DDR_PARAM_DEFAULTS: dict[str, str] = {
    "enabled_items": "REBOOT_DST,DDR_FLAG_CHECK,REBOOT,DB_HISTORY",
    "reboot_dst_times": "1",
    "aging_ddr_flag_times": "1",
    "verification_ddr_flag_times": "1",
    "aging_reboot_times": "10",
    "verification_reboot_times": "1",
    "reboot_interval_sec": "5",
    "aging_check_duration_sec": "10",
    "verification_check_duration_sec": "10",
    "aging_db_times": "1",
    "verification_db_times": "1",
    "aging_module_pass_count": "7",
    "verification_module_pass_count": "1",
}

VALID_DDR_ITEMS = frozenset({"REBOOT_DST", "DDR_FLAG_CHECK", "REBOOT", "DB_HISTORY"})
VALID_MODES = frozenset({"AGING", "VERIFICATION"})

_ABSENT_SENTINEL = "__STP_AGING_DS_ABSENT__"


# ---------------------------------------------------------------------------
# 环境 / 参数 / 输出契约
# ---------------------------------------------------------------------------

def env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def param_or_env(cfg: dict, key: str, env_key: str, default: Any) -> Any:
    value = cfg.get(key)
    if value is not None and str(value) != "":
        return value
    raw = env(env_key, "")
    if raw != "":
        return raw
    return default


def adb_path() -> str:
    return env("STP_ADB_PATH", "adb") or "adb"


def device_serial() -> str:
    serial = env("STP_DEVICE_SERIAL", "")
    if not serial:
        print(
            json.dumps(
                {"success": False, "error_message": "STP_DEVICE_SERIAL is not set"},
                ensure_ascii=False,
            )
        )
        sys.exit(1)
    return serial


def params() -> dict:
    raw = env("STP_STEP_PARAMS", "{}")
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def output_result(success: bool, **kwargs: Any) -> None:
    print(json.dumps({"success": success, **kwargs}, ensure_ascii=False))


def progress_stamp(payload: dict) -> None:
    """往 stderr 输出 PROGRESS 戳，供引擎停滞钟与诊断日志识别。"""
    sys.stderr.write(f"PROGRESS {json.dumps(payload, ensure_ascii=False)}\n")
    sys.stderr.flush()


# ---------------------------------------------------------------------------
# ADB 封装（宽容 UTF-8 解码，避免设备侧脏字节触发 UnicodeDecodeError）
# ---------------------------------------------------------------------------

def decode_device_output(raw: bytes | None) -> str:
    return (raw or b"").decode("utf-8", errors="replace")


def adb(*args: str, timeout: int = 60) -> tuple[int, str, str]:
    cmd = [adb_path(), "-s", device_serial()] + list(args)
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return -1, "", "timeout"
    except OSError as exc:
        return -1, "", str(exc)
    return (
        result.returncode,
        decode_device_output(result.stdout),
        decode_device_output(result.stderr),
    )


def adb_shell(command: str, timeout: int = 60) -> str:
    _, out, _ = adb("shell", command, timeout=timeout)
    return out


def device_online(timeout: int = 15) -> bool:
    rc, out, _ = adb("get-state", timeout=timeout)
    return rc == 0 and out.strip() == "device"


def is_root() -> bool:
    return adb_shell("id -u", timeout=15).strip() == "0"


def ensure_adb_root() -> bool:
    if is_root():
        return True
    adb("root", timeout=15)
    for _ in range(5):
        time.sleep(1.0)
        if device_online(timeout=10) and is_root():
            return True
    return False


# ---------------------------------------------------------------------------
# Jetpack DataStore PreferencesProto 编解码（纯标准库）
# ---------------------------------------------------------------------------

def _enc_varint(n: int) -> bytes:
    if n < 0:
        n &= (1 << 64) - 1
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _dec_varint(buf: bytes, pos: int) -> tuple[int, int]:
    shift = 0
    val = 0
    n = len(buf)
    while pos < n:
        b = buf[pos]
        pos += 1
        val |= (b & 0x7F) << shift
        if not (b & 0x80):
            return val, pos
        shift += 7
        if shift > 70:
            raise ValueError("varint overflow")
    raise ValueError("truncated varint")


def _enc_bytes(field_no: int, payload: bytes) -> bytes:
    return _enc_varint((field_no << 3) | 2) + _enc_varint(len(payload)) + payload


def encode_prefs(mapping: dict[str, Any], *, int32_keys: frozenset[str] = frozenset()) -> bytes:
    """将键值字典编码为 androidx.datastore.preferences.PreferencesProto 字节流。"""
    out = bytearray()
    for k, v in mapping.items():
        kb = str(k).encode("utf-8")
        if isinstance(v, bool):
            vmsg = bytes([0x08, 1 if v else 0])
        elif isinstance(v, int):
            tag = 0x18 if k in int32_keys else 0x20
            vmsg = bytes([tag]) + _enc_varint(v)
        elif isinstance(v, float):
            vmsg = bytes([0x39]) + struct.pack("<d", v)
        elif isinstance(v, str):
            vmsg = _enc_bytes(5, v.encode("utf-8"))
        elif isinstance(v, (bytes, bytearray)):
            vmsg = _enc_bytes(8, bytes(v))
        else:
            raise TypeError(f"unsupported datastore value type for {k!r}: {type(v).__name__}")
        out += _enc_bytes(1, _enc_bytes(1, kb) + _enc_bytes(2, vmsg))
    return bytes(out)


def _skip_field(buf: bytes, pos: int, wire_type: int) -> int:
    if wire_type == 0:
        _, pos = _dec_varint(buf, pos)
        return pos
    if wire_type == 1:
        if pos + 8 > len(buf):
            raise ValueError("truncated 64-bit field")
        return pos + 8
    if wire_type == 2:
        length, pos = _dec_varint(buf, pos)
        if pos + length > len(buf):
            raise ValueError("truncated length-delimited field")
        return pos + length
    if wire_type == 5:
        if pos + 4 > len(buf):
            raise ValueError("truncated 32-bit field")
        return pos + 4
    raise ValueError(f"unsupported protobuf wire type: {wire_type}")


def _decode_value(buf: bytes) -> Any:
    pos = 0
    end = len(buf)
    val: Any = None
    while pos < end:
        tag, pos = _dec_varint(buf, pos)
        field_no = tag >> 3
        wire_type = tag & 0x07
        if field_no == 1 and wire_type == 0:
            raw, pos = _dec_varint(buf, pos)
            val = bool(raw)
        elif field_no == 2 and wire_type == 5:
            if pos + 4 > end:
                raise ValueError("truncated float")
            val = struct.unpack("<f", buf[pos : pos + 4])[0]
            pos += 4
        elif field_no == 3 and wire_type == 0:
            raw, pos = _dec_varint(buf, pos)
            val = raw - (1 << 32) if raw >= (1 << 31) else raw
        elif field_no == 4 and wire_type == 0:
            raw, pos = _dec_varint(buf, pos)
            val = raw - (1 << 64) if raw >= (1 << 63) else raw
        elif field_no == 5 and wire_type == 2:
            length, pos = _dec_varint(buf, pos)
            if pos + length > end:
                raise ValueError("truncated string")
            val = buf[pos : pos + length].decode("utf-8", errors="replace")
            pos += length
        elif field_no == 6 and wire_type == 2:
            length, pos = _dec_varint(buf, pos)
            if pos + length > end:
                raise ValueError("truncated string_set")
            sub = buf[pos : pos + length]
            pos += length
            items: list[str] = []
            spos = 0
            while spos < len(sub):
                stag, spos = _dec_varint(sub, spos)
                sfn, swt = stag >> 3, stag & 0x07
                if sfn == 1 and swt == 2:
                    slen, spos = _dec_varint(sub, spos)
                    items.append(sub[spos : spos + slen].decode("utf-8", errors="replace"))
                    spos += slen
                else:
                    spos = _skip_field(sub, spos, swt)
            val = items
        elif field_no == 7 and wire_type == 1:
            if pos + 8 > end:
                raise ValueError("truncated double")
            val = struct.unpack("<d", buf[pos : pos + 8])[0]
            pos += 8
        elif field_no == 8 and wire_type == 2:
            length, pos = _dec_varint(buf, pos)
            if pos + length > end:
                raise ValueError("truncated bytes")
            val = bytes(buf[pos : pos + length])
            pos += length
        else:
            pos = _skip_field(buf, pos, wire_type)
    return val


def decode_prefs(buf: bytes) -> dict[str, Any]:
    """解码 PreferencesProto 字节流为 Python dict。"""
    result: dict[str, Any] = {}
    pos = 0
    end = len(buf)
    while pos < end:
        tag, pos = _dec_varint(buf, pos)
        field_no = tag >> 3
        wire_type = tag & 0x07
        if field_no == 1 and wire_type == 2:
            length, pos = _dec_varint(buf, pos)
            if pos + length > end:
                raise ValueError("truncated map entry")
            entry = buf[pos : pos + length]
            pos += length
            epos = 0
            key: str | None = None
            val: Any = None
            while epos < len(entry):
                etag, epos = _dec_varint(entry, epos)
                efn, ewt = etag >> 3, etag & 0x07
                if efn == 1 and ewt == 2:
                    klen, epos = _dec_varint(entry, epos)
                    key = entry[epos : epos + klen].decode("utf-8", errors="replace")
                    epos += klen
                elif efn == 2 and ewt == 2:
                    vlen, epos = _dec_varint(entry, epos)
                    val = _decode_value(entry[epos : epos + vlen])
                    epos += vlen
                else:
                    epos = _skip_field(entry, epos, ewt)
            if key is not None:
                result[key] = val
        else:
            pos = _skip_field(buf, pos, wire_type)
    return result


# ---------------------------------------------------------------------------
# 设备端 DataStore 读写与状态探测
# ---------------------------------------------------------------------------

def read_datastore_file(filename: str, *, timeout: int = 15) -> tuple[str, dict[str, Any]]:
    """单调用同源读取 DataStore pb 文件。

    返回 ``(kind, data)``：
      - ``"ok"``: 读取并解码成功；
      - ``"absent"``: 文件不存在；
      - ``"empty"``: 文件存在但 0 字节；
      - ``"denied"``: 权限拒绝；
      - ``"corrupt"``: base64 或 protobuf 解码失败；
      - ``"transient"``: adb 超时或离线。
    """
    remote = f"{DATASTORE_DIR}/{filename}"
    probe = f"if [ ! -f {remote} ]; then echo {_ABSENT_SENTINEL}; else base64 {remote} 2>&1; fi"
    rc, out, err = adb("shell", probe, timeout=timeout)
    text = (out or "").strip()
    if rc != 0:
        combined = f"{text}\n{err or ''}"
        if _ABSENT_SENTINEL in combined or "No such file" in combined:
            return "absent", {}
        if "Permission denied" in combined:
            return "denied", {}
        return "transient", {}
    if text == _ABSENT_SENTINEL or "No such file" in text:
        return "absent", {}
    if "Permission denied" in text:
        return "denied", {}
    if not text:
        return "empty", {}
    compact = re.sub(r"\s+", "", text)
    try:
        raw = base64.b64decode(compact, validate=True)
    except (binascii.Error, ValueError):
        return "corrupt", {}
    if not raw:
        return "empty", {}
    try:
        return "ok", decode_prefs(raw)
    except ValueError:
        return "corrupt", {}


def write_datastore_files(
    files: dict[str, dict[str, Any]],
    *,
    int32_keys: frozenset[str] = frozenset(),
    timeout: int = 20,
) -> None:
    """将多个 DataStore 字典写入设备并修复属主与权限（system:system, 0600）。"""
    with tempfile.TemporaryDirectory(prefix="stp-aging-ds-") as tmp:
        for fname, payload in files.items():
            local_path = Path(tmp) / fname
            local_path.write_bytes(encode_prefs(payload, int32_keys=int32_keys))
            rc, _, err = adb("push", str(local_path), f"{DATASTORE_DIR}/{fname}", timeout=timeout)
            if rc != 0:
                detail = err.strip() or f"rc={rc}"
                raise RuntimeError(f"adb push {fname} 失败: {detail}")
    rc, out, err = adb(
        "shell",
        (
            f"chown system:system {DATASTORE_DIR}/*.preferences_pb && "
            f"chmod 600 {DATASTORE_DIR}/*.preferences_pb && "
            f"(restorecon {DATASTORE_DIR} {DATASTORE_DIR}/*.preferences_pb >/dev/null 2>&1 || true)"
        ),
        timeout=timeout,
    )
    if rc != 0:
        detail = (err or out).strip() or f"rc={rc}"
        raise RuntimeError(f"修复 DataStore 属主/权限失败: {detail}")


def ddr_service_alive(timeout: int = 10) -> tuple[bool, int]:
    """查询 DdrTestService 是否在运行，返回 ``(alive, rc)``。"""
    rc, out, _ = adb("shell", f"dumpsys activity services {PACKAGE}", timeout=timeout)
    if rc != 0:
        return False, rc
    return ("DdrTestService" in out and "ServiceRecord" in out), 0


def preflight_device_info(timeout: int = 15) -> dict[str, Any]:
    """单次 shell 采集 Aging DDR 前置环境信息。"""
    cmd = (
        "echo uid=$(id -u); "
        "echo board=$(getprop ro.product.board); "
        "echo build=$(getprop ro.build.display.id); "
        "echo build_type=$(getprop ro.build.type); "
        f"echo aging_ver=$(dumpsys package {PACKAGE} 2>/dev/null | grep -m1 versionName= | tr -d ' \\r'); "
        "dumpsys battery 2>/dev/null | grep -E '^  (level|USB powered|AC powered):' | tr -d '\\r'; "
        f"echo ddr_svc=$(dumpsys activity services {PACKAGE} 2>/dev/null | grep -c DdrTestService || true); "
        "echo boot_reason=$(getprop sys.boot.reason.last)"
    )
    rc, out, err = adb("shell", cmd, timeout=timeout)
    info: dict[str, Any] = {"rc": rc, "stderr": err.strip()}
    for raw_line in out.splitlines():
        line = raw_line.strip()
        if line.startswith("uid="):
            info["uid"] = line.split("=", 1)[1]
        elif line.startswith("board="):
            info["board"] = line.split("=", 1)[1]
        elif line.startswith("build="):
            info["build"] = line.split("=", 1)[1]
        elif line.startswith("build_type="):
            info["build_type"] = line.split("=", 1)[1]
        elif line.startswith("aging_ver="):
            val = line.split("=", 1)[1]
            info["apk_version"] = val.split("=", 1)[1] if val.startswith("versionName=") else val
        elif line.startswith("level:"):
            raw_level = line.split(":", 1)[1].strip()
            if raw_level.isdigit():
                info["battery_level"] = int(raw_level)
        elif line.startswith("USB powered:"):
            info["usb_powered"] = line.split(":", 1)[1].strip().lower() == "true"
        elif line.startswith("AC powered:"):
            info["ac_powered"] = line.split(":", 1)[1].strip().lower() == "true"
        elif line.startswith("ddr_svc="):
            raw_cnt = line.split("=", 1)[1].strip()
            info["ddr_svc"] = int(raw_cnt) if raw_cnt.isdigit() else 0
        elif line.startswith("boot_reason="):
            info["boot_reason"] = line.split("=", 1)[1]
    return info
