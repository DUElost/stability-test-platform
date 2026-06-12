"""Agent-local helpers for normalizing AEE subtype/package metadata.

Hot-update deploys only backend/agent to hosts, so AEE runtime code must not
require backend.core to be present.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

_PATH_TOKEN_RE = re.compile(r"[A-Z0-9]+")


def normalize_aee_event_type(raw_event_type: str) -> str:
    normalized = (raw_event_type or "").strip().upper()
    if not normalized:
        return ""
    if "ANR" in normalized:
        return "ANR"
    return "CRASH"


def normalize_aee_subtype(
    raw_event_type: str,
    event_type: str,
    *,
    category: str | None = None,
) -> str:
    normalized = " ".join((raw_event_type or "").strip().upper().split())
    normalized_event_type = (event_type or "").strip().upper()
    normalized_category = (category or "").strip().upper()

    if normalized_category == "ANR" or normalized_event_type == "ANR":
        return "ANR"
    if not normalized:
        return "其他"
    if "FATAL" in normalized and "JE" in normalized:
        return "Fatal JE"
    if "FATAL" in normalized and (
        "NATIVE" in normalized or "(NE)" in normalized or normalized.startswith("SIG")
    ):
        return "Fatal NE"
    if "COMBO" in normalized and "EE" in normalized:
        return "Combo EE"
    if "KERNEL" in normalized and "API" in normalized and "DUMP" in normalized:
        return "Kernel API Dump"
    if "SYSTEM" in normalized and "API" in normalized and "DUMP" in normalized:
        return "System API Dump"
    if "MODEM" in normalized and "EE" in normalized:
        return "Modem EE"
    if "OCP" in normalized and "REBOOT" in normalized:
        return "OCP Reboot"
    if "HW" in normalized and "REBOOT" in normalized:
        return "HW Reboot"
    if normalized == "HWT" or " HWT" in f" {normalized}" or "HWT " in f"{normalized} ":
        return "HWT"
    if "HANG_DETECT" in normalized or normalized == "HANG":
        return "HANG"
    if normalized == "KE" or "KERNEL EXCEPTION" in normalized:
        return "KE"
    if "SWT" in normalized:
        return "SWT"
    if "JAVA" in normalized or "(JE)" in normalized or normalized == "JE":
        return "JE"
    if (
        "NATIVE" in normalized
        or "(NE)" in normalized
        or normalized == "NE"
        or normalized.startswith("SIG")
    ):
        return "NE"
    return "其他"


def infer_aee_subtype_from_paths(*paths: str) -> Optional[str]:
    for raw_path in paths:
        subtype = _infer_single_path_subtype(raw_path)
        if subtype:
            return subtype
    return None


def parse_exp_main_summary(entry_dir: Path) -> dict[str, str]:
    exp_main_path = entry_dir / "__exp_main.txt"
    if not exp_main_path.is_file():
        return {}

    try:
        content = exp_main_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return {}

    lines = content.splitlines()
    defect_class = ""
    exception_class = ""
    exception_type = ""
    package_name = ""
    current_process = ""

    for index, raw_line in enumerate(lines):
        line = raw_line.strip()
        if not line:
            continue

        if not defect_class and line.startswith("Defect Class:"):
            defect_class = line.split(":", 1)[1].strip()
            continue
        if not exception_class and line.startswith("Exception Class:"):
            exception_class = line.split(":", 1)[1].strip()
            continue
        if not exception_type and line.startswith("Exception Type:"):
            exception_type = line.split(":", 1)[1].strip()
            continue
        if not package_name and line.startswith("Package:"):
            package_name = normalize_package_name(line.split(":", 1)[1].strip()) or ""
            continue
        if not current_process and line.startswith("Process:"):
            current_process = normalize_package_name(line.split(":", 1)[1].strip()) or ""
            continue
        if not current_process and line == "Current Executing Process:":
            current_process = _read_following_process(lines, index + 1) or ""

    exp_class = defect_class or exception_class

    subtype = _normalize_exp_main_class(defect_class, exception_type)
    if not subtype:
        subtype = _normalize_exp_main_class(exception_class, exception_type)

    # ---- process / package fallback (aligned with ExpMain.analyse) ----

    # 1. Kernel / HWT / HW Reboot 等系统级类型没有用户态 Process 行,用 exp_class 填充
    if not current_process and exp_class in (
        "Kernel (KE)", "HWT", "HANG_DETECT", "Kernel API Dump", "Hardware Reboot",
    ):
        current_process = exp_class
    elif not current_process and exp_class == "External (EE)":
        current_process = exception_type

    # 2. Package → current_process 双向回退
    if not package_name and current_process:
        package_name = current_process
    if not current_process and package_name:
        current_process = package_name

    # 3. 清理值: "android" → "android_framework"; 去掉 ":xxx" 后缀
    if current_process == "android":
        current_process = "android_framework"
    if current_process and ":" in current_process:
        current_process = current_process.split(":", 1)[0].strip()
    if package_name == "android":
        package_name = "android_framework"
    if package_name and ":" in package_name:
        package_name = package_name.split(":", 1)[0].strip()

    result: dict[str, str] = {}
    if subtype:
        result["event_subtype"] = subtype
    if package_name:
        result["package_name"] = package_name
    if current_process:
        result["current_process"] = current_process
    return result


def normalize_package_name(value: str) -> Optional[str]:
    candidate = (value or "").strip().strip("'\"")
    if not candidate:
        return None

    first_line = candidate.splitlines()[0].strip()
    if not first_line:
        return None

    token = first_line.split()[0].strip()
    if not token:
        return None

    if ":" in token and not token.startswith("/"):
        token = token.split(":", 1)[0].strip()
    if not token or token.lower() == "unknown":
        return None
    return token


def _normalize_exp_main_class(value: str, exception_type: str = "") -> Optional[str]:
    normalized = (value or "").strip()
    if not normalized:
        return None
    if normalized == "Native (NE)":
        return "NE"
    if normalized == "Java (JE)":
        return "JE"
    if normalized == "ANR":
        return "ANR"
    if normalized == "Kernel (KE)":
        return "KE"
    if normalized == "SWT":
        return "SWT"
    if normalized == "HANG_DETECT":
        return "HANG"
    if normalized == "HWT":
        return "HWT"
    if normalized == "OCP reboot":
        return "OCP Reboot"
    if normalized == "Hardware Reboot":
        return "HW Reboot"
    if normalized == "Kernel API Dump":
        return "Kernel API Dump"
    if normalized == "System API Dump":
        return "System API Dump"
    if normalized == "External (EE)":
        by_type = normalize_aee_subtype(exception_type, "CRASH")
        return by_type if by_type != "其他" else "Combo EE"

    normalized_fallback = normalize_aee_subtype(normalized, "CRASH")
    return None if normalized_fallback == "其他" else normalized_fallback


def _read_following_process(lines: list[str], start_index: int) -> Optional[str]:
    for raw_line in lines[start_index : start_index + 3]:
        candidate = normalize_package_name(raw_line)
        if candidate:
            return candidate
    return None


def _infer_single_path_subtype(raw_path: str) -> Optional[str]:
    path_str = (raw_path or "").strip()
    if not path_str:
        return None

    basename = Path(path_str).name.upper()
    if not basename:
        return None

    if "KERNEL" in basename and "API" in basename and "DUMP" in basename:
        return "Kernel API Dump"
    if "SYSTEM" in basename and "API" in basename and "DUMP" in basename:
        return "System API Dump"
    if "MODEM" in basename and "EE" in basename:
        return "Modem EE"
    if "OCP" in basename and "REBOOT" in basename:
        return "OCP Reboot"
    if "HW" in basename and "REBOOT" in basename:
        return "HW Reboot"
    if "FATAL" in basename and "JE" in basename:
        return "Fatal JE"
    if "FATAL" in basename and "NE" in basename:
        return "Fatal NE"
    if "COMBO" in basename and "EE" in basename:
        return "Combo EE"

    tokens = _PATH_TOKEN_RE.findall(basename)
    token_set = set(tokens)
    if "ANR" in token_set:
        return "ANR"
    if "JE" in token_set:
        return "JE"
    if "NE" in token_set:
        return "NE"
    if "SWT" in token_set:
        return "SWT"
    if "HWT" in token_set:
        return "HWT"
    if "HANG" in token_set or "HANG_DETECT" in token_set:
        return "HANG"
    if "KE" in token_set:
        return "KE"
    return None
