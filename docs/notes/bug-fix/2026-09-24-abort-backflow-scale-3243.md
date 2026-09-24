# R523 型回流压测：490 RUNNING 同时 abort + 并发终态回传（#3243）

Status: implemented
Class: bug-fix

关联：[#3243](https://github.com/DUElost/stability-test-platform/issues/3243)（本单）、
[#2959](https://github.com/DUElost/stability-test-platform/issues/2959)（父单）、
[ADR-0047 v1.1](../../adr/ADR-0047-db-pool-and-connection-capacity.md)（§4 待回填的分布就是本用例打印的那份）、
[#3241](./2026-09-23-db-pool-budget-and-terminal-bulkhead-2959.md)（控制面：预算门禁 + 过载 503 + 舱壁）、
#3242（Agent：首发单发 + 交 outbox + jitter；其 Note 随 PR #3251，合入后本行可改回链接）、
[#3244](https://github.com/DUElost/stability-test-platform/issues/3244)（终态聚合行锁热点，见下「校准发现 3」）、
相邻用例 [`test_plan_run_abort_scale.py`](../../../backend/tests/services/test_plan_run_abort_scale.py)（钉 abort **请求形状**；本用例钉**回流**）。

## Decision

把 R523 的形态搬进 PG 并让 **owner 的 8 条验收线成为断言**（`backend/tests/services/test_plan_run_abort_backflow_scale_3243.py`）：

- **形状**：37 host / 494 job（490 RUNNING + 4 FAILED）——与现场一致；每 job 一条 **ACTIVE JOB 租约**
  （`/complete` 对 RUNNING 作业校验租约 + fencing token，不造租约拿不到真实路径）；
- **动作**：真实 `abort_plan_run`（只替掉与判据无关的外呼：socketio / 通知 / 锁时长 / chain / dedup）
  → 490 个作业**同时**回传终态；
- **驱动**：复刻 #3242 已上线的 Agent 策略——**首发单发**、每机并发 2、失败转「outbox 重放」轮次
  （真机是 15s drain 周期，这里压缩到 0.5s：契约不变，只是不等真时钟）；
- **8 条验收线**：① 取连接失败 0；② HTTP 500 = 0（背压只能是结构化 503）；③ 可响应性 p99 < 1s；
  ④ 池不越预算（`pool_capacity()` 单一读数口）；⑤ 490 条事实全部 ACK；⑥ 计数一致
  （run / per-host = 实重数）；⑦ 120s 内收敛；⑧ 不依赖重启；
- **校准摘要**：`CALIBRATION_3243 {...}` 一行 JSON，**先打印后断言**——失败时也要拿到分布，
  否则「只在全绿时才看得到数」会让校准这件事永远没有证据（ADR-0047 §4 的判据设计同理）。

## Alternatives

- **起真 uvicorn + 真 Agent 进程**：更真，但没有 48 台机仍是模拟，且把 CI 变成进程编排测试；
  真机口径留部署窗复跑（见 Revisit）。in-process ASGI + 等价驱动是性价比最高的一段。
- **只压控制面（不模拟客户端策略）**：会漏掉「首发单发 + 每机 2 并发」这半场——而 R523 的放大
  （3.35×）正是那半场造成的。
- **post_completion 真跑**：需要 Redis + worker（属部署窗）。这里用记录队列替身，并**按 SAQ 的
  `key=pc:{job_id}` 语义去重**——首版按调用计数把幂等重放误判成扇出漂移（491≠490），
  这个坑写进替身 docstring。
- **种子只造 RUNNING、不预置已终态计数**：首版这么做，run 永远差 4（`terminal_job_count=490≠494`）
  不收敛。改为在 run/host 两级预置那 4 个 FAILED 的计数——它们在本 run 生命周期里**确实**
  早已终态化过，预置是对事实的还原，不是为了让断言好过。

## Verification

- `./scripts/run_pytest.sh backend/tests/services/test_plan_run_abort_backflow_scale_3243.py -q -s`
  → **1 passed**（8 条验收线全过），并打印校准摘要（见下）；
- 与相邻规模用例同跑（检查数据面隔离）→ `test_plan_run_abort_scale.py + 本用例` **4 passed**；
- `python scripts/run_gates.py check:quick` → **OK（15 gates）**。

### 校准发现（ADR-0047 §4 / #3243 的交付物）

实测（单进程 ASGI，37 host / 490 RUNNING，一次 abort）：

| 指标 | 实测 | 对照/预算 |
|---|---|---|
| **放大系数** | **1.14**（557 请求 / 490 事实） | R523 现场 **3.35** ⇒ 单发 + 削峰生效 |
| 背压形态 | 67 次 **503**（12%），**0 次 500** | 验收线 ② ✅ |
| 取连接失败 | `slots_exhausted` **0**、`timeout` **0** | 验收线 ① ✅ |
| **池峰（async）** | **17** | 预算 40 ⇒ 本场景下 20/20 有 ~2.4× 余量、reserve=8 成立 |
| 探针 p99（heartbeat/health） | **51.8ms** | 验收线 ③（<1s）✅ |
| 被接纳的 `/complete` p99 | **1.10s** | 见发现 3 ⚠️ |
| 收敛 | **5.4s**（round 1 排空 67 条用 0.55s） | 预算 120s ✅ |
| post_completion 扇出 | **490**（去重后，0 重复） | 每个终态恰好一次 ✅ |
| run 终态 | `FAILED` | 与 R523 真实结局一致 |

1. **舱壁与单发把「同时打进来的连接需求」压住了**：池峰 17（vs R523 的 86）——说明 20/20 的
   首轮值对本场景不紧；真正决定「会不会再撞 97 槽」的是**削峰前**的瞬时并发，而不是池大小。
2. **背压形态正确**：所有被削掉的请求都是结构化 503（含 `Retry-After`），Agent 侧一轮重放即排空
   （0.55s）——即 `Retry-After`/jitter 的组合在客户端侧是可消费的。
3. **⚠️ 被接纳的 `/complete` p99 ≈ 1.1s > 1s**（p50 ≈ 0.55s ≈ 舱壁等待预算），指向
   `plan_run` 行锁串行段：490 次终态共享同一条 run 行（#3244 要拆的热点），
   而 owner 的「p99 < 1s」是 **UI/heartbeat** 口径（本用例探针 52ms）。
   ⇒ **不要**据此调舱壁并发：调大只会让更多请求挤在行锁上；要动的是 #3244。
   本用例对 `/complete(200)` 先卡 **2× 舱壁等待预算**的临时上界，把决策留给 #3244。
4. 观测附带项：窗口内还出现 1 次 `scan_task` 入队（dedup/scan 路径）——与判据无关，
   记录队列把非 `post_completion` 的入队单独归类，避免污染扇出计数。

## Revisit

1. **本用例的刻意偏差**（别把绿读成生产已验证）：in-process ASGI（无 uvicorn/worker 并行、无真实网络）、
   37 台 host 同源单 IP（Agent 桶 2000/min 未触顶；UI 桶 300/min 会让探针偶发 429，p99 只取 200 样本）、
   post_completion 只记入队不执行（真跑需 Redis + worker）、`HostHeartbeatTimeout` 未断言（以探针延迟代理）、
   4 个 FAILED 为预置终态（计数已按事实预置）。
2. **部署窗复跑**：48 台真机 + uvicorn + worker 的同一场景要再跑一遍，取生产分布回填 ADR-0047 §4；
   届时把 `CALIBRATION_3243` 摘要归档到 #3243 评论。
3. **阈值裁决**：20/20（池）、16/500ms（舱壁）、reserve=8、并发 2、抖动 5s 全部维持首轮值——
   本场景下没有一条需要放宽；若生产复跑出现 `timeout` 类失败，再按 ADR-0047 §4「只改数字」处理。
4. **p99 归因复核**：若 #3244 落地后本用例的 `/complete(200)` p99 掉到 1s 内，即为「行锁串行段是主因」
   的收敛证据；若没掉，再查 in-process ASGI 的排队开销（部署窗真机可分辨）。
