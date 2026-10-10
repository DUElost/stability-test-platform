# control-plane-deploy §3 忙碌判定修正：以平台 `job_instance.host_id` 为准（#3669）

Status: implemented
Class: process

## Decision

按 #3669 正文（含 2026-10-10 Owner 裁决）只改文档：

- 整条替换 `.claude/skills/control-plane-deploy/SKILL.md` §3 原「判哪些 host 忙要经
  `device.host_id` join / `job_instance.host_id` 在生产上常为空」行，改为「忙碌主机以平台
  判定为准」：批量脚本与热更新接口都按 `job_instance.host_id` +
  `PENDING`/`RUNNING`/`UNKNOWN` 判定，与主机详情 `active_jobs` 同源；执行者不需要另写
  SQL 预查；热更新回 `HOST_HAS_ACTIVE_JOBS` 时执行者不得为推进加 `abort_running_jobs`；
  批量默认跳过忙碌主机，run 结束后重跑同一命令补齐。
- §7 校准表**追加**修正行，不改 2026-09-23 原校准行。
- 在 `2026-09-23-sop-adr0051-2b-rollout-calibration.md` 的 Revisit 末尾追加结案指针。

「不需要另写 SQL 预查」用「不需要」不用「不得」（Owner 裁决）：手工查询没有破坏性，只是没有必要。

## Alternatives

- **维持 09-23 行、另开缺陷修 `job_instance.host_id` 空值**：弃——同日
  `batch_hot_update --direct` 的 `SUMMARY … skipped=37` 已证明当时按该列查出了忙碌主机；
  「按它查得 0 台忙」来自未留档手工查询，无法还原，本单不推测成因。空值回填属独立议题，
  不在本单范围。
- **改 skill 要求执行者先写 SQL 经 `device.host_id` join 预查**：弃——设备换过主机时会把
  job 算到错误主机；也会让读者误以为主机详情 `active_jobs` 不可信。
- **生产只读复核（`stp_ro` 计数）作为关闭门槛**：Owner 降为可选，不阻塞关闭；本会话未连库。

## Verification

- 前检：开放 PR 仅 #3671（reconciler 告警），不触及
  `.claude/skills/control-plane-deploy/SKILL.md`。云端 M2 不 declare（契约 §3.6）。
- 基线：`origin/main@95db0ee7`（实施分支自其上创建）。
- 版本核对方法：本仓 `git rev-parse --is-shallow-repository` → `false`，对象
  `28e24185` 已在本地；未执行 `git fetch --shallow-since=…`。用
  `git show 28e24185:<path>` 核对 issue 背景事实 1–2：
  1. `backend/scripts/batch_hot_update.py`：`--direct` 路径 L114
     `JobInstance.host_id == host.id` 且 `status.in_(PENDING/RUNNING/UNKNOWN)`；有活跃则
     `row["skipped"]="active_jobs"`；L244
     `SUMMARY ok=… converged=… fail=… skipped=…` 与现场输出同形。
  2. 同版本写/查该列：`plan_dispatcher_sync.py` L1134 物化 `JobInstance(host_id=…)`
     （L834 为 `PlanRunHost.host_id`）；`agent_claim.py` L266 `job.host_id = host_id`；
     `host_upgrade_gate.py` L128–133 `active_jobs_for_host` 按 `JobInstance.host_id` 过滤。
- 写作约定：改动的 skill 句子按 `writing-conventions.md` §5 自查；「不需要」保留为 Owner
  裁决用词（表示无必要，非禁止）。Agent Note / 09-23 Note 指针不在约定适用范围。
- 未改代码；未连接任何数据库；生产 SQL 复核未做（可选、不交付）。
- `./scripts/project_python.sh scripts/run_gates.py check:quick` →
  `[OK] check:quick (16 gates)`（本会话实测；`schema-at-head` 因无 `DATABASE_URL` WARN 跳过）。

## Revisit

- 若日检任选生产只读复核查出 `RUNNING` 且 `host_id IS NULL` 非零，另开缺陷单（影响
  `diagnose-device-stall` 第 9 步与热更新闸门）；只贴计数，不贴主机清单。
- 若后续代码改变忙碌判定列或状态集合，须同 PR 更新本 skill §3 与 §7，不得只改一处。
