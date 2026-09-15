# RunConsole 归属注册表 P2：状态快照（跨实例 status / 订阅校验）（#1737 / ADR-0027 P3-4）

Status: implemented
Class: feature

## Decision

按 [#1737](https://github.com/DUElost/stability-test-platform/issues/1737) 裁决的分阶段
（[设计稿 §6](../../design/2026-09-13-run-console-multi-instance-ownership.md)）落地 **P2**：
把 `status()`（及其复用者——`console:` 房间订阅校验）从「进程内态」改为「本地优先 +
跨实例快照回退」。

**快照键与发布点**：

- 键：`stp:console:status:<run_id>`——**独立于 P1 的 owner 身份键**（`stp:console:owner`）；
- 发布：`start()`（RUNNING）/ `_finalize()`（终态）/ 续期 tick（刷新 TTL，键丢失则重发全文）；
- 终态 TTL = 本地 `STP_RUN_CONSOLE_TERMINAL_RETENTION_SECONDS`（默认 3600s）——与
  本地终态保留**对齐**，避免「本地还能查到、跨实例已 404」的不对称；本地
  `_sweep_terminal_runs` 淘汰时顺带删除快照（best-effort，TTL 兜底）。
- 读取：`RunConsole.status()` 本地有 run → 本地视图（快照被篡改也不影响本实例）；
  否则注册表启用时读快照 → 命中即返回（含 `updated_at`/`instance_id` 附加字段）；
  未启用注册表 → 行为与 P1/v1.2 完全一致。

**为什么快照用普通 `SET ... EX` 而不是 P1 的 CAS 指纹续期**：`run_id` 全局唯一，不存在
「外部合法持有者」——只有本 run 的 owner 会写该键；而快照内容每次发布都在变
（`seq`/`updated_at`），指纹 CAS 会把**自己的**新值判为异己。互斥键（`run_key` 维度）
才需要指纹 CAS（多实例对同一 key 竞争）。

**为什么不在终结时删除快照**（P1 对 owner 身份键是删除）：终态可见性需要跨实例
一致——dedup/助手/安装的 API 在 run 结束后仍会查 `status()`（结果页/审计），
若终态快照即删，跨实例立刻 404，而本地实例还能查 1 小时。

**文案（两分支）**：`console_run_miss_hint()` 与 `multi_instance_console_warning()`
按注册表状态切换——未启用保持 v1.2 的「单实例语义」告警（原文案与测试断言不变）；
启用后声明 `console_run_key_mutex=true / console_status_cross_instance=true`，
并把剩余限制显式列为 `read_log_replay,cancel_forwarding`（ref=#1737/#1114）。

## Alternatives

- **复用 owner 身份键承载状态（改 payload）**：拒绝。owner 键的 renew-or-rebuild 以
  「payload 指纹」判外部持有；状态字段每 tick 变化会使自己成为「外部持有者」。
  两键分离=身份（CAS）与状态（last-write-wins）各用正确原语。
- **每行日志发布快照**：拒绝。写放大（行级频率 × Redis 往返）；`seq` 在 tick 刷新时
  已足够新鲜（status 的消费方：结果页/订阅校验/审计，不需要行级实时）。
- **终态即删快照（沿用 P1 owner 键策略）**：拒绝（理由见上）。
- **跨实例 status 走控制面间 RPC（读 owner）**：拒绝。那是方向 B 的基建；P2 按裁决
  走「共享状态」，无常驻转发通道。
- **快照写失败阻断 start**：拒绝。status 可见性是可用性能力，不是正确性不变量
  （互斥是）——`_publish_snapshot` 捕获 `ConsoleRegistryUnavailable` 仅告警。

## Verification

- **红绿对照**：仅暂存 `backend/services/run_console.py`（保留注册表新 API）→
  `cross_instance / terminal_snapshot / snapshot_refresh / sweep_deletes` **4 failed**；
  恢复实现 → 三文件合计 **56 passed**；
- 新增 **13** 测试：
  - 注册表（6）：发布/读取（含 `instance_id`/TTL 断言）/ 损坏或缺失返回 None /
    TTL 续期 true→false / 删除 / Redis 错误 fail-closed / 不可用时读续删不抛；
  - 接线（6）：**跨实例 status 读快照**（B 可见 RUNNING）/ **终态快照保留**
    （TTL=本地保留期，B 读 SUCCESS）/ tick 续期与丢失重发 / 本地优先 / 快照失败不阻断
    start / 淘汰时清快照；
  - 文案（1）：P2 分支断言（status 跨实例 + 剩余限制），既有 v1.2 断言保持不变；
- 受影响既有测试（RunConsole 调用面 5 文件）与 `check:quick` 结果见 PR。

## Revisit

- **P3（cancel 转发）**：请求位 + owner 消费（3s 有界等待）——落地后 `console_run_miss_hint`
  的剩余限制再收窄为 `read_log_replay`；
- **P4（read_log 跨实例）**：决定是否引入控制面间 RPC（方向 B）或日志片段外置；
- **订阅校验的 socketio 层用例**：现由 `status()` 语义覆盖（订阅入口即 status 判空）；
  若后续订阅协议变化（如带鉴权上下文），补 socketio 层直测；
- **快照字段演进**：若 P3 需要「取消请求处理中」等中间态，扩展快照字段而非新键
  （向后兼容：未知字段消费方忽略）。
