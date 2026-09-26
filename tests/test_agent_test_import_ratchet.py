"""#739：agent **测试**侧的控制面 import **已清零**（终态不变式守卫）。

与生产侧边界（`tests/test_agent_import_boundary.py`）是不同的两件事：

- **生产代码**越界 → Agent 部署在无控制面的主机上会直接 `ImportError`（真风险，
  已由静态守卫钉死）；
- **测试**里 import 控制面 → 不会炸：套件 conftest 在收集期就 `setdefault` 了
  `DATABASE_URL` / `JWT_SECRET_KEY`（#2428），`agent-tests-collect` 也抓不到
  （2026-09-17 实测：注入越界 import 后仍 2130 全收集通过）——所以这条边界由**静态 AST 守卫**钉住。

历史与收敛（owner 裁决 2026-09-20：**分批迁移到 `backend/tests/` + 横跨契约文件就地解耦**）：

- 存量曾有 **14 个** agent 测试文件 import 控制面；
- 第一批（7 个）：迁移 5（`test_adr0026_params` / `test_logging_setup` /
  `test_app_scheduler_executors` / `test_leader_election` / `test_socketio_redis_adapter`）
  + 就地解耦 2（`test_step_log_batching` 的服务端 2 例、`test_legacy_tool_cleanup`
  的跨包墓碑 2 例）；
- 第二批（7 个）：全部迁移——`test_aee_metadata` / `test_login_lockout` /
  `test_pipeline_validator_parity_738`（2026-09-26 更名 `test_pipeline_validator_contract_738`）
  → `backend/tests/core/`；`test_cron_scheduler`
  → `backend/tests/scheduler/`；`test_mtbf_suite` → `backend/tests/services/`；
  `test_p3_3_multi_instance` → `backend/tests/realtime/`；`test_saq_scan_pipeline`
  → `backend/tests/tasks/`。

本守卫现在的职责是**终态不变式**：清单为空、agent 测试不得再 import 控制面。
确有必要的例外必须先在此登记并写明理由（含「为何不能迁移/解耦」），且只减不增。

判据（四条，对空清单同样成立）：

1. 清单**外**的文件不得 import 控制面包（``backend.<非 agent>``）——即任何越界都红；
2. 清单**内**每个文件的跨包 import **模块数不得超过登记值**（防清单内漂移）；
3. 条目**失效**（文件不再 import 控制面、或文件已删除）→ 报红，提示删条目；
4. 扫描面非空（目录改名/搬走后不能恒绿）。

终态出口：**已到达**（清单清零）；此后任何新增越界都在 PR 门禁（agent-tests*）外
由本文件拦截。
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
AGENT_TESTS_DIR = REPO_ROOT / "backend" / "agent" / "tests"

#: 存量清单：文件名 → (允许的跨包 import 模块数, 说明)。**已清零**（#739 面① 两批收敛完成）；
#: 现在只允许为空——新增越界必须先迁移/解耦，确有例外才登记并写明理由。
_CONTROL_PLANE_IMPORTS: dict[str, tuple[int, str]] = {}


def control_plane_imports(source: str) -> set[str]:
    """源码里引用的 ``backend.<非 agent>`` 模块集合（静态 + 动态字面量形态）。"""
    tree = ast.parse(source)
    found: set[str] = set()
    for node in ast.walk(tree):
        modules: list[str] = []
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
        elif isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.Call):
            func = node.func
            name = getattr(func, "attr", None) or getattr(func, "id", None)
            if name in {"import_module", "__import__"} and node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    modules.append(first.value)
        for module in modules:
            if module.startswith("backend.") and not module.startswith("backend.agent"):
                found.add(module)
    return found


def _scan() -> dict[str, set[str]]:
    """{文件名: 跨包 import 模块集合}（只收非空项）。"""
    result: dict[str, set[str]] = {}
    for path in sorted(AGENT_TESTS_DIR.glob("*.py")):
        modules = control_plane_imports(path.read_text(encoding="utf-8"))
        if modules:
            result[path.name] = modules
    return result


def test_no_new_file_imports_control_plane():
    """清单外的文件不得 import 控制面——新增越界必须走「迁移/解耦」或显式登记。"""
    scanned = _scan()
    offenders = sorted(name for name in scanned if name not in _CONTROL_PLANE_IMPORTS)
    assert offenders == [], (
        "以下 agent 测试新 import 了控制面模块（清单外，禁止新增）：\n  "
        + "\n  ".join(f"{name}: {sorted(scanned[name])}" for name in offenders)
        + "\n要么把用例迁到 backend/tests/、要么就地解耦；确有必要的，"
        "在 _CONTROL_PLANE_IMPORTS 登记并写明理由。"
    )


def test_listed_files_do_not_import_more_than_registered():
    """清单内文件的跨包 import 数不得增长（清单内同样只减不增）。"""
    scanned = _scan()
    grown = [
        f"{name}: {len(scanned[name])} > {limit}（{reason}）"
        for name, (limit, reason) in sorted(_CONTROL_PLANE_IMPORTS.items())
        if name in scanned and len(scanned[name]) > limit
    ]
    assert grown == [], "以下文件的控制面 import 比登记时更多了：\n  " + "\n  ".join(grown)


def test_no_stale_entries():
    """条目失效（文件已不再 import 控制面 / 已删除）→ 必须删条目。"""
    scanned = _scan()
    stale = sorted(name for name in _CONTROL_PLANE_IMPORTS if name not in scanned)
    assert stale == [], f"清单存在失效条目（请移除，台账只减不增）：{stale}"


def test_scan_surface_is_not_empty():
    """扫描面塌陷（目录改名/搬走）不能长得像「全绿」。"""
    files = sorted(AGENT_TESTS_DIR.glob("*.py"))
    assert len(files) >= 20, f"agent 测试目录扫描异常，只看到 {len(files)} 个文件"


def test_registry_covers_every_current_offender():
    """清单必须**恰好**覆盖当前全部越界文件（漏登记会被上面的用例抓到，这里给出总数）。"""
    scanned = _scan()
    assert len(scanned) == len(_CONTROL_PLANE_IMPORTS), (
        f"实测越界文件 {len(scanned)} 个，清单 {len(_CONTROL_PLANE_IMPORTS)} 条——"
        "两侧必须一致（多出=漏登记，少=有条目已失效）"
    )
