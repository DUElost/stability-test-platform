"""JiraVendorEngine：控制面 Tier-1 Jira 厂商工具薄接缝（ADR-0033 D4 / Phase A1）。

接口只包 env 解析、argv 构造与工具目录凭据 env 加载；RunConsole 编排、
JiraRun 落库、鉴权仍在 ``backend/api/routes/dedup.py``。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, List, Optional


class JiraVendorEngine(ABC):
    """厂商 Jira 自动化工具的防腐适配器。"""

    @abstractmethod
    def resolve_tool(self, vendor: str) -> Optional[Dict[str, str]]:
        """从 env 解析解释器 + 工具目录；未配置返回 None。"""
        ...

    @abstractmethod
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
        """按 (vendor, stage) 拼装厂商工具 argv（不走 shell）。"""
        ...

    @abstractmethod
    def load_tool_env(self, tool_dir: str) -> Dict[str, str]:
        """读取工具目录凭据 env 文件（不记录值）。"""
        ...
