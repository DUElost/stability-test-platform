# 平台整体评估：旁观者视角（2026-11-11）

> 基线：`origin/main@c4a06a107`（2026-11-11 16:07 +08）。本稿为只读评估，不构成容量验收，
> 也不修改任何既有结论；发现的处置载体统一并入 [#3230](https://github.com/DUElost/stability-test-platform/issues/3230) 台账与各单，
> 不新增第二事实源。
>
> 命题：**一个了解项目目标但不参与日常决策的旁观者，读完背景与代码后，认为哪里该调整、哪里做得好。**
> 这与主稿 [`STP_FIRST_PRINCIPLES_AUDIT_2026-09-25_codex.md`](./STP_FIRST_PRINCIPLES_AUDIT_2026-09-25_codex.md) 的
> 「150 host / 3750 device 容量与复利」命题不同，因此独立成文；主稿 §10 的「后续复核续写本稿」约束针对的是
> **同一命题的后续轮次**（§13 即第四轮），不是禁止不同命题的独立评估。重叠结论本稿只引用、不重复论证。

## 0. 方法与边界

**做了什么**：五面并行只读取证（控制面后端 / Agent / 测试与 CI / 前端 / 安全），主会话对关键锚点逐条独立复核
（`file:line` 均可回溯），GitHub issue / PR / CI 现态只读，`.git` 内 Execution Registry 只读，文档与代码交叉对读。

**未做什么**：未跑任何测试套件、未连数据库（含测试库与生产库）、未连 Agent 主机、未使用真实浏览器、
未复核生产部署现态。前端交互与几何类结论（命中、autofill、下载、真实浏览器渲染）不在本稿范围。

**独立性声明**：本稿产出方为 Claude Code 本地会话，与主稿 §13（第四轮复核）不同会话。五面取证同属一个会话的
并行子代理，**不互计多源独立佐证**；其中前端与安全两面深度低于其余三面（子代理因 API 限流中断后由主会话补做），
相应结论均标注了证据强度。

**与既有稿的关系**：容量、存续、证据完整率类结论以主稿 §13 与本仓既有单为准，本稿不重复、不覆盖。
本稿新增判断集中在两处（§2.1、§2.2），已分别立单。

## 1. 项目要解决什么

平台要替代的对象是「人工在几十台手机上跑稳定性专项、人工盯日志、人工收结果」。它给出的承诺写在
[`project-vision.md`](../project-vision.md)：**通过 Linux 主机 Agent 集群，无人值守运行大规模 Android 设备稳定性自动化测试**，
并把专项执行闭环（设备连接检测 → 前置准备 → 资源填充 → 运行 → 日志检测 → 风险检查 → 回传导出 → 结束 → 后置）
串成统一流程，事后衔接结果收取、报告生成、JIRA 提交。

这条承诺的难点不在调度，而在**证据**。设备端测试的结论（崩溃、ANR、开关机是否生效、monkey 是否在跑）散落在
设备文件系统、厂商工具输出、脚本自判结果里；平台的价值取决于能否把这些碎片变成**可追溯、可复核、能长期积累的测试事实**。
主稿 §2 把这件事说得更直接：平台的产出应是「可追溯的有效测试时长、故障事实与可复现证据」，
3750 台满负荷一天的名义上限是 90,000 device-hours，而**空转应与实际测试暴露时间区分开**。

因此评估这个项目，看三件事：执行链是否可信（任务是否真的按意图跑了）、证据链是否完整（结果是否收得齐）、
结论层是否诚实（平台说自己「成功」时，那个成功代表什么）。

## 2. 结论

**架构方向不需要改，需要改的是优先级与收敛方式。**

控制面编排 + Agent 执行 + PostgreSQL 作事实 + Redis 只作传输 + 不可变发布包这组边界，与需求相符；
执行链的严谨度（终态唯一入口、租约 fencing、幂等键、DB 级唯一约束）在本仓之外属于少见水平。
本稿不建议重写、不建议拆微服务、不建议引入新的通用框架。

真正影响长期收益的是两件事，且都不是「功能没做完」，而是**验证能力跟不上产出速度**：

1. **平台的"绿灯"还不等于"测试跑过"**（§3.1）。当刺激没有到达设备时，系统可以一路记成 `COMPLETED`，
   并把这段计入 PlanRun 成功与 MTBF 分母。这是本轮最值得优先投入的一处，已立 [#3938](https://github.com/DUElost/stability-test-platform/issues/3938)。
2. **时序 / 并发 / 崩溃恢复类判据的收敛方式成本过高**（§3.2）。同一类问题反复走「散文判据 → 反例 → 退回 →
   改一句话 → 下一个反例」，而仓库里已有成功先例证明**换数据模型能一次性消掉一整类反例**。已立 [#3939](https://github.com/DUElost/stability-test-platform/issues/3939)。

其余六项（§3.3–§3.8）是结构性问题，影响维护速度，不直接危及结论可信度。

## 3. 优势（逐条核对过代码，非转述文档）

### 3.1 不变量写在数据库约束与单一入口里

- 同设备双开：部分唯一索引 `uq_job_active_per_device` 约束 `PENDING|RUNNING|UNKNOWN` 至多一行
  （`07-execution-protocol.md` §1，DDL 在 `backend/alembic/`）。
- 终态唯一入口：`POST /agent/jobs/{id}/complete`；`/status` 与心跳只接受 RUNNING，上报终态返回
  `TERMINAL_STATUS_REQUIRES_COMPLETE`。
- 幂等：`terminal_payload_digest`（冲突 409 `TERMINAL_PAYLOAD_CONFLICT`）与 `trace_event_id`
  （含 retry/cycle 的哈希键）取代了旧的 `(job_id, step_id, event_type)` 唯一约束。
- 设备租约：`device_leases` 带 fencing 代次，释放路径有锁序纪律（Job → Device → Lease → debt，见
  `docs/design/07-execution-protocol.md` §9.5 与 `tests/test_lock_order_collection_total_order.py` 的机械约束）。

大多数项目把这些写成 wiki 条目；这里写成 DDL、单一入口和回归测试。这是本平台最硬的资产。

### 3.2 特权边界窄，且全仓无 `shell=True`

`backend/agent/stp_agent_priv.py`（约 1,600 行）是「单入口 + 子命令白名单 + 固定 argv + fd 相对操作」
（`_open_directory` / `_atomic_write_at` / `_read_regular_at` / `_reject_anchor_drift`）。
本仓 `backend/` 非测试代码 `shell=True` 出现 **0 次**（grep 口径：`grep -rn "shell=True" backend --include='*.py'`）。
`tests/` 下有 10 个专门文件覆盖该 wrapper 的边界（`test_agent_priv_boundary.py`、`test_agent_priv_target_paths.py` 等）。

### 3.3 执行语义与测试结论的分离是有意设计，不是遗漏

`07-execution-protocol.md` §2 明写「PlanRun 绿 ≠ 测试通过」，并把「INCOMPLETE 按收取即成功处理」标为设计边界；
ADR-0048 废止了 run 级通过率阈值判定，把测试结论推到结果层（`case_result` / `metrics`）。
**多数同类平台会在这里撒谎**（把「脚本按预期结束」包装成「测试通过」），本平台明确区分了执行链结论与测试结论——
§3.1 的问题正是这条区分尚未在「刺激是否发生」这一层补齐，属于该设计的延伸，不是推翻。

### 3.4 发布与部署纪律

内容寻址发布单元（ADR-0051）：已发布单元只增不改，`tool_manifest.json` append-only，
`check_tool_manifest.py` / `check_alembic_revision_immutability.py` 在 PR 路径强制；
控制面 systemd 单元有 `ExecStartPre` 硬门禁 `check_alembic_at_head.py`（代码超前于库即拒启，#1882）与
`check_db_pool_budget.py`（连接预算不成立即拒启，ADR-0047），并有 `StartLimitBurst` 防重启风暴（#2058）。
回滚口径 A/B（迁移后不得只重指 `current`）是先想清楚再动手的那类决定。

### 3.5 机械守卫带棘轮，且真的会红

`check_inner_imports.py`（函数体内 import 锁定基线 608，只降不升）、`check_god_files_ceiling.py`
（胖文件行数 ×1.05 封顶）、`test_admin_only_read_surface_register.py`（admin-only 读接口必须登记"前端可达性依据"，
且反向检查防登记表腐烂，扫描器已修过"路径换行导致端点漏扫"）、`tests/test_adr_index_status_2989.py`
（ADR 索引不得把已落地写成未落地）。这些不是文档承诺，是 required CI 里的红灯。

### 3.6 自审与自我否证的能力

主稿 §9–§13 记录了对自身旧结论的订正；`PLATFORM_LEDGER_AUDIT_2026-09-15.md` 指出"仅给汇总计数的风险表
出现登记过期"；台账漂移自行立单（#3519）；#739 把 431 处静默异常压缩到可复现基线（255 处）并写清
触发再治理的条件，而不是上一道无差别门禁。**一个项目能持续否证自己，比任何单点技术设计都难得**，也是它
能以「单人 Owner + 多 AI 会话」跑下去的底气。

## 4. 不足与需要调整

### 4.1 测试有效性事实缺位（→ #3938，本稿第一优先级）

平台记录「脚本怎么结束的」，不记录「刺激是否真的到达设备」。三个机制叠加导致误报为成功：

- 能力前置只查存在性：`backend/agent/scripts/powercycle_setup/_lib.py:645` 的 `check_reboot_permission()`
  先查 `android.permission.REBOOT`，否则 `adb shell which su` 有输出即返回 `"su"`（`:654`）；
- 存活判据是子串匹配：同文件 `:890` 的 `service_alive()` 只判断 `dumpsys activity services` 输出里是否含
  `PowerCycleService`，服务被杀后残留的 Pending ServiceRecord（`app=null`）同样命中；
- 结果层没有「刺激未发生」这一档：`INCOMPLETE` 按收取即成功（有意设计），脚本自判又按 ADR-0048 不参与
  PlanRun 聚合——两边都成立，合起来就是没有消费者能表达"这段没有产生有效测试"。

后果已在生产侧量化（引用既有单，本轮未复算）：#3722 在 110 台展锐设备上测到近 14 天 3,183 段走 `su` 重启的
开关机里 3,184 段 `cycles_done ≤ 1`，而 3,184 个 job 记为 COMPLETED；#3801 / #3807 给出 LX3 两台
（`cycles_done=0`、`expected_cycles=0`、`final_status=INCOMPLETE`，job 仍 COMPLETED）与 1466 的 root 失败路径。

**为什么这是第一位**：平台的唯一产品是可信结论。测试没有跑而报成功，会让上游所有统计（执行量、通过率、
MTBF 分母）系统性偏高。ADR-0056 D4 已把 MTBF 分母定为「租约持有时长」，理由是"租约是设备在跑测试最直接的
持久事实"（§8 裁决 F3）；#3722 正是这条假设的反例，且 ADR-0056 标注"实施未开始"、代码中
`device_runtime_daily` 不存在（本轮只读核实）。

### 4.2 时序类判据的收敛方式（→ #3939，本稿第二优先级）

2026-11-11 的两条现场记录同形：

- B8 G5（#3929 / PR #3930）：判据三轮，「按 `round_ids`」→「按 `max(merged_at)` 与墙钟比较」→被
  「墙钟回拨 5 秒」反例击穿→改**单调 merge 代次**（`scan_round.merge_generation` + `delivery_version.basis_rounds`）。
  改代次之后同类反例消失——这是**换模型消掉一整类反例**的成功示范。
- vbus 刷机（#3892 / PR #3924）：复核第 5–8 轮连续在同一区域发现竞态（在途特权操作与信号结算、门控恢复记录、
  孤儿恢复时序），第 6–8 轮按 D11.3 退回规划者，最终 Owner 拆出 [#3933](https://github.com/DUElost/stability-test-platform/issues/3933)（9 条已复现反例）。

历史上同形：B4 G2 守卫单元 10 轮未闭合（#3649，据此才有 D11）；B6 方案修订 33 次（17 条自标规划缺口）；
B7 试行后 D12 转常态。D11 第 3 条的收敛规则在 G5 上确实触发了（规划者裁定第 3、4 轮属不同被守卫性质），
但**规则每次收敛的代价仍是一轮完整往返**。

仓库自己留了一道门：`backend/requirements-dev.txt:22` 注明「旧状态机 PBT 已退役且全仓 0 处引用，
依赖与空包一并删除；**等确有 property-based testing 用例时再加回依赖和目录**」——正是这类用例。

**建议方向**（形式交规划者，不预裁）：判据写前声明可单调化的基底；验收清单里放可回放的交错测试；
新反例直接进用例而非只进评论。不新增门禁（沿 D6 约束）。

### 4.3 不可控状态住在无类型 JSON 里，消费者按「键是否存在」耦合

`PlanRun.run_context` 是 JSONB 包：**49 个非测试文件、318 处引用**，其中只有 `precheck` 一段有 Pydantic 描述
（`backend/api/schemas/plan_run_precheck.py`）；`abort_requested` / `abort_requested_hosts` / `dispatch_state`
等协议级状态没有模式，且已有四类消费者「只看键是否存在」（聚合污染、热更新门禁、recovery、派发材料化保护）。
`Host.extra` 是同类第二处（约 30 个键）。

已付代价（均为仓库内既有事实）：整段读改写把并发写者的键抹掉（#2285，改为库端 `jsonb_set` 单段写）；
host 级 abort 曾让整个 run 看起来都在 abort 中（#2270）；心跳重建 `host.extra` 只保留白名单键曾致键丢失（#3598）。

**建议**：把 abort / dispatch 这类**在协议里有语义、有并发写者、有多个读者**的状态提成列或独立表
（可加约束、可加锁、可加 CHECK），JSONB 只保留真快照。这是模型级变更，须按 ADR-0058 D2 升级 Owner。

### 4.4 协议核心逻辑长在最长、最难测的函数里

非测试代码共 **4,723 个函数**，中位数 12 行，但 >100 行 144 个、>200 行 34 个、>300 行 11 个。最长的几个恰好在协议关键路径上：

| 行数 | 位置 | 角色 |
|---|---|---|
| 999 | `backend/agent/scripts/flash_firmware/flash_firmware.py:2808` | 刷机主流程（脚本侧） |
| 589 | `backend/services/plan_run_abort.py:285` | abort 协议（8 个参数、62 个分支节点、4 处 commit） |
| 491 | `backend/api/routes/heartbeat.py:211` | 心跳处理，**且是路由函数**（81 个分支节点、3 处 commit） |
| 381 | `backend/services/agent_completion.py:240` | 终态上报（7 处 commit） |

心跳与终态上报是每台设备每分钟都要走的路。业务逻辑在路由里、事务边界在长函数中段，使「按用例隔离的
单元测试」和「事务边界审查」都变贵。**建议**：这两条路径的逻辑下沉为 service 函数，路由只做鉴权与转发
（`check_god_files_ceiling.py` 的棘轮方向与此一致，可直接调小封顶值）。

### 4.5 门禁分层与注意力预算：一处裁定 + 两处到期未结

- **信息性全量测试不阻塞 PR 是 Owner 2026-11-11 的裁定**（注意力预算），本稿不复议；本稿只记录两个附带事实。
- 该裁定的**观察期已经到期而未落结论**：预注册判据是「两个自然月或 ≥60 PR（先到者）」，自 #3580
  （`08b614b69`，2026-10-01）落地信息性作业起，first-parent 合入 **205** 个 PR（其中非 dependabot 139 个）；
  [#3572](https://github.com/DUElost/stability-test-platform/issues/3572) 仍 OPEN、零评论。
  规则本身写着「届期必须落结论，不允许静默续期」——建议按该规则结一次。
- `docs/development/repository-workflow.md:279-280` 仍写「CI 改动尚未落地」，而 `ci.yml:161` 的
  `pr-backend-tests-informational` 已在跑；这是文档与 CI 现状的漂移，与上一条同源。
- 静默异常：本仓 `tools/dev/audit_silent_exceptions.py`（工具自带口径）今日实测 **301 处**
  （pass 132 / continue 69 / return_none 100），而 #739 记录的基线是 **255 处**（110/58/87）——两个月增长约 18%。
  #739 已明确「不上一道无差别门禁」并写明再治理触发条件，本稿不重提门禁；这里只记录数字在增长这一事实，
  供后续按该触发条件判断。

### 4.6 文档与台账漂移（可机械化的部分）

| 漂移 | 证据 | 说明 |
|---|---|---|
| ADR-0052 索引行仍写「未实施」 | `docs/adr/README.md` 主表 ADR-0052 行 vs `backend/services/plan_run_finalization.py`、迁移 `d4e8f2a7c9b1_plan_run_pending_aggregation_3244.py` | 该 ADR 已于 #3370（09-27）部署验收关闭 |
| ADR-0051 状态节与 `transitions.json` 不一致 | 主稿 §13.4 N12 已记录 | 属已登记漂移（#3519） |
| 控制面部署 README 仍以「检出即部署源」描述 | `deploy/control-plane/README.md:12-14`（仓库根 `venv/`、`<repo>/.env.backend`） | ADR-0051 已把生产切成 `stp-releases/<rev>` + 站点 env；`transitions.json` 的 `control-plane-env-lives-in-checkout` 出口即 ADR-0051 D6。systemd 单元模板本身是参数化的 `<deploy-root>`，**不是**漂移（本稿订正一次口头误判） |

`tests/test_adr_index_status_2989.py` 只硬编码校验 ADR-0045 一行 + M7 看板覆盖，因此 ADR-0052 这类漂移可以存活。
**建议**：ADR 索引行的「落地状态」由机械校验或生成物承担（`environment-variables.md` 的生成式附录已有先例），
人只写判断，不手抄状态；这也能减轻 #3519 的负担。

### 4.7 脚本族复制：数字与出口

约 39 个族中，**30 个族各自定义 `adb_shell` / `device_serial` / `params` / `output_result`**，
20 个族各有一份 `progress_stamp`。归一化（去缩进与注释）后逐行比较：

| 族对 | 相似度 | 与至少一个其它族逐字相同的行占比 |
|---|---|---|
| sleep_setup / sleep_check / sleep_finish | 0.78–0.79 | 82%–85% |
| powercycle_setup / disarm / check | 0.43–0.53 | 54%–76% |
| gpu_setup / check / finish | 0.53–0.58 | 57%–70% |
| monkey_setup / monkey_teardown | 0.09 | 9% / 17% |

出口已有载体：[#3498](https://github.com/DUElost/stability-test-platform/issues/3498)（Device Execution SDK / 语义契约 /
打包期冻结）。注意其规划必须先正面回应 ADR-0039 D6 对「共享基础库」的否决——D6 的理由是「跨版共享可变面破坏
每版独立可校验」，而 ADR-0051 之后每版可校验性由 per-version tarball 的 `package_sha256` 承担，这正是 #3498
正文里说的「不能直接复用旧结论」的原因。本稿只提供上表作为该单的证据补充。

### 4.8 判定链的体量显著小于执行链，且分在两个仓库

愿景的后半段（结果收取 → 报告 → JIRA → 测试报告）在代码里是 `backend/services/report_service.py`（688 行）+
`backend/services/jira_vendor/stability_jira.py`（120 行 argv 适配器），实际提交由外部
`create_<vendor>_jira_batch_from_excel.py` 完成；去重归并同样由外部工具（start-log-scan）承担。
平台把重心几乎全部放在执行链与事实层，这是**有意的取舍**；B8（#3800）把「先汇总去重、再按问题上传代表证据」
拉回平台侧，方向正确，且是本季度最有价值的工作之一。

### 4.9 容量目标（引用，不重复）

现役 77 台主机、目标 150 × 25；B1/B2 阶梯（#105 / #106）、整批准入锁窗实测（#3231）、单主机 25 台
心跳预算（#3219，其 §13.2 订正为「测量已补、预算仍可超」）均未收口。处置顺序见主稿 §13.6，本稿不另立。

### 4.10 三处小项

- **同一张状态映射有两份且已经不一致**：`backend/api/routes/results.py:132` 把 `UNKNOWN` 映到 `RUNNING`，
  `backend/services/report_service.py:34` 映到 `FAILED`。两处都是 Job 状态映射到旧 Run 词表（`FINISHED`/`CANCELED`）
  的兼容层；ADU-0020 一次性切换后这层仍留在两处，取值分歧说明它已无单一权威。
- **前端没有浏览器级测试**：依赖里无 Playwright/Cypress（153 个测试文件为 jsdom）；`testing.md` §4 已写明
  jsdom 测不了几何/命中/autofill/下载，现有做法是静态守卫 + 人工真实浏览器验收（如 #3919 的验收记录）。
  本稿不主张引入 e2e 框架（成本高），只指出这是「UI 变更的验证强度」这一既有缺口的根。
- **共享密钥是已接受的模型选择**：Agent 侧鉴权为 `X-Agent-Secret` 单一共享密钥（ADR-0035 Accepted，
  实施未启动；#906 在跟踪）。本稿安全面只做到表面盘点（端点鉴权依赖、`shell=True`、提权 wrapper 结构），
  未做威胁建模，不作为安全结论。

## 5. 规模与结构数据（基线 `c4a06a107`）

| 面 | 数据 |
|---|---|
| 仓库 | 提交 7,942；first-parent 2,764；「Merge pull request」2,053（GitHub merged-PR 口径 2,120） |
| 非测试 Python 代码行 | 控制面 59,314（318 文件）/ Agent 核心 24,248（114）/ 脚本族 14,909（69）/ alembic 10,989（161）/ tools 22,309（77）；合计约 13.2 万行 |
| 测试 | `backend/tests` 378 文件 107,051 行；`backend/agent/tests` 227 文件 62,132 行；根 `tests/` 181 文件 42,171 行；前端 153 文件 |
| 数据模型 | 49 张表；160 个迁移脚本 |
| API | 225 个端点（26 个路由模块） |
| 文档 | `docs/` 1,826 个 md、183,204 行；`docs/notes` 1,456 篇；`docs/reviews` 81 篇；ADR 主表 64 行 |
| 测试构成（`backend/tests`，按夹具计） | 用真 PG 夹具 178 / 用 HTTP client 夹具 111 / 两者皆无 174；用 mock 或 monkeypatch 222；`conftest.py:12,101` 默认起 testcontainers PostgreSQL 16 |
| 全量后端套件墙钟 | 2026-11-11 全量 run：35 分 24 秒（`backend-test`，run 42978735779） |

## 6. 本轮对既往口径的订正（含本稿作者的早期误算，一并留痕）

| 口径 | 早期说法 | 复核后 |
|---|---|---|
| PR 合入总数 | 约 3,990 | 那是**全部合并提交**（含分支同步合并）；PR 合入为 2,053（first-parent 口径） |
| 测试构成 | 「只有 11 个用 TestClient」「约 60% 在替身」 | 按夹具统计：真 PG 178 / HTTP 层 111（夹具注入 `client`、`db_session`，按字面 grep 会漏）；mock 222 |
| 端点 / 表 / ADR 行 / note 数 | 218 / 50 / 63 / 1,455 | 225 / 49 / 64 / 1,456 |
| Agent Python 版本 | 「DEPLOY.md 仍声明 3.8+」 | 订正：`backend/agent/DEPLOY.md:69` 已是 3.10+，install 脚本与 CI floor 步骤同口径；残留的只是 `backend/agent/system_monitor.py:28` 注释里的旧引用 |
| systemd 单元模板 | 「模板未同步发布目录」 | 订正：模板用 `<deploy-root>` 参数化，不构成漂移；漂移在 `deploy/control-plane/README.md` 的描述 |
| 静默异常棘轮 | 「建议上门禁（只降不升）」 | 撤回：#739 已裁决不上无差别门禁并写明触发条件；改为只记录数字（255 → 301） |

## 7. 处置顺序建议（只列相对主稿 §13.6 的增量）

1. **先补「测试有效性」这一层**（#3938）——它决定平台上所有统计是否可信，且越晚补，历史段的回填越贵。
2. **给时序/并发判据换收敛方式**（#3939）——它决定后续每一批次的交付速度；建议与 B8 剩余单元并行推进，
   不必等批次结束。
3. 其余按主稿 §13.6 既有顺序（存续闭环 → 容量证据 → 证据闭环 → 工具生态）。

## 8. 验证与边界（本稿自查）

- **已执行**：五面只读取证（控制面 / Agent / 测试与 CI / 前端 / 安全）；主会话对关键锚点逐条复核到行；
  本轮全部统计均为基线 `c4a06a107` 现测（脚本口径见各表脚注）；GitHub issue/PR/CI 现态只读；
  Execution Registry 只读；`git log --first-parent` 取合入序列。
- **未执行**：任何测试套件、任何数据库连接、真机或浏览器验证、生产部署现态核对、`check:quick`（本稿落盘时执行，见 PR）。
- **证据强度分级**：§3.1–§3.5 与 §4.1–§4.4、§4.7 为一手代码/数据取证；§4.5 中的 PR 数为 Git 计数；
  §4.6 为文档对读；§4.8、§4.9、§4.10 为引用既有单；安全面（§4.10 末条）仅表面盘点。
- 本稿不含内部主机地址、凭据或部署拓扑细节。

## 9. 复审触发

- #3938 / #3939 有裁决结果时，重审 §4.1、§4.2 的结论强度；
- 主稿 §13 触发条件（#3928 转绿、B1/B2 结果、retention 变化等）命中时，容量相关结论以 §13/§14 为准；
- ADR-0056 实施启动（MTBF 分母落地）时，重审 §4.1 对分母的影响判断；
- 前端出现浏览器级验收能力时，§4.10 第 2 条作废。
