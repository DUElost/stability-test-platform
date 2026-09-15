# ADR-0040：部署摘要协议（Deployment Artifact Digest Protocol）

- 状态：**Accepted** v1.1
- 版本记录：v1.0 定稿（2026-09-13；v0.1 初版由 [#1900](https://github.com/DUElost/stability-test-platform/issues/1900) 触发、[#1901](https://github.com/DUElost/stability-test-platform/issues/1901) 跟踪 → owner 裁决采纳 D1–D7，裁决记录见 §9）；v1.1（2026-09-15，[#2057](https://github.com/DUElost/stability-test-platform/issues/2057)）：**判据唯一性**与展示面收口，修订记录见 §10（D1 的身份定义不变）
- 优先级：P2
- 目标里程碑：M7
- 日期：2026-09-13
- 决策者：平台研发组（owner 裁决，2026-09-13）
- 标签：热更新, 内容寻址, 收敛, 幂等, 升级, 可观测, #1900, #2057
- 关联：[#1900](https://github.com/DUElost/stability-test-platform/issues/1900)（问题界定与实测基线）、[#1901](https://github.com/DUElost/stability-test-platform/issues/1901)（本 ADR 跟踪）、[ADR-0021](./ADR-0021-script-content-alignment-gate.md)（升级门禁/维护窗口，本 ADR 复用）、[ADR-0037](./ADR-0037-agent-host-privilege-boundary.md)（提权边界，资源动作扩展须与其联审）、[ADR-0038](./ADR-0038-host-retirement-semantics.md)（D4「禁 `Host.extra` 裸键」先例）、[ADR-0033](./ADR-0033-tool-kit-ecosystem-integration.md)（包存储轨道，未来 artifact 存储复用本协议身份）、`docs/operations/agent-version-and-hot-update.md`（现行热更新契约）

## 1. 背景

### 1.1 问题定性：成本与「差量」无关，副作用与「是否需要变更」无关

热更新的本质是**状态收敛**：让远端宿主机的运行态向控制面期望态收敛。现行实现把它实现为
「每次把完整期望态物化并推送 + 无条件重启」，由此产生三个与本质相悖的性质：

1. **传输成本 ∝ 状态总量**，而不是差量；
2. **副作用（服务中断）与「是否需要变更」无关**——无变化也重启；
3. **没有「已收敛」判定**——写 VERSION 但从不比较，任何一次调用都是全量动作。

随之而来的第二个代价是**入口语义分叉**：每新增一个更新入口都要重答「如何判定差异、是否
重启、如何留痕」。当前四个入口（UI/API、`batch_hot_update.py --direct`、Ansible、precheck
自动同步）已经分叉（§1.2 事实 4）。

本 ADR 的职责是把这个收敛过程立成**协议**（artifact 身份 + 收敛判定 + 副作用判据），
而不是优化某一条代码路径。

### 1.2 事实基线（2026-09-13 只读实测 + 控制面复测）

**1）耗时与构成**：单台端到端 p50 = **20.1s**（UI/API 路径审计 `hot_update_result.duration_ms`
542 条，08-15~09-03：min 18.9s / p90 24.4s / max 38.6s）；当天 48 台 `--direct` run 实测
median 21s（相邻写库时间差）。控制面复测 `_build_tarball()`（`backend/services/host_updater.py:97-123`，
`tarfile w:gz` 默认压缩级 9）：

| 输入 | 源体积 | 压缩后 | 打包耗时 |
|---|---|---|---|
| 现行全量（agent 树 + schema） | 252 MB | 125.7 MB | **16.6s** |
| 其中 `resources/`（108 文件） | 229 MB | 125.0 MB（level 6）/ 130.7 MB（level 1） | 5.9s / 3.0s |
| **剔除 `resources/` 的代码树 + schema（468 文件）** | ~1.6 MB | **1.0 MB** | **0.31s** |

即：**99.2% 的载荷是几乎不变的静态资源**（flashtool / AIMonkey 二进制），却参与了每一次
热更新；且该打包在 `--direct` 循环里**每台重复一次**（`batch_hot_update.py:159-168` →
`host_updater.py:546-548`），48 台 = 48 次纯 CPU。

**2）其余环节同样是全量语义**：SFTP 全量上传 → 远端解包 → 本地 rsync → 递归 chown →
写 VERSION/schema → **无条件 restart** + 探活（`host_updater.py:126-366`）。

**3）已有「内容摘要」先例（本协议的可复用骨架）**：脚本目录已实现同型机制——
Agent 侧按 `(name, version, content_sha256)` 排序摘要 `md5[:12]`
（`backend/agent/registry/script_registry.py:155-162`）→ 心跳上报 `script_catalog_version`
（`backend/agent/heartbeat.py:28,63`）→ 控制面**镜像实现**比对（`backend/services/script_catalog_version.py`，
docstring 要求与 Agent 侧**字节级等价**并配对照测试）→ 心跳/claim 响应返回
`script_catalog_outdated`（`backend/api/routes/agent_api.py:825-832`）→ 落 `host.script_catalog_version`
显式列。**部署 artifact 摘要没有理由另造一套形态。**

**4）入口语义分叉（同日核验）**：

| 入口 | 审计 `hot_update_result` | 写 `agent_code_deployed_at` | 刷远端 VERSION |
|---|---|---|---|
| UI/API（`hosts.py:834`） | ✅ | ✅ | ✅ |
| `batch_hot_update.py --direct`（`:176`） | ❌ | ✅ | ✅ |
| Ansible `update_agent.yml` | ❌ | ❌ | ✅（`:434-442`） |
| precheck 自动同步（`precheck/sync.py:89`） | ❌ | ❌ | ❌ |

（审计最后一条 `hot_update_result` 停在 09-03；09-13 当天 47 台 fleet 更新零审计。）

### 1.3 约束

- **提权边界不动语义**：远端提权面是固定子命令集（`selftest/bootstrap/apply-code/install-schema/
  write-version/sync-env/deps-marker/fix-ownership/restart`），`apply-code` 要求 staged **完整树** +
  `rsync --delete` + 固定排除集 + `HOST_LOCAL_PATHS=["resources/mtbf/"]`
  （`backend/agent/stp_agent_priv.py:445-487`，ADR-0037）。任何资源动作扩展必须回到同一模式
  （白名单 + 路径/属主校验）并联审 ADR-0037。
- **`Host.extra` 不新增裸键**（ADR-0038 D4 先例）；宿主状态走显式列（对照 `host.script_catalog_version`）。
- **硬不变量**：`plan` 只接受 `script:<name>` action、脚本版本不可变契约（ADR-0039 修订中）等不受本 ADR 影响。
- **schema 生效必须重启**：`pipeline_validator._schema_cache` 是进程内缓存
  （`backend/agent/pipeline_validator.py:14-22`），2026-08-04 已有「只发文件不重启导致全轮失败」实证。
- **控制面本机同时是生产 DB 宿主**：控制面 CPU/内存开销是真实成本，不只是延迟。

## 2. 决策

### D1（核心）：部署单元 = 两个内容寻址 artifact，身份与溯源分离

拆分现行单一 tarball 为两个 artifact，各自独立身份、独立判定：

| artifact | 内容 | 事实基线 | 同步频率 |
|---|---|---|---|
| `agent-code` | agent 源码树（沿用现行排除集）+ `pipeline_schema.json` | 1.0 MB / 468 文件 / 0.31s | 每次收敛 |
| `host-resources` | `resources/` 下非主机本地资产（flashtool / AIMonkey 等） | 130.7 MB / 108 文件 | 仅 digest 变化时 |

- **身份 = 内容摘要**：digest 形式 `sha256:<hex>`（前缀即算法标识，允许演进）；输入集 = artifact
  文件集的规范化序列 `(relpath, 可执行位, 内容 sha256)` 序列化后取 sha256。**双侧镜像实现 +
  字节级等价性测试**（先例：`script_catalog_version` 的 `compute_*` 双侧实现与对照测试）。
- **digest 输入集 = 部署流程实际拥有并覆盖的文件集**：排除 `VERSION`、`ARTIFACT_DIGEST`、`.env`、
  deps marker、venv、logs、`resources/mtbf/`、`__pycache__`、`tests/`——**「主机态/部署态」不属于内容身份**。
- **git revision（VERSION）降为溯源展示**，不参与收敛判定（与 `agent_code_revision` 现行语义一致）；
  **且不得作为任何面向运维的「是否需要动作」判据**（判据唯一性，见 D2 与 §10）。v1.1 显式**放弃**
  原「revision 可在内容不变时单独刷新」一条：全链路无该通道（no-op 在 `hosts.py:707-729` 早退、
  不写 VERSION），且判据换 digest 后 revision 只作溯源，不值得为对齐它增加一次远端写。
- `resources/mtbf/` 永远属主机本地（APK 三件套等），不进任何 artifact（维持现行保护语义）。

### D2：状态载体——远端单点上报，控制面现算

- **远端 current digest**：部署流程在收敛成功后，以受控写入（`write-version` 同族能力）落
  `$INSTALL_DIR/agent/ARTIFACT_DIGEST`；Agent 读取后经心跳上报 `agent_artifact_digest`
  （与 `script_catalog_version` 同通道、同信任模型）。Host 侧新增**显式列**（禁 `extra` 裸键）。
- **控制面 desired digest**：由控制面按同一算法**现算 + 进程缓存**（缓存键 = 输入集状态），不落 host 表——
  desired 是控制面 artifact 的属性，不是 host 的属性。
- **信任模型与 VERSION 相同**：写入者是唯一提权入口；本协议**不做**每次心跳全树重算（229MB 级 CPU
  不可接受），带外手工漂移由低频校验任务与 VERSION 漂移兜底（Revisit §7）。
- **`agent_code_deployed_at` 语义修订**：只在**内容实际变更**并收敛成功时刷新；no-op 不刷新
  （no-op 由 digest 匹配状态表达）。该修订需前端展示与测试同步。
- **判据唯一性（v1.1，#2057）**：面向运维的「是否需要动作」信号**唯一**由收敛判据（digest）
  产生；revision、部署时间等溯源信息**不得**被渲染成 drift / 待更新一类动作信号。
  `agent_code_sync_status` 的判等改为 **desired digest ↔ `host.agent_artifact_digest`**
  （相等 = `matched`，不等 = `drift`）；`agent_code_revision` / `expected_code_revision`
  降为纯文本溯源（前端展示「部署于 @x / 期望 HEAD @y」，不再着徽章）。
  理由：`get_agent_code_version()` 取的是**仓库 HEAD**（`host_updater.py:665-681`），任何不动
  `backend/agent/**` 的提交都会让 revision 前进而 digest 不变；若不换判据，假 drift 是**永久**
  的——运维看到 drift 触发热更新，回 `converged(digest-matched)`，徽章不变，唯一出口是
  `--force` 全量 + 重启，把 D3 省下的传输与重启原样花回去。
- **`unknown` 语义（v1.1）**：主机从未上报 digest（#1907 前部署 / 新装未心跳）时为 `unknown`
  **而非 drift**——运维动作为「等一次心跳」或「首次 `--force` 迁移」。禁止把 `unknown` 渲染成
  需更新，否则只是把「看不懂的 drift」换成「看不懂的 unknown」。

### D3：收敛语义——相等即空操作；变更走分层载荷

- **digest 相等 → 全链路 no-op**：不构建、不传输、不重启，返回 `converged(reason=digest-matched)`；
  批处理计入 `converged/skipped` 统计。保留 `--force` 显式强制全量（运维逃生阀，审计留痕）。
- **digest 不等 → 按层收敛**：`agent-code` 为**全量传输**（分层后 1MB 级；全量成本已低于任何差量
  协议的复杂度成本）；`host-resources` 独立判定、独立通道、仅在变化时同步。
- **明确不做** code artifact 的差量/滚动校验和协议（复议触发器见 Revisit §7-2）：先消除
  「不必要」，再优化「必要」——分层后差量的边际收益不足以支撑远端持久状态或提权面扩大。
- **wrapper 保护清单扩展**：`HOST_LOCAL_PATHS` 增补 `resources/`，使 `agent-code` 的
  `apply-code --delete` 不再清掉大件；资源应用动作按 ADR-0037 模式新增受控子命令并联审。

### D4：restart 判据显式化——三类必重启、三类不重启

| 变更 | 是否 restart | 依据 |
|---|---|---|
| 无变更（digest 相等） | **否** | 本协议 D3 |
| 仅 VERSION / ARTIFACT_DIGEST 元数据刷新 | **否** | 内容未变，元数据仅溯源 |
| `host-resources` 变更 | **否**（默认） | 大件由脚本按需调用（子进程读取）；如发现进程内缓存再复议 |
| `agent-code` 变更 | **是** | 运行中进程持有旧代码 |
| `pipeline_schema.json` 变更 | **是** | `_schema_cache` 进程内缓存（2026-08-04 实证） |
| `.env` 白名单键变更 / 依赖变更 | **是** | 标志位启动时读取（#218 顺序）；pip 后需重启 |

### D5：入口统一——同一收敛服务、同一记录语义

- 四条入口（UI/API、`--direct`、Ansible、precheck）复用**同一收敛判定与同一 no-op 语义**；
  维护窗口继续复用 `host_upgrade_gate`（ADR-0021，已统一）。
- **Ansible 轨道归位**：保留 rsync 差量能力，但职责收窄为「首次安装 / `host-resources` 大件通道 /
  灾难修复」；日常 `agent-code` 收敛统一走控制面服务。
- **记录语义统一**：`agent_code_deployed_at` 与审计在四入口一致写入（消除 §1.2 事实 4 的分叉）；
  no-op 结果同样留痕（`converged`），不再出现「跑了但什么都没记」。

### D6：可观测与验收

- 收敛过程新增 **per-phase 计时**（digest 计算 / 打包 / 上传 / 远端应用 / 重启探活）落审计 `details`
  ——当前只有端到端 `duration_ms`，无法归因（#1900 的证据即受此制约）。
- 指标：`converged` / `drift` / `pending` 计数与耗时分布。
- 验收（映射 #1900）：零变更单台 <5s 且不重启；变更时 `agent-code` 载荷 <5MB（目标 ~1MB）；
  单台 p50 ≤8s；48 台批量 ≤10min（现行 ~20min）；四入口记录语义一致。

### D7：显式不做（各带复议触发器）

1. 不做 code artifact 差量协议（触发器见 §7-2）；
2. 不做每次心跳全树重算 digest（触发器见 §7-3）；
3. 不改 ADR-0037 提权边界语义——资源动作扩展按既有白名单模式落地，并回填 ADR-0037 修订；
4. 不纳入并行升级 / 灰度（与维护窗口、设备作业语义耦合，另主题）；
5. 不在本 ADR 裁决 artifact 存储（包存储归 ADR-0033 轨道；若落地，身份直接复用本协议 digest，不另造）。

## 3. 备选方案与权衡

| 备选 | 内容 | 否决/保留理由 |
|---|---|---|
| A. 只做过渡项（缓存打包 + 降压缩级） | 单台 ~21s → ~5s | **保留为 P0 过渡**（终态出口 = D1/D3，不留双轨）：不解决传输/重启/入口分叉 |
| B. 单 artifact + 差量协议（rsync / 变更集） | 传输 ∝ 差量 | **否决**：分层后收益边际小，却需远端持久 staging 或 wrapper 新增带删除语义的子命令（提权面扩大）。列为 §7-2 触发器下的首选备选 |
| C. 每次收敛重算全树 digest | 可识别带外漂移 | **否决**：229MB 级 CPU 与「省时间」目标自相矛盾。列为 §7-3 触发器下的升级路径（低频校验任务） |
| D. artifact 存储 + 增量分发（ADR-0033 轨道） | 统一包管理与分发 | **前瞻保留**：工程量大；若落地，artifact 身份直接用本协议 digest |

## 4. 影响

### 4.1 正面（量化预期）

- 日常收敛：单台 20.1s → **<5s**（no-op 为亚秒级判定 + 秒级收尾）；48 台批量 ~20min → **分钟级**；
- 单台传输 125.7 MB → **~1 MB**；控制面打包 CPU 16.6s/台 → **0.31s/台**（同机还有生产 DB）；
- 服务重启次数大幅下降 → 对在跑设备作业的打扰下降；
- 四入口语义一致 → 审计与 `agent_code_deployed_at` 恢复可信（§1.2 事实 4 的缺口闭合）。

### 4.2 负面 / 接受的代价

- **双侧镜像实现**：digest 算法两侧必须字节级等价（先例已有对照测试模式）；实现漂移风险用
  等价性测试 + digest 前缀算法标识管理。
- **假阳性漏更新**：digest 相等即跳过；若两侧输入集契约漂移（例如某文件被排除在外），会漏更新。
  缓解：输入集契约与部署输入集由**同一测试**守护；`--force` 逃生阀；no-op 结果留痕可审计。
- **信任模型的已知限制**：DIGEST 文件由部署流程写入，带外手工改动不被感知（Revisit §7-3）。
- **`agent_code_deployed_at` 语义变更**：前端/测试需同步（no-op 不刷新）。
- **展示口径必须跟随判据（v1.1）**：任何新增状态面（resources 层、env 层、未来的 artifact 存储
  坐标）都不得用溯源字段当动作判据——溯源与被控量的偏差会随无关提交单调增长，用代理量做反馈
  必然产生幻象误差（§10）。

### 4.3 兼容与回滚

- 分阶段落地（§5），每阶段独立可回滚；
- 分层切换前先把 `resources/` 加入保护清单 → 旧的全量流程是分层的**超集**，回退安全（不会误删大件）。

## 5. 落地与后续动作（依赖顺序）

1. **P0 过渡（不改协议）**：一批只构建一次 tarball / 按树缓存 + 压缩级 9→6；预期单台 ~21s → ~5s。
   过渡项在 P1 落地后由 digest 缓存键取代，不留双轨。
2. **P1 协议最小闭环**：D1 + D2 + D3 + D6——digest 双侧实现与等价性测试、显式列与心跳上报、
   no-op gate、`agent_code_deployed_at` 语义修订、四入口记录统一。
3. **P2 分层扩展**：`host-resources` 独立通道（含 `HOST_LOCAL_PATHS` 扩展 + ADR-0037 回填）；
   Ansible 轨道归位。
4. 实施切片在 **Accepted 后**另开 issue（本 ADR 只作裁决）。

## 6. Verification

- 单元：digest 双侧字节级等价（镜像对照测试）；输入集边界（元数据/主机本地文件不参与）；
- 集成五象限：no-op / 仅 code / 仅 schema / 仅 env / 仅 resources，各自断言「传什么、重启与否、记什么」；
- 灰度：1 台 → 5 台 → 全量，全程走 `host_upgrade_gate` 维护窗口（ADR-0021）；
- 度量：以 D6 的 per-phase 计时复核 §4.1 的量化预期；基线复现方法沿用 #1900 的只读核验口径
  （`audit_logs.hot_update_result.duration_ms` + 逐台 `agent_code_deployed_at`）。

## 7. Revisit（复议触发条件，未触发前不得重提）

1. **载荷分层被证伪**：若 `host-resources` 的实际变更频率高于预期（例如 >1 次/周），说明分层收益
   下降，回到单 artifact + 差量方案评估；
2. **差量协议复议**：`agent-code` artifact 增长到 >5MB，或单台传输耗时占比 >20%（以 D6 数据为准）
   → 将备选 B（rsync / 变更集）提回表决；
3. **带外漂移复议**：出现因手工改动远端而 digest 未感知导致的事故 → 升级为低频全树校验任务（备选 C），
   或将 digest 重算改为可配置；
4. **入口再分叉**：新增第五条更新入口时，必须复用本协议；若无法复用，先修订本 ADR 再实现；
5. **ADR-0037 联动**：资源动作子命令扩展落地时，ADR-0037 必须同 PR 回填其子命令白名单与校验模式；
6. **判据再分叉（v1.1）**：新增任何面向运维的「是否需要动作」信号（徽章/告警/门禁提示）时，必须
   由 digest 产生；若某信号无法由 digest 表达，先修订本 ADR 再实现——不得让第二个判据与 digest 并存。

## 8. 关联实现 / 文档

- 现状实现：`backend/services/host_updater.py`（tarball/SSH/远端脚本）、`backend/scripts/batch_hot_update.py`、
  `backend/services/precheck/sync.py`、`backend/api/routes/hosts.py`、`backend/services/agent_version_info.py`、
  `backend/agent/stp_agent_priv.py`、`backend/agent/registry/script_registry.py`（摘要先例）、
  `backend/services/script_catalog_version.py`（镜像实现先例）；
- 文档：`docs/operations/agent-version-and-hot-update.md`（现行契约，本 ADR Accepted 后须同步修订）、
  `docs/development/script-versioning.md`（不涉及）；
- Issue：[#1900](https://github.com/DUElost/stability-test-platform/issues/1900)（触发与基线）、
  [#1901](https://github.com/DUElost/stability-test-platform/issues/1901)（跟踪）；
- 相邻：#960（维护窗口）、#948（pip 重试）、#1253（重启后 active 校验）、#959（文档漂移）。

## 9. 裁决记录（2026-09-13，owner）

- **结论**：D1–D7 按 v0.1 全部采纳，状态转 **Accepted**（v1.0）。优先级维持 P2、目标里程碑 M7。
- **明确接受的四项取舍**（各带 §7 复访触发器，未触发前不得重提）：
  1. D2 信任模型：远端 digest 由部署流程受控写入，不做每次心跳全树重算（§7-3）；
  2. D3：**不做** code artifact 差量协议（§7-2）；
  3. D2 `agent_code_deployed_at` 语义修订：仅内容实际变更时刷新，no-op 不刷新（前端/测试需同步）；
  4. D4 `host-resources` 变更默认不重启（发现进程内缓存即按 §7 复议）。
- **落地顺序与边界**：
  - **P0 过渡项已落地**（[#1904](https://github.com/DUElost/stability-test-platform/pull/1904)：批量整批一次构建 + 压缩级 6）；
  - P1 最小闭环（D1/D2/D3/D6 + 四入口记录统一）与 P2 分层扩展（含 ADR-0037 子命令白名单回填）**另开实施 issue**；
  - 升级互斥沿用 `host_upgrade_gate`（ADR-0021）；host 侧状态一律显式列，不新增 `Host.extra` 裸键（ADR-0038 D4 先例）。
- **采纳跟踪**：#1900（父项）保持开启至协议落地；本 ADR 的修订另起 PR 并回填 `docs/adr/README.md` 索引。

## 10. 修订记录（v1.1，2026-09-15，#2057）

**性质**：口径澄清与展示面收口，**不改 D1 的身份定义**——artifact 身份 = 内容摘要、revision =
溯源，两者均不变。改的是 **D2 的判据归属**：面向运维的动作判据唯一 = digest。

- **判据唯一性（写入 D2）**：`agent_code_sync_status` 判等换为 digest；`agent_code_revision` /
  `expected_code_revision` 降为纯文本溯源；`unknown` 语义成文（§4.2、§7-6）。
  `pending` 在 digest 判据下**不再产生**（无「已部署但身份未上报」的可靠信号：控制面无从
  区分「刚部署待心跳」与「#1907 前部署」），枚举保留以兼容既有前端与历史数据。
- **显式放弃** D1 原「revision 可在内容不变时单独刷新」：无实现通道，且判据换 digest 后无必要。
- **`expected_code_revision` 维持取仓库 HEAD**（不改写入口径）。若将来希望该文本不再指向与
  agent 无关的提交，可**单独**改为「最后触碰 `backend/agent/` 的提交」——纯溯源改进，
  **不得当作判据**：digest 输入集排除 `tests/` / `__pycache__` / 元数据（D1），只改
  `backend/agent/tests/**` 的提交与 revert 提交仍会造成 revision 前进而 digest 不变，
  即假 drift 只被降频、未被消除。
- **三候选归类（供后续检索）**：C「VERSION 纳入收敛判据」= 真改 D1，已否决（把「无变化也重启」
  请回，推翻 D3/D4 与 §4.1 全部收益）；B「VERSION 记最后触碰 agent 目录的提交」= 改 D1 的
  溯源取值口径，可单独采纳但不得当判据；A「维持现状」= 写入语义维持，配 D2 判据换 digest。
- **同步义务**：前端徽章与 `docs/operations/agent-version-and-hot-update.md`（§4 排障表
  「UI 显示 drift」一行）须随本修订更新；实施由
  [#2155](https://github.com/DUElost/stability-test-platform/issues/2155) 跟踪。
