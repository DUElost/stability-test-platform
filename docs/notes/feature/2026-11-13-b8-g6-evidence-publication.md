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
  ACK 丢失收敛、落点冲突不覆盖、源缺失五次失败、错误 SN 拒绝、持久尝试跨重启）。
- 旧入口：backend/tests/tasks/test_b8_g6_upload_mark.py（MTK DEDUP_FIRST 不标记、
  同 run 展锐仍标记）；backend/tests/api/test_agent_device_log_events.py 新增
  DEDUP_FIRST 未选事件不出现在恢复列表、溢出事件仍出现。
- 回归：test_shadow_delivery.py（32 passed）、test_shadow_merge.py（32 passed）、
  test_issue_fact_store.py、test_shadow_scan_rounds.py、test_retention_cleanup.py、
  test_measure_center_storage.py、test_saq_scan_pipeline.py、test_agent_device_log_events.py
  （22 passed）、Agent 导入边界与测试导入棘轮（14 passed）、Agent control/shutdown
  套件（28 passed）。
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
