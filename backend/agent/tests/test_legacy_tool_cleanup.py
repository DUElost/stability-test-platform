"""Regression checks for the script-only agent cleanup.

#2639 第二批迁移：本文件的守卫全是**源扫描型否定断言**（读被测文件再判「某词不许复活」）。
这类断言在被扫逻辑搬走后会变成**恒真的空守**——还绿着，覆盖是零。所以每条否定断言前都必须
先声明一个正锚点（`SourceGuard.anchored`）证明「扫的还是那台真机」：锚点找不到 →
`AnchorDrift`（用例已过期，改指新真源），词复活了 → `FormRegression`（防线回归）。
两类红的语义不同，看第一行即可判因（#2639 的立单理由）。
"""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import backend.agent.pipeline_engine as pipeline_engine
from tools.dev.source_anchor import SourceGuard


REPO_ROOT = Path(__file__).resolve().parents[3]

#: 退役词汇表（#735 前置清理：Agent 不再自带工具体系目录）。
_RETIRED_TOOL_TERMS = [
    "PipelineAction",
    "TOOL_CATEGORY",
    "TOOL_DESCRIPTION",
    "/agent/tools/",
    "test_framework.py",
    "test_stages.py",
    "EXTERNAL_TOOL_DIR",
]
#: 每个被扫文件的正锚点——**必须选该文件当前真实存在的语义位**，不是 shebang 那种空壳。
_RETIRED_TERM_ANCHORS = {
    "backend/agent/pipeline_engine.py": "class PipelineEngine",
    "backend/agent/install_agent.sh": "echo_info() {",
    "backend/agent/DEPLOY.md": "# Agent 服务部署指南",
    "backend/agent/scripts/monkey_launch/v1.0.0/monkey_launch.py": "def _resolve_aimonkey_dir",
    "backend/agent/scripts/monkey_launch/v2.0.0/monkey_launch.py": "def _ps_grep",
}
_WATCHER_PLAN_REL = "docs/archive/plans/watcher-consolidate-aee-2026-05-27.md"
_WATCHER_PLAN_ANCHOR = "# Watcher 收编 scan_aee / export_mobilelogs 方案"
_RUN_REPORT_IMPORT = "const RunReportPage = lazy(() => import('../pages/runs/RunReportPage'));"


def _load_monkey_launch(version: str):
    script_dir = REPO_ROOT / "backend" / "agent" / "scripts" / "monkey_launch" / version
    module_path = script_dir / "monkey_launch.py"
    sys.path.insert(0, str(script_dir))
    try:
        spec = importlib.util.spec_from_file_location(f"_test_monkey_launch_{version}", module_path)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(script_dir))


def test_pipeline_engine_no_longer_exposes_pipeline_action_base():
    assert not hasattr(pipeline_engine, "PipelineAction")


def test_removed_tool_catalog_terms_do_not_reappear_in_agent_sources():
    for rel, anchor in _RETIRED_TERM_ANCHORS.items():
        guard = SourceGuard.of_repo_path(rel).anchored(anchor)
        for term in _RETIRED_TOOL_TERMS:
            guard.assert_absent(term, why="工具体系目录已随脚本化改造退役，复活即回归")


def test_legacy_aee_script_directories_are_removed_from_agent_repo():
    legacy_dirs = [
        REPO_ROOT / "backend" / "agent" / "scripts" / "scan_aee",
        REPO_ROOT / "backend" / "agent" / "scripts" / "export_mobilelogs",
    ]

    for path in legacy_dirs:
        assert not path.exists(), f"legacy watcher-pre-mainline script directory still present: {path}"


def test_watcher_only_one_off_plan_script_is_removed():
    path = REPO_ROOT / "backend" / "scripts" / "apply_watcher_only_plan2.py"
    assert not path.exists(), f"legacy watcher rollout helper still present: {path}"

    SourceGuard.of_repo_path(_WATCHER_PLAN_REL).anchored(_WATCHER_PLAN_ANCHOR).assert_absent(
        "apply_watcher_only_plan2.py",
        why="一次性上线脚本已删除，方案文档再指向它就是死链接",
    )


def test_legacy_pipeline_cleanup_sql_helpers_are_removed():
    legacy_sql = [
        REPO_ROOT / "tools" / "sql" / "cleanup_legacy_pipeline_data.sql",
        REPO_ROOT / "tools" / "sql" / "cleanup_pytest_fixture_residue.sql",
        REPO_ROOT / "tools" / "sql" / "cleanup_script_sequence_history.sql",
        REPO_ROOT / "tools" / "sql" / "fix_script_paths_after_flatten.sql",
        REPO_ROOT / "tools" / "sql" / "scan_legacy_action_prefix.sql",
    ]

    for path in legacy_sql:
        assert not path.exists(), f"legacy one-off SQL cleanup helper still present: {path}"


def test_obsolete_sqlite_postgres_dual_write_verifier_is_removed():
    path = REPO_ROOT / "tools" / "verify_dual_write.py"
    assert not path.exists(), f"obsolete sqlite/postgres dual-write verifier still present: {path}"


def test_legacy_postgres_bootstrap_optimize_sql_is_removed():
    path = REPO_ROOT / "deploy" / "postgres" / "init" / "01-optimize.sql"
    assert not path.exists(), f"legacy postgres bootstrap optimize sql still present: {path}"


def test_legacy_frontend_task_details_route_is_removed():
    legacy_paths = [
        REPO_ROOT / "frontend" / "src" / "pages" / "tasks" / "TaskDetails.tsx",
        REPO_ROOT / "frontend" / "src" / "pages" / "tasks" / "TaskDetails.test.tsx",
        REPO_ROOT / "frontend" / "src" / "pages" / "tasks" / "taskDetailsState.ts",
    ]

    for path in legacy_paths:
        assert not path.exists(), f"legacy frontend task details artifact still present: {path}"

    router = SourceGuard.of_repo_path("frontend/src/router/index.tsx").anchored(_RUN_REPORT_IMPORT)
    router.assert_absent("TaskDetails", why="旧 tasks 详情页已下线，路由表里不该还有它的影子")
    router.assert_absent('path="tasks"', why="旧路由已删，留着会把死页面重新挂上导航")
    router.assert_absent('path=":taskId"', why="同上：taskId 段属于已退役的 tasks 路由")


def test_dual_track_phase_c_schema_validator_is_removed():
    path = REPO_ROOT / "backend" / "tests" / "e2e" / "validate_phase_c_state.py"
    assert not path.exists(), f"legacy dual-track schema validator still present: {path}"


def test_run_report_page_no_longer_lives_under_legacy_tasks_directory():
    legacy_path = REPO_ROOT / "frontend" / "src" / "pages" / "tasks" / "RunReportPage.tsx"
    assert not legacy_path.exists(), f"run report page still lives under legacy tasks directory: {legacy_path}"

    current_path = REPO_ROOT / "frontend" / "src" / "pages" / "runs" / "RunReportPage.tsx"
    assert current_path.exists(), f"run report page should live under runs directory: {current_path}"

    router = SourceGuard.of_repo_path("frontend/src/router/index.tsx").anchored(_RUN_REPORT_IMPORT)
    router.assert_absent("../pages/tasks/RunReportPage", why="页面已迁到 runs 目录，回指 tasks 即路径腐化")


def test_legacy_tasks_socket_refresh_path_is_removed():
    """旧 tasks 实时刷新链（后端广播 → 前端事件名 → 缓存键）不得复活。"""
    events = (
        SourceGuard.of_repo_path("frontend/src/utils/socketEvents.ts")
        .anchored("export const SOCKET_EVENT_NAMES = {")
    )
    events.assert_absent("TASK_UPDATE", why="旧 tasks 事件名已随路由一起退役")
    events.assert_absent("taskUpdate", why="驼峰别名与 TASK_UPDATE 同批退役")

    hook = (
        SourceGuard.of_repo_path("frontend/src/hooks/useRealtimeDashboard.ts")
        .anchored("export function useRealtimeDashboard(")
    )
    hook.assert_absent("TASK_UPDATE", why="仪表盘钩子不再订阅已下线的 tasks 事件")
    hook.assert_absent("queryKey: ['tasks']", why="旧缓存键已废弃，复活会让失效逻辑静默指向不存在的查询")

    server = (
        SourceGuard.of_repo_path("backend/realtime/socketio_server.py")
        .anchored("def get_sio() -> socketio.AsyncServer:")
    )
    server.assert_absent("broadcast_task_update", why="后端不再广播 task_update")
    server.assert_absent('"task_update"', why="事件名字面量同批退役")


def test_m1_dual_write_runbook_and_recon_script_are_removed():
    legacy_paths = [
        REPO_ROOT / "backend" / "scripts" / "aee_dual_write_recon.py",
        REPO_ROOT / "docs" / "plans" / "watcher-aee-m1-dual-write-runbook.md",
    ]

    for path in legacy_paths:
        assert not path.exists(), f"legacy watcher dual-write artifact still present: {path}"

    plan = SourceGuard.of_repo_path(_WATCHER_PLAN_REL).anchored(_WATCHER_PLAN_ANCHOR)
    plan.assert_absent(
        "watcher-aee-m1-dual-write-runbook.md",
        why="M1 双写期已结束，runbook 与对账脚本都已删除，文档不得再指过去",
    )
    plan.assert_absent("aee_dual_write_recon.py", why="同上：对账脚本是一次性工件")


def test_watcher_summary_api_tests_do_not_reference_legacy_patrol_scripts():
    """聚合端点用例只能引用在位的脚本名（scan_aee/export_mobilelogs 已被 watcher 收编）。"""
    cases = (
        SourceGuard.of_repo_path(
            "backend/tests/api/test_plan_run_aggregation_endpoints.py"
        )
        .anchored("def chain_setup(db_session):")
    )
    cases.assert_absent(
        'script_name": "scan_aee"',
        why="scan_aee 已退役（#2196 watcher 收编），用例引用它等于断言一个不存在的脚本",
    )
    cases.assert_absent(
        'script_name": "export_mobilelogs"',
        why="同上：export_mobilelogs 已随收编删除",
    )


# `test_legacy_aee_script_names_use_single_shared_source`（跨包共享来源断言）
# 与 `test_legacy_backend_task_schema_module_is_removed` 已随 #739 面① 迁出：
# backend/tests/test_legacy_tombstones.py


def test_agent_runtime_imports_without_backend_package():
    """Hot-update deploys backend/agent as package ``agent`` without backend.core."""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT / "backend")
    code = "\n".join([
        "import agent.main",
        "import agent.registry.script_registry",
        "import agent.aee.db_history",
        "import agent.aee.processor",
        "import agent.aee.reconciler",
    ])
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(REPO_ROOT.parent),
        env=env,
        text=True,
        capture_output=True,
        timeout=10,
    )

    assert proc.returncode == 0, proc.stderr


def test_monkey_launch_resolves_aimonkey_from_env_resource_root(tmp_path, monkeypatch):
    resource_root = tmp_path / "resources" / "aimonkey"
    aimonkey_dir = resource_root / "AIMonkeyTest_20260317"
    aimonkey_dir.mkdir(parents=True)
    (aimonkey_dir / "MonkeyTest.py").write_text("# test fixture\n", encoding="utf-8")
    monkeypatch.setenv("AIMONKEY_RESOURCE_DIR", str(resource_root))

    module = _load_monkey_launch("v1.0.0")
    assert module._resolve_aimonkey_dir({}) == aimonkey_dir


def test_monkey_launch_resolves_aimonkey_from_install_resource_root(tmp_path, monkeypatch):
    import aimonkey_paths

    install_root = tmp_path / "stability-test-agent"
    agent_dir = install_root / "agent"
    aimonkey_dir = agent_dir / "resources" / "aimonkey" / "AIMonkeyTest_20260317"
    aimonkey_dir.mkdir(parents=True)
    (aimonkey_dir / "MonkeyTest.py").write_text("# fixture\n", encoding="utf-8")
    monkeypatch.delenv("AIMONKEY_RESOURCE_DIR", raising=False)

    module = _load_monkey_launch("v1.0.0")
    monkeypatch.setattr(aimonkey_paths, "AGENT_DIR", agent_dir)
    assert module._resolve_aimonkey_dir({}) == aimonkey_dir
