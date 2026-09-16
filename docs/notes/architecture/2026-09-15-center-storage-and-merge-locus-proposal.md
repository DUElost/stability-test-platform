# 中心存储结构重排与 merge 执行位置：方案提案（待评审）

Status: proposed
Class: architecture

## Decision

1. I-12（中心存储结构重排）与 I-13（merge 执行位置）**立项**，流程按 owner 裁决
   （2026-09-15）：**先定方案 → 方案评审 → 落地 → 效果对比确认"较原方案有较大优化"
   → 才修订 ADR-0025**。四项在顺序上不可倒置——先改 ADR 再验证等于把未验证的选择写成权威。
2. 两项均属 [ADR-0025](../../adr/ADR-0025-phase4-architecture-alignment.md)（方案 C 存储模型）域，
   **不新立 ADR**；效果确认后作为 ADR-0025 修订推进。
3. 本文件是**评审输入**：给候选方案、量化基线口径、迁移路径、效果判据与评审清单。
   **不含最终选型**——选型在评审会上定，本文只把选项与代价摆平。
4. 本文件不修改任何代码、不改 `docs/adr/`。

来源：[`2026-09-15-device-log-flow-issue-backlog.md`](../process/2026-09-15-device-log-flow-issue-backlog.md)
的 I-12 / I-13；问题发现于 2026-09-15 设备日志流转四层评估。

---

## 1. 基线：现在为什么要谈这两件事

### 1.1 I-12 事实（中心存储当前形态）

| # | 事实 | 证据 |
|---|---|---|
| 1 | 事件目录**存两份**：`devices/{run}/{event_id}/{name}/` 与 `jira/{run}/{name}/` | `backend/agent/event_uploader.py:390-396` vs `backend/services/dedup_extract.py:285-308` |
| 2 | merge xls **存两份**：`dedup/{run}/merge/{platform}/` 与 `jira/{run}/merge/{platform}/` | `backend/services/dedup_scan.py:788-807` vs `backend/services/dedup_extract.py:323-367` |
| 3 | `dedup/` 同时装 scan 输入产物与 merge **输出**，命名与用途错位 | `backend/services/dedup_scan.py:788` |
| 4 | `devices/` 侧用 `event_id` 消除同名碰撞（#1073），`jira/` 侧又按 basename 收敛（#386）——撞名时只保留一份、其余永远停在 REMOTE | `backend/services/dedup_extract.py:270-274` |
| 5 | 生命周期：retention 清 `devices/` `dedup/` `jira/`（先文件后行） | `backend/scheduler/cron_scheduler.py:246,344-376` |

### 1.2 I-13 事实（merge 执行位置）

merge 跑在**控制面本地**：工具固定输出到 `{工具目录}/merge_result/{ts}/`
（`backend/services/dedup_scan.py:344-346`），再 `_publish_merge_to_center` 拷到中心
（`:768-807`），期间用跨进程 flock 串行（`:350`，`#1072`）。控制面因此是有状态的处理节点。

### 1.3 必须先补的基线（当前**没有**数字）

本提案的选型与效果判据都依赖以下量化基线：

| 指标 | 口径 | 采集方式 | 状态 |
|---|---|---|---|
| 事件目录双份占用 | 同规模 run 下 `devices/` 与 `jira/` 的字节数 | `backend/scripts/measure_center_storage.py`（只读） | **工具就绪，数字待采** |
| 报表双份占用 | `dedup/{run}/merge/` 与 `jira/{run}/merge/` | 同上（merge xls 通常很小，预期收益也小） | **工具就绪，数字待采** |
| 控制面本地 `merge_result/` 体积与增速 | 目录大小 + 子目录数 + 最新 mtime | 同上（`--merge-result-root`，或从 `STP_BACKEND_DEDUP_SCAN_SCRIPT` 推导） | **工具就绪，数字待采** |
| retention 后残留 | 到期 run 清理后仍存在的目录数 | 脚本报告各 run 在四族的目录分布，**与控制面 DB 的「应已清理 run」清单对账** | 需 DB 侧清单（脚本不查库） |
| merge 端到端耗时 | scan 产物齐 → merge 产物发布完成的 P50/P95 | 现有日志时间差 | **未覆盖**（需另行采集） |

> **没有基线的"优化"不可判定**。评审应把 §1.3 的采集排在被选项落地之前。

**采集工具**：`backend/scripts/measure_center_storage.py`（只读、stdlib-only）。

```bash
# 隔离环境或生产只读（危险根会被拒绝；STP_AEE_NFS_ROOT 可作默认值）
python -m backend.scripts.measure_center_storage --center-root /mnt/center --top 20
python -m backend.scripts.measure_center_storage --center-root /mnt/center --json
```

只读边界：仅 `os.walk` + `os.stat`，**不创建/不修改/不删除**；`followlinks=False`（不越出被测根）；
不读取任何凭据文件；拒绝 `/`、`/home`、`/tmp`、`/var`、`/usr` 等根。

---

## 裁决记录（2026-09-15，owner）

| 决策点 | 裁决 | 含义 |
|---|---|---|
| D-4 推进序 | **D → A**（B/C 暂缓） | 先落 `_meta/{run}.json`（**不动目录树**），再做 §2.1 方案 A（`dedup/{run}/` 拆 `report/scan` + `report/merge`；`devices/` 与 `jira/` 分 TTL）；B/C 待 A 落地后的实测数据再议 |
| D-4 阈值 | **先采数再定** | §5 的 E-1 阈值不得先验拍定，必须基于 `measure_center_storage.py` 在隔离环境 / 生产只读采到的数字定 |
| D-5 推进序 | **先 A**（无状态化） | merge 产物落中心 staging、控制面本地不保留产物；B 作为目标态，**必须与 ADR-0033 一起推进**，不可单独裁 |
| 依赖顺序 | **D-1（ADR-0032 R3）先于 D-5** | R3 已裁为「条件裁决」：若 B3 判定需要 per-platform merge 工具，I-13 方案 A 的实现方式随之变化 |
| D-5 的 **B 段**（2026-09-16） | **B0：不提前 B + 登记实例绑定限制** | 依 **§3.1** 重评输入裁定：B **不提前**（前置 ADR-0033 未落地、其包存储为条件落地、Phase 2 样板同批、A 残留与 2026-08-09 评审同向、多实例仍 opt-in）；同时把 merge 的实例绑定**登记为 [ADR-0027](../../adr/ADR-0027-control-plane-horizontal-scaling.md) v1.8 清单第 7 条**（附启动 WARN）——**不登记的隐性限制是多实例启用时最贵的一类问题**。**B1 / B2 的触发条件见 §3.1**。本行**不推翻**上表 D-5 的「B 与 ADR-0033 同推」，只明确「不提前」；亦**不触发 ADR-0025 修订**（该修订仍绑 I-12/I-13 方案落地） |

**尚未做**（等上表落地）：ADR-0025 修订；§5 各项阈值；E-2 / E-4 / E-5 的采集（脚本已显式列为 not covered）。

---

## 2. I-12 候选方案

### 2.0 硬约束：`jira/{run}/` 是**外部引用**（历史 JIRA 提单写的是这个路径）

这是本提案最重要的约束，也是我在 2026-09-15 评估报告里给出 `delivery/` 命名时**没有考虑到的**：
`jira/{plan_run_id}/` 被写进 JIRA 提单正文，**重命名会让历史提单链接全部失效**。任何重命名方案必须提供外链兼容路径。

### 2.1 方案对比

| 方案 | 形态 | 收益 | 代价 / 风险 |
|---|---|---|---|
| **A（最小）** | 保持目录树，只做两件事：①`dedup/{run}/` 拆为 `report/scan/{run}/` + `report/merge/{run}/`；②为 `devices/` 与 `jira/` 设**不同 TTL**（`devices/` 短、`jira/` 长） | 命名语义归位；存储峰值下降（不等 retention 才回收 raw） | 仍需改路径 → 报表 URI 与 DLE `remote_path` 存量数据受影响；收益中等 |
| **B（结构重排）** | `raw/{run}/{event_id}/` · `report/{scan,merge}/{run}/{platform}/` · `delivery/{run}/`（即现 `jira/`）· `_meta/{run}.json` | 语义清晰；`raw`/`delivery` 可独立 TTL；manifest 可替代"扫目录推完备性" | 迁移面大；**`delivery` 改名直接撞 §2.0 外链约束**；存量 `remote_path`/artifact URI 需要映射 |
| **C（只存一份）** | 取消 `devices/`，事件目录只在 `delivery/` 出现；extract 变成"移入 + 完成标记" | 存储收益最大（消除最大的一类双份） | 与"上送即固化"语义冲突：raw 是上送目标、delivery 是 extract 结果，合并后 extract 失败即无副本；回滚困难 |
| **D（manifest-first）** | **不动目录树**，先落 `_meta/{run}.json`（事件清单 + 缺口 + 报表引用） | 完备性判定不再靠"扫目录 + 四维收窄"；为 B/C 铺路；**可独立先行** | 不直接省存储；多一份需要维护的派生文件 |

### 2.2 建议的推进序（供评审）

**D → A →（B/C 在评审中权衡，暂缓）**，理由：

- D 不改变任何既有路径，可与 A/B/C 解耦，先拿到"完备性不再补丁摞补丁"的收益；
- A 的收益（命名 + TTL 分层）在两个方向上都成立，且不是 B/C 的前置障碍；
- **B/C 的收益上限受 §2.0 约束压制**：`delivery` 不能真正改名，B 的"语义清晰"收益打折；C 的"只存一份"与 raw/delivery 语义冲突，需要单独论证；
- 现 `jira/` 侧按 basename 收敛 (#386) 造成的撞名丢失，**独立于目录重排**，应单独修（归 I-6/I-7 或单列），不要捆绑进本项。

---

## 3. I-13 候选方案

| 方案 | 形态 | 收益 | 代价 / 风险 |
|---|---|---|---|
| **A（无状态化）** | merge 工具产物落到**中心 staging**（如 `{center}/_staging/merge/{run}/`），发布后清理；控制面本地不保留产物 | 消除控制面本地磁盘依赖与 `merge_result/` 无界增长；本地不再需要"保留期清理" | 依赖工具支持指定输出目录；否则仍需本地中转（收益打折） |
| **B（专用 merge worker / Agent 侧执行）** | merge 在专用 worker 或 Agent 上跑，控制面只登记 | 彻底解耦控制面；与控制面水平扩展相容 | 需要工具分发（**归 ADR-0033**，当前"未落地"）；SPRD/MTK 双工具链；需要新的并发与失败语义 |
| **C（容器化 merge 服务）** | 独立服务 + 队列 | 隔离最好 | 运维成本最高；与 ADR-0025/0027 的部署形态冲突 |

**建议**：先 A（低风险、去掉本地中间副本，收益可测）；B 作为目标态，且**必须与 ADR-0033 一起推进**（工具宿主模型），不可单独裁。

> **A 段已落地（2026-09-15，见 [`2026-09-15-merge-local-intermediate-cleanup-2189.md`](../feature/2026-09-15-merge-local-intermediate-cleanup-2189.md)）**：
> 落地方案**不是**上表 A 的"直落中心 staging"，而是本文件 §Revisit 第二条预留的兜底分支——
> 经代码核实，**工具输出目录不可从调用侧指定**（`run_merge_sync` 靠调用前后快照识别
> `{工具目录}/merge_result/{ts}/`），staging 拿不到。故落成「本地中转 + 发布后立即删除 +
> 24h 超期兜底清理」，且**仅在中心已配置时**生效；**锁保留**（工具仍写共享目录，
> 去锁前提是方案 B / ADR-0033）。E-3（本机 `merge_result/` = 0）在成功路径成立。

### 3.1 方案 B 优先级重评（**裁决输入**，2026-09-16）

本节的义务来自两处自设条款：§3 建议里的「B 作为目标态，且**必须与 ADR-0033 一起推进**」，以及
§Revisit 第二条的「A 受工具能力限制 → 退回兜底形态，**并重新评估 B 的优先级**」。A 段已按兜底形态
落地（上文引注），故这里给出**重评结论与依据**——是输入，不是裁决。

> **结论：B 的优先级不提前**（不因 A 打折而升格为近期项）；**但新增一条必须登记的隐性限制**（见下"缺口"）。
>
> **已裁决（2026-09-16，owner 裁 B0）并已落地**：ADR-0027 **v1.8** 清单第 7 条 + 启动告警
> （`dedup_scan.multi_instance_merge_warning`，`backend/main.py`）；见 §裁决记录。

#### 依据（逐条可核）

1. **B 的前置不在本域，且被自己设成了"条件落地"**。B 需要「工具分发到 worker/Agent + 统一调用契约」
   = [ADR-0033](../../adr/ADR-0033-tool-kit-ecosystem-integration.md)（Accepted v1.2，**落地状态：未落地**，
   Phase 2/3 零启动）。其工具分发（包存储）被 §5.4 改为**条件落地**，三条触发（出现第二个需版本化分发的
   Tier 1/2 族 / 多机复制不一致或源码被就地改的真实事故 / Phase 3 启动）**一条都未出现**；
   且 §5.4 明令**不得再新增工具私有 env 键**——若用"加一个路径键把 merge 指到 worker"来实现 B，
   会**违反 ADR-0033 本身**。
2. **B 与 ADR-0033 的 Phase 2 样板是同一批工作**。§5.3 把 D2 由"存量要求"收窄为"新族准入、**按族采用**"，
   而 Phase 2 的样板正是「在 ADR-0032 已建的 per-platform 循环内为 unisoc 分区接入第一个
   `DedupMergeEngine`」（= #463 P2）。即 **B 不是独立项目，它是 ADR-0033 Phase 2/3 的产物**；
   现在单独立项等于把同一批工作拆成两个权威。
3. **A 的残留已被证明"可接受 + 与既有评审同向"**。落地形态（发布后删本机中转 + 24h 超期兜底）
   与 2026-08-09 的 [`DEVICE_LOG_FLOW_REVIEW`](../../reviews/DEVICE_LOG_FLOW_REVIEW_2026-08-09.md)
   对同一问题的建议**同向**：那份评审当年就写「merge 子进程在控制面本地执行（**无法迁 CIFS**）」，
   并建议"`merge_result/` 在 extract 完成后按保留期清理；artifact 注册后 `jira/` bundle 才是交付物，
   本地目录降级为**可丢弃缓存**"。**两次独立取证得到同一结论**（2026-08-09 静态评审 / 2026-09-15 代码
   实读），说明这不是某一次的判断失误。残留代价（失败路径本机 ≤24h、锁仍在）不落在成功路径上，
   而 E-3 已按成功路径成立。
4. **唯一会真正抬升 B 的现实因素是"控制面多实例"，而它仍是 opt-in**。
   [ADR-0027](../../adr/ADR-0027-control-plane-horizontal-scaling.md)（Accepted）明确「生产多实例仍为
   **opt-in**」，且 ADR-0025 D1 定的重启条件是"设备池 >80 / 零停机滚动 / 多控制面"。在那之前，
   B 的"与控制面水平扩展相容"这条收益**无法兑现**——收益在未来，成本在现在。

#### 缺口：`run_merge_sync` 的互斥是"单机"的，却不在 ADR-0027 的多实例约束清单里

这是本节的可执行产出，**与 B 的优先级无关也该做**：

- **现态事实**：merge 的串行靠 `script_parent/merge_result/` 上的 `fcntl.flock`（**仅同机生效**），
  而工具目录是**本机路径**（示例配置见 `deploy/control-plane/env/.env.backend.example`：`/opt/...`）。
- **多实例下的后果（前瞻推断，不是当前线上故障）**：同一 run 的两次 merge——例如手动
  `POST /plan-runs/{id}/dedup/merge` 落到实例 B，而实例 A 的 SAQ `merge_task` 同时在跑——
  **没有跨实例互斥**：两实例各自在本机跑工具、各自向中心**同一路径**发布、各自登记 artifact。
  产物内容取决于各自的输入快照（round / waterline 过滤），故可能出现**中心产物归属不确定**；
  artifact 行因 `_register_merge_artifacts` 带 `storage_uri` 存在性检查而不至大量重复。
- **而 ADR-0027 的清单第 6 条只登记了 RunConsole 依赖功能**（dedup 串行 / 安装 console / 助手 console /
  console 房间），**未含 `run_merge_sync`**。即：多实例一旦启用，merge 的"实例绑定"是**未登记的隐性限制**
  （隐性约束比已知约束贵——这正是本仓反复吃过的形态）。
- **已核实（故不是"待确认"）：SAQ 作业不会双跑，缺口因此收窄为"跨路径并发"**。
  ADR-0027 明写「SAQ in-process worker 仍可每实例各跑一个（共享 Redis 队列，**由 SAQ 本身去重消费**）」——
  同一作业由某一实例消费一次，故不存在"两个实例各跑一次 `merge_task`"。剩下的缺口是：
  **手动 API 与 SAQ 两条路径不共享任何跨实例互斥**（唯一的互斥是本机 `flock`）。
  这也解释了它为何比 RunConsole 那条更难被发现：**两条路径各自看都是"串行"的**。
- **同类需一并评估**：`extract` 是同一个双路径形态（`POST /plan-runs/{id}/dedup/extract` × SAQ `extract_task`），
  且它与 merge 不同——它直接写**共享**中心目录 `jira/{run}/`（extract 侧有完成标记
  `.stp_extract_complete` 与 basename 去重）。本输入不对它下结论（其并发后果取决于完成标记语义，
  需单独取证），仅登记为**同一族问题**，避免只修 merge 而漏掉同形态的另一处。

#### 选项（供裁决）

| 选项 | 内容 | 成本 | 收益 | 何时该选 |
|---|---|---|---|---|
| **B0（推荐）** | **不提前 B**；把"merge 是实例绑定操作"按 ADR-0027 既有形态登记进多实例清单（或在设计文档显式化），并重申 B 归 ADR-0033 Phase 2/3 | 一条登记（+ 清单维护） | 消除"多实例启用时会踩到的未登记限制" | 默认 |
| **B1** | 提前 B：现在把 merge 移到 worker/Agent | 需先做 ADR-0033 D2/D3（未启动；且 §5.4 禁止新增私有路径键） | 彻底解耦、一次到位 | 仅当多实例**已决定启用**且 merge 是已知卡点 |
| **B2** | 折中：merge 仍在本机跑，但把互斥换成**跨实例**互斥（复用 ADR-0027 P3-4 的 `run_key` 全局互斥 / 注册表原语） | 中等（一个新注册表 + 失效语义） | 解除 merge 的实例绑定，使多实例下 merge 可用 | 多实例启用**在即**、但不想先做 ADR-0033 |

**建议 B0**：B1 的成本被 ADR-0033 自己的条件落地条款锁住（且提前做会违反其 §5.4）；B2 的原语虽已存在
（ADR-0027 P3-4），但它解决的问题只在多实例启用后才成立——现在做等于为未来形态预付成本。
B0 近乎零成本，且它挡住的是最贵的一类问题：**多实例启用时才发现有未登记的实例绑定**。

#### 重启评估的触发条件（写死，避免"以后再议"）

1. **控制面多实例决定启用**（ADR-0025 D1 三条件任一成立）→ 立即回到本表裁决 B0/B2；
2. **ADR-0033 Phase 2 启动**（unisoc `DedupMergeEngine`，#463 P2）→ 借该批次顺带评估 B；
3. **merge 成为吞吐瓶颈**（E-4 实测 P95 显著劣化，或本机磁盘/工具目录成为增长源）→ 评估 B1。

---

## 4. 迁移路径（I-12 / I-13 共用骨架）

| 阶段 | 动作 | 退出条件 | 回滚点 |
|---|---|---|---|
| 0 | 采集 §1.3 基线（隔离环境或只读生产） | 五项指标有数字 | 无副作用 |
| 1 | **影子/双写**：新路径并行写入，读路径不变 | 新旧内容一致（逐 run 校验） | 停止双写即可 |
| 2 | **切读**：extract / 报表注册 / 前端改用新路径 | 一轮端到端 run 产物完整 | 切回旧读路径 |
| 3 | **清理旧树**：按保留期删除旧目录，保留外链兼容路径 | 残留数为 0 且历史链接仍有效 | 从备份恢复（需在阶段 1 定义备份范围） |

**共同退出条件**：迁移期间产生的 run 必须记录"写在哪一侧"，否则阶段 3 的清理会漏或误删。

---

## 5. 效果判据（"较原方案有较大优化"的可测定义）

评审需**先定阈值再落地**（避免事后调参）。建议判据与口径：

| 编号 | 指标 | 判据 | 备注 |
|---|---|---|---|
| E-1 | 同规模中心盘占用 | 降幅 ≥ 阈值（**阈值由评审依 §1.3 基线定**；若仅做 A，预期收益有限，应据此下调期望） | 事件目录双份未消除则降幅有上限 |
| E-2 | retention 后残留目录数 | `= 0`（含 `jira/`、`jobs/`） | 现状清轨已含三目录，需验证 |
| E-3 | 控制面本地 `merge_result/` | **成功路径 = 0**（2026-09-15 落地：发布 + 登记后立即删除本机中转）；失败/重试残留由 24h 兜底清理 | 未落中心 staging（工具不可指定输出目录，见 §3.1），故"本地无产物"只在成功路径成立 |
| E-4 | merge 端到端耗时 | P95 **不劣化**（≤ 基线 × 1.0） | 迁移不应牺牲时延 |
| E-5 | 历史 JIRA 外链有效率 | `= 100%` | §2.0 约束的验收位 |
| E-6 | 回归 | dedup/extract/saq 既有测试全绿 + 新增迁移用例 | — |

**不达标即回到评审，不修订 ADR**——这是 owner 定的顺序，也是本提案的存在意义。

---

## 6. 评审清单（Reviewer 逐条回答）

1. 选型是否正面回应了 §2.0 的 JIRA 外链硬约束？改名方案是否给了兼容路径？
2. §1.3 的五项基线是否**真的可测**？在隔离环境造得出等价规模吗？若测不了，判据是否要改?
3. 迁移三阶段是否每步都有回滚点？阶段 3 的备份范围是否定义清楚？
4. E-1 的阈值是否**先验**合理（而非落地后再定）？
5. 是否与在窗 Execution 冲突（尤其 `docs/adr/README.md` 与 ADR-0025 正文）？
6. 是否引出了 ADR-0025 之外的新决策（例如 raw/delivery 各自 TTL 会改变 retention 语义）？若有，载体是 ADR-0025 修订还是新 ADR？
7. I-13 方案 B 与 ADR-0033（工具宿主，**未落地**）的依赖顺序是否成立？
8. 本项与 [ADR-0032 v0.8 提案](./2026-09-15-adr0032-v08-platform-routing-revision.md) 是否正交（应正交，不得捆绑）？

---

## Alternatives

- **直接按评估报告的 `raw/report/delivery` 命名落地**——放弃。`delivery` 改名会使历史 JIRA 提单链接失效（§2.0），评估报告当时未考虑该约束。
- **先修订 ADR-0025 再实施**——放弃。owner 已定序「先方案 → 评审 → 落地 → 效果确认 → 修订」；先改 ADR 等于把未验证选择写成权威。
- **I-12 + I-13 合并成一次迁移**——放弃。两者耦合面小（一个改路径、一个改执行位置），合并会放大回滚成本；且 I-13 方案 A 可独立收益。
- **把 #386 撞名收敛一并纳入重排**——放弃。撞名丢失是 extract 去重策略问题，与目录重排正交；捆绑会让"存储重排"变成多目标变更。
- **不采基线直接落地**——放弃。无基线的优化不可判定，与 owner 的"确认效果"要求直接冲突。

## Verification

- 本文件为**提案**：未改 `docs/adr/`、未改产品代码路径；新增的
  `backend/scripts/measure_center_storage.py` 是只读诊断工具（§1.3 的采集手段）。
- §1.1 / §1.2 全部 `file:line` 为 2026-09-15 会话亲读；`jira/` 外链约束来源为
  [`docs/design/2026-scan-upload-merge-contract.md`](../../design/2026-scan-upload-merge-contract.md)
  与 ADR-0025 D2「Jira 提单引用的报表路径必须指向中心存储」。
- 采集脚本验证：`backend/tests/test_measure_center_storage.py` →
  `TESTING=1 JWT_SECRET_KEY=test-secret python -m pytest
  backend/tests/test_measure_center_storage.py -q` → **16 passed**（覆盖：字节/文件/目录计数、
  **不跟随 symlink**、危险根与空根拒绝、双份口径与 `unassigned` 不计入 run、merge 子树分离、
  本地 `merge_result` 存在/缺失、CLI 拒绝危险根与 JSON 输出、表格渲染）。
  另做一次构造树的 CLI 端到端冒烟：`devices`/`jira` 双份、`dedup|jira` 的 `merge/` 子树、
  本地 `merge_result` 三项均按预期输出。
- **待补**：§1.3 的**实际数字**（本提案的评审前置）；§5 各项阈值。
- 落地阶段的验证按仓库纪律：对应 PR 附 Agent Note + `python scripts/run_gates.py check:quick`。

## Revisit

- 基线采集显示"双份占用"占比很小（例如 merge xls 可忽略、事件目录因 retention 快速回收）→ 应下调 I-12 的收益预期，甚至只做 D，不做 A/B/C。
- I-13 方案 A 受工具能力限制（无法指定输出目录）→ 退回"本地中转 + 保留期清理"的最小修复，并重新评估 B 的优先级。
  **该条件已命中并已处置（2026-09-15 / 09-16）**：兜底形态已落地（见 §3 引注）；B 的重评依据与结论见
  **§3.1**（结论：**不提前 B**，但新增一条必须登记的隐性限制——`run_merge_sync` 的实例绑定未进 ADR-0027 清单）。
- ADR-0033 若在 I-13 方案 B 之前落地，B 的实施成本显著下降，应重排优先级。
- 评审结论为"两项都不做"时，本文件应转为 `Status: rejected` 并写明理由（供后续避免重复讨论）。
