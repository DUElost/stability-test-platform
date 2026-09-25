# ADR 待裁决项一轮收口（9 个 ADR 的未裁子项）

Status: implemented
Class: architecture

## Decision

对全部 ADR 做了一次清查。`Proposed` 状态为零，但正文里仍有 9 处未裁子项，本次在 owner 授权下
**全部作出裁决**。这次只改文档，不改代码、规则、迁移或生产状态。判断的标准是：当前代码里这个问题是否仍然存在，以及最小的本质解是什么。
代码事实都在 `origin/main @ 0aba48e` 上读码核对过。

| ADR | 原状态 | 裁决 | 关键依据 |
|---|---|---|---|
| 0038 §7 D9 | v0.3 草案，待 owner 裁决 | A1/A3/A4/A5 采纳草案取向；A2 定为 UsbBlind **和** AdbOfflineConcentration 都豁免，内核证据类两条不豁免；新增 D9.8 `StabilityHostDeviceIntentStale` | 心跳会把未再出现的设备行确定性地改写为 `adb_state="offline"`（`heartbeat.py:139-165`），所以空置机上 OfflineConcentration 一定会同形误报。D9.8 用「设备回场」这个可观测事实兜住 D9.7 接受的静默风险，不靠自动过期 |
| 0053 | §5「跨 run 是否重复提单」待裁 | 新增 D7：提单单位是「故障发生」 | 观察、内容、发生是三种身份（§1.3）。同一发生不重复提单；baseline 首次出现默认不提单；同签名的新发生照常提单；身份无法证明时 fail-open；随 Phase C 落地，现行提单链路不改 |
| 0047 D3/D4 | 保留开放 | D3 不合并双池；D4 不引入 pgbouncer；两项都带复评触发器 | 真机峰值 async 4 / sync 11（预算各 40），D1 门禁已经覆盖总量。会话级 advisory lock 的归属问题没有解决之前，不进入 pgbouncer 选型 |
| 0052 D6 / §7 | D6 延后；§7 四项开放 | D6 改判为不采纳，保留触发器；§7 定值：不设固定窗口，聚合者用排空循环；单批初值 500；pending 行消费即删；表名 `plan_run_pending_aggregation` | 排空循环由 SAQ 按 key 去重与 §5-③ 的 120s 收敛要求推出，不是压测才能决定的结构 |
| 0031-A §7 | 标注「评审时裁定」，但没有回填结果 | 中止权限与 API 对齐，不收紧；审批路径不设助手专属设备上限；`wifi_pool_id` 必须传显式 ID | 已合入的实现就是这样（`plan_run_ops.py`、`t2b_allowlist.py` 20/50、`dispatch.py`）；D8 原则是助手权限 ⊆ 账号的 API 权限 |
| 0030 开放问题 3 | 「P1 评审定」 | `X-Agent-Secret` 对套件/用例管理面零授权 | agent secret 是全机群共用的凭据（ADR-0035 §1）。CLI 发这个头只是为了让 `/auth/token` 请求通过 CSRF 中间件，不构成授权 |
| 0028 阶段 4 | PRUNE_LOCAL fleet 待决策 | 不在 fleet 开启；终态出口是 ADR-0053 Phase B | HddSpill 已兜住磁盘安全（#382）；scan/baseline 依赖本地证据；真正稀缺的是中心盘 |
| 0012 第 2–3 层 | Proposed | 第 2 层收敛为「S/A 级 PlanRun 自动生成 `upload_list` 清单」，待实施；第 3 层无人值守建单不采纳 | 2026-09-19 交付出口裁定：唯一建单路径是 JiraRun。实施前置：先实证厂商 `upload_list` 阶段不向 JIRA 写入 |
| 0023 D2–D8 | 自 06-12 起 Proposed | D2/D3/D4 接受，待实施；D6 改判为源头守卫；D5/D7/D8 撤销 | D5 已被 `ResourceAllocation` 取代，D7 已被 `/scripts/{id}/usage` 取代，D8 与退役 SOP 的判据冲突；D6 读码发现 `script_catalog.py` 处理 `retired: true` 时不查 plan_step 引用，这是唯一的停用入口缺口 |

本次改动的文件：上表各 ADR 正文（头部状态与版本记录、裁决节）、`docs/adr/README.md`
（主表行、M3/M7 看板、「ADR 内未裁子项：无」）、`docs/DOC-MAP.md`（0038/0047/0052 行）、本 Note。
2026-09-22 起草的 D9 草案 Note（`2026-09-22-adr0038-v03-draft-device-intent-3159.md`）作为历史记录保留，不做改写。
它在 Revisit 里设想「裁决后把 §7 提升到 §2」。本次没有这样做，而是在 §7 原位加上 §7.7 裁决记录。原因是 §7 自带
触发、事实、备选和验收，整节搬动只会打断既有引用（#3159、规则注释），不增加信息。

待实施的后续工作（本 PR 不实施）：已有实施单追加了裁决指针评论——#3159（D9 + D9.8）、#3244（ADR-0052 §7 定值）、
#3308 Phase C（ADR-0053 D7）；新开跟踪单——#3351（ADR-0012 第 2 层）、#3350（ADR-0023 D2–D4）、
#3349（ADR-0023 D6 源头守卫）、#3348（灰机 `192-0-2-143` 的 `PRUNE_LOCAL` 回退为 0，运维动作）。
偏离原草案/原文的 3 项裁决（0038 A2、0052 D6、0023 D6）的 owner 确认在 #3347 跟踪；确认后直接关闭，不需要改动。

## Alternatives

- **把「延后」和「保留开放」原样留着**：这样它们会继续出现在待裁清单上。ADR-0052 D6 与 ADR-0047 D3/D4 手上已有否定证据，
  所以改成「否决 + 复议触发器」，结论闭合，同时保留重开的条件。
- **ADR-0038 A2 只豁免 UsbBlind，其余等实测**：读码已经确定 OfflineConcentration 在空置机上必然误报，再等实测只是把
  已知的误报推迟暴露。
- **ADR-0053 同签名也在平台侧去重**：这样会压掉复现频次这一修复优先级信号；按签名并单应由厂商工具 / JIRA 负责。
- **ADR-0023 D6 按原文做 PlanList 徽标**：这是事后补救。所有停用入口共用同一个守卫，才是在源头消除这种状态。
- **ADR-0012 第 3 层直接开放无人值守建单**：误报工单的清理成本高于人工确认的成本，所以先用 dry-run 清单攒数据，
  并给出量化的复议触发器。

## Verification

- 读码锚点（`origin/main @ 0aba48e`）：`backend/api/routes/heartbeat.py:139-165`、`backend/api/routes/metrics.py`
  （`_ADB_STATE_BUCKETS`、在册 host 过滤）、`deploy/prometheus/alerts-stability-platform.yml`（四条设备面规则）、
  `backend/services/ai_assistant/{t2b_allowlist,dispatch,plan_run_ops}.py`、`backend/api/routes/{suites,plan_runs,scripts,plans}.py`、
  `backend/core/csrf.py`、`tools/dev/mtbf-cases.py`、`backend/agent/event_uploader.py`（`_maybe_prune_local`）、
  `backend/services/{plan_dispatcher_sync,script_catalog,script_params,script_retirement}.py`、
  `backend/models/jira_run.py`、`backend/services/jira_vendor/stability_jira.py`。
- 在飞冲突检查：`ai_work.py status` 显示 registry 为空；3 个在开 PR（#3339/#3340/#3343）都不触碰本次改动的 ADR 正文。
- 门禁：`python -m pytest tests/test_adr_index_status_2989.py`（ADR 索引一致性 + M7 看板覆盖）与
  `python scripts/run_gates.py check:quick`，结果见 PR 描述。

## Revisit

- 各项的复议触发器写在对应 ADR 里：0038 §7.5 与 D9.8、0047 D3/D4、0052 §9 v1.1、0031-A §7、0030 v1.10、
  0012 第 3 层、0053 D7 规则 4（D4 强身份落地后，fail-open 的适用面会收窄）。
- 下次清查时以 `docs/adr/README.md` 看板的「ADR 内未裁子项」一行为入口。新 ADR 若留下未裁子项，须同步登记到这一行。
