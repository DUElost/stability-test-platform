/** 工具名 → 运维语义标签（与 backend/services/ai_assistant/tools.py 注册表对齐）。 */
export const AI_TOOL_LABELS: Record<string, string> = {
  run_quality_gate: '运行质量门禁',
  run_agent_tests: '运行 Agent 测试',
  run_gov_checks: '运行治理检查',
  scan_script_catalog: '脚本目录扫描',
  test_notification_channel: '通知通道测试发送',
  reload_agent_config: 'Agent 配置重载',
  dispatch_plan_run: '发起 Plan 执行',
  abort_plan_run: '中止 PlanRun',
  retry_plan_run_dispatch: '重试 PlanRun 派发',
  manual_retry_job: '手动重试 Job',
  manual_exit_job: '手动退出 Job',
  trigger_plan_run_archive: '触发 PlanRun 归档扫描',
};
