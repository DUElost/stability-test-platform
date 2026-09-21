"""stability_Jira-Automation 族：Transsion / Tinno 共用 argv 模板（ADR-0033 Phase A1）。

Moto 等未接厂商不在本引擎；新增厂商不得借此引入新 ``STP_JIRA_*_DIR`` 键扩散
（见 ADR-0033 §5.4 评估相邻纪律）。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List, Optional

from backend.services.jira_vendor.base import JiraVendorEngine


class StabilityJiraAutomationEngine(JiraVendorEngine):
    """现态 Transsion / Tinno generate_/create_ 脚本 argv 接缝。"""

    def resolve_tool(self, vendor: str) -> Optional[Dict[str, str]]:
        v = vendor.upper()
        python = os.getenv(f"STP_JIRA_{v}_PYTHON", "").strip()
        tool_dir = os.getenv(f"STP_JIRA_{v}_DIR", "").strip()
        if not python or not tool_dir:
            return None
        return {"python": python, "dir": tool_dir}

    def build_argv(
        self,
        vendor: str,
        stage: str,
        tool_dir: str,
        python: str,
        *,
        input_xls: str,
        dry_run: bool = True,
        reporter: Optional[str] = None,
        jira_project_key: Optional[str] = None,
    ) -> List[str]:
        d = Path(tool_dir)
        if stage == "upload_list":
            script = d / f"generate_{vendor}_jira_upload_list.py"
            argv = [python, str(script), "--add-main-excel", input_xls]
            if jira_project_key:
                argv += ["--set-project-key", jira_project_key]
            return argv
        if stage == "create":
            script = d / f"create_{vendor}_jira_batch_from_excel.py"
            argv = [python, str(script), "--add-excel-file", input_xls]
            if dry_run:
                argv.append("--dry-run")
            if reporter:
                argv += ["--reporter", reporter]
            return argv
        raise ValueError(f"unknown jira stage: {stage}")

    def load_tool_env(self, tool_dir: str) -> Dict[str, str]:
        root = Path(tool_dir)
        out: Dict[str, str] = {}
        for path in (root / ".env.local", root / "tools" / ".env"):
            if not path.is_file():
                continue
            for raw_line in path.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[7:].strip()
                key, _, value = line.partition("=")
                key = key.strip()
                if not key:
                    continue
                out[key] = value.strip().strip('"').strip("'")
        return out


_ENGINE: JiraVendorEngine | None = None


def get_jira_vendor_engine() -> JiraVendorEngine:
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = StabilityJiraAutomationEngine()
    return _ENGINE


def reset_jira_vendor_engine_for_tests() -> None:
    global _ENGINE
    _ENGINE = None


# --- 门面：保持历史函数名，供路由与既有测试迁移 ---

def resolve_vendor_tool(vendor: str) -> Optional[Dict[str, str]]:
    return get_jira_vendor_engine().resolve_tool(vendor)


def build_jira_argv(
    vendor: str,
    stage: str,
    tool_dir: str,
    python: str,
    *,
    input_xls: str,
    dry_run: bool = True,
    reporter: Optional[str] = None,
    jira_project_key: Optional[str] = None,
) -> List[str]:
    return get_jira_vendor_engine().build_argv(
        vendor,
        stage,
        tool_dir,
        python,
        input_xls=input_xls,
        dry_run=dry_run,
        reporter=reporter,
        jira_project_key=jira_project_key,
    )


def load_vendor_tool_env(tool_dir: str) -> Dict[str, str]:
    return get_jira_vendor_engine().load_tool_env(tool_dir)
