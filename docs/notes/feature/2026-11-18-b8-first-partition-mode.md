# B8 阶段二 #4043 首次分区模式写入

Status: implemented
Class: feature

日期：2026-11-18　交付：Draft PR，待按 ADR-0058 D8 独立复核　基线：`origin/main@e7325af03`
批次：[#3800](https://github.com/DUElost/stability-test-platform/issues/3800) §2.1 第 1 项 / [ADR-0062 v1.2](../../adr/ADR-0062-device-log-dedup-before-upload.md) D3 第 1 条、D13

## Decision

1. **一个决策入口**：新增 `backend/services/partition_mode_admission.py`。控制面在分区
   第一个受运行模式影响的动作之前调用 `fixate_partition_mode*`，判定与落库同处完成；之后所有
   入口只读那条记录分流（D13 第 2 条）。`decide_partition_mode` 只判定不落库，给测试与
   复核者一个「为什么是这个模式」的读接口（原因码：`owner_admitted` /
   `not_in_owner_whitelist` / `dedup_first_switch_disabled` / `capability_unverified` /
   `old_chain_side_effects` / `shadow_phase_disabled` / `no_expected_mtk_hosts` /
   `non_mtk_partition` / `already_recorded`）。
2. **入口与首个受影响动作**（本次全部前移到分流之前）：

   | 入口 | 该入口的第一个分流动作 | 固化点 |
   |---|---|---|
   | `shadow_scan_rounds._advance_run` | 登记轮次（D3 第 2 条） | `shadow_scan_rounds.py:189` |
   | `shadow_scan_rounds.prepare_shadow_scan` | 提交后返回下发载荷（同事务） | `shadow_scan_rounds.py:385` |
   | SAQ `scan_task`（自动与手动 scan 共用） | 向 Agent 下发 `scan_now` | `saq_tasks.py:473` |
   | SAQ `upload_task` | `UPLOAD_PENDING` 标记 UPDATE | `saq_tasks.py:692` |
   | 手动 merge 路由 | `partition_rows` 决定要不要「先跑 scan」 | `api/routes/dedup.py:727` |
   | merge 服务（自动 merge_task 与手动合并共用） | `_partition_targets` 的平台分流 | `dedup_scan.py:807` |
   | extract（自动 extract_task 与手动路由共用） | 按 CURRENT 平台过滤 | `dedup_extract.py:240` |

   DLE 登记（D2 第 1 条）不随模式变化，因此不是分流动作、不触发固化；Agent 恢复循环读取
   `dedup_first` 标记（G6）与 `legacy_report_scope` 的 D10 报表守卫都只读既有事实：前者只在
   `upload_task` 标记之后才有对象，后者不读开关值（D10 自己区分「有记录/无记录」），故本次
   不在这些读取点写模式。`GET /dedup/status` 与前端视图同理。
3. **Owner 白名单按实际 PlanRun**：`STP_DEDUP_FIRST_PLAN_RUN_IDS`（逗号/空白分隔的实际
   `plan_run.id`）+ `STP_DEDUP_FIRST_ENABLED`（默认关闭）。解析失败即报错，不把「配错的试点
   run」静默当成「没批准」。阶段二开关必须与阶段一 `STP_SCAN_ROUND_SHADOW_ENABLED` 同开，
   由 `SchedulerSettings` 的 model_validator 拒绝半开配置——轮次调度、交付登记与回收都在影子
   开关之下，只开阶段二会让我方刚拒绝旧链标记的事件无人处理。
4. **能力证据单一来源**：新增 `backend/services/partition_capability.py`。准入用
   `verify_expected_mtk_hosts` 核验该分区**全部**预期 MTK host（期望集与 D5 的
   `scan_completeness` 同源），影子下发守卫 `_shadow_capability_verified` 改为委托同一函数
   （Owner 裁定 6975368043「模式准入和发送守卫共用可核验能力证据」）。现行判据仍是 #3875
   v1.23 的过渡预筛；#4044 换成当前心跳显式上报时只改这一个模块。
   任一 host 未证实 → 硬阻断：**既不写 `DEDUP_FIRST`，也不写 `CURRENT`**。能力是瞬时事实，
   而模式一旦写下不可改写，一次心跳抖动不得把获批 run 永久锁在旧链上。
5. **在途旧 run 的可判定信号**：旧链专有的库内痕迹——本分区（路径段 `mtk` 或归属无法确认，
   fail-closed）的 `scan_result_xls` / `merge_result_xls` 产物，以及旧链写的 `run_context`
   段 `archive` / `upload_mark` / `merge_platforms` / `extract`。命中即固化 `CURRENT` 并记
   原因，两套链不混跑。DLE 的 `state` / `remote_path` **不**作信号：`DEDUP_FIRST` 分区的代表
   与 HddSpill 溢出副本（I2）本来就会产生这两种值，拿它们判旧链会误封。
6. **落库范围**：获批分区无论放行还是被拒都留下记录（决定必须可审计，之后白名单变动也不
   翻转它）；未获批 / 开关关闭的分区只有影子入口写 `CURRENT`——那是主干既有行为（#3875 的
   settled 出口把终态记在 `partition_mode` 行上），其余入口不写，消费方继续按 D13 第 2 条把
   「无记录」读作现行，避免按开关回读改写存量分区、也避免给无平台归属的历史 DLE 收窄 extract。
7. **并发首写**：`plan_run` 行锁（与影子轮同口径）+ 新增
   `issue_fact_store.insert_partition_mode_if_absent`（`uq_partition_mode_run_platform`
   ON CONFLICT DO NOTHING 后回读）。同一分区并发只留一条记录，后到的写入者拿到先提交的决定并
   据此分流；改写仍由 `record_partition_mode` 显式拒绝。

## Alternatives

- **只把影子轮的 `CURRENT` 改成 `DEDUP_FIRST`**：否决。基线探针证明旧链的 scan/upload/merge/
  extract 独立进入处理链且早于影子轮：下发 `scan_now` 与标记 `UPLOAD_PENDING` 时库里没有任
  何模式记录，旧链已按「无记录=现行」完成分流。
- **在共享读视图 `partition_rows` 里顺带固化**：否决。它被状态 API、前端列表与巡检读取，读操
  作会写库并对 `plan_run` 取行锁；这正是 D13 第 2 条禁止的「按开关当前值回读改写存量分区」。
- **能力未证实就写 `CURRENT`**：否决（见 Decision 4）。
- **按 `started_at` / 白名单生效时间推断「新 run」**：否决。Owner 裁定是按实际 run 点名，
  推断既不可审计，也会误伤在途 run。
- **白名单进数据库配置表**：本批不新增表。激活窗口由 Owner 逐 run 指定（#3800 §5 阶段二），
  env 与既有阶段一开关同族、随发布可核对；若复核认为需要 UI 化管理再另立单点。
- **在 `agent_device_log_events` 的列表读取处固化**：否决。标记（`upload_task`）先于任何旧链
  上传发生，列表读取不产生新模式下的副作用；在那里写模式只会把固化点推迟到真正分流之后。

## Verification

- 判定矩阵：`backend/tests/services/test_b8_4043_partition_mode_admission.py`（14 例）——
  准入放行、两个获批条件不满足（同一 Plan 兄弟 run、白名单为空）、能力硬阻断（离线 host 与
  摘要上报早于心跳两种原因码）、在途旧 run、展锐产物不算副作用、`run_context` 信号、历史无
  模式不写记录、关开关后已固定分区不变、只有 MTK 可固化、混合平台、8 路并发首写只留 1 行、
  重试/新会话读到同一决定且改写被拒。
- 真实入口：`backend/tests/services/test_b8_4043_entry_points.py`（9 例）——手动 scan 路由
  入队的 SAQ 作业跑真实 `scan_task`，探针在**下发 `scan_now` 的那一刻**用另一个数据库连接读
  模式（跨连接可见＝已提交，排除「只在任务体内补写」）；真实 `upload_task` 在标记前读到
  `DEDUP_FIRST`（展锐事件仍被标记，MTK 不标）；真实 `sweep_shadow_scan_rounds` 登记轮次前写入
  获批模式、未获批分区仍按主干落 `CURRENT`；真实手动 extract / merge 路由的分流前置；能力恢复
  后由下一次入场固化，且 `partition_mode` 仍只有一行。
- 基线/HEAD 同探针：`backend/tests/services/test_b8_4043_baseline_probe.py`（只 import 主干
  既有模块与 env 名，可在基线原样运行）。
  - 基线 `e7325af03`：5 failed / 1 passed。逐条：影子轮记的是 `CURRENT`（应为
    `DEDUP_FIRST`）；`upload_task` 标记时跨连接读到 `None`（旧链已分流）；`scan_task` 下发时读到
    `None`；手动 extract 走旧链（`no merge result available, run merge first`，无模式记录）；手动
    merge 走旧链前置（`no scan result available, run scan first`）。第 6 项是反向护栏（能力未证实
    不得写 `DEDUP_FIRST`），基线同样为绿，只防我方把硬阻断改坏，不作为判别证据。
  - HEAD：6 passed。
- 受影响回归：`backend/tests/tasks`（74）、`backend/tests/api/test_dedup_scan_endpoints.py`、
  `backend/tests/api/test_b8_g8_legacy_report_scope.py`、`backend/tests/test_issue_fact_store.py`、
  `backend/tests/services/test_shadow_*`、`test_shadow_scan_send_3875`、
  `test_b8_g6_upload_mark`、`test_b8_g7_partition_consumers`、`test_b8_g8_*`、
  `tests/test_settings_scheduler.py` 全绿。其中
  `test_saq_scan_pipeline.py::test_scan_task_no_hosts_triggered_skips_poll` 的判据按新增的前置
  固化步收窄（线程调用次数 == 1，仍然证明「没有 host 被触发 ⇒ 不进轮询」）。
- 待补：`check:quick` 与六项 required CI 在 Draft PR 上跑；生产启用属 #3800 §5，不在本单。

## Revisit

- **获批分区仍会收到旧链 `scan_now`**：本次只保证模式在分流前提交，其后续 upload/merge/extract
  已被 G6/G7/G8 按分区挡住，但旧 scan 仍会注册 MTK 报表产物（容量浪费）。按平台抑制 `scan_now`
  需要改下发载荷/工具契约，按 ADR-0062 D12 第 3 条须先取得 Owner 同意，另立单点。
- **#4044 收口后替换证据来源**：`partition_capability` 换成当前心跳显式能力（含重连、Agent
  回退、跳拍、当前心跳缺字段）时，准入与下发守卫同时生效；本单的反例只覆盖过渡判据。
- **未获批分区是否也要在每个入口落 `CURRENT`**：本单按 D13 第 2 条取保守侧（不写），把「无
  记录」留给消费方解释；若规划者要求 D3 第 1 条字面全覆盖，只需把 merge/extract/upload/scan
  四处 `record_current_fallback` 置 True，并同步放宽「无模式存量 extract 不加平台过滤」的既有
  行为断言。
- **实际 PlanRun ID 与旧链副作用的可判定性**：本单枚举的入口在动作前都拿得到 `plan_run_id`，
  且旧链痕迹有库内信号；若后续新增入口在这些之前产生副作用（例如 Agent 侧自发上送报表），
  该入口无法自证「尚无旧链副作用」，须交规划者裁定而不是就地猜。
- 白名单若需要按链根（`root_plan_run_id`）批量放行，属模型级变化（D13 第 1 条明确链根只用于
  物理分组），须 ADR 复议而非实施者自行扩展。
