"""#739：Agent 生产代码的 import 边界（静态守卫）。

Agent 进程部署在**没有控制面**的主机上（`backend/agent/` 自成一套运行时），
因此生产代码（含 `scripts/` 下已发布脚本）不得 import 控制面包：
``api`` / ``services`` / ``tasks`` / ``realtime`` / ``scheduler`` / ``models`` /
``alembic`` / ``main``。共享层 ``backend.core.*`` 是例外——但只允许**显式登记**
且经确认是纯模块（无 DB/Redis 依赖）的少数几个，新增必须写明理由。

为什么是**静态 AST** 而不是靠运行时暴露：agent 测试 conftest 会给
`DATABASE_URL` / `JWT_SECRET_KEY` 占位（#2428 有意为之，让套件在干净 shell 能跑），
于是「import 控制面会在干净环境炸掉」这条运行时判据被兜住了——
`agent-tests-collect` 门禁实测抓不到新增的越界 import（本文件记录该反例）。
部署面（agent 跑在 host 上）与测试面（conftest 供 env）是两件事，前者才是真风险：
一个 `import backend.services.x` 会让 agent 在目标机上直接 ImportError。

判据边界：只看**直接 import**（含 ``importlib.import_module`` / ``__import__`` 的
字符串字面量）。相对导入（``from ...core.metrics import x``）按 PEP 328 解析为绝对名
后判定（#2798：此前 ``ast.ImportFrom.level`` 被忽略，相对形态整体逃逸）。传递依赖
（共享模块自己 import 了控制面）不在本判据内——``_SHARED_ALLOWLIST`` 的每个条目都
人工核过依赖，新增条目也要照做。
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
AGENT_DIR = REPO_ROOT / "backend" / "agent"

#: Agent 生产代码只允许 import 这些跨包模块 → 理由（必须人工核实为纯模块）。
#: 其余任何 ``backend.<非 agent>`` 一律判违约——包括控制面包与将来新增的顶层包。
_SHARED_ALLOWLIST: dict[str, str] = {
    "backend.core.legacy_aee": "LEGACY_AEE_SCRIPT_NAMES 常量表（纯数据，无运行时依赖）",
    "backend.core.pipeline_validator": "pipeline_def 校验器（双端共享的纯函数，#738）",
    # #2798：模块体纯（prometheus_client 可选，无 DB/Redis）；消费方（aee/reconciler）
    # 用 try/except + no-op 兜底。注意 backend.core 的**包 init** 会拉 DB（agent 主机上
    # 该 import 会失败并走兜底），本豁免按「模块体纯度」判，与上两条同口径。
    "backend.core.metrics": "指标原语 record_reconciler_skip_unchanged / burst gauge（best-effort，兜底 no-op）",
}


def _module_name_for(path: Path) -> str:
    """文件 → 点分模块名；``__init__.py`` 保留 ``.__init__`` 末段（供相对导入解析）。"""
    rel = path.relative_to(REPO_ROOT).with_suffix("")
    return ".".join(rel.parts)


def _resolve_import_from(node: ast.ImportFrom, module: str | None) -> str | None:
    """把 ``ImportFrom`` 解析为绝对模块名（PEP 328）；不可解析时返回 ``None``。

    ``module`` = 源码文件的点分模块名（``backend.agent.aee.reconciler`` 或
    ``backend.agent.aee.__init__``）。所属包恒为「去掉最后一段」——对普通文件是
    其目录，对 ``__init__`` 是包自身——故 ``level=1`` 落在所属包，
    每加一级上溯一层（``from ...core.metrics`` ⇒ ``backend.core.metrics``）。
    """
    if not node.level:
        return node.module
    if not module:
        return node.module      # 无文件上下文：尽力而为，保持旧行为
    base = module.split(".")[:-1]
    up = node.level - 1
    if up > len(base):
        return None             # 越过顶层包：不可解析
    parts = base[: len(base) - up]
    if node.module:
        parts = parts + node.module.split(".")
    return ".".join(parts) or None


def cross_package_imports(
    source: str, *, module: str | None = None,
) -> list[tuple[int, str]]:
    """返回 ``[(行号, 模块名), …]``：源码里对 ``backend.<非 agent>`` 的全部引用。

    覆盖静态 import 与 ``import_module("…")`` / ``__import__("…")`` 的字面量形态；
    **相对导入**（``from ...core.metrics import x``）在给出 ``module`` 时按 PEP 328
    解析为绝对名（#2798：此前 ``ast.ImportFrom.level`` 被忽略，相对形态直接逃逸）。
    是否允许由调用方按 ``_SHARED_ALLOWLIST`` 判定（本函数只负责"看见"）。
    """
    tree = ast.parse(source)
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        modules: list[str] = []
        if isinstance(node, ast.ImportFrom):
            resolved = _resolve_import_from(node, module)
            if resolved:
                modules.append(resolved)
        elif isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.Call):
            func = node.func
            name = getattr(func, "attr", None) or getattr(func, "id", None)
            if name in {"import_module", "__import__"} and node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    modules.append(first.value)
        for mod in modules:
            if not mod.startswith("backend."):
                continue
            if mod == "backend" or mod.startswith("backend.agent"):
                continue
            hits.append((getattr(node, "lineno", 0), mod))
    return sorted(set(hits))


def _scan_files() -> list[Path]:
    """agent 生产面（不含它自己的测试目录）。"""
    return sorted(
        path
        for path in AGENT_DIR.rglob("*.py")
        if "/tests/" not in path.as_posix()
    )


def test_agent_production_imports_only_allowed_shared_modules():
    """agent 生产代码不得 import 控制面包（共享层仅限登记条目）。"""
    offenders: list[str] = []
    for path in _scan_files():
        for lineno, module in cross_package_imports(
            path.read_text(encoding="utf-8"), module=_module_name_for(path),
        ):
            if module in _SHARED_ALLOWLIST:
                continue
            offenders.append(f"{path.relative_to(REPO_ROOT).as_posix()}:{lineno} → {module}")
    assert offenders == [], (
        "agent 生产代码 import 了控制面包（agent 部署在没有控制面的主机上，"
        "目标机会直接 ImportError）：\n  " + "\n  ".join(offenders)
        + "\n若确属共享纯模块，登记进 _SHARED_ALLOWLIST 并写明理由（附依赖核实）。"
    )


def test_shared_allowlist_entries_are_still_used():
    """豁免表不得留下已不再被 import 的条目（防豁免面悄悄扩大）。"""
    used: set[str] = set()
    for path in _scan_files():
        used.update(
            module
            for _, module in cross_package_imports(
                path.read_text(encoding="utf-8"), module=_module_name_for(path),
            )
        )
    stale = sorted(module for module in _SHARED_ALLOWLIST if module not in used)
    assert stale == [], f"豁免表存在失效条目（已无人 import，请移除）：{stale}"


def test_detector_sees_static_and_dynamic_control_plane_imports():
    """判据自身的守卫：静态、动态两种形态都要判得出；agent 内 import 不得误报。

    扫描器只负责「看见」全部跨包 import（含被豁免的共享层）；是否允许由
    ``_SHARED_ALLOWLIST`` 判定——所以 ``backend.core.pipeline_validator`` 也应在结果里。
    """
    source = (
        "from backend.api.routes import plans\n"
        "import backend.scheduler.cron_scheduler as mod\n"
        "import importlib\n"
        "importlib.import_module('backend.tasks.saq_tasks')\n"
        "__import__('backend.realtime.socketio_server')\n"
        "from backend.agent.pipeline_engine import x\n"
        "from backend.core.pipeline_validator import validate_pipeline_def\n"
        "from sqlalchemy import select\n"
    )
    found = {module for _, module in cross_package_imports(source)}
    assert found == {
        "backend.api.routes",
        "backend.scheduler.cron_scheduler",
        "backend.tasks.saq_tasks",
        "backend.realtime.socketio_server",
        "backend.core.pipeline_validator",
    }, found


def test_detector_resolves_relative_imports():
    """#2798：相对导入（PEP 328）必须解析为绝对名后判定——此前 ``level`` 被忽略。

    反例即本单来源：``backend/agent/aee/reconciler.py`` 的
    ``from ...core.metrics import (…)`` 解析为 ``backend.core.metrics``，
    旧检测器只看到 ``"core.metrics"``（不以 ``backend.`` 开头）而放行。
    """
    source = (
        "from ...core.metrics import record_reconciler_skip_unchanged\n"
        "from ..heartbeat import beat\n"
        "from .sibling import helper\n"
        "from backend.agent.job_runner import run\n"
    )
    found = {
        module
        for _, module in cross_package_imports(
            source, module="backend.agent.aee.reconciler",
        )
    }
    assert found == {"backend.core.metrics"}, found

    # 包（__init__.py）语义：level=1 落在包自身，不是其父目录。
    pkg_found = {
        module
        for _, module in cross_package_imports(
            "from ...core import x\n", module="backend.agent.aee.__init__",
        )
    }
    assert pkg_found == {"backend.core"}, pkg_found

    # 越过顶层包的相对形态不可解析（fail-safe：不抛异常、不误报）。
    assert cross_package_imports(
        "from .....x import y\n", module="backend.agent.m",
    ) == []


def test_scan_surface_is_not_empty():
    """扫描面塌陷（目录改名/搬走）不能长得像「全绿」。"""
    files = _scan_files()
    assert len(files) >= 20, f"agent 生产面扫描异常，只看到 {len(files)} 个文件"
    assert any(path.name == "main.py" for path in files)
