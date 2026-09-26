"""#739 面①：跨包「墓碑」不变量（自 agent 套件迁出的控制面侧用例）。

两条断言横跨 `api/schemas`、`services`、`core` 与 agent：控制面删旧模块后不得有人
再导出旧符号；`LEGACY_AEE_SCRIPT_NAMES` 必须只有一份共享来源。原挂在
`backend/agent/tests/test_legacy_tool_cleanup.py`（agent 套件因此要 import 控制面），
按 #739 面① 迁到这里——`tests/test_agent_import_boundary.py` 的边界是方向性的
（禁 agent → 控制面），反向 import 合法。台账与迁移批次见
`tests/test_agent_test_import_ratchet.py`。
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_legacy_backend_task_schema_module_is_removed():
    legacy_path = REPO_ROOT / "backend" / "api" / "schemas" / "task.py"
    assert not legacy_path.exists(), f"legacy backend task schema module still present: {legacy_path}"

    import backend.api as backend_api
    from backend.api import schemas as api_schemas

    assert "TaskCreate" not in backend_api.__dict__
    assert "TaskDispatch" not in backend_api.__dict__
    assert "TaskCreate" not in api_schemas.__dict__
    assert "TaskDispatch" not in api_schemas.__dict__
    assert "TaskOut" in api_schemas.__dict__


def test_legacy_aee_script_names_use_single_shared_source():
    from backend.agent.registry import script_registry
    from backend.agent.contracts import aee_state_migration as state_migration
    from backend.api.routes import plans, scripts
    from backend.agent.contracts.legacy_aee import LEGACY_AEE_SCRIPT_NAMES
    from backend.services import script_catalog

    assert plans.LEGACY_AEE_SCRIPT_NAMES is LEGACY_AEE_SCRIPT_NAMES
    assert scripts.LEGACY_AEE_SCRIPT_NAMES is LEGACY_AEE_SCRIPT_NAMES
    assert script_catalog.LEGACY_AEE_SCRIPT_NAMES is LEGACY_AEE_SCRIPT_NAMES
    assert script_registry.LEGACY_AEE_SCRIPT_NAMES is LEGACY_AEE_SCRIPT_NAMES

    assert "_LEGACY_AEE_SCRIPT_NAMES" not in plans.__dict__
    assert "_LEGACY_AEE_SCRIPT_NAMES" not in scripts.__dict__
    assert "_LEGACY_AEE_SCRIPT_NAMES" not in script_catalog.__dict__
    assert "_LEGACY_AEE_SCRIPT_NAMES" not in script_registry.__dict__
    assert not hasattr(state_migration, "migrate_legacy_aee_state_store")


def test_legacy_aee_constants_live_only_in_contracts_package():
    """ADR-0054 D5：常量表只有 contracts/ 一份实现，旧副本与兜底壳已删。"""
    contract = REPO_ROOT / "backend" / "agent" / "contracts" / "legacy_aee.py"
    assert contract.is_file(), f"缺少契约常量表：{contract}"

    for legacy in ("backend/core/legacy_aee.py", "backend/agent/legacy_aee.py"):
        assert not (REPO_ROOT / legacy).exists(), (
            f"legacy_aee 旧副本仍在：{legacy}——ADR-0054 D5 要求删除且不留兜底/再导出壳"
        )


def test_aee_state_migration_lives_only_in_contracts_package():
    """ADR-0054 D5：state_migration 只有 contracts/ 一份实现，旧位置已删。"""
    contract = REPO_ROOT / "backend" / "agent" / "contracts" / "aee_state_migration.py"
    assert contract.is_file(), f"缺少契约实现：{contract}"

    legacy = REPO_ROOT / "backend" / "agent" / "aee" / "state_migration.py"
    assert not legacy.exists(), (
        f"state_migration 旧副本仍在：{legacy}——ADR-0054 D5 要求删除且不留再导出壳"
    )
