"""#739：agent **测试**侧的控制面 import 清单冻结（棘轮）。

与生产侧边界（`tests/test_agent_import_boundary.py`）是不同的两件事：

- **生产代码**越界 → Agent 部署在无控制面的主机上会直接 `ImportError`（真风险，
  已由静态守卫钉死）；
- **测试**里 import 控制面 → 不会炸：套件 conftest 在收集期就 `setdefault` 了
  `DATABASE_URL` / `JWT_SECRET_KEY`（#2428），`agent-tests-collect` 也抓不到
  （2026-09-17 实测：注入越界 import 后仍 2130 全收集通过）。

实测有 14 个 agent 测试文件仍在 import 控制面模块。收敛方式**已裁决**
（2026-09-20，owner）：**分批迁移到 `backend/tests/` + 横跨契约文件就地解耦**。
第一批已完成 7 个——迁移 5 个纯控制面文件（`test_adr0026_params` /
`test_app_scheduler_executors` / `test_leader_election` / `test_logging_setup` /
`test_socketio_redis_adapter` → `backend/tests/{core,scheduler,realtime}/`）、
就地解耦 2 个横跨文件（`test_step_log_batching` 的控制面侧 2 例 →
`backend/tests/realtime/test_step_log_ingest_contract.py`；`test_legacy_tool_cleanup`
的跨包墓碑 2 例 → `backend/tests/test_legacy_tombstones.py`）。**当前剩 7 个**
（大文件为主：`test_saq_scan_pipeline` 1287 行 / `test_cron_scheduler` 408 行），
按同方向分批收敛。

本守卫只做一件事：**冻结现状、不许新增**，并给每一个条目留下「为什么现在还允许」的说明。

判据（四条）：

1. 清单**外**的文件不得 import 控制面包（``backend.<非 agent>``）；
2. 清单**内**每个文件的跨包 import **模块数不得超过登记值**——防止「既然在清单里，
   就再多引几个」这种清单内漂移；
3. 条目**失效**（文件不再 import 控制面、或文件已删除）→ 报红，提示删条目
   （同 `_LEGACY_SEEDS_WITHOUT_REF_CHECK` 的过期判定）；
4. 扫描面非空（目录改名/搬走后不能恒绿）。

终态出口：随各文件迁移或解耦，**逐条删除**本清单——它是存量台账，不是许可。
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
AGENT_TESTS_DIR = REPO_ROOT / "backend" / "agent" / "tests"

#: 存量清单：文件名 → (允许的跨包 import 模块数, 说明)。**只减不增**。
_CONTROL_PLANE_IMPORTS: dict[str, tuple[int, str]] = {
    "test_aee_metadata.py": (1, "待裁决：aee_metadata 归控制面 core/"),
    "test_cron_scheduler.py": (7, "待裁决：控制面 cron 调度（最大的一处）"),
    "test_login_lockout.py": (2, "待裁决：登录锁定属控制面 auth"),
    "test_mtbf_suite.py": (1, "待裁决：mtbf_suite 属控制面 services/"),
    "test_p3_3_multi_instance.py": (3, "待裁决：多实例（SocketIO/调度）属控制面"),
    "test_pipeline_validator_parity_738.py": (
        1,
        "**有意**：#738 的双端 parity 测试——它本来就该同时 import 两份实现",
    ),
    "test_saq_scan_pipeline.py": (5, "待裁决：SAQ/scan 链属控制面"),
}


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
