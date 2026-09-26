# 终态舱壁默认并发 16 → 8：ADR-0052 移除父行锁后按事件循环重校（#3403）

Status: implemented
Class: bug-fix

关联：[#3403](https://github.com/DUElost/stability-test-platform/issues/3403)（本单：定位与全部证据）、
[#3247](https://github.com/DUElost/stability-test-platform/issues/3247)（夜间兜底：heartbeat 探针 p99 1.003 / 1.043 s）、
#3359（ADR-0052 D1–D5，引入点）、[#3375](https://github.com/DUElost/stability-test-platform/issues/3375)（ADR-0052 §5 真机验收）、
[ADR-0047 D2](../../adr/ADR-0047-db-pool-and-connection-capacity.md)（终态舱壁）、
首轮值来源 [2026-09-23 Note](./2026-09-23-db-pool-budget-and-terminal-bulkhead-2959.md)（16 / 500ms）。

## Decision

`STP_TERMINAL_BULKHEAD_CONCURRENCY` 默认值由 **16 改为 8**（`backend/core/terminal_bulkhead.py`）。
等待预算 500ms、env 覆盖和非法值回退语义都不变（非法值回退到新默认 8）。CI 探针的 1 s 线保持不变
（owner 2026-09-26 裁决）。

**成因**（#3403 实测，本机 testcontainers 隔离库、同一 venv、交替运行）：

- #3359 之前，每个 `/complete` 两次 `SELECT plan_run … FOR NO KEY UPDATE`（`agent_completion` 的
  ABORTED 分支 + `on_job_terminal`），同一 run 的终态在 **PG 里串行**。等锁的协程不占事件循环，
  所以舱壁**名义 16、实效约 1**。
- ADR-0052 D1 按设计移除了父行锁，16 个名额变成真并发。每个 `/complete` 约 12 次 DB 往返都经过
  同一个事件循环，每轮就绪回调从约 3.8 个涨到 24–27 个，事件循环延迟 p50 从 0.6 ms 升到
  3–5.5 ms。探针请求每次 await 都要排在整批之后。CPU 总量没有变多，变的只是排队点。

**为什么改舱壁**：舱壁本来就是 ADR-0047 D2 为终态风暴设的闸。首轮值 16 定在「行锁已经串行」的
时期；ADR-0052 拿掉了这道隐性的闸，显性的闸就要按实效并发重新校准。ADR-0052 §5 ② 只禁止
「放宽」舱壁，收紧不在其列。

## Alternatives

1. **保持 16，CI 探针线改为「记录 + 宽松界」**：驳回。owner 裁决保持 1 s 线；而且回归已经定位为
   真实的行为变化（同机 3–8×），放宽会让它从 CI 上消失。
2. **16 → 4**：`/health` 回到 #3359 之前的水平（33–34 ms），但 503 多约 40–50%、放大系数升到
   1.6–1.67。生产的 outbox 重放周期是 15 s，收敛风险更高。先取 8，#3375 真机数据不够再往下调。
3. **进程内按 run 串行终态**（asyncio 锁）：等于把 ADR-0052 拆掉的串行段搬回进程里，`/complete`
   p99 会回退。驳回。
4. **减少 `/complete` 每次的 DB 往返和 await**（约 12 次）：从根上降低每个终态占用的事件循环时间，
   属于长期项（#3403 选项 C），不在本次范围。
5. **线程池饥饿 / TESTING 内联 drain 方向的修复**：实测已排除。heartbeat 在执行器里排队 p99 只有
   0.5–1.3 ms；风暴中完全不聚合时，探针仍在 132–256 ms。

## Verification

舱壁并发扫描（main `d8e3ef22`，TESTING 内联 drain，即 CI 实际配置；每档 2 轮，本机为生产控制面宿主）：

| 舱壁并发 | heartbeat p99 | `/health` p99 | 事件循环延迟 p50 | 每轮回调数 | 削峰 503 | 收敛 |
|---|---|---|---|---|---|---|
| 16（原默认） | 255–269 ms | 150–151 ms | 4.6–5.0 ms | 19.8 | 215–217 | 9.3–9.4 s |
| 8 | 152–154 ms | 118–119 ms | 1.9–2.5 ms | 12.0–12.8 | 257–276 | 8.8–9.3 s |
| 4 | 93–176 ms | 33–34 ms | 1.0–1.1 ms | 7.7–7.8 | 293–327 | 8.9–9.8 s |
| #3359 之前（行锁串行） | 55–89 ms | 13–34 ms | 0.6–0.7 ms | ~3.8 | 112–196 | 6.4–8.2 s |

- 本分支（默认 8）单独连跑 3 轮（机器负载 4–5）：heartbeat 158–216 ms、`/health` 76–93 ms、
  503 300–348、放大 1.61–1.71、收敛 9.5–10.9 s、async 池峰 13–14 / 40。
- `backend/tests/test_terminal_bulkhead_2959.py` + `test_db_overload_response_2959.py` +
  `services/test_plan_run_abort_backflow_scale_3243.py` → **28 passed**。
- CI 取证：待回填（分支 dispatch `ci.yml` 的 backend-test 读数）。

## Revisit

- **#3375 真机验收**（ADR-0052 §5）用新默认 8 跑：核对 503 数、放大系数、120 s 收敛，以及风暴中的
  heartbeat / UI p99。如果收敛或 503 明显变差，用 env 调回，不需要发版。
- 如果 CI 上 heartbeat 仍然贴线：下一步是 4（数据见上表），或者做 #3403 选项 C。
- 该进程内压测把 httpx 客户端协程也放在同一个事件循环里，会**高估**生产的拥塞；绝对延迟以
  #3375 真机读数为准。
