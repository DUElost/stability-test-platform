# 稳定性测试平台：第一性原理 × 长期复利全域审计

> 日期：2026-09-23（Asia/Shanghai）。性质：七域并行只读审计 + 主会话交叉核验的**综合裁决报告**。
> 基线：`main@ae232a30`（与当日早些时候的容量定向审计
> [`PLATFORM_FIRST_PRINCIPLES_CAPACITY_2026-09-23_ae232a3_codex.md`](./PLATFORM_FIRST_PRINCIPLES_CAPACITY_2026-09-23_ae232a3_codex.md)
> 同基线；本报告**不重复其 F01–F08 立案**，只引用编号并对增量证据补注）。
> 本报告与两份 2026-09-23 未跟踪审查稿（codex 容量稿、ADR-0033–0051 复审稿）同树共存，互不覆写。
> 取证等级同 codex 稿：`代码确定` / `历史记录` / `模型推导` / `待验证`。全部结论不依赖连库、
> 不依赖请求生产端点、不读凭据。

> **落盘附注（2026-09-23，落盘 Execution 追加，不改写上文快照）**：
> ① 本稿是当日五份在仓审查稿中**最后落盘**的一份——U1/U2/U4/U5 已分别经 #3211、
> #3212（+ #3224 立案登记）、#3220、ADR-0033–0051 复审入主干；落盘时 `main` 已前进至
> `1d1bd33c`，故上文「两份未跟踪稿同树共存」的表述已过期（二者均已合入）。
> ② **P0-7 已解决**：ADR-0051 Phase 1 bundle 发布根已于当日实切（`systemctl cat` 实测
> `WorkingDirectory` 指 `stp-releases/current`、软守卫已从 unit 移除；见
> [`../notes/process/2026-09-23-sop-phase1-bundle-cutover.md`](../notes/process/2026-09-23-sop-phase1-bundle-cutover.md)），
> §2 的 P0-7 按「已执行」读，不再是活口。
> ③ 本稿缺口已登记入波次台账 [#3230](https://github.com/DUElost/stability-test-platform/issues/3230)：
> G3 备份/恢复/告警 → #3233、G4 数据资产/终态持久层 → 待 ADR、G5 重连 jitter → 随 R1 排期；
> 与 U1 的重合项 G1 准入持锁窗 → #3231、G2 刷机路径 → #3232。
> ④ **公开面裁剪**：§2 P1-4 中三项可直接利用的端口/服务指纹已按公开仓口径脱敏，
> 原始取证留档于仓库外（位置见配套 Agent Note 的 Verification 节）；脱敏只裁剪公开面，
> 不改结论与整改判据。

## 0. 执行摘要

**输入事实**：平台现状 ≈48 host / 862 device 在场；目标 150 host × 25 device = 3750 device
（3.1× / 4.3×）。8 个月、6293 commits、1706 merged PR、53 ADR、docs 156k 行、产品代码 ~130k 行、
Agent 脚本树 35 族 210 版本目录 130k 行。单人 + 多 AI Harness 维护。

**第一性原理定位**：本平台的本质产品是三件事——
①把 3750 台设备的物理时间变成**可信的可靠性结论**（MTBF/故障率/回归判定）；
②把崩溃证据变成**去重、可归因、可复现的缺陷单**；
③以上全部在**无人值守**下成立（单人运维 = 自动化即人力）。
凡不服务这三件事的复杂度都是税。

**四条核心判断**：

1. **执行调度核方向正确、证据不足**。租约/fencing/准入队列/批量续租/permit 分层
   （ADR-0019/0026 系）是按「长跑而非瞬时」语义设计的，外推到 3750 不必然破，
   但**从未在 150/3750 的任何分档上验证过**（B1 合成 150 host 从未跑、B2 真机最高 87 台、
   fake_agent 是功能夹具非压测设施）。当前它是「架构正确性资产」，不是「容量证明」。
2. **最先坏的不会是吞吐，而是真实与存续**。控制面连接预算算术不成立（codex F01，已有一次
   生产伤害记录 #2959）、告警链闭环自依赖平台自身、备份同盘无演练、证据链终态 3 天后
   连文件带行全族自焚——这组问题的共同点是：**平时完全无症状，出事时不可恢复**。
3. **扩展成本正在双向背离**：平台面（编排/聚合/前端/告警）已做到专项无关，第 N 个专项
   趋近一个 JSON——复利递减正确；脚本面是全量副本 + 上线后 fix 风暴（powercycle 三周
   15–23 commits、相邻版本 97.7% 相同）——复利递增错误；工具类型面（apk/jar/pwsh/插桩
   直接执行、Windows 宿主、能力匹配调度）当前**零路径**。业务目标「持续新增多语言工具」
   与执行契约现状不相容，ADR-0051/0033 是既定出口但 fleet 未切换。
4. **治理体系本身越过了边际收益拐点**。9 月 fix:feat 比从 5 月的 1.67 升至 ≈4.5–7.6
   （口径不同、方向一致）、docs-only 提交 25%、merge 簿记占 61%、Agent Note 0.86 篇/PR、
   对审查的审查最深 11 稿、13 篇 ADR 半落地、8+ 默认 off 双轨开关。可执行不变量（S1–S15
   自证门禁、对账自动化）是体系里唯一还在强正复利的部分；**登记簿型文档复利已转负**，
   开始消耗扩容所需同一份注意力预算。

**一句话裁决**：平台用 8 个月把「控制的正确性」建到了超出当前规模的水位，但「数据的复利」
（签名库/设备健康史/MTBF 史=这个平台应该越用越值钱的部分）是零，「容量的证明」是零，
「存续的兜底」（离机备份/出带告警/第二人可恢复）接近零。150/3750 之前，先补这三个零。

---

## 1. 负载模型（150/3750 全在跑稳态，代码默认值推导）

来源常量：心跳 hint=`clamp(20+本机健康数//10,15,60)`→25 台/host 为 22s
（`backend/services/agent_host_heartbeat.py:39-42`）；claim 轮询 5s（`backend/agent/claim_loop.py:51`）；
租约批续 60s/TTL 600（`backend/agent/lease_renewer.py:232`）；coordinator 心跳 30s
（`backend/core`/settings `coordinator_heartbeat_interval: float = 30`）；step_trace 批 5s/100；
patrol 300s/job（`backend/agent/pipeline_engine.py:2239`）。

| 交互 | 公式 | 150/3750 结果 | 备注 |
|---|---|---|---|
| host 心跳 | 150/22 | 6.8 req/s | 重路径 `/api/v1/heartbeat` 是唯一权威通道；轻路径 `/agent/heartbeat` 仍零调用方（遗留双通道） |
| device `last_seen` UPDATE | 3750/22，逐行不批量 | **≈170 行写/s**（`api/routes/heartbeat.py:535,639`） | codex F01 同款，补：commit 单点 |
| 租约续期 | 150/60 ×(1 CAS UPDATE+≤3 桶) | 2.5 req/s, ≈125 行/s | **批量化已落地=资产** |
| coordinator 心跳 | 3750 job 态/30s 逐 job UPDATE | ≈130 行/s | 逐 job 放大点 |
| recovery-sync | 150/60 ×逐 job 3×FOR UPDATE | 2.5 req/s，**≈255 锁读/s** | 全链最重锁源（`services/agent_recovery.py:319-357`） |
| claim 轮询 | 150/5（空闲期） | 30 req/s 恒定 | 无退避无 jitter |
| patrol 心跳 | 3750/300 | 12.5 req/s | |
| step_trace | 批 100/5s | ≈2 req/s，**≈200 行 INSERT/s** | 逐条 ON CONFLICT（`services/reconciler.py:34-62`） |
| **合计** | — | **周期 REST ≈66 req/s、DB 写 ≈650–750 行/s、读 ≈1000+/s（~315/s 携行锁）、WS ≈200 conn** | 全部压单 uvicorn 进程 |

UPDATE churn 外推 ≈**3,000 万行/日**（device 14.7M + job_instance 10.8M + lease 5.4M）；
实测生产增速参照：一周 4 天涌入 3 万 job（09-19 powercycle 批次，从备份 dump 行数对拍），
gz 备份 26→62MB/周。`step_trace` 当前 536k 行且 recycler 每 30s 做 2 次全表级聚合 +
pass4 无 LIMIT `.all()`（`backend/scheduler/recycler.py:294-298,1303-1351`）。

## 2. 发现清单（跨域去重后按「谁先坏 × 后果可逆性」排序）

### P0（150/3750 前必须处置，或已在今日越线）

- **P0-1 连接预算不成立 + 终态热点行放大器**｜`backend/core/database.py:254-303`（双池
  30+60×2=峰值 180）vs `deploy/postgres/docker-compose.yml:28`（max_connections=100）；
  终态化每 job 1 次 run 行 `FOR NO KEY UPDATE`（`services/job_terminalization.py:146-152`），
  3000 job 终态=3000 次串行排队、等待方各占 1 连接。ADR-0047 Proposed，#2959 历史记录
  TooManyConnections 已伤及终态回传。**×实例数复利恶化**。｜覆盖：codex F01（热点行量化为新增）。
  最小解：裁决 ADR-0047 总预算+快失败语义；计数器改 SQL 原子增量。
- **P0-2 单进程单点且「>80 device 应重启多实例」的既定判据已字面越线**｜uvicorn 无
  `--workers`（`deploy/control-plane/systemd/stability-backend.service`）、APScheduler 内存
  jobstore、SAQ 进程内、socketio 默认 PyManager（`STP_SOCKETIO_REDIS_ADAPTER=0`）；
  ADR-0025 D1 重启条件「设备池>80」在 862 台现状下已不满足而系统仍在跑=**该判据已失效未重裁**。
  崩=全停、Restart=5s 但无零停机、66 req/s+700 写/s 全挤一个 event loop。｜覆盖：09-11 C-02、
  codex F07（「已越线」表述为新增）。最小解：要么按判据启动 ADR-0027 多实例前置（P0-1 是其前置），
  要么修订判据并书面接受单实例风险。
- **P0-3 告警/备份/恢复三件套不闭环，出事即全灭**｜Alertmanager 唯一 receiver 回打**平台自身**
  webhook（`/etc/prometheus/alertmanager.yml:9-14` 实测）；35 条规则**零** backup/磁盘水位规则
  （grep 无命中）而中心盘 sda1 916G 已用 **78%**；备份同盘、0664、无离机/无加密/无恢复演练/
  RPO·RTO 零文档化（`scripts/pg_backup.sh:26-51`）；「整机重建/从备份恢复」无任何 runbook=
  第二人不可独立处置的最大缺口。｜覆盖：无既有立案。最小解：任一出带渠道（SMTP 代码已存在）
  + `pg_backup_success`/disk 两条 freshness 告警 + 一次恢复演练记录 + 备份离机。
- **P0-4 fleet 重连风暴零治理（150 台是乘法）**｜全 Agent 零 random：claim 固定 5s=150 台
  恒定 30 QPS；WS 退避同相封顶 30s→控制面恢复瞬间 ~75 conn/s；outbox 5xx 无限重试，
  洪泛上界 150×20/15s≈200 QPS；限流按 IP 分桶对 150 台≈无效且 heartbeat 豁免
  （`backend/api/limiter.py:241`）。｜覆盖：早前审查提过零 jitter，**现状仍未修**（本次复核）。
  最小解：退避全加 jitter + fleet 级准入桶；与 codex B3「断线接管」演练同批验。
- **P0-5 adb 假死整柜误翻 + xHCI 自动 rebind 是纸面能力**｜`adb devices -l` 10s 超时返回
  `[]` 无重试无阈值（`backend/agent/device_discovery.py:378-380`）→一次假死 25 台集体 OFFLINE
  （60–85s 落库），与真波次在告警面不可分；`xhci_auto_rebind.py` 决策层完整（#2972）但
  **生产零调用方**（grep 复核），25 台≈同控制器（incident-2026-07-29 实证 16 台单控制器），
  控制器死=整柜失明、纯人工。#2957 journal 组未授权致内核 L1 判据大面积「未知」。
  按 3750×日 1% 异常（推断）≈38 起/日，单人不可持续。｜覆盖：incident 记录、#2972 Revisit。
  最小解：连续 N 拍防抖 + server 健康探针；xhci 接线+fleet 并发闸（即其 Revisit 项）；授 systemd-journal 组。
- **P0-6 证据链 3 天自焚、数据复利为零**｜`plan_run_retention_days=3`
  （`backend/core/settings/scheduler.py:110`）级联删 StepTrace/DeviceLease/JobArtifact/
  JobLogSignal/DeviceLogEvent/Job/PlanRun 行 + 中心 `devices|dedup|jira|_meta|jobs` 全族
  （`backend/scheduler/cron_scheduler.py:918-953`、`backend/storage_families.py:32-42`）；
  且保留只认 `SUCCESS/FAILED/PARTIAL_SUCCESS`——**CANCELED/UNKNOWN run 及其证据永不删除**
  （`cron_scheduler.py:441`，单调堆积+口径不一致两头都错）。问题签名 100% 在厂商 xls 黑盒内、
  平台无 signature 列、merge 强制本轮封闭→**跨版本回归/已知问题库/MTBF 史结构性不存在**；
  `plan_run.build_version` 有列零读方；全仓无 MTBF/故障率/可用率计算。
  这是第一性原理下最大的一条：平台最终产出的载体被设计成「消费一次即焚」。｜覆盖：无既有立案。
  最小解：立「终态持久层」ADR——签名行+DLE 摘要(serial/类型/构建版本)+设备健康时间线落
  永不随 run 删的表，Jira key 回写挂签名；先于任何容量扩容，因为它是唯一让平台
  「越用越值钱」的投资。
- **P0-7 部署源=多 AI 免确认会话共享的开发工作树，守卫是软守卫且切根决策已裁决未执行**｜
  systemd `WorkingDirectory=/home/debian13/stability-test-platform`（实测 `systemctl cat`）；
  `check-deploy-source.sh` 挂 `ExecStartPre=-`（失败也启动）；ps 实测本机 2×claude
  `--dangerously-skip-permissions` + 2×codex `--yolo` 同树（事实陈述：这是运行形态风险，
  非行为指控）；bundle 树 `/mnt/stp-aee/packages/` 37 个发布单元已就位但 **ADR-0051 Phase 1
  切根未执行**（当日复审稿 :83 同判）；`STP_SCRIPT_PACKAGES` 默认 off。任一会话误切分支=
  下次重启进生产；历史上「生产跑在 CI 分支」已发生过。｜覆盖：ADR-0046→0051 D6 已裁决。
  最小解：执行 Phase 1 切根（决策已在，只欠动作），之后退役软守卫。

### P1（150/3750 爬坡中会兑现）

- **P1-1 归档链吞吐=单机串行**：merge 全局单 flock（`backend/services/dedup_scan.py:884-894`
  复核）+ 跨实例无互斥（#2189 已登记）+ 中心存储与控制面同机同盘（78%）。E-4 merge wall time
  未测=吞吐未知即上限未知。解向：`(run,platform)` 分片锁、merge 下 worker（ADR-0033 Phase2
  B1 已登记）、中心存储迁独立盘（存储角色文档的既定终态）。
- **P1-2 读端点写库与无界查询**：`GET /devices` 每请求 ≈153 条 SQL（逐行 `device.host`
  懒加载 + 命中翻转**写状态并 commit**，`backend/api/routes/devices.py:43,439-442` 复核）
  ×前端 10s×4 页×查看者；`/results/summary` 对 job_instance 无时间界全表 GROUP BY
  （`api/routes/results.py:224-230`）。codex F05 只标「未验」，SQL 计数与写放大为新增。
- **P1-3 大 PlanRun 事务形状**：准入单事务 `FOR UPDATE` 锁全部目标 host+device 行至 commit
  （`services/admission_pump.py:99-105` 复核）；abort reaper 与 abort-ack 各有一处 O(n²)
  （`device_lease_reconciler.py:566,656-677`、`agent_completion.py:438-457`）；session_watchdog
  网络风暴形态单事务 150 host N+1+3750 锁改写。
- **P1-4 共享 AGENT_SECRET + 明文外溢面**：单值全 fleet、host_id 客户端自报无绑定
  （`backend/core/agent_secret.py:19` 五处读全局 env，models 无 per-host 列）；nginx 配置文件
  权限过宽（0644）且硬编码注入 `X-Agent-Secret` 的 webhook 头；控制面宿主上另存在
  **无鉴权的告警接收面**（同网段可静默/注入告警链）、**开放代理监听**与**远程管理/文件共享
  端口暴露**；防火墙规则非 root 不可核实（推断 Debian 默认全通）。internal 无 TLS（#46 未落地，
  443 不监听）。ADR-0035 §6 升级触发条件应随 150 台正式重评。
  > 公开仓脱敏（2026-09-23 落盘）：上述三项的**具体端口与服务指纹不在本稿内**，
  > 原始取证留档于仓库外（见页首附注 ④ 与 Agent Note Verification 节）。
- **P1-5 执行契约与扩展目标不相容**（详见 §3）：runners={python,shell} 硬编码
  （`backend/agent/pipeline_engine.py:1766-1773` 复核）+ catalog 只认 `.py/.sh`
  （`services/script_catalog.py:21-24`）+ **能力匹配调度零建模**（`backend/models/host.py`
  无 os/arch/capability 列，派发=device_ids 人肉清单）→ 异构工具接入要么改核心要么错派无防线。
  codex F06 裁决「勿先加 script_type」方向维持：宿主/契约/适配器收敛先行。
- **P1-6 控制面↔Agent 契约双定义无版本号**：Agent 手拼 dict（`agent/heartbeat.py:78`）vs
  服务端 `HeartbeatIn`（`api/schemas/host.py:183`）；19 个 Agent 端点仅靠 digest gate +
  `agent_min_version` 协商；前端 `types.ts`（2401 行）手工同步、60 天 99–117 commits 触顶。
  150 台灰度期协议变更=改两处+靠运行时 4xx 暴露。
- **P1-7 半落地双轨存量税**：8 个默认 off 开关（含 `STP_SCRIPT_PACKAGES` 三态、
  SOCKETIO_REDIS_ADAPTER、EVENT_UPLOADER_PRUNE_LOCAL、XHCI_AUTO_REBIND、DEDUP_AUTO_SCAN…）、
  ≈13 篇 Accepted-but-partial ADR、4 条 heartbeat 端点并存、5 套内容/版本哈希并存。
  每条双轨是未来每次改动的分支相乘。**每条需写死切换 deadline，到期删旧路。**
- **P1-8 fleet 更新面口径**：ansible 更新面 14 台/配置面 48 台、34 台 legacy 走热更通道
  （无快照、串行 ~3s/台、忙碌即跳——实测一次 11/48 成功 37 跳过）；agentctl 无 rollback；
  systemd 无 `WatchdogSec`、`TimeoutStopSec=30` 截断无界 shutdown、StartLimit 打满永久 held。
  150 台外推：一次全 fleet 升级 ≈8–10min+金丝雀+二跑，但决策与失败处置全程人工。
- **P1-9 治理吞吐指标失真**：9 月 4184 commits 中 merge 簿记 61.5%（其中 "Merge main into
  branch" 同步噪音 ≈1100 条/30%）、docs-only 25.2%、fix:feat 比 5 月 1.67→9 月 4.5–7.6。
  以 commit 数看是产能巅峰，以 first-parent 非 docs 看净产品变更流趋平。**用错口径做决策
  会把「治理+返工」误读为「交付加速」。**

### P2（复利负、慢性）

recycler 全表聚合无索引支撑且 ABORTED/CANCELED run 永不出保留（两头都错，见 P0-6）；
`mtbf/`、`tools/` 中心目录不在 `ALL_FAMILIES` 测量口径（`storage_families.py:32-42` vs
`docs/design/2026-storage-roles-and-aliases.md:18`）→容量对账系统性少算；
job_log_signal 孤儿无自动清理 + reconcile 每 5min 全表扫；`LeaseStatus.EXPIRED` 零写入僵尸态；
DLE 9 态无转移表；services→api.schemas 22 条反向边合法通过分层门禁（`check_layering.py:2-9`
只拦 routes）；`pipeline_engine.py` 2673 行 ≥10 职责不在 god 棘轮（棘轮只钉 4 只文件）；
specialty 字典无 REST 管理；suite 导入=runtask.xml 专属；NTP 检查仅控制面 precheck；
根 tests/ >240s 无耗时棘轮；`outbox` 无积压上限。

## 3. 扩展成本专项：双向背离的量化

| 扩展动作 | 当前成本 | 趋势 |
|---|---|---|
| 新增专项（同 SoC 同宿主） | runbook 6 步+7 检查单；前端/告警/聚合**零改动**；模板+脚本族；powercycle/sleep 首引各 11 文件 ~2.5k 行 | 平台面递减（第 N 个≈1 JSON）=**资产** |
| 专项上线后 3 周 | powercycle 23 commits（19 fix）、sleep 15（14 fix）、版本目录 26/14 个 | 脚本面递增=**负债**（全量副本使每次修复 ×版本数） |
| 新增工具语言/形态 | 一律 python 包装 + 全量版本目录；apk/jar/pwsh/exe 无一等类型；**Windows-only 工具零承载路径** | 每种新形态重付一次包装税=**负债** |
| 新增项目/SoC 刷机 | MTK-only 全链（vid 门控）；新机型=manifest+新版本副本；新 SoC=全新族；QCOM 零代码 | 线性增长、无能力调度兜底 |
| 复制冗余实测 | 相邻版本 diff ≈60/2600 行（97.7% 副本）；`_lib.py` 544 行 ×3 族字节级相同 | ADR-0051 strict+删 210 目录是唯一出口 |

注：`docs/reviews/SCRIPT_VERSION_BLOAT_ENDGAME_FEASIBILITY_2026-09-10.md` 与 ADR-0051 已把
方向裁决完毕；本审计的增量是——**在 fleet 切换完成前，新增每一个专项/版本都在给 Phase 3
的删除清单加项**，扩容与包模型正在互相等。给「strict deadline → Phase 3」写死日期是打破
互等的最小动作。

## 4. 复利资产清单（勿退回）

1. **执行事实层**：状态机单校验表+终态唯一入口+O(1) 计数器聚合+PG 部分唯一索引
   （每设备单活）+租约批续 `FOR UPDATE SKIP LOCKED`+fencing token——最高频交互均无逐 job 放大。
2. **背压设计**：心跳 hint（服务端按本机设备数→Agent clamp 15–60s）、step_log 限速随规模
   收紧且落盘零 DB、material-only 设备推送、dashboard 1Hz 合流。
3. **持久 outbox 语义**（DLE）：DB 事实驱动、重试落本地持久面、队列满可感知——codex F08
   已裁 JobArtifact 道与其「同形但缺失」，这是正确的复制模板。
4. **可执行不变量**：S1–S15 全部红绿双向 self-test、check:quick 24s 全绿、env/schema/包清单
   三方对账、存储族单源+越界不删+持锁窗口判据注释——治理体系里唯一强正复利的部分。
5. **发布纪律**：金丝雀+20% serial 门禁+digest 相等即 no-op+fail-closed 升级链；
   ADR-0037 提权 wrapper、ADR-0038 退役语义已实现。
6. **自持防线**：earlyoom 保护名单（PG/Redis/uvicorn 优先存活）、硬件 watchdog（30s/10min
   reboot）——整机硬挂可自恢复，这是单人运维的隐性地基。
7. **知识资产**：53 ADR 溯源链、10 SOP skills、incident 复盘文化——bus factor 的现有对冲。

## 5. 复利负债与「最该停止做的事」

1. **停止「每 PR 一 Note、同题 N 稿 review」**：Note/PR 已 0.86、最深 11 稿、09-23 当天
   三个新 issue（#3203/04/05）全是修审查产物——审查产能已反噬决策带宽。一题一 synthesis，
   复审判据化（触发器未命中不得重开）。
2. **停止在旧路退役前开新路**：包模型/提权/心跳通道/inotifyd 四线并存，每条永久税。
   新行为照 ADR-0051 走包，不开第五线。
3. **停止以 commit 数为效能口径**：61% 是 merge 簿记。改 first-parent 非 docs 口径。
4. **停止在 210 个冻结目录副本上继续接专项**：与 §3 结论绑定，strict 切换限期。
5. **停止「登记即完成」的台账义务扩散**：DOC-MAP 行内嵌数百字摘要的做法维护成本已高于
   信息价值；文档跟着代码生成（对账方向），不是代码跟着文档登记。

## 6. 改造顺序（先保底、再证明、再扩容、最后堆能力）

- **R0 存续兜底**（周级，先于一切扩容）：裁决 ADR-0047（P0-1）；出带告警+备份 freshness+
  磁盘水位两条规则+一次恢复演练（P0-3）；`submits_dropped` 接心跳观测面（codex F08 出口，
  低成本高杠杆）；ADR-0051 Phase 1 切根执行（P0-7）。
- **R1 fleet 韧性**：jitter+退避+fleet 准入桶（P0-4）；adb 防抖+server 探针（P0-5）；
  xhci 接线+journal 组；systemd watchdog/TimeoutStop 修正（P1-8）。
- **R2 数据资产层**（P0-6，第一性原理优先级最高的**新增能力**）：签名/DLE 摘要/设备健康
  时间线/build_version 激活的终态持久层 ADR + MTBF 分母（有效运行小时数）推导。
- **R3 吞吐证明**：codex 稿 B1/B2/B3 阶梯照单执行——先造规模验收设施（fake_agent 升级为
  可编排+测 p99），合成 150 host→1×25 真机→分档；merge 分片锁+中心存储迁机在 B 阶梯
  撞墙前完成（P1-1）。
- **R4 扩展契约**：capability 建模（心跳上报+派发交集过滤）；宿主/契约/适配器收敛
  （codex F06 方向）后 apk/jar/pwsh 以包内适配器进入；types.ts 走 openapi 生成；
  agent 契约单源+protocol_version（P1-5/6）。
- **R5 治理减法**：§5 清单执行；双轨 deadline 全表化。

**明确不做**（与 codex Alternatives 一致并维持）：先加 worker / 先调大连接池 / 抬 1200 页限 /
每后缀造一个 script_type / 抬 256 队列为无界 / 生产库试跑。

## 7. 证据边界与未决

- 未连生产库、未请求生产端点、未读凭据（按 AGENTS.md 红线）；「默认值生效」类结论以代码
  默认+`.env.example` 为据，生产 `.env` 实际覆盖**未核实**（推断）。
- 心跳/merge/scan 的真实 wall time 与分布未测（禁压测）；§1 全部为模型推导，是压测输入
  不是吞吐承诺。
- 防火墙规则（需 root）不可读；`.bak-*` 文件与 0664 备份的暴露面按权限位事实陈述，
  内容未读。
- `docs/reviews/` 当日另两份未跟踪审查稿（codex 容量稿、ADR 复审稿）归属其他 Execution，
  本稿只引用不裁定其归属；本稿同样**未提交**，落盘即归属本 Execution 声明。
- ADR-0041 site 列当前为零（正确最小模型），但「只读总览永不合库」这一前提应在 ADR 显式
  钉死，否则未来合库=全唯一键复合化=改核心（架构域结论）。

## 8. 方法

7 个只读子审计并行（控制面容量 / 执行面 fleet / 日志证据链 / 专项扩展 / 架构模型 /
工程经济学 / 生产形态安全），主会话对每条 P0 级断言做了独立 grep/sed/统计复核
（runners 字典、retention 状态白名单、GET 写库、准入锁、merge flock、告警规则、
AM receiver、备份曲线、fix:feat 口径），并对同日 codex 容量稿做了重叠去重。
吞吐/文档/治理数字来自 git 统计与备份 dump 行计数（无数据内容读取）。
