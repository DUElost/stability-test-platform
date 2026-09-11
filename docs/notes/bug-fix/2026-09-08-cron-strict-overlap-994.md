# Cron 严格防重叠裁决与实现（#994 / R06-F09）

Status: implemented
Class: bug-fix

## Decision

**裁决（用户拍板，选项「不允许排队」）**：Cron 窗口遇到同 Plan 尚未结束的 Run
时，**不允许排队执行**——窗口错过即错过。

现行实现两个缺口并存：只查 `RUNNING` 且带 10 分钟 `started_at` cutoff——
排队态（QUEUED/PRECHECK）不阻断 → **积压**；长跑超 10 分钟不再阻断 → **重叠**。
按裁决改为**严格防重叠**：

- `_fire_schedule` 重叠判定：`PlanRun.status in (QUEUED, PRECHECK, RUNNING)` 即
  跳过本窗口（去掉 `started_at` 年龄阈值与 `PATROL_TIMEOUT_MINUTES` 死常量）；
  跳过仅推进 `next_run_at`、不写 `last_run_at`、**不补跑**；
- 抖动去重（同 schedule 60s）与 fail-closed 语义/顺序不变；
- 文档：`docs/design/06-realtime-and-background.md` §3 新增「Cron 防重叠策略」
  小节（两级判定、长跑不豁免、错过不补跑、补跑用 CHAIN/手动触发）；
- 回归：新增 `backend/tests/scheduler/test_cron_overlap_policy.py` **7 例**
  （QUEUED/PRECHECK 阻断且只推进 `next_run_at`、2h 长跑仍阻断、三种终态放行、
  抖动去重先于重叠）。

## Alternatives

- **允许有限积压（上限+过期）**——用户裁决放弃：需定义上限/过期/取消语义，
  实现与参数面更大；
- **严格 + 可选补跑开关**——放弃（可作后续）：新增配置面与补跑状态机，当前无
  明确需求；
- **保留 10 分钟年龄豁免、只补排队态**——放弃：与「长跑不豁免」裁决矛盾，且
  年龄豁免正是原审计的「双跑」缺口之一；
- **保留 `PATROL_TIMEOUT_MINUTES` 常量待用**——放弃：失去唯一用途后是死旋钮
  （同 #1053 教训），删除避免误配；需要逃生阀时再显式引入。

## Verification

实际运行（worktree `/tmp/stp-994`，2026-09-11）：

- `pytest backend/tests/scheduler/test_cron_overlap_policy.py -q` → **7 passed**
  （新增 7 例如上）；
- `pytest backend/tests/scheduler/ -q` → **65 passed**（含既有 scheduler 套件）；
- `pytest tests/ -q` → **154 passed**；
- `ruff check .` → All checks passed；
- `check:quick` → **7 gates 全绿**。

未完成（pending）：无（策略文档与回归同 PR 落地）。

## Revisit

- **卡死逃生阀**：严格策略下，卡在非终态且清理机制失效的 Run 会持续阻断该 Plan
  的窗口。历史设计（archive 2026-03-04）用年龄豁免规避此风险，本轮裁决明确
  去掉。当前依赖 `recycler` / `precheck_reaper` / 任务终态化收口；若生产出现
  真实卡死导致窗口长期阻断的案例，再引入**显式**逃生机制（独立开关 + 告警），
  而不是恢复隐式年龄阈值；
- 若产品上确需「设备空闲后补跑」，按 CHAIN / 手动触发组合设计，不以 cron 积压
  实现。
