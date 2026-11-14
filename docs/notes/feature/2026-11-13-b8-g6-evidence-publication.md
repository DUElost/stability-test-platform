# B8 G6：代表证据传输、校验回执与交付接线（#3957）

Status: implemented
Class: feature

## Decision

S8 的发布与校验回执按 Owner 2026-11-12 裁定独立持久化：新表
evidence_publication 以 (实际 PlanRun, 平台, 代表 DLE) 为唯一身份，多个问题复用
同一条记录；记录来源类型与创建时的源定位、链根、采集时目录名、命名规则版本、
固定目的相对路径、重试状态、已校验哈希与字节数。目的相对路径对非 FAILED 行
唯一（部分唯一索引）：不同身份的落点冲突拒绝覆盖，冲突行以 FAILED 落库保留
诊断路径。

关键边界与落点：

- **来源**：普通代表由持有本地原件的 Agent 上送（LOCAL）；HddSpill 代表由控制面
  从中心已校验的溢出副本复制（SPILL）。两条路径共用 contracts/evidence_layout
  的目的路径规则、目录哈希、无符号链接复制与五次尝试上限；终态判据一致，尝试
  计数在 Agent 侧持久化（evidence_upload_attempts:）、在控制面侧落 attempts 列，
  重启都不清零。HddSpill 的 DLE.remote_path 原样保留，溢出补扫/留存继续读
  devices/ 源；证据目的路径只存在于发布台账，不写回 DLE。本模块也从不删除
  Agent 本地源或中心溢出副本（I4 / D11）。
- **命名输入**：D9 第 6 条要求的项目名与版本缩写取自事件采集时的设备属性。
  Agent 采集器当时用 get_aee_log_folder_name（离线工具 _get_aee_log_folder_name
  规则）把 getprop 结果固化成目录名，DLE.local_path 是这条派生结果的不可变记录。
  G6 只校验该目录名的形状并复用（aee-folder-name/v1），不读当前设备状态；因此
  刷机、离线、Agent 重启后目的路径稳定。属性来源缺失（watcher 的 jobs/ 布局、
  历史异形路径、SN 不符）记为显式 FAILED（naming_source_missing），不猜测。
- **接收端**：VERIFIED 回执必须通过当前分区（DEDUP_FIRST）、代表身份、执行方 host、
  完整相对路径、链根前缀、SN、事件目录与目录哈希八项复核；复核由控制面重新
  读取目的目录并计算哈希，不信任 Agent 自报值。重复回执收敛到同一记录；已
  VERIFIED 的同一哈希幂等；不同哈希/终态冲突拒绝。
- **目录原子性**：两侧都先写同目录临时叶子、校验后 rename 到正式目的路径，正式
  目的路径不会是写到一半的目录；目录已存在且哈希一致按 ACK 丢失收敛，哈希
  不同按 destination_conflict 拒绝覆盖。
- **交付接线**：G4 在 DEDUP_FIRST 的成功 merge 同事务调用 ensure（选择与发布记录
  一起提交）；G5 构建快照时对 DEDUP_FIRST 使用真实发布台账（状态映射、
  pending_items、primary_folders），CURRENT 分区保持阶段一 scope_only 语义。
  RETRYING 使交付未就绪（不是缺口）；FAILED 进入 publish_failed 缺口。回执进入
  VERIFIED/FAILED 后重算交付版本；只有登记成功且对应事件在已登记交付物内、且
  该代表没有 evidence_incomplete/publish_failed 缺口时，才把 LOCAL/REMOTE 的代表
  DLE 转为 ARCHIVED；PRUNED 保持终态。登记失败不归档，留待下一轮补偿。
- **旧入口**：DEDUP_FIRST 分区的现行 upload_task 标记增加 NOT EXISTS 排除；Agent 的
  普通恢复列表（UPLOAD_PENDING/UPLOAD_FAILED/UPLOADING）过滤掉 DEDUP_FIRST 的
  非溢出事件。HddSpill 继续按 D2 通过 force 通道上送与重试；展锐与无模式记录的
  分区不受影响。
- **存储族**：evidence 登记为日志根的链主键族（CHAIN_FAMILIES），不进
  RUN_FAMILIES，按 run 清理器不会触碰它；ALL_FAMILIES 纳入后测量面可见，G7 仍
  负责下载、归档完成判据、UI、交付统计与链级留存。
- **可见性**：阶段二激活闸门未打开时不存在 DEDUP_FIRST 模式记录，本单元全部
  路径不介入生产；新表迁移是 additive。阶段一影子分区继续按 CURRENT 解释。


### 第 1 轮复核返修（F1 / 评论 6842934772）

- 根因：list_agent_device_log_events 对所有 state 无条件过滤 DEDUP_FIRST，且只放行
  SpillLedger 行；SpillLedger 仅在 VERIFIED REMOTE 回执时落库，所以 HddSpill 的
  LOCAL 候选、scan_scope 的 state="" 输入、首次溢出失败（尚无台账）的
  UPLOAD_FAILED/UPLOADING 全被隐藏，I2 的溢出回接不可达。
- 修复：共享查询不再按 state 过滤，逐条返回 dedup_first；是否允许经旧入口上送由
  EventUploader.enqueue_local_event 在入队处判定——DEDUP_FIRST 且无溢出意图
  （hdd_spill / prune_after_upload / 持久 event_upload_spill:）直接拒绝。HddSpill
  的 force 入队仍允许；scan_scope 只读，不受影响。
- 持久意图：event_uploader._state_store() 在 STP_AGENT_STATE_DB 未设置时回落到主
  Agent 的 BASE_DIR/agent_state.db（仅当文件已存在），使 event_upload_spill: 与
  event_upload_attempts: 在 Agent 重启后仍可读；SpillLedger 仍只在 REMOTE 校验回执
  成功时登记，回执前保留源文件；_MAX_RETRIES=5 不变，恢复沿用已持久 attempts。
- 探针：在 8e0c2647 复现两条 F1 失败探针（state=LOCAL 与 UPLOAD_FAILED,UPLOADING
  返回空集），返修后转绿；完整链 test_spill_recovery_full_chain_candidate_to_rescan
  覆盖真实候选查询 → force 入队 → 首传失败（REMOTE 500，源保留、无台账）→ 重启恢复
  → REMOTE 回执 → 真实 ingest 登记 SpillLedger → list_partition_spill_inputs 读回。
  同组覆盖无溢出意图的 DEDUP_FIRST 事件被入队拒绝，以及 CURRENT/无模式/展锐不标
  dedup_first。

### 第 2 轮复核返修（评论 6849949320）

- 根因：R1 把判别从 SQL 层移到入队层后，被拒的 DEDUP_FIRST 非溢出事件既不
  上送也不转出 UPLOAD_FAILED/UPLOADING；它们占满 LIMIT 200 的恢复窗（按
  detected_at 升序），detected_at 更晚的溢出事件永不返回，首次溢出失败在积压
  下不可恢复（违约 F1「首次溢出失败后可重试」、#3957 边界 1、ADR-0062 D2 #3 / I2）。
  这是永久饥饿，不是窗口抖动。
- 按用途分域（服务端）：GET /device-log-events 新增 purpose 与 ids。默认用途
  保持如实返回（HddSpill 候选 D2 #3、scan_scope 输入 I2 不变）；purpose=recovery
  在 limit 之前排除「DEDUP_FIRST 且无 SpillLedger」的事件——正是入队守卫会拒绝、
  且状态不会自行转出的集合。响应逐条增加 hdd_spill，恢复循环可据此直接判定。
- 按事件 ID 恢复（Agent）：溢出意图在 force 入队时已写入 agent_state
  （event_upload_spill:）。两个恢复循环每轮先取有界意图集合（≤200），按 ids
  精确查询并优先入队；首次溢出失败（尚无台账）只有这条路可见。只处理本循环
  负责的状态；UPLOAD_FAILED 且持久 attempts ≥ 5 仍跳过；REMOTE/ARCHIVED/
  PRUNED/PULL_FAILED 或查询不到的残留意图清除。入队守卫与 _MAX_RETRIES=5 不变。
- SpillLedger 仍只在 VERIFIED REMOTE 回执成功后登记；回执失败保留源文件。
- 探针（A/B 同探针，真实 _retry_failed_loop + 真实服务端查询，仅 HTTP 传输为
  进程内桥接）：200 条 DEDUP_FIRST 无溢出意图填充 + 第 201 条仍有预算的溢出
  事件（attempts=4），有台账与无台账两种。基线 6f621823 → 2 failed（跨两轮
  饥饿，普通事件 0 jobs）；返修后 → 2 passed（溢出事件实际入队、attempt=4、
  普通事件不入队）。变异验证：去掉服务端 recovery 过滤 → 有台账用例 RED；
  去掉按 ID 恢复 → 无台账用例 RED。

### 第 3 轮复核返修（14895494 补跑探针）

- **HTTP 路由漏接**：R2 已在服务层 `list_agent_device_log_events` 支持
  `purpose` / `ids`，但 FastAPI 路由 `list_device_log_events` 既未声明、也未转发
  这两个 query 参数；请求仍 200，参数被静默丢弃，按用途过滤与按 ID 恢复在生产
  HTTP 路径上全部失效。R2 的探针用进程内桥接**直接调用服务层**，绕过了这处
  缺口，所以没抓到。本轮在路由签名补 `ids: Optional[str]` /
  `purpose: Optional[str]` 并逐字透传；`agent_api.py` 恰好触及 god-files 封顶
  411 行，docstring 压成单行、透参并入同一表达式保持不越界。
- **按 ID 恢复窗口饥饿**：`_recover_spill_intents` 每轮都调用
  `_durable_spill_event_ids()` 读同一批前 200 个 key（ORDER BY key LIMIT 200）。
  若这 200 个都已耗尽预算、或状态不属于本循环（被跳过且不清意图），第 201 个
  （仍有预算）永远不被查询/入队，连续两轮读同一批——持续占窗。修复：
  - `state_store.list_states` 增加 keyset 游标 `after_key`（`AND key > ?`）；
  - `_durable_spill_entries(limit, after_key)` 返回 `(意图键, event_id)`，
    `_read/_write_spill_cursor` 把游标持久化到 agent_state（按 `states` 分键，
    快/慢循环互不干扰），进程重启可续接；
  - `_recover_spill_intents` 每轮从游标续读，整页被跳过（无事件入队）时本轮
    继续翻页（`_SPILL_INTENT_MAX_PAGES=4` 上限，每页仍 ≤200，有界），到末尾
    （不足一页）把游标归零供下一轮回卷；无论本页是否入队，游标都推进到本页
    末尾，被跳过的意图不再下一轮占位。已耗尽预算事件仍按原 attempts 跳过、
    不从零开始；入队守卫、五次上限、台账只在校验回执成功后登记等语义不变。
- 验收探针改为**穿过 FastAPI**：`backend/tests/api/test_b8_g6_r3_route_and_intent_window.py`
  用 `httpx.ASGITransport(app=fastapi_app)` 让请求真正经路由 → 服务层 → 隔离
  PG16（testcontainers），并用 spy 记录服务层收到的 kwargs + 行为断言双重证明
  `purpose` / `ids` 到达；意图窗口探针用真实 `_recover_spill_intents`（两轮）+
  临时 SQLite 201 意图（前 200 耗尽/状态不匹配、第 201 个 attempts=4），
  `requests.get` 桥接到同一 ASGITransport，按 ID 恢复查询也走真实路由。
  参数化 4 例（有台账/首次失败无台账 × 已耗尽/当前循环无法处理）。

## Alternatives

- **把证据目的路径写回 DLE.remote_path**：否决。溢出补扫与留存以该字段定位
  devices/ 中的源；覆盖后迟到补扫会读到代表证据的目的路径。独立台账同时满足
  D16 的下载读取与 D8 的源保留。
- **给 DLE 增加四个设备属性列，在采集登记时上报**：否决。需要 Agent 载荷与表
  结构的双迁移，且没有比 local_path 更强的可信度——local_path 已经是采集时
  getprop 派生并固化的结果；只要校验形状并记录规则版本即可证明输入时点。
- **所有复制都由控制面执行**：否决。普通代表的原件在 Agent 本地，控制面不可达；
  拆成 LOCAL/SPILL 两条执行路径，但共享同一目的规则、哈希与终态判据。
- **用时间窗替代五次尝试**：否决。D8/D9 的终态是重试次数语义；墙钟超时会把
  暂时离线误判为终态失败。尝试计数持久化，重启不清零。
- **重试中的代表记为缺口**：否决。D9 第 3 条只允许 FAILED 代表进缺口；重试中
  只让交付未就绪。

## Verification

- 契约：backend/tests/core/test_evidence_layout.py（往返/越根/错链/错 SN/错事件目录/
  symlink/半写目录/哈希一致）、backend/tests/core/test_artifact_paths_evidence.py
  （日志根、symlink 分量与树内 symlink、must_exist）。
- 服务与矩阵：backend/tests/services/test_b8_g6_evidence_publication.py（共享 DLE 一条
  记录、CURRENT/无模式/展锐不发布、命名缺失终态缺口、同名落点冲突、两 run 同链根、
  多软件版本多主文件夹、本地回执归档、哈希不符五次失败、ACK 丢失幂等、错误执行方/
  DLE/路径拒绝、登记失败不归档、SPILL 复制保留 remote_path、SPILL 冲突拒绝覆盖、
  SPILL 源缺失五次终态、空/全缺回执矩阵、Agent 待办过滤、路由鉴权映射）。
- Agent：backend/agent/tests/test_b8_g6_evidence_uploader.py（复制+校验+VERIFIED、
  ACK 丢失收敛、落点冲突不覆盖、源缺失五次失败、错误 SN 拒绝、持久尝试跨重启、
  DEDUP_FIRST 无溢出意图入队拒绝、溢出意图保留尝试预算、持久意图跨重启）。
- 旧入口与共享查询：backend/tests/tasks/test_b8_g6_upload_mark.py（MTK DEDUP_FIRST
  不标记、同 run 展锐仍标记）；backend/tests/api/test_b8_g6_dedup_first_query_visibility.py
  （HddSpill LOCAL 候选、无台账 UPLOAD_FAILED/UPLOADING、scan state="" 输入可见；
  CURRENT/无模式/展锐不标 dedup_first；完整溢出恢复链候选→force→失败→重启→回执→补扫）。
- 回归：test_shadow_delivery.py（32 passed）、test_shadow_merge.py（32 passed）、
  test_issue_fact_store.py、test_shadow_scan_rounds.py、test_retention_cleanup.py、
  test_measure_center_storage.py、test_saq_scan_pipeline.py、test_agent_device_log_events.py
  （21 passed）、Agent 导入边界与测试导入棘轮（14 passed）、Agent control/shutdown
  套件（28 passed）。
- R1 探针：在 8e0c2647 的临时 worktree 跑
  test_hddspill_local_candidates_visible_in_dedup_first 与
  test_first_failed_spill_retry_visible_without_ledger → 2 failed（返回集为空）；
  同一文件在返修后 6 passed（含完整恢复链）。
- R2 探针：真实 _retry_failed_loop（两轮）+ 真实 list_agent_device_log_events
  桥接，200 填充 + 第 201 条溢出（attempts=4）：6f621823 → 2 failed；
  本 HEAD → 2 passed。变异：去 recovery 过滤 / 去按 ID 恢复各 1 RED。
- R3 探针（穿 FastAPI → PG16）：backend/tests/api/test_b8_g6_r3_route_and_intent_window.py
  5 例，同一探针文件在 14895494 反例基线 worktree → **5 failed**（路由 spy 收到
  的 kwargs 无 purpose/ids；201 意图两轮 queried=2/enqueued=[] 跨轮饥饿），
  返修后 → **5 passed**。变异自守：去掉路由透传 → HTTP 用例 RED；禁用游标
  翻页 → 4 个意图窗口用例全 RED。
- 定向套件 376 passed（含 R1 的 LOCAL 候选、扫描输入、失败后重启与补扫读回，
  CURRENT/无模式/展锐回归，G6 服务/契约/Agent/G4/G5/G1/G2/retention/measure/
  Agent control+shutdown/导入棘轮，及 R3 新探针）。全量 Agent 套件 3384 passed /
  2 skipped / 1 既有 ambient 失败（test_dialout_absent_and_no_rule_fails，已在
  origin/main 复现，与本次改动无关；CI pr-agent-tests 绿）。
- 门禁：python scripts/run_gates.py check:quick（16 gates 全绿）；
  tools/dev/check_pr_migrate.py（空库 alembic upgrade head + schema 比对 + seed 身份，
  [OK]）；PYTHONPATH=. .venv/bin/lint-imports --no-cache（5 contracts kept）。
- #3831 陈旧记录核对：注册表里 fix/scan-report-project-version 的 worktree 与分支
  均不存在，git diff 为空（零实际改动）；本单元未改 scan_runner.py 或其测试。

## Revisit

- 新项目或命名规则演进时：只能新增规则版本，不得改 aee-folder-name/v1；需要时
  在 contracts/evidence_layout 增加版本并让发布记录按采集时规则记录。
- G7 把下载/归档完成判据/链级留存切到发布台账后，删除或收窄 D16 中 devices/ 专用
  的读取路径；届时复核 evidence 族的测量口径与 retention 的链级清理。
- 阶段二激活后若出现大量 Agent 每 30s 轮询的负载问题，改为由 scan 回执携带待办
  或长轮询；本单元只用独立 HTTP 拉取，未改 Socket.IO 控制面。
- 若现场出现不同身份落到同一目的路径（同名事件）且需要保留两份，本单元的
  destination_conflict 会显式失败；届时应先由 Owner 裁定路径扩展规则，不得静默
  覆盖或改名绕过。
