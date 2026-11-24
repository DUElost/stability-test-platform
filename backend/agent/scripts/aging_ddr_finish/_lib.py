# -*- coding: utf-8 -*-
"""FacAging (com.ape.aging) Aging DDR 结果收取脚本共享库。"""
from __future__ import annotations

import base64
import binascii
import json
import os
import re
import struct
import subprocess
import sys
from pathlib import Path
from typing import Any

PACKAGE = "com.ape.aging"
DATASTORE_DIR = f"/data/user/0/{PACKAGE}/files/datastore"
EXPORTS_DIR = f"/storage/emulated/0/Android/data/{PACKAGE}/files/exports"
_ABSENT_SENTINEL = "__STP_AGING_DS_ABSENT__"


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
    sys.stderr.write(f"PROGRESS {json.dumps(payload, ensure_ascii=False)}\n")
    sys.stderr.flush()


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


def adb_shell(command: str, timeout: int = 30) -> str:
    _, out, _ = adb("shell", command, timeout=timeout)
    return out


def device_online(timeout: int = 10) -> bool:
    rc, out, _ = adb("get-state", timeout=timeout)
    return rc == 0 and out.strip() == "device"


def results_dir(project: str) -> Path | None:
    root = env("STP_AEE_NFS_ROOT", "").strip()
    if root:
        return Path(root) / "aging-ddr" / project / "results"
    log_dir = env("STP_LOG_DIR", "").strip()
    if log_dir:
        return Path(log_dir)
    return None


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


def read_datastore_file(filename: str, *, timeout: int = 15) -> tuple[str, dict[str, Any]]:
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
