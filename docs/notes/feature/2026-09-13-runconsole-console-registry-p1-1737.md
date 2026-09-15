# RunConsole 归属注册表 P1：全局 run_key 互斥 + owner 登记（#1737 / ADR-0027 P3-4）

Status: implemented
Class: feature

## Decision

按 [#1737](https://github.com/DUElost/stability-test-platform/issues/1737) 的裁决（
[设计稿 §8](../../design/2026-09-13-run-console-multi-instance-ownership.md)，方向 A /
续期失败策略 ③ 窄化自杀 / TTL 120s / 验收=进程内双实例模拟）落地 **P1**：

1. **新模块** `backend/realtime/console_registry.py`——与 P3-3 `agent_sid_registry`
   同族（TTL + 指纹 CAS Lua），但用**同步** Redis 客户端：
   `RunConsole` 是同步线程模型且会被事件循环线程直接调用，
   `run_coroutine_threadsafe(...).result()` 会在循环线程内**死锁**——因此自建
   `redis.Redis`（`socket_timeout=2s` 有界），而非复用 lifespan 的 async 客户端。
2. **键与语义**：
   - `stp:console:key:<run_key>`：`SET NX PX` 获取；Lua CAS 续期（**严格**：
     不重建，键丢失=失去互斥）/CAS 释放；获取失败 **fail-closed**（Redis 不可达或
     未配置 → `ConsoleRegistryUnavailable`，调用方拒绝启动，不静默降级本地互斥）；
   - `stp:console:owner:<run_id>`：登记 + renew-or-rebuild（同 #1113；外部持有 →
     `foreign` 仅 ERROR 日志，绝不覆盖）；
   - **续期 `lost`（确认外部持有/键丢失）→ 止损取消本 run**（保「同 key 全局至多
     一个 RUNNING」）；纯瞬态错误（`unavailable`）仅告警、等下个 tick（不误杀）。
3. **RunConsole 接线**：`start()` 获取全局互斥（失败回滚本地 `_inflight_keys`/`_runs`）
   + owner 登记；续期 ticker（间隔 = TTL/3，下限 10s，daemon）；`_finalize`/
   spawn 失败路径 CAS 释放；`shutdown()` 停 ticker + 显式兜底释放（幂等）。
4. **门控**：`STP_CONSOLE_REGISTRY`（默认跟随 `STP_SOCKETIO_REDIS_ADAPTER`；
   `TESTING=1` 恒关）——**单实例默认零变化、零 Redis 流量**。
5. **文档**：ADR-0027 **v1.4**（新增 P3-4 节；清单第 6 条改为按注册表状态区分：
   未启用=单实例语义不变；启用=互斥/登记跨实例生效，`status()`/`cancel()`/`read_log()`
   为 P2/P3 剩余限制）+ 环境变量清单两行 + 设计稿回填 Accepted。

**明确未做（P2–P4 在途，见 ADR-0027 P3-4 与设计稿 §6）**：跨实例 `status()`、
`console:` 房间订阅、`cancel` 转发、`read_log`——因此**启动 WARN 与 404 诊断提示保留**
（未启用注册表时语义与 v1.2 完全一致；启用后仍未具备跨实例路由，故提示文案不改）。

## Alternatives

- **复用 lifespan 的 async Redis 客户端 + 桥接主循环**：拒绝。`start()` 由 async 路由
  同步调用（在循环线程内），`run_coroutine_threadsafe(...).result()` 必然死锁；
  不改 `RunConsole` 为 async（调用面 dedup/agent_installer/ai_assistant/socketio 全同步）。
- **互斥键续期用 renew-or-rebuild（同 owner 键）**：拒绝。互斥键丢失=已失去互斥，
  重建等于自造「双实例同时持有」窗口；宁可走止损取消（裁决 ③）。
- **续期失败一律自杀（策略 ②）**：拒绝。Redis 瞬态抖动会误杀在跑 run（代价高于
  窗口风险）；只对**确认**外部持有/键丢失（CAS 判定）止损。
- **把 `read_log` 一并外置（方向 C）**：拒绝（本单范围外）。与本地日志文件模型冲突、
  体量大；留在 P4 评估。
- **未启用时也写入 owner 键（后置开关）**：拒绝。门控不启用时保持零 Redis 依赖，
  与 P3-2/P3-3 的 opt-in 纪律一致。

## Verification

- 新增测试 **22**：
  - `backend/tests/realtime/test_console_registry.py`（15）：门控（TESTING 恒关 /
    显式 0/1）/ TTL 下限与非法值 / 获取 + 第二持有者 busy / Redis 错误 fail-closed /
    `SET NX PX` TTL / 续期 ok→lost（外部覆盖/键丢失）/ 错误→unavailable /
    CAS 释放仅删自有 / owner 重建与 foreign 不覆盖 / configure 门控 / shutdown 关连接；
  - `backend/tests/services/test_run_console_registry.py`（7）：第二实例同 key
    RunKeyBusyError / 终态释放后可再启动 / 注册表不可用 fail-closed 且本地回滚 /
    owner 登记失败回滚 / **续期 lost → 止损取消**（error 前缀 `run_key_lost`）/
    瞬态 unavailable 不误杀 / shutdown 兜底释放；
- 受影响既有测试（RunConsole 调用面）：`pytest backend/tests/api/test_dedup_helpers.py
  test_main_lifespan.py test_ai_assistant_endpoints.py test_dedup_jira_endpoints.py
  test_dedup_scan_endpoints.py -q` → **123 passed**（48s）；
- `ruff check`（5 个改动文件）→ 全过；
- `python scripts/run_gates.py check:quick` → 7 门禁全绿（含 gov-surface S1–S13，
  ADR v1.4 版本面同步通过）。

## Revisit

- **P2/P3/P4**：status/订阅走共享状态 → cancel 请求位（3s 有界等待）→ read_log；
  P2 落地后同步改写 `console_run_miss_hint()`/`multi_instance_console_warning()`
  （届时「单实例语义」告警应收窄为「read_log 等剩余限制」）；
- **真双进程验收**：进程内双实例模拟已覆盖语义；真进程隔离（kill -9 owner →
  TTL 释放）归多实例 rollout 清单执行（与本单触发条件对齐）；
- **TTL 标定**：120s 与 sid registry 同口径；若现场观察到续期拥塞或失联释放过慢，
  经 `STP_CONSOLE_REGISTRY_TTL_SECONDS` 调整（不影响其它组件）；
- **`is_key_busy()` 保持本地视角**：多实例下跨实例 busy 查询属 P2（status 快照）范围。
