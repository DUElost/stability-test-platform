# B8 G8 FAILED 日志交付与受控兼容补跑（#4031）

Status: implemented
Class: feature

日期：2026-11-16　基线：origin/main@73144bf9　批次：#3800
R2 返修：2026-11-17（判据见 #4031「第 2 轮复核后的返修判据」）

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

### 第 1 轮复核返修（R1，绑定 `a4885d420`，两项需修改 + 三项非阻塞）

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
   **R2 起列值改读工具原生 org 报表（见下节），本段「留空」偏离仅保留作历史。**
3. **并发同 key 触发 500（B-3，非阻塞，已顺手修）**：唯一约束冲突从 500 改为
   409（回滚后提示重查复用已登记行），变异自证红。
4. **D9 部分/待校验未经 sweep 断言（B-5，非阻塞，已补）**：新增
   `test_sweep_registers_delivery_and_d9_judges_publish_states`——部分覆盖+已校验
   → sweep 登记 v1、判定 READY_WITH_GAPS；待校验 → 登记 v1、判定
   NOT_READY/`representative_retrying`，并断言统计导出文件落盘。

变异自证（返修 HEAD 上，见 #4034 评论）：M1 13 列表头回潮 → 格式探针红；M2 失败登记
no-op → 路由探针红；M3 去掉 409 映射 → 并发探针红；还原后三文件 23 passed。

### 第 2 轮独立复核后的返修（R2 判据，复核 HEAD `40809ed420`）

R2 实现 HEAD：`71fa97afe070642ee3023dea50f67802a59010a6`（服务、路由、模型与
迁移）；本 Note 与强化探针随后提交，最终 HEAD 与六项 required CI 以 PR #4034
为准。按 #4031「第 2 轮复核后的返修判据」落地；对应独立复核 [评论 6936007187]。

1. **B1 同 key 的尝试归属（需修改）**。原实现下两个并发请求都能读到同一
   FAILED 行并各自复制；A 的迟到失败登记还能把 B 已提交的 COMPLETED 改回
   FAILED。返修：
   - 领取改为 `get_compat_rerun_for_update`：`SELECT … FOR UPDATE` +
     `populate_existing`，锁内重读最新已提交状态；后到者看到 COMPLETED 转
     replay，不再复制。
   - 独立失败登记改为 `guarded_compat_rerun_failure`：行锁内守卫，COMPLETED
     原样返回；`attempts` 低于现值的写回跳过；只推进 status/attempts/error，
     不触碰产物路径与 `completed_at`。
   - 探针：两个独立 PG16 后端并发请求同一 FAILED 行，copy 计数 1、后到者
     `replayed=true`；A 复制失败停在回滚后，B 提交 COMPLETED/2，A 放行后
     终态仍为 B 的 COMPLETED/2（次数、产物路径、`completed_at` 保全）；
     顺序失败重试复用同一行并递增 attempts。
2. **B3 版本绑定（需修改）**。同 key 绑定第一次接受的交付版本：预读 + 锁内
   双重解析（锁内权威）；重试省略版本沿用绑定，即使最新版本已推进；显式另一
   版本在复制前 409；新 key 显式 v2 独立执行。复制、计数、台账与 manifest
   同版本；manifest 增 `binding`（版本、issue/event、每行 dle 与 org 行号）。
3. **B2 现行报表与同行一致性（需修改）**。15 列列值改取已登记轮次的工具原生
   org 报表：`DeliveryVersion.round_ids` → 轮次覆盖记录的 `storage_uri` +
   伴随映射 `mapping_uri`；映射行 `dle_id`/`row`（1 基、含表头）把 org 行绑定
   到发生。
   - `ExpTime`（含采集侧时区串）、`ExpClass`（含 `Java (…)` 展现形）与
     `ExpType/CurProcess/Package/Detail/CausedBy/extraTag/Activity/Rom_Ram/
     Version` 逐字取原生值；原生空列保留为空。
   - 原生材料不可用 → `field_gaps`（signature/dle/reason/fields，可定位到
     发生与字段）；全源可读但存在缺口 → 登记 FAILED（XLS 仍产出供取证）；
     D15 允许的缺源仍按部分成果登记（is_partial + 缺源清单 + 缺口并存）。
   - 代表缺失采用替补时，Path、ExpTime 及其余发生相关字段取自替补的同一
     原生行，修正原代表缺失时的时间错绑。
   - 控制面只读工具输出，不重跑、不改写解析/归并规则（#3800 v1.12 边界）；
     `compat_rerun` 的 xlrd 只读面已登记 #739 台账。

**与 R1 的关系**：R1 的失败登记独立提交与 15 列结构保留；R2 在其上增加尝试
独占、版本绑定与原生列值。R1 声明的「六列留空」偏离自 R2 起只出现在原生材料
缺失形态，且该形态登记 FAILED 或部分成果，不再静默登记为完成。

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

### R1 同探针基线 RED / 新 HEAD GREEN

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

### R2 同探针基线 RED / 返修 HEAD GREEN

固定反例基线 `40809ed420`（第 2 轮复核 HEAD）与 R2 返修 HEAD 使用同一探针
文件；隔离 PG16、临时文件系统、真实服务/HTTP 入口。基线 checkout 中只替换
探针文件，余为基线代码。

```bash
# 基线（detached 40809ed420）
./scripts/project_python.sh scripts/run_pytest.py \
  backend/tests/services/test_b8_g8_compat_rerun.py -q \
  -k "concurrent_retry_single_copy_two_sessions or \
      late_failure_write_preserves_committed_completion or \
      same_key_retry_uses_bound_version_when_latest_advances or \
      same_key_explicit_other_version_rejected_before_copy or \
      full_source_with_native_org_reports_completes_faithful_values or \
      full_source_with_missing_org_report_fails_with_field_gaps or \
      substitute_row_fields_come_from_substitute_occurrence"
# 返修 HEAD
./scripts/project_python.sh scripts/run_pytest.py \
  backend/tests/services/test_b8_g8_compat_rerun.py -q
```

基线 **7 failed / 10 deselected**；返修 HEAD **17 passed**。基线失败原话
（`--tb=line`）：

- 原生列值：`KeyError: 'field_gaps'`（覆盖记录无该字段，原生非空列未进入产物）
- 缺口门控：`assert 'COMPLETED' == 'FAILED'`（覆盖满但值缺失仍登记完成）
- 替补时间：`assert 'Mon Nov 16 10:00:00 2026' == 'Sun Nov 17 10:40:46 CST 2026'`
  （时间仍绑原代表）
- 版本绑定：`assert ['8797543d-…'] == ['c44e7b68-…']`（实际复制 v2 范围）
- 显式 v2：`DID NOT RAISE`（未拒绝）
- 并发同 key：`replayed=false` ×2、attempts 2（PG 后端 83/84，两个独立会话）
- 迟到失败：`assert 'FAILED' == 'COMPLETED'`（已提交行被改回失败）

| 判据 | 基线 `40809ed420` 失败 | 返修 HEAD |
|---|---|---|
| B2 原生列值 | 覆盖记录无字段缺口表达，且原生非空列在兼容产物为空 | 逐字原生值，含时区串与 `Java (…)` |
| B2 缺口门控 | `COMPLETED`（覆盖满但值缺失仍登记完成） | `FAILED` + 可定位 `field_gaps` |
| B2 替补时间 | `Mon Nov 16 10:00:00 2026`（原代表） | `Sun Nov 17 10:40:46 CST 2026`（替补同一行） |
| B3 同 key 省略版本 | 实际复制 v2 范围 | 沿用绑定 v1 |
| B3 同 key 显式 v2 | 未拒绝（200） | 复制前 409，登记行不变 |
| B1 并发同 key | 两个请求均 `replayed=false`，attempts 2（PG 后端 83/84） | 复制计数 1，后到者 replay，两后端 PID 不同 |
| B1 迟到失败 | 已提交行被改回 `FAILED` | 保全 `COMPLETED/2`、产物路径与完成时间 |

### 回归与门禁

本轮（R2）实跑：

```bash
./scripts/project_python.sh scripts/run_pytest.py \
  backend/tests/services/test_b8_g8_failed_delivery.py \
  backend/tests/services/test_b8_g8_compat_rerun.py \
  backend/tests/api/test_b8_g8_legacy_report_scope.py \
  backend/tests/services/test_b8_g7_partition_consumers.py \
  backend/tests/services/test_b8_g6_evidence_publication.py \
  backend/tests/services/test_shadow_scan_rounds.py \
  backend/tests/services/test_shadow_delivery.py \
  backend/tests/api/test_dedup_jira_endpoints.py \
  backend/tests/test_audit_action_retention_guard_3017.py \
  tests/test_api_response_shape_contract.py -q
./scripts/project_python.sh scripts/run_gates.py check:quick
./scripts/project_python.sh tools/dev/check_pr_migrate.py
```

- 定向回归 **210 passed**（含 FAILED 最终轮、D9 sweep、旧报表入口分区校验与
  源保护回归）。
- `check:quick` → **16 gates 通过**；`schema-at-head` 因未配置 `DATABASE_URL`
  跳过（与独立复核同口径）。
- 空库迁移：`check_pr_migrate.py` → alembic upgrade head、schema 比对
  （基线 5 项、新增 0）、seed 身份对拍通过（postgres:16 一次性容器）。
- 六项 required CI：以 PR #4034 最终 HEAD 的 GitHub checks 为准；本地只报告
  已实际运行的命令与结果。

R1 阶段的历史实跑（保留）：

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

- **兼容报表列值来源（R2 后）**：列值取自已登记轮次的工具原生 org 报表，不需要
  控制面重跑工具。若旧分区的轮次 org 报表或伴随映射已被回收，原生值无法复原，
  按判据登记 `FAILED` + `field_gaps`（发生 × 字段），不登记为完成；工具重跑
  （复用已发布的 `stability_Start-Log-Scan`）是将来可选的复原路径，需另立单
  评估，不在本单。阶段二启用前必须确认：轮次 org 报表与 mapping 的留存期覆盖
  I4 保护期，否则兼容补跑会以 `FAILED` 收场。
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
