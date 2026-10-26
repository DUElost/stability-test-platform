# B6-U7 激活后的文档收尾

Status: implemented
Class: process

## Decision

Refs #3732，Refs #3723。按 [#3723 v1.7.25](https://github.com/DUElost/stability-test-platform/issues/3723)
§3.7 U7、§5 第 7–10 条和[第三个 PR 派单](https://github.com/DUElost/stability-test-platform/issues/3732#issuecomment-6412694715)，
将已激活的 B6 行为写成现行规则。激活及 U9/U10 前滚修复、L0–L3 的事实引用
[生效证据](https://github.com/DUElost/stability-test-platform/issues/3723#issuecomment-6412678344)，本 PR 不重新执行生产核验。

基线为 `origin/main@981c85df8c43770f06a26ac1f7f7153352c06fae`。
专属 worktree 为 `.wt/b6-u7-post-activation-3732`，分支为 `docs/b6-u7-post-activation-3732`；
Execution 为 `b6-u7-post-activation-3732`，关联 issue 为 #3732，test impact 为 none。
本 PR 保持 Draft，规划者核对后由 Owner 转 Ready，不增加独立复核环节。

删除与改写清单：

- [开关机手册](../../operations/powercycle-chain-disarm-runbook.md)：删除文首流程选择、旧警告和原 §1–§4 的人工过渡处置；
  原 §5 及 §5.1–§5.3 改为现行 §1 及 §1.1–§1.3，移除旧的待生效条件和保留说明；
  文首用一句指向 ADR-0061 D8 第 6 条与 git 历史。同步正文内全部章节引用。
- [紧急释放手册](../../operations/device-lease-emergency-release.md)：删除文首分流说明与原 §1 的独立流程；
  原 §2 成为现行 §1。把原 §2 所引用的原 §1 共享内容就地纳入现行步骤：回收器 PromQL 与无采样的停止条件、
  释放审计、RELEASED 后的租约及心跳回查；删除所有指向已删章节的内部引用，保持 409、原地转换和生产写授权边界。
- [执行协议](../../design/07-execution-protocol.md)：文首引用已完成的激活记录；§1、§2、§3、§7 删除旧流程分支，
  保留现行规则；§9 及 §9.1–§9.8 去掉待生效标题；§9.5 的待交付清单改指已执行清单。
  §9.2 补齐 U10 停机 outbox 的租约令牌来源、缺令牌跳过规则与已有 20 秒窗口，§9.5 写明 U9 的远期占位常量。
  最后更新日期同步为 2026-10-26。
- [数据模型](../../design/05-data-model.md)：原 §2 的 JobInstance 两列、Host/Device、device_leases、device_return_debt
  及 §3 关系图改为现行陈述；device_return_debt 标题删除时间条件，字段、索引与生命周期语义保持。
- [ADR-0019](../../adr/ADR-0019-android-device-lease-and-capacity-scheduling.md)、
  [ADR-0022](../../adr/ADR-0022-patrol-heartbeat-aggregation.md)、
  [ADR-0043](../../adr/ADR-0043-abort-grace-subject-alignment.md)、
  [ADR-0057](../../adr/ADR-0057-device-retirement-semantics.md)：只更新各文件文首一条后续修订说明的时态；原决策正文逐字保持。
- [运维索引](../../operations/README.md)：激活清单标为已执行（2026-10-25 至 10-26）并链接生效证据；
  紧急释放、开关机排障与执行协议 §9 的介绍改为当前入口。
- [激活清单](../../operations/2026-10-25-b6-device-return-activation-runbook.md)：仅更新文首执行状态、日期和生效证据；
  依据派单 F 项机械修正原第 14 步与末尾的两个入站链接及章节号。步骤顺序、命令、SQL、预期与停止条件保持。
- 新增本 Note。历史 Note 与 `docs/archive/` 不改；全仓检索未发现历史 Note 或归档对本轮旧锚点的入链。

## Alternatives

- 继续保留两个时期的现行分支，会让运维误用已退出的人工清理流程，违反 ADR-0061 D8 第 6 条的出口。
- 直接删除紧急释放手册的旧章节而不迁移共享检查，会留下失效的步骤引用并丢失回收器可用性、审计和心跳回查。
- 本 PR 不扩展 #3801 的服务存活判据或 #3802 的 APK 选择；两项按 #3723 §5 v1.7.25 记录在本批之外。
  版本交错兼容、未接入专项的未验证规则和旧 job 冻结版本仍是现行协议，不因删除流程选择而移除。

## Verification

代码对证（仅阅读当前 main 的仓库文件）：

- `backend/agent/graceful_shutdown.py::_persist_incomplete_disarm_to_outbox`：
  先取 `local_db.get_active_jobs()` 返回的租约令牌，缺失时取 `lease_renewer.get_fencing_token(job_id)`；
  不使用 worker token 或 `active_job_tokens`，均无有效令牌时记录 `shutdown_incomplete_outbox_skip_no_token` 并跳过。
  `shutdown_agent_runtime` 保持 20 秒等待窗口，outbox drain、重试与死信规则未改。
- `backend/services/device_return.py::_DEBT_LEASE_EXPIRES_AT` 为
  `datetime(9999, 1, 1, tzinfo=timezone.utc)`；新建及原地转换都使用此常量。
  `u9b6d3793a01_normalize_debt_lease_expiry.py` 将更晚值归一到同一 UTC 时刻，无状态或租约类型过滤。
  文档未把该占位值当作欠账回收依据。

命令与结果：

- 开工前开放 PR 查询：0 个；`./scripts/project_python.sh tools/dev/ai_work.py status --risk`：rc=0，在窗 0 条。
- `git fetch origin main`、`git worktree add -b docs/b6-u7-post-activation-3732 /home/debian13/stability-test-platform/.wt/b6-u7-post-activation-3732 origin/main`：rc=0；基线见 Decision。
- Registry declare 与 update：rc=0；scope 为本次 10 份文档与新 Note，按独立路径参数完整声明。
- `git grep -n -E '自[ ]B6[ ]激活起生效|激活前' -- <下列 9 份现行文档>`：rc=1，0 命中。
  范围为两个运维手册、07、05、运维 README 与四份 ADR，具体路径见 Decision。
  一次性激活清单按派单 E 项保留历史步骤原文，不把其中的历史时点文字作为现行分流；本 Note 的删除清单也不参与零命中断言。
- `./scripts/project_python.sh /home/debian13/stp-review-evidence/b6-u7-post-activation-3732/check_docs.py`：rc=0。
  离线静态核对 11 个 scope 文件、1809 份 Markdown 与 7 处相关入站锚点；本次文件中的相对路径均存在。
  四份 ADR 对比基线只有修订说明时态变化；激活清单的步骤正文对比只有两处链接变化。
  两个运维手册保留的 SQL/bash/PromQL 代码块均与基线相同，只移动或删除旧示例；本次未运行 SQL。
- `env -i HOME=/home/debian13 PATH=/usr/local/bin:/usr/bin:/bin LANG=C.UTF-8 ./scripts/project_python.sh scripts/run_gates.py check:quick`：rc=0，16 gates profile 通过。
  其中治理面 S1–S15、S5x 与链接检查通过。专属 worktree 不链接 env 文件，不继承 DATABASE_URL；
  schema-at-head 明确 WARN/skip，此跳过不算数据库核验。存量 SyntaxWarning 原样保留。
- `git diff --check`：rc=0。
- CI 以本 PR 最终完整 HEAD 的 required checks 回执为准，交付回告另列；本地成功不代签 CI。

第 1 轮复核 R1 返修：依据[权威复核评论](https://github.com/DUElost/stability-test-platform/pull/3803#issuecomment-6415108811)，
将执行协议 §9.2 的停机函数索引与本 Note 的同名引用改为实际入口 `shutdown_agent_runtime`。
本轮仅修改上述两个文档文件，原 Execution 与完整 scope 保留，运行时代码未改。
`./scripts/project_python.sh -` 的离线 AST 事实核对：rc=0；源码存在 `shutdown_agent_runtime` 与
`_persist_incomplete_disarm_to_outbox`，不存在旧的同名模块函数；文档中的两个函数索引均能解析。
返修后的 `check:quick`、文档静态核对与 `git diff --check` 已按上方命令复跑，均 rc=0。

ADR-0059 自查（仅本次新写或改动的句子）：

- [x] §2：使用控制面、Agent host、PlanRun、设备租约与归还欠账等推荐名；job 与 PlanRun 分开指称，代码标识符保持原值。
- [x] §3：停机令牌与占用规则写明 Agent、控制面或执行者，要求与禁止使用必须/不得；状态和时间记录使用事实陈述。
- [x] §4：紧急释放流程保留授权警告并前置于写操作；PromQL、审计与租约/心跳回查写出查法；
  条件放在动作前，停止条件单独成步，写操作后保留 HTTP、租约状态与审计预期。
- [x] §2.3、§2.4：没有新增术语对照条目；没有为本约定改写未触及的 ADR 原决策或历史材料。

## Revisit

规划者核对本 PR 后由 Owner 转 Ready。收尾 PR 合入后，规划者按 #3723 §5 v1.7.25 完成关单；#3761 保持开放。
本 Note 不授权部署、初始化、生产查询、人工放行或设备操作。
今后若移除版本交错兼容、修改归还语义或再次改变章节结构，另立 Requirement，并重新核对调用点与入站链接。
