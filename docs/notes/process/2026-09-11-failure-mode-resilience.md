# Audit — Failure-Mode / Resilience Reasoning

Status: implemented
Class: process

`/home/debian13/stability-test-platform`, working tree. Every claim cites file:line; `UNVERIFIED` = no evidence found.

## EXISTS

**Health/readiness (spec + impl + test)**
- `backend/main.py:382` `/health/live` "liveness 探针——进程在即可用，不做依赖检查"; `:392-394` `/health` readiness: "DB 断开 → 503 DB_UNAVAILABLE；Redis 不可达 → 503 REDIS_UNREACHABLE；SAQ 未就绪 → 503 SAQ_NOT_READY".
- `backend/main.py:117-120` `_HEALTH_REDIS_PING_TIMEOUT = float(os.getenv("REDIS_PING_TIMEOUT", "3.0"))` — "黑洞分区（SYN 丢弃）下 ping 不得悬挂 … 否则探针任务无限累积" (#1177).
- Tested: `backend/tests/api/test_health_saq.py` (`test_redis_unreachable_returns_503`, `test_redis_ping_hang_times_out_returns_503`, `test_producer_mode_saq_not_ready_returns_503`), `backend/tests/api/test_health.py`.

**Retry policies (first-party)**
- SAQ default `retries: int = 3` — `saq_worker.py:265`; per-task `retries=2, retry_delay=10.0, retry_backoff=True` — `backend/tasks/saq_tasks.py:482-485,502-505,604-607`. Flash loop bounded: `flash_firmware/v1.3.10/flash_firmware.py:1403-1410` (`max_attempts` default 2, `max(1, min(4, ...))`).
- Agent HTTP exponential `backend/agent/api_client.py:116-175` `delay = retry_base_delay * (2 ** (attempt - 1))`; terminal payloads ride a persistent outbox — `agent/outbox_drainer.py:17` "retries un-acked terminal-state payloads".
- DB deadlock bounded retry: `backend/tests/test_truncate_deadlock_retry.py` ("#1273 — db_session 清库（全表 TRUNCATE）对 DeadlockDetected 的有界重试").

**Idempotency**
- Spec `docs/design/07-execution-protocol.md:25-26` — "`terminal_payload_digest`：同 payload 幂等；冲突 → 409 `TERMINAL_PAYLOAD_CONFLICT`。`trace_event_id`：step_trace 幂等键（含 retry/cycle）". Impl `backend/api/routes/agent_api.py:1021,1080,1086,762`; models `backend/models/job.py:33,119`; tests `backend/tests/api/test_agent_dual_write.py:457,858`.
- Claim dedup: `agent_api.py:379` "`FOR UPDATE OF JobInstance SKIP LOCKED` prevents thundering herd"; `:436,464`; `test_agent_dual_write.py:1465`.
- Dispatch key `backend/services/precheck/idempotency.py:14 compute_idempotency_key(plan_run_id) -> f"plan_run_dispatch:{plan_run_id}"` + `compute_dispatch_payload_hash`; channel dedup `notification_service.py:233-240 _delivery_identity()` "Stable identity for idempotent channel delivery across SAQ retries", `:283-285` "Already-successful channels … are skipped on retry."
- 41 `UniqueConstraint`/`unique=True` in `backend/models/` incl. `uq_job_instance_plan_run_device`, `uq_job_active_per_device` (partial), `uq_script_name_version`, `uq_test_suite_name`, `uq_test_project_key`.

**Partial-failure state machines (spec)**
- `07-execution-protocol.md:10-26` Job PENDING/RUNNING/COMPLETED/FAILED/ABORTED/UNKNOWN — "**UNKNOWN**：围栏恢复态。晚到完成须先续约/恢复为 RUNNING（token 匹配）再 complete；或 grace 到期 → FAILED + 释租约"; `UNKNOWN→COMPLETED` forbidden. `:37-52` PlanRun "存在 UNKNOWN Job **不得**落终态". Only named compensation: `:103` "补偿：`scheduler/plan_chain_reconciler.py` + `reconcile_chain_trigger_sync`（孤儿 flag / 缺子 Run）".
- Scan/merge partial: `docs/design/2026-scan-upload-merge-contract.md:27-28` "`scan_task` … 记录 `since` 水位线，随后最多等待 300 秒。等待超时仍会 enqueue 后继，避免单台慢 host 把部分报告变成零报告"; impl `saq_tasks.py:795 incomplete_reason = "upload_mark_timeout"`.
- Agent outbox bounded: `agent/watcher/emitter.py:182` "Prune 策略：避免 log_signal_outbox 无限增长"; `outbox_drainer.py:93,165 prune_acked_terminals()`.

**Degraded-mode specs (partial)**
- `docs/design/06-realtime-and-background.md:64` "**派发**：MANUAL PlanRun 的 async precheck 常经 SAQ；Redis 不可用 → 503（显式失败）。"
- `docs/design/2026-09-08-session-identity-revocation.md:87` "DB 故障时**显式失败（fail-closed）**——metrics 返回 5xx、握手以 `ConnectionRefusedError` 拒绝，不降级到签名级放行".

**Delivery semantics (ADR, Proposed only)** — `docs/adr/ADR-0036-notification-delivery-semantics.md:1,5` "状态：**Proposed**", "v0.1（2026-09-08 初版草案）"; `:78,126-128` "D7 | idempotency | at-least-once + 每通道去重键；**不承诺端到端去重**"; `:70-76` D1-D9 (D2 `REJECTED_PERMANENT`/`REJECTED_TRANSIENT`/`UNKNOWN`; D3 "每次网络投递必须有显式 deadline").

**Agent-offline numbers** — `backend/core/adr0026_params.py:37-40` coord 30s / timeout 300s / running-exec 900s / patrol 300s; `:96` "Coordinator must survive ≥3 missed heartbeats before kill."; `backend/core/job_timeout_config.py:9` 900 / 300.

## ABSENT

1. **Chaos / fault injection: entirely absent.** `chaos`→0, `toxiproxy`→0, `fault.inject`→0, `network.partition`→0 repo-wide (excl. node_modules/venv/archive). No `docker stop|kill`, no `pg_terminate_backend`, no `redis-cli shutdown`/`FLUSHALL`. Every `kill -9` hit is an ADB device-process kill in scripts.
2. **No test injects a real dependency outage.** All 16 is monkeypatch-level: `test_health_saq.py:105 ping_raises=ConnectionError("refused")`; `test_main_lifespan.py:83 AsyncMock(side_effect=RuntimeError("Redis unreachable"))`; `test_dedup_scan_endpoints.py:420,431 raise RuntimeError("redis down")`; `test_saq_tasks.py:39 RuntimeError("db gone")`, `:171 ConnectionError("redis down")`.
3. **No circuit breaker.** `circuit.break`→0, `pybreaker`→0, `tenacity`→0, and none in `requirements*.txt`/`pyproject.toml`.
4. **No jitter in first-party retry/reconnect.** `jitter`→0 in `backend/`+`docs/`. SAQ's own jitter (`venv/.../saq/utils.py:62-63 if jitter: backoff = backoff * random()`) is third-party.
5. **No reconnect-storm spec.** `stampede`→0; `thundering`→1, only the claim-path comment `agent_api.py:379`.
6. **No read-only / queue-paused mode.** `只读|read.only|queue paused|暂停队列` in design/operations/adr → only `BEGIN READ ONLY` SQL in `docs/operations/adr-0026-admission-and-scale-gray-rollout.md:157,178` (diagnostic query).
7. **No client-supplied idempotency keys.** `Idempotency-Key|idempotency_key|X-Request-Id|request_id` in `backend/api`+`backend/services` → only `precheck/idempotency.py` (server-derived). 0 of 104 mutating endpoints accept the header.
8. **No delivery semantics outside notifications.** `at.least.once|exactly.once|至少一次` in `docs/` → only ADR-0036 + `docs/notes/architecture/2026-09-08-notification-delivery-semantics-adr0036.md`. Job dispatch, agent commands, scan upload, flash, pipeline dispatch: no stated semantics.
9. **No crash-mid-job recovery test.** No test kills a worker mid-job; closest is `test_saq_tasks.py:414 dead.exception.return_value = RuntimeError("worker crashed")` (mocked exception). No test file for the lease reconciler (`backend/services/reconciler.py` exposes only `reconcile_step_traces`).
10. **No clock/timezone policy doc** — no "UTC everywhere" statement in `docs/`. Product TZ is explicitly non-UTC: `backend/api/routes/settings.py:20 _PLATFORM_TIMEZONE = os.getenv("STP_TIMEZONE", "Asia/Shanghai")`.
11. **No TZ/DST test** — `tzset|TZ="|environ["TZ"]|DST|fold=` → 0 hits in `backend/`+`tests/`. `backend/agent/tests/test_aee_paths.py:34` pins a fixed clock; Asia/Shanghai has no DST, so DST is never crossed.
12. **No leak / FD-leak test.**
13. **No firmware-flash crash-recovery state machine** — `docs/design/2026-08-honor-flash-firmware-routing.md` + `docs/operations/honor-flash-runbook.md` have 0 matches for `幂等|idempot|resume|中断`; only the retry loop.

## SPECS vs NO SPECS

| Failure mode | Spec exists? | Tested? |
|---|---|---|
| DB down (readiness) | Yes — `main.py:392-394` (503 DB_UNAVAILABLE) | Yes — `test_health.py` |
| Redis down (readiness) | Yes — `main.py:117-120,392-394` (#1177) | Yes — `test_health_saq.py` (refused + hang) |
| Redis down (dispatch) | Yes — `06-realtime-and-background.md:64` (503 explicit) | Partial — `test_dedup_scan_endpoints.py:420,431` swallow branch |
| Redis down (non-ping ops) | **No** — no `socket_timeout` anywhere | **No** |
| SAQ worker not ready | Yes — `main.py:392-394` (503 SAQ_NOT_READY) | Yes — `test_health_saq.py:93` |
| DB down (auth/metrics) | Yes — `2026-09-08-session-identity-revocation.md:87` fail-closed | UNVERIFIED |
| Agent network loss | Partial — timeout numbers only (`adr0026_params.py:37-40`) | Partial — liveness only (`test_agent_routes.py:261`) |
| Agent reconnect storm | **No** | **No** |
| Job terminal replay | Yes — `07-execution-protocol.md:25` | Yes — `test_agent_dual_write.py:457,858` |
| Job claim concurrency | Yes — `07:31` + `agent_api.py:379` | Yes — `test_agent_dual_write.py:1465` |
| Step-trace replay | Yes — `07:26` | Yes — `agent_api.py:762` path |
| Dispatch retry | Partial — server-derived key (`precheck/idempotency.py:14`) | Yes — `test_plan_chain_trigger.py` |
| Notification delivery | Yes but **Proposed** — `ADR-0036:1,5`; impl `notification_service.py:233,283` | Partial — `test_saq_tasks.py:73` |
| Crash mid-job (SIGKILL) | **Known-undocumented dishonesty** — `ADR-0021:33,280` | **No** |
| Chain-trigger orphan | Yes — `07:103` (compensation) | Partial — `test_plan_chain_trigger.py:112,182` |
| Scan/merge partial | Yes — `2026-scan-upload-merge-contract.md:27-28` | Partial — `saq_tasks.py:795` |
| Firmware flash mid-write crash | **No** | **No** |
| PlanRun partial terminal | Yes — `07:37-52` | Yes — `test_plan_run_state_machine.py` |
| Timezone / DST | **No** | **No** |

## RISK

**P1 — Synchronized reconnect storm, no bound, no jitter.** `backend/agent/socketio_client.py:212-230`: `backoff = 1.0` … `self._stop_event.wait(backoff)` … `backoff = min(backoff * 2, self.MAX_RECONNECT_DELAY)`, with `MAX_RECONNECT_DELAY = _env_float("WS_RECONNECT_MAX_DELAY", 30.0)` (`:61`). The sequence is bit-identical for every agent → after a control-plane restart the whole fleet reconnects in lockstep at 1/2/4/8/16/30s then every 30s forever. `jitter`→0 repo-wide; the only herd defence is claim-path `SKIP LOCKED` (`agent_api.py:379`), which does not cover connect. Same determinism in flash retry (fixed default 10s + `time.sleep(retry_backoff)`, `flash_firmware/v1.3.10/flash_firmware.py:1410,1512`).

**P1 — Redis client has no per-operation timeout.** `backend/main.py:147-151 redis_client = await aioredis.from_url(redis_url, encoding="utf-8", decode_responses=True)` — no `socket_timeout`/`socket_connect_timeout`; repo-wide grep returns **0 hits**. Only the readiness ping (`main.py:117-120`) and SAQ's own ping (`saq_worker.py:112`) are bounded. Under a blackhole partition every SAQ enqueue and `agent_sid_registry` SET/GET hangs for the OS TCP timeout, with no circuit breaker to shed load.

**P1 — "Interrupted" recorded as "Failed" for SIGKILLed jobs, untested.** `docs/adr/ADR-0021-script-content-alignment-gate.md:33` "rsync 期间 Agent systemd 重启 → `pipeline_engine` 进程被 SIGKILL → Job 被 reconciler 标 FAILED + lease 释放，状态机不诚实（"被中断"被记成"失败"）"; `:146` states the invariant only for the non-forced hot-update path; `:280` lists it as a rejected alternative's downside. No mid-job-kill test (item 9), so it is neither locked nor bounded.

**P2 — Unbounded per-job lock registry.** `backend/realtime/log_writer.py:20-26`: `_locks: Dict[int, asyncio.Lock] = {}` / `if job_id not in _locks: _locks[job_id] = asyncio.Lock()`. Grep for `_locks` returns only 20,24,25,26 — **no eviction**; growth is monotonic in distinct job ids over process lifetime. Contrast correctly-bounded neighbours: `socketio_server.py:192`, `precheck/notify.py:67,94,95,111,122`, `agent_installer.py:37`, `ai_assistant.py:62`.

**P2 — Wall-clock deadlines in agent scripts are defeatable by an NTP step.** `deadline = time.time() + timeout_s` / `while time.time() < deadline` at `backend/agent/scripts/mtbf_setup/v1.3.0/mtbf_setup.py:236-237`, `sleep_setup/v1.0.2/sleep_setup.py:100-101`, `powercycle_setup/v1.0.2/powercycle_setup.py:110-111` (also v1.0.0/v1.0.1/v1.1.0/v1.2.0 copies). A backward step extends the loop without bound (device hang → host spins); a forward step truncates a sleep/powercycle window. Control plane does it right — `deadline = time.monotonic() + timeout` ×53 vs `time.time()` ×25 in `backend/`. Scripts are version-pinned (`AGENTS.md`: published `backend/agent/scripts/<name>/v<version>/` immutable) → fixable only via new versions.

**P2 — Naive/local timestamps leak into prod paths and are silently "repaired" as UTC.** `backend/agent/aee/timestamp.py:103 return datetime.now().strftime("%Y_%m%d_%H%M%S") + "_000"` — agent-host-local wall clock, no `ZoneInfo`; counter-example to `backend/agent/aee/paths.py:257` "MMDD in Asia/Shanghai — same clock as control-plane scan_now stamps" (`paths.py:14 ZoneInfo("Asia/Shanghai")`). Compensating `replace(tzinfo=timezone.utc)` at `api/routes/hosts.py:62`, `devices.py:62`, `heartbeat.py:58`, `plan_runs.py:904,2743,2746`, `plans.py:717,892,1057`, `agent_api.py:97`, `agent/watcher/emitter.py:102` silently shifts local values by the offset instead of rejecting them — and `plans.py:718,893,1058` use these for equality-based optimistic concurrency (`if plan.updated_at != expected.astimezone(timezone.utc)`), so skew is a spurious conflict. Good: 0 `datetime.utcnow()` in prod backend; 98 `datetime.now(timezone.utc)`; DB columns `DateTime(timezone=True)` (`models/device_lease.py:37-40`).

**P3 — `_pending_reconnected_serials` can grow unbounded.** `backend/agent/heartbeat_thread.py:84`, appended `:185-186`, cleared `:361` only when `if response and self._pending_reconnected_serials and self._on_devices_reconnected:` — with a `None` callback or persistent heartbeat failure, entries accumulate for process lifetime.

**P3 — 36 subprocess call sites without a timeout.** 557 `subprocess.run|Popen|check_output|check_call|call` sites in `backend/`; 36 lack `timeout` within 20 lines — e.g. `backend/scripts/preflight_control_plane.py:49,54`, `backend/agent/scripts/flash_firmware/v1.3.6/flash_firmware.py:565`, `v1.3.7:582,590`. A hung `adb`/`flash_tool` child blocks the step; the common path is covered by `killpg(SIGTERM→SIGKILL)` (`docs/notes/bug-fix/2026-09-08-process-tree-parent-exit-1003.md:30-31`), not by timeouts.

**Positive counter-example (not a risk).** First-party outbound coverage is strong: of 32 `requests`/`httpx`/`redis.from_url`/`asyncpg`/`paramiko`/`urlopen` call sites in non-test `backend/`, 31 specify an explicit `timeout` within 20 lines; the single gap is the Redis client above. SMTP was fixed with a stated reason at `backend/services/notification_service.py:31-35` "#1122：网络 deadline —— 无超时的 SMTP 会把通知线程挂死在 connect/read 上", `SMTP_TIMEOUT_SECONDS = max(..., float(os.getenv("STP_SMTP_TIMEOUT_SECONDS", "15")))`, used `:224`.

## EVIDENCE COMMANDS

```bash
# 1 chaos — every grep below is EMPTY (excl. node_modules/venv/archive)
for p in "circuit.break" pybreaker tenacity toxiproxy chaos "fault.inject" "network.partition" jitter stampede; do echo -n "$p -> "; grep -rniE "$p" --include=*.py --include=*.md --include=*.txt --include=*.toml --include=*.yml . 2>/dev/null | grep -vE "node_modules|/venv/|/archive/|\.pyc" | wc -l; done
grep -rniE "docker (stop|kill|restart)|pg_terminate_backend|redis-cli.*shutdown|FLUSHALL" --include=*.py --include=*.sh --include=*.yml . | grep -v node_modules   # empty
grep -rn "ping_raises\|connection refused\|redis down\|db gone\|smtp down" backend/tests/ --include=*.py | wc -l   # 16, all monkeypatch-level
# 2 outage / health
sed -n '382,470p' backend/main.py
grep -rnE "socket_timeout|socket_connect_timeout|retry_on_timeout|health_check_interval" --include=*.py backend/   # empty
# 3 idempotency
grep -rnE "@router\.(post|put|delete|patch)" backend/api/routes/*.py | wc -l      # 104
python3 /tmp/idem.py                                                              # 47/104 mention conflict/idempotency constructs
grep -rnE "Idempotency-Key|idempotency_key|X-Request-Id|request_id" --include=*.py backend/api backend/services   # only precheck/idempotency.py
grep -rnE "UniqueConstraint" --include=*.py backend/models/*.py | wc -l           # 41 (w/ unique=True, Index(unique))
grep -rniE "at.least.once|exactly.once|至少一次|恰好一次" --include=*.md docs/   # only ADR-0036 + its note
# 4 retry storms: sed -n '205,235p' backend/agent/socketio_client.py
grep -rn "jitter" venv/lib/python3.13/site-packages/saq/utils.py                  # 62-63, third-party only
# 5 partial failure / crash recovery
sed -n '10,52p;99,105p' docs/design/07-execution-protocol.md
sed -n '33p;146p;280p' docs/adr/ADR-0021-script-content-alignment-gate.md
grep -rniE "\bkill\b|SIGKILL|SIGTERM|crash" backend/tests/ --include=*.py | grep -v pycache   # no mid-job kill
# 6 clock — greps 1 and 4 below are EMPTY
grep -rnE "datetime\.utcnow\(\)|deadline = time\.time\(\)|while time\.time\(\) <" --include=*.py backend/ | grep -vE "/tests/|test_"
grep -rnE "replace\(tzinfo=timezone\.utc\)" --include=*.py backend/ | grep -vE "/tests/"
grep -rniE "tzset|TZ=\"|DST|fold=" --include=*.py backend/ tests/
# 7/8 leaks + timeouts
grep -rn "_locks" backend/realtime/log_writer.py          # 20,24,25,26 — no eviction
grep -rn "_pending_reconnected_serials" backend/agent/heartbeat_thread.py
python3 /tmp/tocover3.py    # 32 outbound sites / 31 with timeout / 1 gap (backend/main.py:147)
```

## Decision

对失效模式与弹性面进行单轴证据审计，结果以本 Note 记录，不在审计范围内发起代码修复。
三项 P1 风险（同步重连风暴无抖动、Redis 客户端无 per-op timeout、SIGKILL 被错记为 FAILED）
及四项 P2/P3 风险作为发现列入，后续独立 PR 承接修复，避免将审计发现与修复方案混入同一提交。

## Alternatives

- 直接在 ADR-0021/ADR-0026/ADR-0018 内联补充：会模糊现有 ADR 规范主张与实际缺口的边界。
- 开多个 Issue 分项登记：无法在 `docs/notes/process/` 里保留可追溯的全量证据命令，选择 Note 形式。
- 立即提交 jitter 补丁：超出单次审计 scope，应在专项 PR 中引用本 Note 作为背景证据。

## Verification

所有引用均含 `file:line`；§EVIDENCE COMMANDS 内的命令可独立复现。
"Positive counter-example"段（31/32 外部调用有显式 timeout）以工具脚本 `/tmp/tocover3.py` 统计，
数据可在同一环境重跑验证。

## Revisit

任一 P1 风险修复后（jitter 加入、Redis `socket_timeout` 补齐、mid-job-kill 测试落地），
重跑 §EVIDENCE COMMANDS 核验结论仍有效；ADR-0036 从 Proposed 转 Accepted 后，
§SPECS vs NO SPECS 中"Delivery semantics"行需更新判定。
