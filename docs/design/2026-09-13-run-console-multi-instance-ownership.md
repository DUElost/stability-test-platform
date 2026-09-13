# RunConsole 多实例归属语义裁决草案（#1737）

- **状态**：**Accepted（2026-09-13 裁决）**——方向 **A（共享状态 + owner 句柄）**；
  续期失败策略 **③ 窄化自杀**（仅「确认外部持有/键丢失」止损取消，瞬态错误不误杀）；
  TTL **120s**；`cancel` 等待窗 **3s + 可配**；验收=**进程内双实例模拟**（真双进程归 rollout）。
  **P1 已落地**（`stp:console:key/owner` + fail-closed 获取 + 止损取消，ADR-0027 v1.4）；
  **P2 已落地**（状态快照 `stp:console:status`：跨实例 `status()`/订阅校验生效，ADR-0027 v1.5）；
  **P3 已落地**（cancel 转发：请求位 + owner control tick 消费 + 有界等待 ack，超时 fail-closed；ADR-0027 v1.6）；
  **P4 已落地**（跨实例 replay：文件源 + 共享 `STP_RUN_CONSOLE_LOG_ROOT` 前提 +
  `replay_unavailable` 显式化；ADR-0027 v1.7）——**四阶段全部落地**。
- **日期**：2026-09-13
- **来源**：#1737（承接 #1114 / #1517 的 Revisit）；ADR-0027 v1.2 清单第 6 条；#720 Epic。
- **关联 note**：[`2026-09-11-runconsole-multi-instance-boundary-1114.md`](../notes/bug-fix/2026-09-11-runconsole-multi-instance-boundary-1114.md)。

## 1. 问题本质

`RunConsole` 是**进程级单例**（`backend/services/run_console.py`）：`_runs` / `_inflight_keys`
仅本进程可见。多实例形态下：

- A 实例 `start(run_key=...)` 后，B 实例的 **订阅校验**、`status()`、`cancel()` 均查本地 → 必然失败
  （现状：404 + `console_run_miss_hint()` 可诊断，属**降级**而非修复）；
- 同 `run_key` 可**跨实例并发**——`jira:{vendor}` 的「同厂商串行」硬约束（dedup）在多副本下失效，
  这是本单的**正确性缺口**（其余为可用性缺口）；
- subprocess 句柄天然不可跨进程：任何跨实例动作要么**转发到 owner**，要么只读**共享状态**。

现状缓解（保留为降级路径，本单不撤销）：ADR-0027 清单第 6 条「使用 console 依赖功能的部署禁止
启用多实例（或 sticky）」+ 启动 WARN + 404 提示。

## 2. 约束与既有机制

- **单实例零变化**：默认路径不进 Redis（门控缺省跟随 `STP_SOCKETIO_REDIS_ADAPTER`，`TESTING=1`
  恒关）——与 `agent_sid_registry`（P3-3）同款门控；
- **fail-closed 优先于可用性**：互斥获取在 Redis 不可达时**拒绝启动**，不得静默放行（宁拒绝不重复执行）；
- **范式可复用**：`backend/realtime/agent_sid_registry.py`（#887/#1113）已给出 TTL 登记 + 指纹
  payload + **Lua CAS 续期/释放**的成熟形态，console 注册表按同族实现；
- **不做日志上云**：`read_log` 现读本地文件、房间事件经 P3-2 adapter 已跨实例扇出；
  把日志片段搬进 Redis 的代价与一致性风险另议（见方向 C）；
- `console:` 房间订阅校验入口是 `RunConsole.instance().status(run_id)`（`socketio_server.py`）——
  **status 一旦读共享状态，订阅校验自动跨实例**，无需新增订阅协议。

## 3. 方向选项

### A（推荐）共享状态 + owner 句柄

| 能力 | 机制 | 结果 |
|---|---|---|
| `run_key` 全局互斥 | `SET stp:console:key:<run_key> <instance>:<run_id> NX PX ttl`；CAS 续期/释放（Lua，同 #887 指纹比对） | ✅（本单首要缺口） |
| owner 登记 | `stp:console:owner:<run_id>` → JSON `{instance_id, run_key, status, started_at, updated_at}`，TTL 续期 | ✅ |
| 订阅校验 / `status()` | owner 快照读（非 owner 实例返回快照；过期 → 明确 UNKNOWN 语义） | ✅ |
| `cancel()` | `stp:console:cancelreq:<run_id>` 请求位 + owner 周期 tick 消费并写 ack（有界等待，超时 fail-closed + 诊断） | ✅（秒级延迟） |
| `read_log()` | 保持 owner 本地 | ⚠️ 显式剩余限制（跨实例返回可诊断错误） |

**续期 tick**：owner 侧单 daemon 线程，间隔 = TTL/3，续期自己持有的全部 run_key 与 owner 键。

### B owner 路由（RPC 转发）

在 A 之上把 `cancel` / `read_log` **转发**到 owner 实例（控制面↔控制面请求-应答通道；P3-3 的
「房间 + adapter」是 agent 方向的同类先例，但控制面间 RPC 是新基建）。能力最全（read_log 也 ✅），
代价是超时/重试/鉴权语义与测试面显著增大。

### C 全外置（注册表 + 共享日志）

run 状态与日志片段全部外置（Redis/共享存储），owner 仅执行。能力最全但**与现有本地日志文件
模型冲突**，日志体量与一致性成本最高；不建议在本单触发条件下引入。

## 4. 失败与失联语义（各方向共同裁决点）

| 场景 | 候选策略 | 推荐 |
|---|---|---|
| 获取互斥时 Redis 不可达 | ① fail-closed 拒绝启动 ② 降级为本地互斥 | **①**（正确性优先；错误文案含「多实例互斥不可用」） |
| 续期失败 | ① 仅告警不自杀 ② 自杀止损（保互斥不变量） ③ 仅「确认外部持有」时自杀，纯瞬态错误先重试告警 | **③**（避免瞬态抖动误杀；CAS 判定「外部持有」= 确定性让位） |
| owner 进程失联 | TTL 过期 → run_key 自动可再获取；孤儿 run 的 `status` 返回过期快照 + `STALE` 语义（不假装 RUNNING） | 采纳 |
| 终态释放 | finalize 时 CAS 释放 key/owner 键；崩溃路径靠 TTL 兜底（≤TTL 窗口内不可重入） | 采纳 |
| TTL 取值 | 120s（与 sid registry 一致）/ 更短=释放快但续期更密 | **120s**（先用既有口径） |
| cancel 等待窗 | 例 3s（超时 → 失败 + 「owner 未响应，请核对实例」文案） | 3s + 可配 |

## 5. 验收方案

- **单实例回归**：门控关闭时行为与现状**逐条一致**（含 `RunKeyBusyError` 文案、404 提示）；
- **双实例（进程内模拟）**：两个 `RunConsole` 实例 + 共享 fake Redis（实现同族 Lua 的 test double），
  覆盖：同 key 并发仅一方成功 / A 起后 B `status` 可见 / cancel 转发 / owner kill → TTL 释放后可再起 /
  Redis 不可达 → fail-closed；
- **真双进程验收**：按本单触发条件归入多实例 rollout 清单（不在触发前强做）。

## 6. 分阶段落地（已裁决方向 A）

1. **P1（✅ 已落地）**：全局互斥 + owner 登记 + 续期/释放 + fail-closed——
   实现：`backend/realtime/console_registry.py`（`stp:console:key/owner`，Lua CAS）
   + `RunConsole` 接线（获取/续期/释放/止损取消/`shutdown` 兜底）+ 生命周期装配；
   测试：`backend/tests/realtime/test_console_registry.py`（15）+ 
   `backend/tests/services/test_run_console_registry.py`（7）；
2. **P2（✅ 已落地）**：`status()` / 订阅校验走共享状态——快照键 `stp:console:status:<run_id>`（start/终态/tick 发布；终态 TTL=本地保留期；tick 续期、丢失重发；本地优先，跨实例回退读快照）；
3. **P3（✅ 已落地）**：`cancel` 请求位 + owner 消费——`stp:console:cancelreq/ack`（指纹匹配），owner control tick（默认 1s）消费，请求方有界等待（默认 3s，`STP_RUN_CONSOLE_CANCEL_WAIT_SECONDS`）超时 fail-closed；事件循环内调用方 to_thread；
4. **P4（✅ 已落地）**：`read_log` 跨实例——**评估结论：不引入 RPC/日志外置**。理由：`read_log` 的既有文件回退即 replay 源（写入路径每行落盘、文件行号 = seq），RPC 只在「日志目录不共享」的部署里才有增量，而该场景用挂载同一存储即可覆盖，无需新增控制面间通道与超时语义；Redis 日志镜像则与本地文件模型重复。落地内容：status 由 P2 快照补全 + `replay_unavailable` 显式标记 + 部署前提文档化。

## 7. 对 ADR-0027 的影响（已执行：v1.4）

- 清单第 6 条改写为：**启用 console 注册表（env 门控）时**，RunConsole 依赖功能不再强制单实例；
  `read_log` 跨实例为显式剩余限制（返回可诊断错误）；
- 新增 P3-4 节：console 归属注册表机制 + fail-closed 语义 + owner 失联窗口（≤TTL）；
- 版本记 v1.4（头部版本记录 / 修订记录 / `adr/README` 主表 / DOC-MAP 行同步，S12 门禁）。

## 8. 裁决（2026-09-13，已回填）

- [x] 方向：**A（共享状态 + owner 句柄）**
- [x] 续期失败策略：**③ 窄化自杀**（仅「确认外部持有/键丢失」止损取消；瞬态错误仅告警）
- [x] `run_key`/owner TTL：**120s**（`STP_CONSOLE_REGISTRY_TTL_SECONDS`，下限 30）
- [x] `cancel` 等待窗：**3s + 可配**（P3 实施时生效）
- [x] 验收口径：**进程内双实例模拟足够**（真双进程归 rollout 清单）
