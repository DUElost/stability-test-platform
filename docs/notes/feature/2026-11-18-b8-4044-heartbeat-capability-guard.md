# B8 阶段二 Agent 心跳能力守卫（#4044）

Status: implemented（Draft，待独立复核）
Class: feature

日期：2026-11-18　基线：origin/main@e7325af0372a9b202a283b0893c040e4415b877c　批次：#3800 §0.1 / §2.1
判据来源：#4044 正文、#3800 §2.1（Owner 裁定 6975368043）、#3875 遗留的 digest 过渡判据（v1.23 / 6704562784）

## 转手说明

本单先前由 Registry 记录 `B8 #4044 Agent 心跳能力守卫`（harness=qoder）在 2026-11-18 18:38 领单，
现场为 `.wt/b8-4044-heartbeat-capability` 的 14 个未提交文件，无测试、无 PR。此后 55 分钟无文件改动、
无进程占用该 worktree。按 Owner 指示续做时，先用 `dd2773b74` 原样把该现场提交到分支（不 reset、不 stash），
再以 `finish --abandon` 出旧记录、由 harness=qwen 重新 `declare` 同一 Requirement。实现主体继承自该现场；
本 Note 只记录在其上做的判据修正与补齐。

## Decision

### 1. 能力是当前心跳的显式正向断言

- 新增 `backend/agent/contracts/capabilities.py`（ADR-0054 D1 的共享契约包）：`SCAN_ROUND_V1`
  与构建内常量 `AGENT_CAPABILITIES`，以及 `normalize_capabilities`。心跳载荷与 Socket.IO 握手
  都从这里取值，两侧词表不可能各自漂移（`backend/agent/tests/test_capabilities_4044.py` 锁死）。
- Agent 心跳载荷新增键 `capabilities`（字符串数组）；握手 auth 新增 `agent_instance_id`。
  载荷缺键＝「本构建无能力」，控制面不 422（旧 Agent 照常心跳），但把该 host 的证据清成 `[]`。
- 控制面接收每拍覆盖三列（`backend/models/host.py`，迁移 `b8u2h1c2a3p4`）：
  `host.agent_capabilities`（JSONB，NULL＝从未上报，`[]`＝自陈无能力）、
  `agent_capabilities_reported_at`、`agent_capabilities_instance_id`。
  这里刻意不沿用 digest 的「空值不覆盖」写法：能力是准入凭据，保留上一实例的证明正是本单要关的洞。

### 2. 准入判据只有一条：`evaluate_host_capability`

`backend/services/agent_capabilities.py` 是唯一判据实现，按顺序给出稳定原因码：
`host_missing → host_offline → host_retired → heartbeat_missing →
capability_never_reported → capability_not_current_heartbeat → capability_missing → instance_unbound`。

选用的现有机制与理由：

- 新鲜度：`agent_capabilities_reported_at >= last_heartbeat`（6704562784 的比较符）——
  证明必须来自不晚于当前心跳的那一拍。**不**在此重算墙钟窗口：机队存活仍由
  `Host.status`（`session_watchdog` 按 `HOST_HEARTBEAT_TIMEOUT_SECONDS` 维护）决定，
  #3875 的 digest 预筛也是这个口径。判据里另加一套时钟会让调度器自己的 `now`
  成为存活裁判，并在假时钟测试与真实节律之间产生假阴性。
- 会话绑定：证据带 `agent_instance_id`；发送时把心跳实例 id 交给
  `call_shadow_scan_control(required_instance_id=…)`，与该 sid 声明的实例比对。
  心跳证据与连接分属两个进程实例时一律拒发。
- `retired` 从 host 行自身读出：调用方漏传标记也不会把命令发给退役主机。

### 3. digest 过渡判据收口

删除 `shadow_scan_rounds._shadow_capability_verified` 及其 `artifact_digest` /
`resolve_agent_code_sync_status` 依赖。摘要列仍在写入与展示（ADR-0040 的 no-op 判定不受影响），
但不再参与影子准入。#3875 的两组探针（`test_shadow_scan_send_3875.py`、
`test_shadow_scan_settled_3875.py`）与 #1907 探针按同一形状改挂到能力证据上，
探针数量与断言强度不减（`test_dynamic_digest_guard_...` → `test_capability_guard_...`，
四种参数从摘要取值换成能力证据取值）。

### 4. 双层守卫：预筛 + 发送前复核

- `prepare_shadow_scan`：按当前心跳证据决定是否预约（未证实即 `coverage=MISSING` +
  `reason=capability_unverified`，日志带原因码）。
- `verify_shadow_scan_dispatch`（新增）：在真正发送前用**新事务**重读证据，
  并确认该 host 的预约仍未被覆盖，返回可下发的实例 id。
- `call_shadow_scan_control`：要求本机 sid 存在、握手声明 `scan_round_v1`、
  且 sid 实例 id 与心跳证据一致；影子下发永不走 room 广播回退。
- 任一道拦住 → `reject_shadow_scan_dispatch` 登记缺口并保留重试时点。
  这使「预筛正确」不再是充分条件：迟到心跳、预筛后重连、预筛本身被绕过都发不出去。

### 5. 交给 #4043 的能力证据接口

`partition_capability_evidence(db, plan_run_id=…, platform=…)` 返回
`PartitionCapabilityEvidence(plan_run_id, platform, hosts[HostCapabilityEvidence…])`，
带 `admitted`（全部预期 host 均已证实）、`unverified` 与 `as_dict()`（含 `evaluated_at`，
供入场台账记录核验时刻）。期望 host 的推导与 `shadow_scan_rounds._expected_units` 同源
（实际 job + prepare 快照，再按该 host 预期产出的分区过滤），因此：

- 作用域是**实际 PlanRun × 平台**，不会把同 run 的纯展锐 host 算进 MTK 期望集；
- 未证实的 host 逐台给出原因码，入场硬阻断可按 host 登记缺谁，而不是只答「有/没有」；
- 退役与缺行 host 计入未证实（fail closed），不被静默剔除。

契约形态在本单内固定；#4043 侧的消费点与硬阻断写库属其自身交付。

### 6. 两个场景的现行语义

- 入场：由 #4043 在首次写 `DEDUP_FIRST` 前调用第 5 条接口（Owner 裁定：任一台未证实即禁止切入）。
- 运行中：分区已固定 `DEDUP_FIRST` 后能力失效——模式不回退，只在轮次台账登记缺口；
  能力在安静期内恢复则由 `_capability_recovered_units` 建立**补充轮**（SUPPLEMENT，
  期望集只含该缺口 host），已覆盖结果与模式都不回溯。
- 若缺口没等到恢复就进入 settled：新增
  `shadow_scan_settled_with_capability_gap`（WARNING，带 run/轮次/host 列表）留痕。
  这是**已登记缺口**，不是新的恢复通路——settled 之后仍要按既有补跑/重开路径人工处置，
  是否为此再加自动恢复留给 #4043 / 规划者裁定（见 Revisit）。
- 现行 `scan_now`（`fact_round_id` 为空）与 settled 出口沿既有路径，不读能力证据；
  `test_current_scan_now_path_never_enters_the_capability_guard` 以三道 tripwire 锁住。

## Alternatives

1. **保留 digest 作为附加条件（digest AND 能力）**——否。双判据下「当前心跳可解释实际候选结论」
   不成立，且 fleet 回退场景仍能被旧摘要放行，正是 #3875 标注为过渡的形态。
2. **只靠握手能力，不引入心跳证据**——否。握手只活在连接期：控制面无法在预筛与入场时点给出
   持久可核验的证据，也无法区分「旧 Agent 回退但连接尚在」。
3. **写进 `Host.extra` / 复用 `agent_artifact_digest` 派生能力**——否。摘要相同不代表支持新载荷，
   且把准入凭据混进展示口径会让两侧演进互相牵制。显式列 + 迁移与既有 heartbeat 事实列同款。
4. **`reported_at == last_heartbeat` 严格相等**（继承现场的写法）——改为 `>=`。相等把
   「轻量心跳端点也写 `last_heartbeat`」当成隐性前提（该端点今天零调用方），一旦有别的写入方
   就整机队静默失去准入；`>=` 在现有两条通路上判定完全相同，只少一个不可达方向。
5. **判据内重算 300s 心跳窗口**——否，理由见 Decision §2 新鲜度条。
6. **能力失效即回退 CURRENT 模式**——否。Owner 裁定 6975368043 明确维持模式、登记缺口、按补充轮恢复。

## Verification

已实跑（本机 cgroup 保护入口 `scripts/run_pytest.py`，testcontainers PG16）：

| 命令范围 | 结果 |
|---|---|
| 本单 4 个新探针文件（能力证据接口 / 心跳接收 / 发送守卫矩阵 / Agent 契约同源） | 45 passed |
| 返修前基线红清单（判据未改时实跑 HEAD） | 15 处：1 collection error + 14 failures，全部落在 digest 过渡判据上 |
| `scripts/run_gates.py check:quick` | 16 gates OK（含 import-linter C3：控制面对 `backend.agent` 只走 `contracts` 通配） |
| 同一行为探针（只在基线写的 2 条：无能力列、摘要即可准入） | 基线 `e7325af03` 2 passed；本分支同文件 2 failed |
| 交付探针放到基线 | 8 个文件全部 collection error（`ModuleNotFoundError: backend.agent.contracts.capabilities`）——判据在基线不存在 |
| 受影响回归（services/api/realtime/tasks/agent 全量） | 见 PR 评论区实跑结论（本 Note 不预判） |

新旧 Agent 兼容矩阵（每行都有对应测试；「实际发送」指 `sio.call` 调用点次数）：

| 场景 | 心跳 capabilities | 握手 capabilities / 实例 | 预筛 | 实际新轮次发送 | 台账 |
|---|---|---|---|---|---|
| 新 Agent 正常 | 含 `scan_round_v1` | 含，实例一致 | 通过 | 1 次/轮/host | 无缺口 |
| 旧 Agent（整机回退） | 缺键 | 缺 | 拒绝 | 0 | `capability_unverified`（capability_missing） |
| 当前心跳漏字段 | 缺键 | 含 | 拒绝 | 0 | 同上 |
| 显式空列表 | `[]` | 含 | 拒绝 | 0 | 同上 |
| 从未上报（迁移前存量） | NULL 列 | — | 拒绝 | 0 | capability_never_reported |
| 迟到心跳撤回能力 | 预筛后被撤回 | 含 | 预筛通过、复核拒绝 | 0 | `capability_unverified` |
| 预筛后重连旧构建 | 仍含 | 缺 | 预筛通过 | 0（连接守卫拦住） | `..._at_send` |
| 预筛后换实例 | 属实例 A | 连接属 B | 预筛通过 | 0；B 上报后同轮恢复 1 次 | 缺口后清除 |
| 畸形握手形状 | — | 标量非数组 | — | 0 | fail closed |
| 现行 scan_now（非影子） | 任意 | 任意 | 不经判据 | 照旧 | 不变 |

未做（不得当作已通过）：生产控制面部署与重启、真实 fleet Agent 升级/回退实跑、
真实 Socket.IO 端到端连接（断言止于 `sio.call` 调用点）、生产 Execution Registry
与影子开关现网状态核对、容量与迁移在产库上的执行。迁移 `b8u2h1c2a3p4` 的
upgrade/downgrade 与 `pr-migrate-empty-db` 门禁以 CI 结论为准。

## Revisit

- settled 之后才恢复的能力：目前只留 WARNING 与台账缺口，不自动重开分区。
  若 #4043 试点期出现真实案例，需要规划者裁定「补跑代表证据」还是「允许受控重开影子轮」。
- 若 fleet 需要「最低版本」表达（而非逐台能力声明），或 `capabilities` 要进入
  ADR-0040 的部署一致性判定，属全局协议变更，交 Owner 裁定。
- 能力证据的只读展示面（Hosts 页 / 生产验收查询）本单未做：现可读
  `host.agent_capabilities` / `_reported_at` / `_instance_id` 三列。若生产验收工作面
  需要 API 或页面入口，另立单，并同步 `frontend/src/utils/api/types.ts`。
- `_dispatch_due` 与实例绑定的交互：换实例后到下一拍心跳之间的窗口按拒发处理；
  若生产观测到明显空转，可考虑放宽为「同 boot 即放行」——需先用真实数据证明不是漏判。
