# RunConsole 归属注册表 P4：跨实例日志 replay（#1737 / ADR-0027 P3-4）

Status: implemented
Class: feature

## Decision

按 [#1737](https://github.com/DUElost/stability-test-platform/issues/1737) 裁决分阶段
（[设计稿 §6](../design/2026-09-13-run-console-multi-instance-ownership.md)）落地 **P4**
（最后一项）：`read_log` 的跨实例语义。

**评估结论：不引入控制面间 RPC，也不做 Redis 日志镜像。**

事实依据（源码核对）：

- 写入路径（`_reader_loop.flush`）**每批都落盘** `log_root/{run_id}.log`，且 `seq` 与
  文件行号在同一临界区内分配（#1275 串行化）→ **文件即 replay 源**，行号 = seq；
- `read_log` **既有文件回退**（run 不在内存 → 按约定路径读文件），`_replay_max_lines`
  上限与单行截断已把内存/响应体约束好；
- 因此跨实例 replay 的唯一缺口是：**文件是否对各实例可见**（部署属性）+ **status 缺省
  UNKNOWN**（P2 前无跨实例状态）。

据此的最小落地：

1. **status 由 P2 快照补全**：run 不在本实例且注册表启用 → 读 `stp:console:status:<run_id>`
   快照填 `status`（launcher 终态快照保留期与本地一致）；
2. **`replay_unavailable` 显式化**：文件缺失 / 文件行数落后于 owner 快照 `seq` 时，
   返回 `replay_unavailable: true` + WARNING（含 `owner_instance` 与 `hint=检查
   STP_RUN_CONSOLE_LOG_ROOT 是否各实例共享`），`seq` 取 `max(file, owner)`——
   **不把「读不到」伪装成「没有输出」**；
3. **部署前提文档化**：`STP_RUN_CONSOLE_LOG_ROOT` 对各实例可见（同机多进程天然共享；
   多机需挂同一存储）；env 清单新增该行说明。

**ADR-0027 升至 v1.7**：清单第 6 条收口为「启用注册表后，RunConsole 依赖功能不再要求
单实例」——互斥 / 登记 / status / 订阅 / cancel / replay 全部有跨实例语义（replay 带
上述存储前提）。

## Alternatives

- **控制面间 RPC 转发 `read_log`**：驳回。RPC 只在「日志目录不共享」的部署里有增量，
  而该场景用「挂同一存储」即可覆盖；为此新增请求通道 + 超时/重试语义不划算
  （对比 P3 cancel：cancel 必须触达 subprocess，无共享替代，故引入请求位是必要的）。
- **Redis 日志镜像（有界 replay 到列表）**：驳回。与本地文件模型重复（双写、双一致性面），
  且快照 seq/文件行号已给出精确回溯语义；纯为「不共享存储」的部署买单。
- **保持现状（replay 永远 owner 本地）**：驳回。断线重放（UI 核心路径）在非 sticky 多实例
  下会 404/空——与 P1–P3 的推进目标矛盾。
- **文件缺失时静默返回空 lines（P4 前行为）**：驳回。会把「存储未共享」伪装成「无输出」，
  运维无从察觉（本 PR 以 `replay_unavailable` + WARNING 替代）。
- **`status` 保持 UNKNOWN（P4 前行为）**：驳回。P2 已有快照，跨实例 replay 的响应没有任何
  理由退化。

## Verification

- **红绿对照**：仅暂存 `backend/services/run_console.py` → `*_read_log_*` 三例 **failed**
  （status 未补全 / 无 `replay_unavailable` / `seq` 未取 max）；恢复 → **38 passed**
  （`test_run_console_registry.py` + `test_run_console.py`）；
- 新增 **3** 测试：共享 log_root 跨实例 replay（行 + status 快照补全 + seq）/
  未共享（缺失文件 → `replay_unavailable` + owner seq）/ 文件落后（部分共享 → 标记 +
  seq=max）；
- 既有 `read_log` 用例（历史回退 status=UNKNOWN、from_seq、上限截断、单行截断、
  文件序≠seq 序）全部保持通过；
- 受影响既有测试（RunConsole 调用面 5 文件）与 `check:quick` 结果见 PR。

## Revisit

- **#1737 关闭条件**：P1–P4 已全部落地；验收②「双实例验收」建议在真双进程/多实例
  rollout 时执行（本机以进程内双实例模拟覆盖语义：互斥 / status / 订阅 / cancel / replay）；
  届时按 ADR-0027 v1.7 清单第 6 条复核即可关闭本单；
- **`replay_unavailable` 的消费面**：当前作为响应附加字段 + WARNING；若 UI 需要显著提示
  （如「日志不完整」横幅），前端按 `data.replay_unavailable` 渲染即可（无需后端改动）；
- **共享存储形态**：同机多进程零成本；多机部署把 `STP_RUN_CONSOLE_LOG_ROOT` 指到
  与其它共享资产（如 `STP_AEE_NFS_ROOT` 同族的挂载）一致的路径，纳入 rollout 清单。
