# Agent 终态上报削峰：首发单发 + 503/429 交 outbox + jitter + 每机并发上限（#3242）

Status: implemented
Class: bug-fix

关联：[#3242](https://github.com/DUElost/stability-test-platform/issues/3242)（本单）、
[#2959](https://github.com/DUElost/stability-test-platform/issues/2959)（父单）、
[ADR-0047 v1.1](../../adr/ADR-0047-db-pool-and-connection-capacity.md)（D2：过载对外 503 + `Retry-After`，
控制面侧已由 PR #3241/#3249 落地）、
[#3243](https://github.com/DUElost/stability-test-platform/issues/3243)（组合版压测与参数校准）、
[#1005](./2026-09-08-outbox-dual-failure-1005.md)（终态三分结果：远端确认 / 仅本地持久化 / 双故障）。

## Decision

R523 现场（2026-09-23，plan_run 523）的放大回路有两半：控制面舱壁没挡住连接需求（已由 ADR-0047 D1/D2 处理），
**Agent 侧把削掉的峰又打了回去**——490 个终态事实共产生 **1644 次** `/complete` 请求（3.35×），
因为 `_post_with_retry` 在每个 Job 线程内按 1s/2s 重试 3 次，且 48 台 host 的相位几乎一致。
本单按 owner 批准的五条落地：

1. **首发只打一次**（`complete_job` 传 `attempts=1`）——**当且仅当**终态事实已成功入本地
   `job_terminal_outbox`。首发此时只值一次「当场确认」；失败由 drainer 补送。若连入队都失败
   （没有补送落点），仍回退 `AGENT_POST_RETRIES`：那时多试两次比丢掉终态事实便宜。
2. **429/503 立即交 outbox**：不再在线程内连续重试；新增 INFO 一行
   `complete_job_overload_deferred_to_outbox`（带 `Retry-After`），把「中心让我们慢下来」与真故障分开。
3. **每机并发上限**：`AGENT_TERMINAL_UPLOAD_CONCURRENCY`（默认 2）信号量包住首发 POST——
   单机 13–23 个设备同时收尾时，瞬时连接需求被压到中心舱壁（16）以内。
4. **abort 族错峰**：`ABORTED` 经 `_build_complete_payload` 归一为 `CANCELED`，两者在首发前随机等
   0–`AGENT_TERMINAL_ABORT_JITTER_SECONDS`（默认 5）秒；事实已在本地 outbox，延迟只影响收敛时点。
5. **drainer 退避**：`503` 与其它 5xx 一并走瞬时分枝（此前只有 408/429 读 `Retry-After`），
   无头时用**指数 + full jitter**（`uniform(0, min(15·2^(attempts-1), 300))`）；周期本身加 ±20% 抖动，
   避免 48 台 host 长期同相位。`Retry-After` 是 HTTP 语义上的**下限**：在其上加 0–25% 抖动，
   且抖动后仍受 `_MAX_RETRY_AFTER_SECONDS` 硬上限约束（不突破 #1551 的既有判据）。

## Alternatives

- **终态首发也不发，全部交给 outbox**——不选：PlanRun 收敛要多等一个 drain 周期（15s+），
  而且失去「当场确认」这一层快速反馈；单发已把放大系数从 3.35× 降到 ~1×。
- **保留 3 次重试但把间隔抖动化**——不选：总请求量仍是 3×，舱壁照样被顶满；抖动只治相位不治量。
- **只在控制面拒绝时退避（Agent 不做并发上限）**——不选：舱壁拒绝本身也要消耗连接与事件循环时间，
  「先打到被拒绝」比「出门前排队」贵。
- **`Retry-After` 上做 full jitter（`uniform(0, retry_after)`）**——不选：会随机**早于**中心建议的
  间隔重试，违反 HTTP 语义；改为「下限 + 上浮抖动」。
- **把首次延迟/退避持久化（本地库加 `not_before` 列）**——不选：要动 host 侧 SQLite schema 与恢复
  载荷，而进程内退避最坏退化为既有 15s 节奏（不比现在差）；先用 `_defer_until`（重启即忘）。
- **`AGENT_TERMINAL_UPLOAD_CONCURRENCY` 默认 1**——不选：串行会把单机收敛时间放大到设备数 × RTT
  （R523 单机最多 23 台）；2 是「不过载也不排长队」的首轮值，待 #3243 校准。

## Verification

- `./scripts/run_pytest.sh backend/agent/tests/ -k "terminal or outbox or api_client or job_runner or recovery or fencing"`
  → **198 passed**；其中新增 `test_terminal_upload_shaving_3242.py` **10 例**：
  首发单发（`attempts == [1]`）、入队失败回退重试、无 local_db 旧语义、并发峰值 ≤2 且 ≥2、
  abort 抖动在界内（含 CANCELED 归一形态）、非 abort 不延迟、503 认 `Retry-After`、
  无头 5xx 首轮退避 ≤15s、退避随 attempts 增长且不破硬上限。
- 因口径变化同步更新 3 处既有用例（各自标注理由）：`test_terminal_durability.py` 的假 `_post_with_retry`
  接受 `attempts=`；`test_terminal_outbox_dead_letter_742.py` 两例在两轮之间清退避以模拟 drain 周期。
- `tools/dev/env_inventory.py --check` → **OK（267 个读取名）**；两个新键登记进 `backend/agent/.env.example`。
- `python scripts/run_gates.py check:quick` → 见 PR 描述。

## Revisit

1. **生效需机队分发**：本单只改 `backend/agent/**`；上线要走 hot-update 把新代码铺到 48 台 host
   （从**产线工作树/暂存树**跑 `batch_hot_update.py --direct`，见 `project_prod_control_plane_ops` 记忆与
   `repository-workflow.md`），且按 owner 指定与**控制面部署同一窗口**。
2. **参数校准**：并发 2、abort 抖动 5s、退避 base 15s/cap 300s 均为首轮值；
   #3243 在「舱壁 + Agent 削峰」组合版上给出分布后回填（连同控制面的 20/20、16/500ms、reserve=8）。
3. **收敛口径**：削峰后 PlanRun 收敛会晚几十秒——验收线是「490 条事实最终全部 ACK、120s 内终态」，
   不是「尽快」；若 #3243 发现超标，优先调 `AGENT_TERMINAL_UPLOAD_CONCURRENCY` 而不是取消 jitter。
4. **重启风暴**：`_defer_until` 进程内、重启即忘；若机队同时重启后出现同相位补送，再评估持久化退避。
5. **R523 仍非生产闭环**：需 #3242 分发 + #3243 校准 + 同窗部署 + Prometheus 规则同步，缺一不可。
