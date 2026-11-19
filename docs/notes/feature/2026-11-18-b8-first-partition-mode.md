# B8 阶段二 #4043 首次分区模式写入

Status: implemented
Class: feature

日期：2026-11-18，接口收敛返修 2026-11-19　交付：Draft PR，待按 ADR-0058 D8 独立复核
基线：`origin/main@e7325af03`；本轮已接合 `origin/main@f2fae5679`（含 #4044 的 PR #4049 与
PR #4050）
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
   | `shadow_scan_rounds._advance_run` | 登记轮次（D3 第 2 条） | `shadow_scan_rounds.py:211` |
   | `shadow_scan_rounds.prepare_shadow_scan` | 提交后返回下发载荷（同事务）；候选等待时不返回下发 | `shadow_scan_rounds.py:438` |
   | SAQ `scan_task`（自动与手动 scan 共用） | 向 Agent 下发 `scan_now` | `saq_tasks.py:497` |
   | SAQ `upload_task` | `UPLOAD_PENDING` 标记 UPDATE | `saq_tasks.py:744` |
   | 手动 merge 路由 | `partition_rows` 决定要不要「先跑 scan」 | `api/routes/dedup.py:727` |
   | merge 服务（自动 merge_task 与手动合并共用） | `_partition_targets` 的平台分流 | `dedup_scan.py:812` |
   | extract（自动 extract_task 与手动路由共用） | 按 CURRENT 平台过滤 | `dedup_extract.py:243` |

   DLE 登记（D2 第 1 条）不随模式变化，因此不是分流动作、不触发固化；Agent 恢复循环读取
   `dedup_first` 标记（G6）与 `legacy_report_scope` 的 D10 报表守卫都只读既有事实：前者只在
   `upload_task` 标记之后才有对象，后者不读开关值（D10 自己区分「有记录/无记录」），故本次
   不在这些读取点写模式。`GET /dedup/status` 与前端视图同理。
3. **Owner 白名单按实际 PlanRun**：`STP_DEDUP_FIRST_PLAN_RUN_IDS`（逗号/空白分隔的实际
   `plan_run.id`）+ `STP_DEDUP_FIRST_ENABLED`（默认关闭）。解析失败即报错，不把「配错的试点
   run」静默当成「没批准」。阶段二开关必须与阶段一 `STP_SCAN_ROUND_SHADOW_ENABLED` 同开，
   由 `SchedulerSettings` 的 model_validator 拒绝半开配置——轮次调度、交付登记与回收都在影子
   开关之下，只开阶段二会让我方刚拒绝旧链标记的事件无人处理。
4. **能力证据单一来源（接口收敛后）**：入场判定直接消费 #4044 的
   `backend/services/agent_capabilities.partition_capability_evidence(db, plan_run_id=…, platform=…)`。
   本单原先自建的 `backend/services/partition_capability.py` 已删除，`shadow_scan_rounds` 里委托
   它的 `_shadow_capability_verified` 一并撤除，#3875 v1.23 的制品摘要（digest）过渡判据在本单
   **不留残留**——Owner 裁定 6975368043 要的是「模式准入和发送守卫共用可核验能力证据」，两份
   判据并存本身就是分叉。
   - 期望 host 口径不再自行派生：该函数与 `shadow_scan_rounds._expected_units` 同源（实际
     `JobInstance` + prepare 快照，再按该 host 预期产出的分区过滤），作用域是**实际 PlanRun ×
     平台**——同 run 的纯展锐 host 不进入 MTK 期望集（不误阻断），同 Plan 的兄弟 run 各自独立。
   - 逐台原因码沿用 #4044 的稳定字符串（`host_missing` / `host_offline` / `host_retired` /
     `heartbeat_missing` / `capability_never_reported` / `capability_not_current_heartbeat` /
     `capability_missing` / `instance_unbound`），本单不新增别名；被挡时把 `as_dict()` 的逐台
     证据（含 `evaluated_at`＝何时核验过全量预期 host）放进决定 `detail`，供入场台账留痕。
   - 任一 host 未证实 → 硬阻断：**既不写 `DEDUP_FIRST`，也不写 `CURRENT`**。能力是瞬时事实，
     而模式一旦写下不可改写，一次心跳抖动不得把获批 run 永久锁在旧链上。
   - **空期望集显式裁定为 fail-closed**：#4044 的谓词对「一台预期 host 都没有」返回
     `admitted=True`（它明确不替调用方判断这是否异常）。本单不接受该默认值——没有可核验的
     能力证据就不得写入 `DEDUP_FIRST`：判定记 `no_expected_mtk_hosts`，入口按 `held=True`
     停住本分区动作（Decision 7a），影子入口也不得借 `record_current_fallback` 顺手写
     `CURRENT`（那会把一次还没做出来的决定固化成不可改写的事实）。
5. **在途旧 run 的可判定信号（返修后）**：只认**能归因到本分区（MTK）**的旧链痕迹——
   (a) MTK 或归属无法确认（fail-closed）的 `scan_result_xls` / `merge_result_xls` 产物；
   (b) 含 MTK 的 `run_context.merge_platforms`；
   (c) 候选的旧链扫描**下发之前**写下的 `run_context.legacy_scan_dispatch` 痕迹段。
   命中即固化 `CURRENT` 并记原因，两套链不混跑。
   裁决第 1 项要求「即使报表还没登记，也必须排除已经下发或正在执行的旧链工作」，(c) 就是为此
   而设：混合运行的展锐现行侧必须照扫，而这台 Agent 的旧 `scan_now` 会顺带扫 MTK（Revisit 第 1
   条的同主机反例），所以控制面在发出之前先把「已下发」变成库内事实，之后任何入场都只能给
   `CURRENT`。展锐产物、纯展锐 merge 记录、以及 run 级的 `upload_mark` / `archive` / `extract`
   段**不算**本分区失格证据——否则每个混合试点都会在第一次展锐上传后被打回旧链，直接违反裁决
   第 2 项；而「真正标记过事件」必然伴随已登记的报表，(a) 已覆盖该情形。
   DLE 的 `state` / `remote_path` 也不作信号：`DEDUP_FIRST` 分区的代表与 HddSpill 溢出副本
   （I2）本来就会产生这两种值，拿它们判旧链会误封。

5a. **判定顺序：先查旧链痕迹，再查能力**（返修修正）。失格是持久事实，能力是瞬时事实。若先查
   能力，「旧扫描已下发 + 心跳还没恢复」的窗口会被读成「等待」，于是不固化也不设防，下一个
   入场可以在没有任何报表的情况下写进 `DEDUP_FIRST`——正是裁决点名要封死的那条交错。
6. **落库范围**：获批分区无论放行还是被拒都留下记录（决定必须可审计，之后白名单变动也不
   翻转它）；未获批 / 开关关闭的分区只有影子入口写 `CURRENT`——那是主干既有行为（#3875 的
   settled 出口把终态记在 `partition_mode` 行上），其余入口不写，消费方继续按 D13 第 2 条把
   「无记录」读作现行，避免按开关回读改写存量分区、也避免给无平台归属的历史 DLE 收窄 extract。
7a. **候选等待能力证据 = 该分区停摆，不是该分区走旧链**（裁决第 2 项）。入口消费
   `PartitionAction(mode, held, reason, detail)`：
   `held=True` 时纯 MTK 运行整段跳过旧 `scan_now` / 标记 / 旧 merge / extract，影子轮既不登记
   也不入队；混合运行里展锐现行侧照旧处理，但那次下发会让 MTK 失格（Decision 5 的 (c)）。
   等待期间库里**仍然没有**模式记录——这与「存量按现行读取」在数据上不可区分，所以判据用
   `held` 标志而不是靠库状态猜；一旦能力恢复，下一次入场照常固化 `DEDUP_FIRST`。
   若白名单被撤下，该分区自动回到 `held=False` 的现行语义，不会永久卡住。
   extract 侧同理**不看** `recorded_modes` 是否为空：只要本分区处于等待态就把它排除出提取
   范围（其余现行分区照常提取，只剩候选自己时返回 `-4`）。此前用「held 且无模式记录」做条件，
   在「别的平台已有模式记录」的形态下会漏网——判据应当直接来自消费的那个结论，而不是从
   库状态反推。

7b. **已固化 `DEDUP_FIRST` 的 MTK 只经登记轮次扫描**（裁决第 3 项）。`scan_task` 的非影子分支
   在下发前消费模式：`DEDUP_FIRST`（或 `held`）时先查「这个运行还有没有别的现行分区需要这次
   扫描」，没有就返回（自动、手动、`is_final` 三个入口同一条判据）；有则照发展锐侧并按 5(c)
   留痕。轮次侧由 `sweep_shadow_scan_rounds` → `prepare_shadow_scan` → `call_shadow_scan_control`
   承担，载荷带 `fact_round_id`。

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
- **先判能力、后判旧链痕迹**：否决（返修前后顺序颠倒过一次）。失格是持久事实，能力是瞬时
  事实；先判能力会让「旧扫描已下发 + 心跳未恢复」的窗口读成「等待」，既不固化也不设防，
  下一条入场可以在零报表下写进 `DEDUP_FIRST`。
- **把 run 级旧链段（`upload_mark` / `archive` / `extract`）一律当作 MTK 失格证据**：否决。
  混合运行的展锐现行侧本来就要写这些段，照此判定每个混合试点都会在第一次展锐上传后被打回
  旧链，与裁决第 2 项冲突；只保留可按平台归因的信号 + 专用下发痕迹段。
- **靠控制面过滤 `device_serials` 来抑制旧 MTK 扫描**：否决（有同主机反例）。Agent 的 MTK 分支
  只看 runner 是否配置，不看串号平台；真要抑制需要改下发契约，交 Owner（Revisit 第 1 条）。
- **白名单进数据库配置表**：本批不新增表。激活窗口由 Owner 逐 run 指定（#3800 §5 阶段二），
  env 与既有阶段一开关同族、随发布可核对；若复核认为需要 UI 化管理再另立单点。
- **在 `agent_device_log_events` 的列表读取处固化**：否决。标记（`upload_task`）先于任何旧链
  上传发生，列表读取不产生新模式下的副作用；在那里写模式只会把固化点推迟到真正分流之后。
- **本单自建的 `partition_capability` 与 #4044 的心跳能力并存**：否决（接口收敛轮的根据）。
  「制品摘要 + 上报时间」与「当前心跳显式上报 `scan_round_v1`」是两份真相源，同一台机器可以
  一边被准入放行、一边被下发拒绝——Owner 裁定 6975368043 要排除的正是这种分叉。第二套判据
  即使暂时结果一致也不可保留：它让期望 host 集合与原因码各自漂移，而两者都是入场台账的内容。
  收敛后唯一入口是 `agent_capabilities.partition_capability_evidence`，摘要残留由静态守卫禁止。
- **保留 `partition_capability` 作为转封装（同一结论、换个入口名）**：同样否决。本分支上出现过
  这一形态（把 digest 判据换成再导出 `caps.REASON_*` 后继续自行派生期望 host 集），它比 digest
  版本进步，但仍然重复了 `iter_plan_run_scan_hosts` + `load_expected_scan_platforms` 这一段
  期望集推导——#4044 的接口契约（评论 6979207483 第 1 条）明确要求别再另起一份 host 集合推导，
  否则影子预筛与本单入场会在「谁算预期 MTK host」上分叉。与那份改动合流时按本条裁定取其测试
  适配、弃其转封装层（模块删除，`shadow_scan_rounds` 保持主干 #4049 形态）。

## Verification

- 判定矩阵：`backend/tests/services/test_b8_4043_partition_mode_admission.py`（23 例）——
  准入放行、两个获批条件不满足（同一 Plan 兄弟 run、白名单为空）、在途旧 run（MTK 报表产物 /
  含 MTK 的 merge_platforms）、展锐产物与纯展锐 merge 记录不算本分区副作用、历史无模式不写记录、
  关开关后已固定分区不变、只有 MTK 可固化、混合平台、8 路并发首写只留 1 行、重试/新会话读到
  同一决定且改写被拒。
  能力硬阻断改为**按 #4044 的原因码逐条**参数化（7 例：`capability_never_reported` /
  `capability_missing` / `host_offline` / `host_retired` / `heartbeat_missing` /
  `capability_not_current_heartbeat` / `instance_unbound`），每一例都同时断言三件事：入场被挡
  时既不写 `DEDUP_FIRST` 也不写 `CURRENT`、台账里只有那一台进 `unverified_hosts` 且原因码就是
  #4044 的那一个、证据恢复后由下一次入场**首次**固化。
  接缝与残留（接口收敛新增）：`test_admission_and_send_guard_share_one_capability_source`
  逐台比对入场证据与发送侧 `load_host_capability_evidence` 的结论与原因码，并断言
  `admission.partition_capability_evidence is caps.partition_capability_evidence`（同一函数对象，
  不是同形实现）；`test_no_digest_transitional_capability_judge_remains` 用 `SourceGuard`
  先锚定能力消费点仍在位、再判 `artifact_digest` 不得回潮，并断言
  `backend/services/partition_capability.py` 已不存在；
  `test_empty_expected_host_set_never_fixates_dedup_first` 锁死空期望集 fail-closed
  （谓词 `admitted=True` 也不写模式、入口 `held=True`、影子入口不借兜底写 `CURRENT`）；
  `test_fixated_dedup_first_keeps_mode_and_records_capability_gap` 证明已固定分区失能时模式
  不回退、缺口由 #4044 守卫在轮次 coverage 里记成 `capability_unverified`。
  本文件的 host 一律按 #4044 心跳能力构造（在线 + 显式上报 `scan_round_v1` + 绑定实例 id），
  不再出现摘要字段。
- 真实入口：`backend/tests/services/test_b8_4043_entry_points.py`（10 例）——手动 scan 路由
  入队的 SAQ 作业跑真实 `scan_task`；纯 MTK 已固化时**旧 scan_now 一次都不发**，混合运行时
  探针在**下发那一刻**用另一个数据库连接读模式（跨连接可见＝已提交，排除「只在任务体内补写」），
  并断言已固化分区不会被写候选留痕；真实 `upload_task` 在标记前读到 `DEDUP_FIRST`（展锐事件仍
  被标记，MTK 不标）；在途旧 run 走完整 `upload_task` 后仍按现行标记；真实
  `sweep_shadow_scan_rounds` 登记轮次前写入获批模式、未获批分区仍按主干落 `CURRENT`；真实手动
  extract / merge 路由的分流前置；能力恢复后由下一次入场固化且 `partition_mode` 只有一行。
- 返修新增（`test_b8_4043_partition_hold.py`，12 例）：三态可区分（候选等待 / 存量现行 /
  已固化）；纯 MTK 候选等待时 `scan_now` 下发次数为 0、`_query_hosts_for_scan` 不被调用、
  `collect_upload_event_dir_names` 不被调用（连空水位线都不写）、旧 merge 不被调用、
  `run_extract_sync` 返回 `-4`、模式记录仍为空；混合候选等待时展锐事件照常 `UPLOAD_PENDING`
  而 MTK 留在 `LOCAL`，`_partition_targets` 给 `held=['mtk']`；等待期不登记轮次也不入队，
  能力恢复后同一 sweep 固化并建周期轮；`legacy_scan_dispatch` 痕迹在没有报表登记的条件下即
  否定准入（固化 CURRENT）；非候选不写该痕迹、`run_context` 保持为空；**受控交错**：下发那一
  刻跨会话已能读到痕迹 → 能力恢复 → 另一入场只能 `CURRENT`；失格在能力仍未证实时也不回退成
  「等待」（判据顺序回归位）；已固化 DEDUP_FIRST 的纯 MTK 运行在自动 / 手动 / `is_final` 三个
  入口旧 scan 下发均为 0，且首次轮次下发前独立会话读到唯一 `DEDUP_FIRST` 记录、载荷
  `fact_round_id` 等于登记轮次 id；终态入口改由 FINAL 轮承担；同主机 Agent 反例（真实
  `ScanRunner._execute_job`）证明只传展锐串号时 MTK runner 仍被调用。接口收敛轮再补两点：
  候选等待不限于离线机（`test_candidate_wait_also_covers_capability_never_reported`：在线但心跳
  没报能力，同样 `held=True`、旧 `scan_now` 零下发、原因码取 #4044 的
  `capability_never_reported`）；首次轮次下发的探针现在断言
  `call_shadow_scan_control(required_instance_id="agent-hold")`，即 #4044 第三道「实例绑定」守卫
  在本单接合后仍然生效（不是只靠预筛）。
- 基线/HEAD 同探针：`backend/tests/services/test_b8_4043_baseline_probe.py` 只 import 主干既有
  模块与既有 env 名，可在基线 checkout 原样运行（`git worktree` 于 `e7325af03`）。
  接口收敛轮：host 的能力证据改由 `_stamp_capability()` 按**当前 checkout 实际存在的列**写入
  ——HEAD 侧写 #4044 的心跳能力三列，基线侧（还没有那三列）才落 #3875 的摘要字段。这是探针
  为了「同一份文件两边都能跑」做的适配，不是 HEAD 里的第二套判据：主干已不读摘要
  （由 `test_no_digest_transitional_capability_judge_remains` 锁死）。
  - 基线 `e7325af03`：**6 failed / 1 passed**，全部是断言失败而非基础设施报错——
    1) `assert [('mtk','CURRENT')] == [('mtk','DEDUP_FIRST')]`（影子轮只会写 CURRENT）；
    2) `upload_task` 标记时跨连接 `assert None == 'DEDUP_FIRST'`；
    3) 纯 MTK 获批分区仍被旧扫描：`assert [('pr-…','scan_now')] == []`；
    4) 混合运行下发 `scan_now` 时 `assert None == 'DEDUP_FIRST'`；
    5) 手动 extract 走旧链：`no merge result available, run merge first`；
    6) 手动 merge 走旧链前置：`no scan result available, run scan first`。
    第 7 项是反向护栏（能力未证实不得写 `DEDUP_FIRST`），基线同样绿，**不作为判别证据**。
  - HEAD：**7 passed**（2.2s，基线侧 12s，两侧都不依赖轮询等待）。
- 接合 `origin/main@a902a3183`（含 #4049）之后本轮实跑，全部用
  `JWT_SECRET_KEY=test-secret ./scripts/project_python.sh scripts/run_pytest.py …`：
  - 本单四份 + #4044 两份能力守卫：`test_b8_4043_partition_mode_admission.py`（23）、
    `test_b8_4043_entry_points.py`（10）、`test_b8_4043_partition_hold.py`（12）、
    `test_b8_4043_baseline_probe.py`（7）、`test_agent_capability_evidence_4044.py`（19）、
    `test_shadow_scan_capability_4044.py`（15）→ **86 passed**。
  - 共享判定面（三道发送守卫 + 轮次 + 旧链入口）：`test_shadow_scan_rounds.py`、
    `test_shadow_scan_send_3875.py`、`test_shadow_scan_settled_3875.py`、
    `backend/tests/api/test_heartbeat_capability_4044.py`、
    `backend/tests/api/test_heartbeat_artifact_digest_1907.py`、
    `backend/tests/tasks/test_saq_scan_pipeline.py`、`test_saq_tasks.py`、
    `test_b8_g6_upload_mark.py`、`backend/tests/services/test_dedup_extract.py`、
    `test_dedup_scan_merge.py`、`test_b8_g7_partition_consumers.py` → **256 passed**。
  - `tests/test_settings_scheduler.py` → **29 passed**。
  - `tests/`（顶层静态守卫全量）首轮 **1 failed / 2566 passed / 18 skipped**：红的那条是本单
    新写的静态守卫用了裸 `assert "artifact_digest" not in source`，被
    `test_source_scan_anchor_ratchet.py` 判为新增 offender（源扫描型否定断言必须走 `SourceGuard`
    锚点）。改写成 `SourceGuard.of_module(...).anchored(...).assert_absent(...)` 后，
    `test_b8_4043_partition_mode_admission.py` + `tests/test_source_scan_anchor_ratchet.py`
    → **33 passed**。
  `test_saq_scan_pipeline.py::test_scan_task_no_hosts_triggered_skips_poll` 的原判据保持不变：
  本单的模式门控走 `asyncio.to_thread` 直调（与同函数里 `_query_hosts_for_scan` 同一写法），
  不占用该测试用于拦截轮询路径的 `asyncio_to_thread` 别名。
- 待补：`check:quick` 与六项 required CI 在本 Draft PR 上跑；生产启用属 #3800 §5，不在本单。

## Revisit

1. **交 Owner 裁定：混合平台上「旧任务不执行 MTK 扫描」这条边界现有接口无法表达**
   （裁决第 3 项要求的证据如下）：
   - 同主机反例（`test_same_host_legacy_scan_still_runs_mtk_runner`，跑真实
     `backend/agent/scan_runner.py::ScanRunner._execute_job`）：payload 里只放展锐串号时，
     MTK 分支**仍被调用**——该分支只看 `ScanRunner.instance().is_configured()`，没有串号平台
     门控（展锐分支才有 `detect_device_platform` 过滤）。因此控制面过滤 `device_serials` 不能
     证明旧 MTK 扫描被抑制；被抑制的只是 MTK 的下游消费（upload 标记、merge、extract、交付
     都按分区挡住），代价是中心存储多收一份本分区的 org 报表。
   - 旧 Agent 兼容影响：若在 `scan_now` payload 里新增「本平台跳过」字段，未升级到识别该字段的
     Agent 会照旧执行 MTK 扫描——字段对老进程是惰性的，抑制效果取决于 fleet 版本一致率，
     不是发布即生效；这与 ADR-0051「已发布工具/脚本版本不可原地修改」以及 D12 第 3 条「需要
     新增工具参数时实施者必须先取得 Owner 同意」直接相关。
   - 最小改动方案（择一，均需 Owner 签字后再实施）：
     (甲) `scan_now` payload 增加 `skip_platforms: [mtk]`，Agent 两分支各按该集合跳过，且
     控制面在下发前核验目标 Agent 已具备识别能力（与 #4044 的心跳能力同源）；
     (乙) 现行侧扫描改按分区单独下发（每台 host 两个命令），payload 不变但队列键与回执计数要
     重做；
     (丙) 试点只选纯 MTK run（Owner §2.1 的首批偏好），混合 run 在阶段二一律不点名——本单
     当前实现即按 (丙) 的假设运作：混合 run 的 MTK 一旦必须与展锐共用旧 scan，就落痕迹失格。
2. **#4044 收口后替换能力证据来源**：**本轮已办结**——自建模块删除，入场改消费
   `agent_capabilities.partition_capability_evidence`。原列四类反例的落点：「当前心跳省略能力
   字段」= `capability_never_reported` / `capability_missing` 两例（判定矩阵 +
   `test_candidate_wait_also_covers_capability_never_reported`）；「迟到心跳」=
   `capability_not_current_heartbeat` 一例；「预筛后连接变更」= #4049 的实例绑定守卫，本单在
   `test_admitted_partition_scans_only_through_registered_rounds` 里断言下发入参
   `required_instance_id` 仍是心跳里的实例 id。仍**未覆盖**的是「先上报能力后换旧 Agent」这类
   fleet 现场形态（需要真实主机与发布顺序），属 #3800 §5 阶段二激活验收，不在本单。
   过渡判据在 `docs/governance/transitions.json` 的登记属 #4044 侧（其 scope 内），本单不触碰。
3. **接合后需复跑的共享判定**：**本轮已复跑**（见 Verification 的 86 / 256 passed 两条），
   接缝本身另由 `test_admission_and_send_guard_share_one_capability_source` 与
   `test_fixated_dedup_first_keeps_mode_and_records_capability_gap` 常驻。任何一侧再改能力
   判据都必须重跑这一组，并把两条接缝一起看：结论一致还不够，**原因码**也要一致，否则台账
   里会出现两套「能力未证实」的写法。
4. **未获批分区是否也要在每个入口落 `CURRENT`**：本单按 D13 第 2 条取保守侧（不写），把「无
   记录」留给消费方解释；规划裁决第 1 项已确认该口径，无需再改。
5. **实际 PlanRun ID 与旧链副作用的可判定性**：本单枚举的入口在动作前都拿得到 `plan_run_id`，
   且旧链痕迹可归因（Decision 5），因此未触发退回条件。若将来新增更早产生副作用的入口
   （例如 Agent 自发上送报表、或 scan 之外的直接上传），该入口无法自证「尚无旧链副作用」，
   须交规划者裁定而不是就地猜。
6. 白名单若需要按链根（`root_plan_run_id`）批量放行，属模型级变化（D13 第 1 条明确链根只用于
   物理分组），须 ADR 复议而非实施者自行扩展。

7. **能力缺口过安静期后的恢复通路待规划者裁定**（#4044 评论 6979207483 §3 提出的边界）：
   已固定 `DEDUP_FIRST` 的分区若在安静期内没等到能力恢复，影子侧会 settled 并只留
   `shadow_scan_settled_with_capability_gap` 的 WARNING，**没有自动恢复通路**。允许「受控重开
   影子轮」还是「一律走 #4045 补跑代表证据」需要规划者写进验收判据——它决定 Decision 6 里
   「关闭开关后既有分区继续消费」这一行的解释边界（本单不改 #4044 的语义，只登记边界）。
