# RunConsole 归属注册表 P3：跨实例 cancel 转发（#1737 / ADR-0027 P3-4）

Status: implemented
Class: feature

## Decision

按 [#1737](https://github.com/DUElost/stability-test-platform/issues/1737) 裁决分阶段
（[设计稿 §6](../design/2026-09-13-run-console-multi-instance-ownership.md)）落地 **P3**：
`cancel()` 在本地无此 run 时经**请求位 + 有界等待 ack** 转发给 owner。

**机制**：

- 请求位 `stp:console:cancelreq:<run_id>`（`SET EX`，TTL 默认 60s）：payload 带
  `instance_id` + **`requested_at` 指纹**；
- owner 端 **control tick（默认 1s，`STP_CONSOLE_CONTROL_TICK_SECONDS`）**：扫描本实例
  非终态 run 的请求位 → 执行本地取消语义（既有进程组 kill 路径）→ 清请求位 → 回写
  `stp:console:cancelack:<run_id>`（**回带同一指纹**）；
- 请求方 `_request_remote_cancel`：**先读状态快照短路**（已终态 → 直接 False，不投递），
  再投递请求并轮询 ack（100ms 间隔）直到上界 `STP_RUN_CONSOLE_CANCEL_WAIT_SECONDS`
  （默认 3s）；命中指纹匹配的 ack → 返回其 `canceled`；超时/注册表不可用 →
  **fail-closed 返回 False**（绝不假装成功），日志含 `run_console_cancel_timeout`。

**ticker 拆分**：原 ticker 只有 TTL/3（≥10s）的注册表续期节奏，无法支撑 3s 等待窗——
拆为**控制 tick（1s，消费取消请求）** + **续期到期才做（TTL/3）**，同一条线程；
控制 tick 常量保证 < 等待窗。

**阻塞边界（关键）**：`_request_remote_cancel` 的等待只能发生在**非事件循环线程**：

- 同步路由（`dedup.cancel_jira_run` / `hosts.host_install_agent`）由 FastAPI 线程池执行
  ——等待只占用一个 worker；
- 定时器线程（`orchestrator._arm_run_timeout._fire`）——本来就是后台线程；
- **事件循环内**的 `ai_assistant.cancel_action` 改为
  `await asyncio.to_thread(RunConsole.instance().cancel, run_id)`——否则 3s 等待会阻塞
  整个事件循环（本 PR 同步修正该调用点）。

**语义取舍**：请求方拿不到 ack 时返回 False（API 层 409 CANCEL_NOT_ROUTABLE 的既有
映射不变）——宁可让调用方看到「未能确认取消」，也不返回未经验证的 True；
owner 的 ack 只回带指纹匹配的结果，重试/并发不串线。

## Alternatives

- **阻塞式等待放在 `cancel()` 内直接轮询（不区分调用线程）**：拒绝。事件循环内调用会
  阻塞全站 3s；本 PR 以「调用点 to_thread + 文档化线程约束」处理，而非让底层猜上下文。
- **ack 只写不匹配指纹（任何 ack 都算）**：拒绝。并发/重试下会把他人请求的结果当成
  自己的（例如上次超时后这次秒回 False）。
- **请求方等待窗拉长到覆盖 owner 的 10s 级 tick**：拒绝。等待窗越长，API 线程占用越久；
  改为 owner 侧拆分 1s 控制 tick（Redis 1 GET/s/实例，成本可忽略）。
- **owner 侧把请求位当命令队列批量处理（ack 批量）**：拒绝（过度设计）。每 run 一次
  读+删+写，规模 = 活跃 run 数，量级极小。
- **`cancel()` 只投递不等 ack（fire-and-forget，恒返回 True）**：拒绝。会把「请求已投递」
  伪装成「已取消」，且 409 语义失效（P2 前 ai_assistant 的教训 #1222 正是「不检查结果」）。

## Verification

- **红绿对照**：仅暂存 `backend/services/run_console.py`（保留注册表 P3 API）→
  `cross_instance_cancel_*` 三例 **failed**；恢复 → 三文件合计 **62 passed**；
- 新增 **8** 测试：
  - 注册表（3）：请求位 roundtrip（含 TTL 断言）/ ack 指纹匹配（不匹配视为未到）/
    不可用时「投递抛、读清写不抛」；
  - 接线（3）：**B 发起 → 请求位投递 → A（owner）消费 → ack → B 得 True 且 run 取消** /
    无 owner 消费 → 超时 fail-closed False 且请求位保留 / 终态快照短路（不投递）；
  - 文案（1，改写 P2 用例）：`console_cancel_forwarding=true` +
    `remaining_limits=read_log_replay`；
- 受影响既有测试（ai_assistant / dedup / hosts 调用面 5 文件）与 `check:quick` 见 PR。

## Revisit

- **P4（read_log 跨实例）**：最后一项限制；决定日志片段外置或控制面间 RPC（方向 B）；
- **cancel 竞态窗口**：owner 在「读请求位 → 清位」之间崩溃 → 请求方超时 False，请求位
  TTL 60s 自清（无重复取消风险：取消是幂等语义）；
- **等待窗/控制 tick 标定**：默认 3s/1s 覆盖单次 tick 往返；若现场网络 RTT 或负载导致
  超时率升高，调 `STP_RUN_CONSOLE_CANCEL_WAIT_SECONDS` / `STP_CONSOLE_CONTROL_TICK_SECONDS`
  （两者须保持「等待窗 ≥ 3× 控制 tick」）；
- **多请求合并**：短窗口内重复 cancel 会产生多条请求位（后写覆盖）——`requested_at`
  指纹保证各自等待窗内只认自己的 ack，无正确性问题。
