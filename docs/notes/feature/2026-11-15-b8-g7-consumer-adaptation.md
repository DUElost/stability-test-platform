# B8 G7 消费方适配（#3990）

Status: implemented
Class: feature

日期：2026-11-15　基线：origin/main@4807758e　批次：#3800

## Decision

按 ADR-0062 D13/D16 把「实际 PlanRun × 平台」带进消费方，同时保持无模式存量与
现行分区的行为不变：

1. **分区事实只经 G1 服务层读**。新增 `backend/services/partition_scope.py`：
   分区清单 = 已登记模式 ∪ 运行内设备平台 ∪ DLE 平台；缺记录按 CURRENT。
   该模块只读，提供归档判据、交付视图、能力与上传口径。
2. **extract 只作用于 CURRENT**（D13 第 4 条）。`run_extract_sync` 在存在模式记录
   时按 CURRENT 平台过滤 DLE 与 merge 报表；只有先去重分区返回 `-3`（路由 409）。
   无模式存量不加过滤，保持 `#766` 的双平台行为。
3. **归档判据按分区**（D16）。先去重分区以交付状态 READY / READY_WITH_GAPS 为完成
   （不要求 merge 产物或 extract）；现行分区沿用 `#1110` 的 merge + extract。
   有任一先去重分区时，其余 CURRENT 分区仍须满足现行判据；两侧互不伪装。
4. **下载按登记定位**（D9 第 2、6–8 条）。DLE 代表证据优先走 G6 已校验发布记录
   （`evidence/<主文件夹>/<SN>/<事件目录>`），`DLE.remote_path` 只留作溢出补扫；
   新增统计导出与证据下载入口，均按 `root_plan_run_id` 校验链根，跨 run 404。
5. **统计口径统一到发生身份**（#3886）。`log_observation` 的 DLE 计数由
   `COALESCE(remote_path, local_path)` 改为
   `COALESCE(occurrence_key, 'dle:' || id)`：未被选中的发生（只有 local_path）、
   代表上传、HddSpill 与路径改写都不再改变同一发生的计数，也不会让累计值下降。
   watcher-summary 的 AEE 事件集补入「有 DLE、无对应 signal」的发生（按
   `device_log_event_id` / basename 去重），摘要与详情同源。
6. **留存遵守 I4/D11**。retention 在删行前查询「代表发布 PENDING/RETRYING」或
   「FINAL/SUPPLEMENT 轮未关闭」的 run，整批推迟；链族 `evidence/` 不在
   `RUN_FAMILIES`，单 run 清理永不删除它；measure 脚本增加链族容量分解。

### 第二轮返修（规划侧 #3800 探针，基线 `b28ee8f3`）

规划侧在隔离 PG16 加挂两条验收探针，基线全红，暴露两处服务层误判：

1. **混合运行归档误判完成**：MTK 先去重交付已就绪、run 有旧 extract 记录，
   但唯一的 merge 报表属于 MTK，展锐现行分区没有自己的 merge，`archive_complete()`
   仍返回 True。返修：`_legacy_archive_complete(platforms=...)` 在混合运行时按
   `scan_artifact_uri_platform` 归类 `merge_result_xls` 的分区路径，要求**每个**
   CURRENT 分区都有本分区报表；无模式存量分区仍走 #1110 的「任意一份报表 +
   extract」旧判据（查询形态不变，旧的 mock 单测不受影响）。真实
   `auto_archive_sweep` 入口验证：缺展锐报表继续入队现行侧，补齐后不再入队。
2. **共享代表被重复计数**：两个问题共用同一 DLE，台账只有一条 VERIFIED
   发布记录，`representatives.published` 却为 2。返修：`_representative_counts`
   把选择关系（问题 × DLE）与证据状态（每 DLE 一条）分开——`selected` 保留
   选中的问题数（2），`published` / `pending` / `failed` 按代表 DLE 去重（一份
   证据只算一次）。四种共享形态（无记录 / PENDING / FAILED / VERIFIED）各有断言。

### 统计口径（同 PR 交付）

| 数字 | 含义 | 来源 |
|---|---|---|
| 发生数 `occurrences` | 本分区全部发生，含未选为代表 | `issue_occurrence`，按 `occurrence_key` 去重（无 key 按行），`facts.count_partition_occurrences` |
| 设备数 `machines` | 发生涉及的设备 | `issue_occurrence.serial` 去重，`facts.count_partition_machines` |
| 已发布代表 `representatives.published` | 已校验代表证据 | `evidence_publication.state = VERIFIED` ∩ SELECTED，**按代表 DLE 去重**（多问题共享一份证据只算 1） |
| 已选 `representatives.selected` | 选中的问题数 | `representative_selection` 中 SELECTED 的问题数（共享 DLE 时仍为 2） |
| DLE 观察数 `observed_events` | 仅现行分区展示，供对账 | `device_log_event` 按 `occurrence_key`/DLE id 去重（`partition_scope.observation_counts`） |
| AEE 风险桶次数 | 观测面计数 | `log_observation` 同一发生身份口径 |
| 上传汇总 `by_platform` | 按平台镜像顶层键 | `device_log_event.platform` 分组，pending/remote 与顶层同义 |

路径数量不再冒充发生数量：路径只用于定位，计数键是发生身份。

### D16 逐行去向

| D16 消费方 | 本单状态 | 代码 / 验证入口 |
|---|---|---|
| `artifact_paths` 证据区校验 | G6 已有，G7 消费 | `resolve_evidence_event_path`；`test_evidence_download_rejects_cross_root` |
| DLE 回执白名单 | G6 | `agent_device_log_events`（未改） |
| DLE 下载 / 产物下载 | **本单** | `device_log_event_download`（发布记录优先）、`delivery_download` + dedup 路由；`test_dle_download_uses_verified_publication`、`test_delivery_export_download_and_cross_run` |
| extract / 手动 extract / extract_bundle | **本单** | `dedup_extract.run_extract_sync`、`trigger_extract`；`test_mixed_run_extract_only_current_partition` |
| JiraRun 与提单工具 | G8 | 未改 |
| `auto_archive_sweep` | **本单** | `cron_scheduler._terminal_archive_complete` → `partition_scope.archive_complete`；`test_dedup_first_ready_completes_without_extract` 等 |
| 前端三处 | **本单**（UI 不要求独立复核） | `DedupReportCard` 分区行 + extract 禁用；`LogEventsCard` 按 `delivery_evidence` 放行 PRUNED 证据下载；`types.ts` 同步 `DedupPartition` / `delivery_evidence`；jsdom 用例三条 |
| `storage_families` 与清理 | G6 登记 + **本单清理** | `EVIDENCE_FAMILY` 不在 `RUN_FAMILIES`；retention I4 保护；`test_single_run_purge_keeps_chain_evidence_and_other_run` |
| 未归属事件认领 | G3/G6 | extract 的目录搬移只作用于 CURRENT，避免移动先去重的溢出源 |
| `measure_center_storage` | G6 总量 + **本单链族分解** | `collect_chain_usage` / `chain_family_usage` |
| HddSpill | G6（D2 第 3 条 / I2） | 未改 |
| 报表 Path 重写 / basename | 现行路径保留；先去重分区不执行现行 merge（D14） | `run_merge_for_partitions_sync`；伴随映射由 G3/G4 提供 |
| 手动 merge | **本单** | `run_manual_merge_sync`（CURRENT 走现行窗口，DEDUP_FIRST 走已关闭轮次的幂等影子合并）；路由测试沿用 #3935 用例 |
| 上传汇总 / 状态 / 类型 | **本单** | `summarize_upload_states.by_platform`、`DedupStatusOut.partitions`、`types.ts` |
| PlanRun 能力目录 | **本单** | `plan_run_catalog.build_plan_run_detail` + `partition_scope.run_capabilities`（`extract` / `partition_modes`） |
| `log_observation`（§1.1 新发现） | **本单** | 发生身份计数（见上表） |

## Alternatives

- **在路由里分别判断分区**：会造成 routes / scheduler / catalog 三套判断漂移；
  收在 `partition_scope` 一处，测试直接打服务层。
- **让 watcher-summary 完全改读 DLE**：DLE 没有 signal 的 package / entry_origin
  元数据，会把 baseline/runtime 拆分变成猜测。采用并集 + 保守置空 `entry_origin`。
- **给 `delivery_download` 加 C2 豁免**：.importlinter 的 ignore 是只减不增的
  违规基线；改为服务只定位、路由构造响应。
- **自动链也触发影子合并**：影子轮由 `sweep_shadow_scan_rounds` 按轮次推进，
  手动入口只补「人对当前轮次的重跑」，不在自动链里叠第二条触发路径。
- **链级自动清理**：D11 要求清理前单独取得 Owner 确认；本单只保证不会被
  run 级清理误删，不新增删除入口。

## Verification

- 新增 `backend/tests/services/test_b8_g7_partition_consumers.py`（31 项，含规划侧
  2 条探针 + 共享代表 4 形态 + 真实 `auto_archive_sweep` 入口）：
  混合/纯先去重/存量 extract、READY / READY_WITH_GAPS / 重试中、两侧独立、
  下载与跨 run / 跨根拒绝、I4 推迟、单 run 清理保留链证据、发生计数稳定、
  DLE-only 进入观测面、上传汇总与分区视图、能力目录。
- 基线反例（逐项红→绿，恢复后 31/31 绿）：
  1. 归档判据退回「只认现行」→ READY / READY_WITH_GAPS 两例失败；
  2. extract 去掉平台过滤 → 混合运行多拷 1 个事件，用例失败；
  3. 计数退回路径口径 → 同一发生两条路径被计 2 次，用例失败；
  4. 关掉 I4 保护 → retention 用例失败；
  5. 第二轮：关掉「CURRENT 分区本分区报表」校验 → 2 条归档探针红，恢复绿；
  6. 第二轮：去掉代表 DLE 去重 → 5 条共享代表计数用例红，恢复绿。
- 规划侧固定反例基线 `b28ee8f3fab611af03185f3a00a6c14f3854eda6`：挂上探针后
  2 failed（`assert True is False`、`assert 2 == 1`），返修后 7/7 绿。
- 受影响回归 492 项全绿；`frontend` vitest `DedupReportCard` 18 +
  `LogEventsCard` 16 = 34 项全绿；`npm run type-check` 通过。
- `python scripts/run_gates.py check:quick` 16 道门全绿。
- 完整 `scripts/run_pytest.py tests/`：2539 passed, 18 skipped（EXIT 0）。
- 六项 required CI 全 pass。
- `pr-backend-tests-informational` 复跑暴露 1 个假会话回归
  （`test_b8_g0a_storage_roots` 缺 `platform`）：DLE 下载改为按
  `(plan_run_id, dle_id, VERIFIED)` 定位发布记录并同步测试桩（`6ce195625`）；
  同期合入 origin/main 消除落后分支的 tool-manifest 假红。

## Revisit

- **watcher-summary 的 package 归属**：DLE-only 发生没有 package 元数据，当前记
  `unknown`；若 #3886 要求按包名逐条对齐，需要 DLE 侧补 package 或从 exp_main
  反查（受 `_resolve_dashboard_local_aee_dir` 可读性限制）。
- **纯先去重运行的自动链**：`auto_archive_sweep` 在交付未就绪时仍会入队一次现行
  终端 scan（现行 merge 已被过滤、extract 返回 -3），属于无效但安全的工作；
  阶段二观测到频次后再决定是否在 sweep 层跳过。
- **兼容补跑**：D15 的补跑源与结果登记属于 G8，本单只保证 I4 期间不回收。
- **链级清理入口**：若后续要提供，应作为独立运维单元，带 Owner 确认与链根参数。
