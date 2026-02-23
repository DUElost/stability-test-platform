# -*- coding: utf-8 -*-
"""
工具自动发现模块
扫描指定目录，自动发现可用的测试工具
"""

import os
import ast
import json
from pathlib import Path
from typing import List, Dict, Any, Optional


from .config import EXTERNAL_TOOL_DIR, BUILTIN_TOOL_DIR


# 默认扫描路径
DEFAULT_TOOL_DIR = EXTERNAL_TOOL_DIR


class ToolDiscovery:
    """工具自动发现器"""

    def __init__(self, tool_dir: str = DEFAULT_TOOL_DIR, include_builtin: bool = True):
        self._dirs: List[Path] = []
        external = Path(tool_dir)
        if external.exists():
            self._dirs.append(external)
        if include_builtin and BUILTIN_TOOL_DIR.exists():
            self._dirs.append(BUILTIN_TOOL_DIR)

    def scan(self) -> List[Dict[str, Any]]:
        """
        扫描工具目录，返回发现的工具列表

        支持两种目录结构：

        1) 外部分类目录（Test_Tool/）:
        Test_Tool/
        ├── Monkey/
        │   ├── mtk_monkey.py
        │   └── qcom_monkey.py
        └── GPU/
            └── gpu_stress.py

        2) 内置扁平目录（backend/agent/tools/）:
        tools/
        ├── monkey_test.py
        ├── gpu_stress_test.py
        └── ...

        返回：
        [
            {
                "category": "Monkey",
                "script_path": ".../mtk_monkey.py",
                "class_name": "MtkMonkeyTest",
                "params": {...}
            },
            ...
        ]
        """
        tools = []

        for tool_dir in self._dirs:
            if not tool_dir.exists():
                continue

            # Check if this dir contains sub-category folders or flat scripts
            has_subdirs = any(p.is_dir() and not p.name.startswith("_") for p in tool_dir.iterdir())

            if has_subdirs:
                # External layout: category sub-directories
                for category_dir in tool_dir.iterdir():
                    if not category_dir.is_dir() or category_dir.name.startswith("_"):
                        continue
                    for script_file in category_dir.iterdir():
                        if script_file.suffix != ".py" or script_file.name.startswith("_"):
                            continue
                        tool_info = self._parse_script(script_file, category_dir.name)
                        if tool_info:
                            tools.append(tool_info)
            else:
                # Built-in flat layout: derive category from TEST_TYPE
                for script_file in tool_dir.iterdir():
                    if script_file.suffix != ".py" or script_file.name.startswith("_"):
                        continue
                    tool_info = self._parse_script(script_file, None)
                    if tool_info:
                        tools.append(tool_info)

        return tools

    def _parse_script(self, script_path: Path, category: Optional[str]) -> Optional[Dict]:
        """解析脚本获取测试类信息"""
        try:
            with open(script_path, "r", encoding="utf-8") as f:
                source = f.read()

            # 解析 AST 查找继承 BaseTestCase 的类
            tree = ast.parse(source)

            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    # 检查是否继承 BaseTestCase
                    for base in node.bases:
                        if isinstance(base, ast.Name) and base.id == "BaseTestCase":
                            # 提取默认参数
                            default_params = self._extract_default_params(node)

                            # 提取 TEST_TYPE 作为 category 回退
                            resolved_category = category or self._extract_test_type(node) or script_path.stem
                            return {
                                "category": resolved_category,
                                "script_path": str(script_path),
                                "class_name": node.name,
                                "default_params": default_params,
                                "description": ast.get_docstring(node) or "",
                            }

            return None
        except Exception as e:
            print(f"解析脚本失败 {script_path}: {e}")
            return None

    def _extract_test_type(self, class_node: ast.ClassDef) -> Optional[str]:
        """Extract TEST_TYPE class attribute from a ClassDef AST node."""
        for node in class_node.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "TEST_TYPE":
                        if isinstance(node.value, ast.Constant):
                            return str(node.value.value)
        return None

    def _extract_default_params(self, class_node: ast.ClassDef) -> Dict[str, Any]:
        """从类中提取默认参数"""
        params = {}

        for node in class_node.body:
            if isinstance(node, ast.FunctionDef) and node.name == "get_default_params":
                # 解析返回值 - 方式1: type hint 直接返回字典
                if node.returns and isinstance(node.returns, ast.Dict):
                    params = self._parse_dict(node.returns)
                # 方式2: 遍历函数体查找 return 语句
                elif node.body:
                    for stmt in node.body:
                        if isinstance(stmt, ast.Return) and stmt.value:
                            if isinstance(stmt.value, ast.Dict):
                                params = self._parse_dict(stmt.value)
                                break
                            elif isinstance(stmt.value, ast.Call):
                                # 处理 dict() 或 {} 形式
                                if isinstance(stmt.value.func, ast.Name) and stmt.value.func.id == "dict":
                                    # 处理 dict(key=value) 形式
                                    for kw in stmt.value.keywords:
                                        if kw.value:
                                            if isinstance(kw.value, ast.Constant):
                                                params[kw.arg] = kw.value.value
                break

        return params

    def _parse_dict(self, node: ast.Dict) -> Dict:
        """解析 AST Dict 节点"""
        result = {}
        for key, value in zip(node.keys, node.values):
            if isinstance(key, ast.Constant):
                k = key.value
            elif isinstance(key, ast.Str):  # Python 3.7-
                k = key.s
            else:
                continue

            if isinstance(value, ast.Constant):
                result[k] = value.value
            elif isinstance(value, ast.Str):
                result[k] = value.s
            elif isinstance(value, ast.Num):
                result[k] = value.n
            elif isinstance(value, ast.Dict):
                result[k] = self._parse_dict(value)

        return result


class ToolDiscoveryService:
    """工具发现服务 - 用于同步工具到数据库"""

    def __init__(self, db_session):
        self.db = db_session
        self.discovery = ToolDiscovery()

    def sync(self) -> Dict[str, int]:
        """
        同步发现的工具到数据库
        返回：{"categories": 新增分类数, "tools": 新增工具数}

        注意：此方法为服务端功能（需要 backend.models），Agent 运行时不调用。
        """
        from backend.models.schemas import ToolCategory, Tool

        tools = self.discovery.scan()
        categories_created = 0
        tools_created = 0

        for tool_info in tools:
            # 获取或创建分类
            category_name = tool_info["category"]
            category = self.db.query(ToolCategory).filter_by(name=category_name).first()

            if not category:
                category = ToolCategory(
                    name=category_name,
                    description=f"{category_name} 测试类型"
                )
                self.db.add(category)
                self.db.flush()
                categories_created += 1

            # 检查工具是否已存在（按 script_path + script_class 唯一判断）
            existing = self.db.query(Tool).filter_by(
                category_id=category.id,
                script_class=tool_info["class_name"]
            ).first()

            if not existing:
                new_tool = Tool(
                    category_id=category.id,
                    name=f"{category_name} - {tool_info['class_name']}",
                    script_path=tool_info["script_path"],
                    script_class=tool_info["class_name"],
                    default_params=tool_info.get("default_params", {}),
                    description=tool_info.get("description", ""),
                )
                self.db.add(new_tool)
                tools_created += 1

        self.db.commit()
        return {"categories": categories_created, "tools": tools_created}


if __name__ == "__main__":
    # 测试扫描功能
    discovery = ToolDiscovery()
    tools = discovery.scan()

    print(f"发现 {len(tools)} 个工具:")
    for tool in tools:
        print(f"  - [{tool['category']}] {tool['class_name']}")
        print(f"    脚本: {tool['script_path']}")
        print(f"    参数: {tool.get('default_params', {})}")
        print()
