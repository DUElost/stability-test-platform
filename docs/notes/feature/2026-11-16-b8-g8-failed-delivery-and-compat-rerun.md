# B8 G8 FAILED 日志交付与受控兼容补跑（#4031）

Status: implemented
Class: feature

日期：2026-11-16　基线：origin/main@73144bf9　批次：#3800

## Decision

按 #4031 当前正文与 ADR-0062 v1.2 修订交付三项能力。工具集成只涉及
`stability_Start-Log-Scan`；Jira 提单工具、D3 首次模式写入、Agent 心跳能力守卫
和生产启用不在本单内。

1. **FAILED 分区继续完成 D9 交付（D15 第 1、3 条）**
   - `list_shadow_scan_run_ids` 把「已有 DEDUP_FIRST 模式记录、但尚无任何轮次」
     的终态 run 纳入候选；没有模式记录的终态 run 仍要求已观察到轮次，不追溯历史。
   - `_advance_run` 允许上述分区在无既有轮次时建立 FINAL 轮；已有模式记录时
     不再被 `record_partition_mode(CURRENT)` 覆盖。
   - `sweep_evidence_publications` 纳入 `list_dedup_first_delivery_due_run_ids`，
     使 NO_INPUT、零代表发布记录和发布回执终态后的分区都能登记 D9 交付版本，
     不必依赖 EvidencePublication 是否已存在。
   - 四种结果沿用 G5 的 D9 顺序判定：NO_INPUT / 部分覆盖 / 代表待校验 /
     已校验分别落 NOT_READY(`latest_merge_no_input`)、READY_WITH_GAPS、
     NOT_READY(`representative_retrying`)、READY。补充轮、迟到输入与入队中断
     后的重新入队保持原有服务入口。

2. **受控兼容补跑（D15 第 5–7 条）**
   - 新增单数表 `compat_rerun`（迁移 `b8g8c1d2e3f4`，down_revision
     `b8g6d0e1f234`）与 `backend/services/compat_rerun.py`。
   - 仅 Owner/admin 可对单个 DEDUP_FIRST 分区发起；源顺序为中心证据区已校验
     代表 → DLE `remote_path` 中心目录 → 中心溢出副本 → I4 保护期内的 Agent
     本地原件（`local_source_roots` 是控制面读取本地原件的集成点）。PRUNED
     事件绝不回读本地原件；`DLE.state`、原 `DeliveryVersion`、证据发布记录
     与累计发生数都不写入。
   - 产物为独立目录下的 `Result_MergeFiles_Compat_*.xls`（与现行 merge 相同的
     `aeeexp` 表头）与 `compat_manifest.json`（源记录、覆盖、缺失清单、
     `is_partial`）。按 `(plan_run_id, platform, request_key)` 幂等：重复请求
     返回原登记；失败后同 key 重试复用同一行并递增 `attempts`。
   - 报表计数按所选 `DeliveryVersion` 的 `issue_ids` / `event_ids` 重算，
     不读取本分区全部历史发生；v1/v2 不串读。

3. **既有报表入口按分区限定（D10、D16）**
   - 新增 `backend/services/legacy_report_scope.py`，在
     `POST /api/v1/jira/runs` 的 `source=plan_run` 路径启动工具前校验：
     产物属于请求的 actual PlanRun、类型为 scan/merge 报表、URI 能确认平台、
     且该平台为 CURRENT。混合运行只放行 CURRENT 侧；纯 DEDUP_FIRST 拒绝；
     无模式存量沿用原规则。无法确认平台的扁平 URI 在已有模式记录时拒绝。
   - G7 的 `run_extract_sync` 现行分区范围未改动，本单以回归覆盖确认：
     纯 DEDUP_FIRST 返回 -3，混合运行只取 CURRENT 侧，无模式存量不加过滤。

### 第二轮返修（独立复核固定 HEAD `a4885d420`，两项需修改 + 三项非阻塞）

独立复核结论「需修改」，绑定 `a4885d420`。返修与自证：

1. **失败登记被路由回滚（B-1，需修改）**：路由对 `CompatRerunError` 回滚主事务
   （`get_db` 不 commit），服务在主事务里写的 FAILED 与 attempts 一并丢弃，
   同 key 重试会新建行。返修：服务失败分支先回滚主事务，再用独立会话
   `_record_failed_registration` 以同 key 幂等落库 FAILED 终态；路由回滚不再
   影响登记。新增真实 HTTP 路由探针 `test_route_failure_registers_and_retry_reuses_row`
   （copy 注入 OSError → 500 → 台账 FAILED attempts=1 → 同 key 重试 → 200、
   同一行 attempts=2、台账仍 1 行）。
2. **兼容报表列头/列值不符现行格式（B-2，需修改）**：现行 merge 产物为
   **15 列**（对拍 run 1216 真实产物：`Id, Path, Version, ExpTime, ExpClass,
   "ExpType ", CurProcess, Package, Detail, CausedBy, extraTag, Count, Activity,
   DeviceCount, Rom_Ram`；第 6 列带尾随空格、第 14 列是 DeviceCount，org 报表
   同位置是 DeviceId），此前实现钉死了自造的 13 列（`DeviceId`/`Device Count`）。
   返修：`DEDUP_XLS_HEADERS` 改为逐字 15 列；`Id` 改 0 起行序（下游按行序建
   members 映射）；`Count`/`DeviceCount` 按所选交付版本的事实集合；`Path` 副本
   保留 aee 采集布局（`{采集目录}/{serial}/aee_exp/{事件目录}`，消费方可从 Path
   反解 serial/entry，与 `.claude/skills/monkey-aee-dedup-handoff` 的
   `parse_target` 一致；`ExpTime` 用 asctime(UTC)。仓库内消费方
   `05_make_jira_form.py` 本就按真实 15 列取数，无需改动。
   **列值偏离（登记，见下）**：G1 事实层只持久化工具签名
   `{exp_class, package, caused_by, caused_by_sha256}`（membership sidecar 证据），
   `ExpType/CurProcess/Detail/Activity/Rom_Ram/extraTag` 只存在于工具输出
   （org xls）与事件原文，平台按「平台不复刻工具规则」（#3800 v1.12）留空；
   `ExpClass` 写签名归一形（`JE`/`ANR`），`JE` 族缺现行展现形 `Java (…)`；
   `ExpTime` 无采集侧时区。列值复原的三条出路已一屏呈给规划者裁决。
3. **并发同 key 触发 500（B-3，非阻塞，已顺手修）**：唯一约束冲突从 500 改为
   409（回滚后提示重查复用已登记行），变异自证红。
4. **D9 部分/待校验未经 sweep 断言（B-5，非阻塞，已补）**：新增
   `test_sweep_registers_delivery_and_d9_judges_publish_states`——部分覆盖+已校验
   → sweep 登记 v1、判定 READY_WITH_GAPS；待校验 → 登记 v1、判定
   NOT_READY/`representative_retrying`，并断言统计导出文件落盘。

变异自证（返修 HEAD 上，见 #4034 评论）：M1 13 列表头回潮 → 格式探针红；M2 失败登记
no-op → 路由探针红；M3 去掉 409 映射 → 并发探针红；还原后三文件 23 passed。

### 与已退回 #4026 的关系

只复用属于本单的服务形态：`shadow_scan_rounds` / `issue_fact_store` /
`evidence_publication` 的 DEDUP_FIRST 发现修复、`compat_rerun` 表与服务骨架、
`write_dedup_xls`。未复用 Jira 交付绑定（`jira_delivery.py`、`jira_run` 迁移列、
`jira_occurrence_link`）、`openpyxl` 依赖与夹具，也未保留其 `jira_run_id` 字段。
复用时修正了 #4026 的版本串读问题：报表 Count 由所选版本的 scoped occurrence
集合计算，而不是当前分区的全量统计。

## Alternatives

- **直接复用 #4026 全量分支**：否决。该分支把厂商提单适配与新交付版本绑定作为
  G8 交付门槛，#4031 已把工具范围收窄到 `stability_Start-Log-Scan`；保留会引入
  本单不需要的依赖、迁移列和公开夹具。
- **用链根主文件夹推断报表归属**：否决。D9 第 7 条与 D10 要求按 actual PlanRun ×
  平台确认；本单用产物 URI 的平台段确认，无法确认即拒绝，不回落猜 mtk。
- **兼容补跑直接读取 Agent 本地路径**：否决。控制面不能假定能读 Agent 磁盘；
  服务保留显式 `local_source_roots` 映射作为集成点，生产侧如何提供该映射随
  批次激活另行验收。
- **让兼容补跑覆盖原交付版本**：否决。D15 要求保留原交付登记与累计发生数；
  本单只新增独立产物和登记。

## Verification

### 同探针基线 RED / 新 HEAD GREEN

隔离 PG16（`scripts/run_pytest.py` 默认 testcontainer）+ 临时文件系统 + 合成夹具，
三个新测试文件使用同一组入口：

| 探针文件 | 基线 `73144bf9` | 新 HEAD |
|---|---|---|
| `test_b8_g8_failed_delivery.py` | 4 failed（无轮次 FINAL 未建立、无输入未登记交付、中断未恢复、补充轮未产生） | 6 passed |
| `test_b8_g8_compat_rerun.py` | 8 failed（无服务/路由；路由 404） | 8 passed |
| `test_b8_g8_legacy_report_scope.py` | 4 failed（DEDUP_FIRST 与跨 run 产物仍启动工具） | 5 passed |

基线合计 **16 failed / 3 passed**，新 HEAD **19 passed**。基线通过的 3 项是
D9 既有判定、CURRENT 分区不追溯与无模式存量兼容，属于「不应改变」的对照，
不是修复判别项。

### 回归与门禁

- 受影响回归：`test_shadow_scan_rounds`、`test_shadow_scan_send_3875`、
  `test_shadow_scan_settled_3875`、`test_b8_g6_evidence_publication`、
  `test_b8_g7_partition_consumers`、`test_issue_fact_store`、
  `test_dedup_jira_endpoints`、`test_dedup_scan_endpoints`、
  `test_dedup_extract`、`test_dedup_scan_merge` → **276 passed**。
- 仓库级离线子集：`scripts/run_pytest.py tests/ -q` → **2555 passed, 18 skipped**。
- `scripts/run_gates.py check:quick` → **16 gates 通过**。
- 空库迁移：`tools/dev/check_pr_migrate.py` → alembic upgrade head、schema 比对
  （基线 5 项、新增 0）、seed 身份对拍均通过（postgres:16 一次性容器）。
- 六项 required CI：`lint` / `CodeQL` / `pr-typecheck` / `pr-compileall` /
  `pr-agent-tests` / `pr-migrate-empty-db` 以 Draft PR 的 GitHub checks 为准；
  本地只报告已实际运行的命令，未把本地结果当作 CI 通过。

## Revisit

- **兼容报表列值复原待规划者裁决**：忠实 15 列值需要工具参与（控制面重跑
  `stability_Start-Log-Scan` 的 scan/merge，或工具仓提供 compat 导出模式），
  或接受本单的降列值报表（列头齐、可复原列忠实、其余空）。已一屏呈交
  （#4031），阶段二启用前需要结论；在此之前 compat 报表只保证列头/行序/
  事实列与现行产物一致。
- **D3 首次模式写入**：本单只处理已有 DEDUP_FIRST 模式记录的 FAILED 分区；
  首次写入由批次方案另行验收。若激活时 D3 尚未落地，需要先在真实 run 上建立
  模式记录，再验证本单的 FINAL 轮路径。
- **Agent 本地原件传输**：兼容补跑的服务接口已接受 `local_source_roots`，但生产
  控制面如何取得 Agent 本地目录、以及该动作是否走 Agent 任务，属于阶段二启用面。
  当前线上默认只能用中心证据/溢出副本；缺本地源时如实记 `is_partial` 与缺失清单。
- **Jira 提单绑定**：按 #4031 与 ADR-0062 v1.2 不在本单；既有 `source=upload` /
  `source=plan_run` 手工入口保留，本单只收紧后者的分区边界。
- **前端消费**：新增两个响应模型暂登记在 `_MODEL_UNREGISTERED`（Owner/admin 运维
  触发面，无 SPA 消费者）；接入页面时应在 `types.ts` 建镜像并转登记 `_MODEL_PAIRS`。
- **compat_reruns 目录的留存登记（B-6）**：`{log_root}/compat_reruns/` 尚未进
  `storage_families` 与按链清理（I4 单位之外的派生产物）；D16/阶段二登记，
  登记前该目录只增不清，容量由水位告警兜底。
- **Agent 本地原件源的生产通路（B-7）**：路由不暴露 `local_source_roots`，
  生产入口当前只能用中心已校验证据/中心目录/溢出副本；缺本地源时如实
  `is_partial` + 缺失清单。激活前置必须确认：本地原件通道（Agent 任务或
  主机侧导出）如何供给，否则部分源是常态而非例外。
- **PENDING/RUNNING 分支**：服务同步执行、登记随主事务提交，PENDING/RUNNING
  行只在进程于工作阶段崩溃时可能出现（未提交即回滚，实际不可持久）；保留
  分支为将来异步执行留语义，不引入 running 锁。
