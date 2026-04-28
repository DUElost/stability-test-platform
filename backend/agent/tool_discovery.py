# -*- coding: utf-8 -*-
"""工具自动发现模块

扫描指定目录，自动发现继承 ``PipelineAction`` 的工具类并注册到数据库。

发现规则
--------
- 扫描目录下的所有 ``.py`` 文件（不含以 ``_`` 开头的文件）
- 解析 AST，查找继承 ``PipelineAction`` 的类（**不再支持 BaseTestCase**）
- 从类属性读取元数据：``TOOL_CATEGORY``、``TOOL_DESCRIPTION``
- 从 ``get_default_params()`` 方法体读取默认参数

目录结构（支持两种布局）
------------------------
外部分类目录（Test_Tool/）::

    Test_Tool/
    ├── Monkey/
    │   └── mtk_monkey.py          # class MtkMonkeyAction(PipelineAction)
    └── GPU/
        └── gpu_stress.py

内置扁平目录（backend/agent/tools/）::

    tools/
    ├── monkey_test.py             # class MonkeyAction(PipelineAction)
    └── gpu_stress_test.py
"""

import ast
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import EXTERNAL_TOOL_DIR, BUILTIN_TOOL_DIR

DEFAULT_TOOL_DIR = EXTERNAL_TOOL_DIR

# 标记基类名称（与 pipeline_engine.PipelineAction 一致）
_PIPELINE_ACTION_BASE = "PipelineAction"
_TOOL_BUNDLE_MARKER = ".stp-tool-bundle"


class ToolDiscovery:
    """Pipeline Action 工具自动发现器。"""

    def __init__(self, tool_dir: str = DEFAULT_TOOL_DIR, include_builtin: bool = True):
        self._dirs: List[Path] = []
        self._tool_runtime_root = os.getenv("STP_TOOL_RUNTIME_ROOT", "").strip()
        external = Path(tool_dir)
        if external.exists():
            self._dirs.append(external)
        if include_builtin and BUILTIN_TOOL_DIR.exists():
            self._dirs.append(BUILTIN_TOOL_DIR)

    def scan(self) -> List[Dict[str, Any]]:
        """扫描工具目录，返回发现的 PipelineAction 列表。

        每项格式::

            {
                "category": "MONKEY",
                "script_path": ".../monkey_test.py",
                "class_name": "MonkeyAction",
                "default_params": {"event_count": 10000, ...},
                "description": "...",
            }
        """
        tools: List[Dict[str, Any]] = []

        for tool_dir in self._dirs:
            if not tool_dir.exists():
                continue

            for script_file in tool_dir.iterdir():
                if not script_file.is_file() or script_file.suffix != ".py" or script_file.name.startswith("_"):
                    continue
                info = self._parse_script(script_file, None)
                if info:
                    info["script_path"] = self._script_path_for_runtime(script_file)
                    tools.append(info)

            for category_dir in tool_dir.iterdir():
                if not category_dir.is_dir() or category_dir.name.startswith("_"):
                    continue
                if (category_dir / _TOOL_BUNDLE_MARKER).exists():
                    continue
                for script_file in category_dir.iterdir():
                    if not script_file.is_file() or script_file.suffix != ".py" or script_file.name.startswith("_"):
                        continue
                    info = self._parse_script(script_file, category_dir.name)
                    if info:
                        info["script_path"] = self._script_path_for_runtime(script_file)
                        tools.append(info)

        return tools

    def _script_path_for_runtime(self, script_path: Path) -> str:
        """Return the path agents should load for built-in tools."""
        if not self._tool_runtime_root:
            return str(script_path)

        try:
            relative = script_path.resolve().relative_to(BUILTIN_TOOL_DIR.resolve())
        except ValueError:
            return str(script_path)

        root = self._tool_runtime_root.rstrip("\\/")
        relative_posix = relative.as_posix()
        if root.startswith("/"):
            return f"{root}/{relative_posix}"
        return str(Path(root) / relative)

    def _parse_script(self, script_path: Path, category: Optional[str]) -> Optional[Dict[str, Any]]:
        """解析脚本，查找继承 PipelineAction 的类。"""
        try:
            with open(script_path, "r", encoding="utf-8") as f:
                source = f.read()

            tree = ast.parse(source)

            for node in ast.walk(tree):
                if not isinstance(node, ast.ClassDef):
                    continue

                if not self._inherits_pipeline_action(node):
                    continue

                default_params = self._extract_default_params(node)
                description = ast.get_docstring(node) or ""

                # TOOL_CATEGORY 优先于 category 目录名，再回退到文件名
                tool_category = (
                    self._extract_str_attr(node, "TOOL_CATEGORY")
                    or category
                    or script_path.stem
                )
                tool_description = (
                    self._extract_str_attr(node, "TOOL_DESCRIPTION")
                    or description
                )

                return {
                    "category": tool_category,
                    "script_path": str(script_path),
                    "class_name": node.name,
                    "default_params": default_params,
                    "description": tool_description,
                }

        except Exception as exc:
            print(f"解析脚本失败 {script_path}: {exc}")

        return None

    # ------------------------------------------------------------------
    # AST helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _inherits_pipeline_action(class_node: ast.ClassDef) -> bool:
        """Return True if the class directly inherits PipelineAction."""
        for base in class_node.bases:
            if isinstance(base, ast.Name) and base.id == _PIPELINE_ACTION_BASE:
                return True
            # Support: from pipeline_engine import PipelineAction (as Attribute)
            if isinstance(base, ast.Attribute) and base.attr == _PIPELINE_ACTION_BASE:
                return True
        return False

    @staticmethod
    def _extract_str_attr(class_node: ast.ClassDef, attr_name: str) -> str:
        """Extract a string class-level attribute (e.g. TOOL_CATEGORY = "...")."""
        for node in class_node.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == attr_name:
                        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                            return node.value.value
        return ""

    @staticmethod
    def _extract_default_params(class_node: ast.ClassDef) -> Dict[str, Any]:
        """Extract the return dict of get_default_params() via AST."""
        for node in class_node.body:
            if not (isinstance(node, ast.FunctionDef) and node.name == "get_default_params"):
                continue
            for stmt in node.body:
                if isinstance(stmt, ast.Return) and stmt.value:
                    if isinstance(stmt.value, ast.Dict):
                        return ToolDiscovery._parse_dict(stmt.value)
                    if isinstance(stmt.value, ast.Call):
                        func = stmt.value.func
                        if isinstance(func, ast.Name) and func.id == "dict":
                            return {
                                kw.arg: kw.value.value
                                for kw in stmt.value.keywords
                                if kw.arg and isinstance(kw.value, ast.Constant)
                            }
        return {}

    @staticmethod
    def _parse_dict(node: ast.Dict) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        for key, value in zip(node.keys, node.values):
            if isinstance(key, ast.Constant):
                k = key.value
            else:
                continue
            if isinstance(value, ast.Constant):
                result[k] = value.value
            elif isinstance(value, ast.Dict):
                result[k] = ToolDiscovery._parse_dict(value)
            elif isinstance(value, (ast.List, ast.Tuple)):
                elts = []
                for elt in value.elts:
                    if isinstance(elt, ast.Constant):
                        elts.append(elt.value)
                result[k] = elts
        return result


class ToolDiscoveryService:
    """服务端工具：将发现的 PipelineAction 同步到数据库。

    注意：此类仅在服务端运行（需要 backend.models），Agent 运行时不调用。
    """

    def __init__(self, db_session):
        self.db = db_session
        self.discovery = ToolDiscovery()

    def sync(self) -> Dict[str, int]:
        """同步发现的工具到数据库（新 tool 表），返回 {"created": N, "updated": N}。"""
        from backend.models.tool import Tool

        tools = self.discovery.scan()
        created = 0
        updated = 0

        for tool_info in tools:
            category_name = tool_info["category"]
            existing = self.db.query(Tool).filter_by(
                script_class=tool_info["class_name"],
            ).first()

            if not existing:
                self.db.add(Tool(
                    name=f"{category_name} - {tool_info['class_name']}",
                    version="discovered",
                    script_path=tool_info["script_path"],
                    script_class=tool_info["class_name"],
                    param_schema=tool_info.get("default_params", {}),
                    description=tool_info.get("description", ""),
                    category=category_name,
                    is_active=True,
                ))
                created += 1
            else:
                existing.script_path = tool_info["script_path"]
                existing.category = category_name
                if tool_info.get("description"):
                    existing.description = tool_info["description"]
                updated += 1

        self.db.commit()
        return {"created": created, "updated": updated}


if __name__ == "__main__":
    discovery = ToolDiscovery()
    found = discovery.scan()
    print(f"发现 {len(found)} 个 PipelineAction 工具:")
    for t in found:
        print(f"  [{t['category']}] {t['class_name']} — {t['script_path']}")
        if t.get("default_params"):
            print(f"    默认参数: {t['default_params']}")
