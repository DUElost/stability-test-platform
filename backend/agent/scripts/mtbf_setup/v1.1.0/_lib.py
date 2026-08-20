# -*- coding: utf-8 -*-
"""MTBF 脚本共享库（mtbf_setup / mtbf_check / mtbf_finish 三件套共用）。

- 环境/参数/stdout 契约与既有 monkey 脚本一致：
  ``STP_DEVICE_SERIAL`` / ``STP_STEP_PARAMS``（JSON）/ stdout 单行 JSON ``{"success": ...}``。
- 路径解析（P0 设计 §4）：清单/全局参数在 ``{STP_AEE_NFS_ROOT}/mtbf/{project}/``，
  APK 在 ``{mtbf_resources_dir}/{project}/``（aimonkey resources 先例）。
- realresult 解析与 times patch 规则与**控制面** ``backend/services/mtbf_suite.py`` 同源，
  两侧以 golden 测试对齐（fixtures 同源）。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

# times patch：`<runtask ... times="N" ...>`，属性顺序不定（与控制面同规则）
_RUNTASK_TIMES_RE = re.compile(r'(<runtask\b[^>]*\btimes=")\d+(")')

STATUS_PASS = "PASS"
STATUS_FAILURE = "FAILURE"
STATUS_ERROR = "ERROR"


# ---------------------------------------------------------------------------
# 环境 / 参数 / 输出契约
# ---------------------------------------------------------------------------

def env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def param_or_env(cfg: dict, key: str, env_key: str, default):
    """配置解析：STP_STEP_PARAMS > STP_* env > 代码默认。

    平台惯例 default_params 恒为空（扫描注册），逐计划参数通道不存在
    （ADR-0029 D1 挂起）；部署级配置经 hot-update 同步的 STP_* env 注入
    （aimonkey 的 cfg > env > 内置默认 先例同款）。
    """
    value = cfg.get(key)
    if value is not None and str(value) != "":
        return value
    raw = env(env_key, "")
    if raw != "":
        return raw
    return default


def adb_path() -> str:
    return env("STP_ADB_PATH", "adb")


def device_serial() -> str:
    serial = env("STP_DEVICE_SERIAL", "")
    if not serial:
        print(
            json.dumps({"success": False, "error_message": "STP_DEVICE_SERIAL is not set"}, ensure_ascii=False)
        )
        sys.exit(1)
    return serial


def params() -> dict:
    raw = env("STP_STEP_PARAMS", "{}")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def output_result(success: bool, **kwargs) -> None:
    print(json.dumps({"success": success, **kwargs}, ensure_ascii=False))


def progress_stamp(payload: dict) -> None:
    """#115 PROGRESS 打戳（stderr，reader B 识别并丢弃；不污染 stdout 结果契约）。"""
    sys.stderr.write(f"PROGRESS {json.dumps(payload, ensure_ascii=False)}\n")
    sys.stderr.flush()


# ---------------------------------------------------------------------------
# ADB 封装
# ---------------------------------------------------------------------------

def adb(*args: str, timeout: int = 60) -> tuple[int, str, str]:
    """adb -s <serial> <args...>，返回 (returncode, stdout, stderr)。"""
    cmd = [adb_path(), "-s", device_serial()] + list(args)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return -1, "", "timeout"
    return result.returncode, result.stdout or "", result.stderr or ""


def adb_shell(command: str, timeout: int = 60) -> str:
    """adb shell <command>，返回 stdout。"""
    _, out, _ = adb("shell", command, timeout=timeout)
    return out


# ---------------------------------------------------------------------------
# 路径解析（P0 设计 §4）
# ---------------------------------------------------------------------------

def suite_dir(project: str) -> Path:
    root = env("STP_AEE_NFS_ROOT", "")
    if not root:
        raise RuntimeError("STP_AEE_NFS_ROOT is not set")
    return Path(root) / "mtbf" / project


def _default_resources_root() -> Path:
    """默认 resources 根：相对 Agent 目录解析（aimonkey_paths 先例同构）。

    部署布局 /opt/stability-test-agent/agent/resources/mtbf；本文件位于
    .../agent/scripts/{script}/v{version}/_lib.py → parents[3] = .../agent。
    """
    return Path(__file__).resolve().parents[3] / "resources" / "mtbf"


def resources_dir(cfg: dict) -> Path:
    base = cfg.get("mtbf_resources_dir") or env("STP_MTBF_RESOURCES_DIR", str(_default_resources_root()))
    project = cfg.get("project") or env("STP_MTBF_PROJECT", "legacy")
    return Path(base) / project


def results_dir(project: str) -> Path:
    return suite_dir(project) / "results"


# ---------------------------------------------------------------------------
# 文件工具
# ---------------------------------------------------------------------------

def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def patch_runtask_times(content: bytes, times: int) -> bytes:
    """替换 <runtask ... times="N">；times <= 0 原样返回（调用方语义：不覆盖）。"""
    if times <= 0:
        return content
    return _RUNTASK_TIMES_RE.sub(
        lambda m: f"{m.group(1)}{times}{m.group(2)}", content.decode("utf-8")
    ).encode("utf-8")


def count_testpoints(content: bytes) -> int:
    """统计 <testpoint> 条目数（setup 的 metrics.testpoint_count 用）。"""
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return 0
    return len(root.findall("testpoint"))


# ---------------------------------------------------------------------------
# realresult 解析（mtbf_finish 用；schema 见 P0 设计 §2，反编译实测定稿）
# ---------------------------------------------------------------------------

def parse_realresult(content: bytes) -> dict:
    """解析 TESTS-RealResult-TestPoints.xml → 摘要 + testpoints 明细。

    规则（P0 设计 §2.3）：
    - join 键 = testpoint ``name``（``id`` 恒为 0，不可用）；
    - testcase 有 ``<failure>`` → FAILURE；有 ``<error>`` → ERROR；否则 PASS
      （INCOMPLETE 落盘归入 ``<error>``，P0 不区分）；
    - testpoint 任一 testcase 非 PASS → 非 PASS；
    - 同名 testpoint 多条 = 多轮次（含回归重跑，不折算）。
    """
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        raise ValueError("realresult XML 解析失败")
    entries = []
    for tp in root.findall("testpoint"):
        cases = []
        for tc in tp.findall("testcase"):
            failure = tc.find("failure")
            error = tc.find("error")
            if failure is not None:
                status = STATUS_FAILURE
                message = (failure.text or "").replace("<\\0>", "\u0000")
            elif error is not None:
                status = STATUS_ERROR
                message = (error.text or "").replace("<\\0>", "\u0000")
            else:
                status = STATUS_PASS
                message = ""
            cases.append(
                {
                    "type": tc.get("type", ""),
                    "class": tc.get("classname", ""),
                    "method": tc.get("name", ""),
                    "status": status,
                    "message": message,
                    "screenshot": tc.get("screenshot"),
                    "time_ms": tc.get("time"),
                }
            )
        tp_status = STATUS_PASS
        for case in cases:
            if case["status"] != STATUS_PASS:
                tp_status = case["status"]
                break
        entries.append(
            {
                "name": tp.get("name", ""),
                "status": tp_status,
                "failures": tp.get("failures", "0"),
                "time_ms": tp.get("time"),
                "starttime": tp.get("starttime"),
                "endtime": tp.get("endtime"),
                "startbattery": tp.get("startbattery"),
                "stopbattery": tp.get("stopbattery"),
                "regression": tp.get("regression"),
                "testcases": cases,
            }
        )
    return {
        "taskname": root.get("taskname", ""),
        "entries": len(entries),
        "passed": sum(1 for e in entries if e["status"] == STATUS_PASS),
        "failed": sum(1 for e in entries if e["status"] == STATUS_FAILURE),
        "error": sum(1 for e in entries if e["status"] == STATUS_ERROR),
        "testpoints": entries,
    }
