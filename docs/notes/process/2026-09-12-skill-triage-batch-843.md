# Skill 台账第三批：triage 类 3 个 skill 落地（#843）

Status: implemented
Class: process

## Decision

用户确认开建（覆盖前批「触发条件门控」的延缓裁决——门控是防膨胀手段而非硬约束，
台账 owner 可直接确认推进）。三个 skill 统一五段结构（触发描述 frontmatter →
前置检查 → SOP → 后置验证 → 踩坑守卫），薄且指向权威来源，未发明新规则：

1. `diagnose-device-stall`：查租约（`device_leases` 只读）→ 查 ADB 端口
   （WSL 5039 / 生产 5037）→ 查心跳 → 按类处置（释放走既有
   `device-lease-release`，避免重复）；守卫=**跳步会把「租约未释放」误判成
   「设备硬件故障」**；
2. `signal-link-health-triage`：`fixable_link_rate < 1.0` / `unlinked_fixable > 0`
   为**真实故障桶**；`not_yet_archived` 高属归档及时性、`link_rate` 非失败率
   （schema `WatcherSignalLinkStatsOut` 注释 + 三口径 note）；修复归
   `signal_link_reconcile` sweep（`backend/scheduler/signal_link_reconciler.py`）；
3. `scan-artifact-gap-triage`：`saq_scan_no_artifacts` / `saq_scan_partial_artifacts`
   告警 → 读 `run_context.archive`（`signaled_jobs` / `pending_jobs` /
   `failed_jobs`）分流**慢 / 坏 host**；守卫=「部分报表优于零报表」是既定取舍
   （`DEVICE_LOG_FLOW_REVIEW_2026-08-09`），整轮重跑不是处置。

## Alternatives

- **维持触发门控等待**：owner 已确认开建——门控服务于防膨胀，本批三项的判据/顺序
  均已固化于代码/文档（非临场推导），具备固化价值；
- **三合一 skill**：三者触发场景与判据彼此独立，分开能让 `description` 与 agent 的
  触发匹配更精确（合一会导致任一场景加载全部 SOP）。

## Verification

- `python tools/dev/check_governance_surface.py --check` 全绿（含 **S7**：三个新
  SKILL.md 的 name 与目录一致、description 非空）；
- 内容逐条对照权威来源核对：`docs/operations/device-lease-emergency-release.md`、
  `backend/api/schemas/plan_run.py`（`WatcherSignalLinkStatsOut` /
  `WatcherArchiveOut` 注释）、`backend/scheduler/signal_link_reconciler.py`
  调度注册、`backend/tasks/saq_tasks.py` 日志键、`DEVICE_LOG_FLOW_REVIEW_2026-08-09`；
- `python scripts/run_gates.py check:quick` 通过。

## Revisit

- 台账候选至此**全部裁定完毕**（建 6：add-api-endpoint / prod-db-readonly-diagnose /
  script-version-lifecycle / 本批 3 个；降级/不建 5；确认不建 3）——#843 满足关闭条件，
  2026-10-03 例行窗口仅保留「新增第 5 个 skill 前先按同一把尺子量」的纪律；
- 若三场景的权威来源发生迁移（如 reconciler 改名/接口变更），按「指向权威」原则同步
  对应 SKILL.md 的引用路径。
