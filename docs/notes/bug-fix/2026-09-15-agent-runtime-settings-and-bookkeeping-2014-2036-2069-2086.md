# Agent 运行期加固批：#2014 / #2036 / #2069 / #2086

Status: implemented
Class: bug-fix

## Decision

四单同批，共同点是「配置/簿记边界在长驻 Agent 进程里失守」；改动面互不重叠。

### 1. #2014 —— 惰性 Settings 读取不得杀死 spill 守护线程

`local_disk_monitor._next_wait_seconds()` 在 `_run()` 的 try **之外**被调用，却要读
惰性 property（每次读取都校验整个 `DiskArchiveSettings`，含严格组）。热重载引入非法
严格值时 pydantic `ValidationError` 从这里冒泡 → 守护线程永久死亡，且不留任何
`hdd_spill_monitor_*` 日志。

现在整个读取（含 `_catchup_needed` 短路判定）包在 try 内，异常回落**常规轮询间隔**
（`self._interval`，`configure()` 保证 ≥30s）+ `hdd_spill_monitor_catchup_interval_unavailable`
日志。回落值选常规间隔而非 0/默认常量：既不杀死线程，也不退化成忙循环；配置修好后
下一次 reload 自动恢复。

**残留在案（有意）**：配置非法期间 `check_once()` 内的同类读取仍会抛（被 `_run()` 既有
的 try 捕获），即 spill 处于「线程存活但不动作」。不拿未通过校验的配置去动主机磁盘，
比「静默用默认值继续腾退」更可取。

涉及文件：`backend/agent/local_disk_monitor.py`。

### 2. #2036 —— 两处无界簿记结构

- `EventUploader._retry_timers`：退避 Timer 改为**触发即自出表**（新增 `_schedule_retry()`）。
  表长上界从「累计失败次数」降为「未决退避数」；`stop()` 的 cancel 语义不变（未决
  Timer 仍在表内）。
- `OutboxDrainThread._defer_until`：新增 `_prune_deferrals()`，每轮 `_drain_once()` 用
  **全量** pending job_id 集合做差集裁剪（配套新增 `LocalDB.list_pending_terminal_job_ids()`）。
  #1551 的退避语义**完整保留**：`_MAX_RETRY_AFTER_SECONDS` 上限、408/429 不判永久失败、
  「进程内、重启即忘」全部不动（#2036 评论的显式约束）。

涉及文件：`backend/agent/event_uploader.py`、`backend/agent/outbox_drainer.py`、
`backend/agent/registry/local_db.py`。

### 3. #2069 —— `sync-env` 值侧与键侧同档校验

override 的**值**原先只判「是不是字符串」，值内换行会在 Agent `.env` 里顶出额外行
（如注入 `LD_PRELOAD=…`）。现在值侧与键侧同档：含 CR/LF/NUL 即拒（exit 2 +
`STP_AGENT_PRIV_ERROR` 哨兵）。并在 `_write_env_preserving_owner()` 增加**第二层**逐行
兜底——secret 分支与未来新增的任何写路径同样被拦。错误信息只回显键名/位置标签，
不回显值内容（override 可能承载配置类敏感值）。

涉及文件：`backend/agent/stp_agent_priv.py`。

### 4. #2086 —— 节奏旋钮正数下界 + reload 生效面

- **正数下界在 Settings 层单点收口**（`_clamp_positive_seconds`）：四个心跳/协调域节奏
  旋钮（`COORDINATOR_HEARTBEAT_INTERVAL`、`STP_HEARTBEAT_INTERVAL_MIN/_MAX`、
  `STP_ADB_REPAIR_COOLDOWN_SECONDS`）+ `AGENT_LOCK_RENEWAL_INTERVAL`，非正值 → `1.0s` +
  WARNING。它们直接喂 `Event.wait(...)`，0/负值 = 忙循环。
  **非数值仍然抛 `ValidationError`**——ADR-0042 的等价失败面不放宽
  （`test_strict_group_raises_like_before` 原样保留）。
- **实例级 re-apply**：`HeartbeatThread.reload_from_settings()` /
  `HostRunCoordinator.reload_from_settings()`，由 `main.py` 的 `reload_config` 分支在
  `reset_agent_settings_caches()` 之后调用。此前这两个常驻对象只在构造时取 Settings，
  热重载打印 `control_reload_config_done` 却静默无效。
- **未做（有意）**：`AGENT_LEASE_TTL`、`COORDINATOR_MAX_PLAN_RUN_HOSTS` 未加正数下界
  （前者不是节奏旋钮；后者是防御性上限，0 只影响兜底裁剪的触发时机）。但
  `COORDINATOR_MAX_PLAN_RUN_HOSTS` 仍纳入 re-apply——避免同域出现「部分旋钮 reload
  生效、部分不生效」的隐性分层。

涉及文件：`backend/agent/settings.py`、`heartbeat_thread.py`、`coordinator.py`、`main.py`。

**文档同步**：`backend/agent/AGENTS.md` 的 reload 动作清单从「三样」更正为实际的五样
（原文已落后于代码：`UnisocScanRunner` / `EventUploader` 未列）+ 两条实例级 re-apply；
`docs/development/environment-variables.md` 由 `tools/dev/env_inventory.py --write`
重生成（settings.py 行号位移，生成块逐字节门禁会红）。

## Alternatives

- **#2014 方案 1（把 `wait()` 也纳入 `_run()` 的 try）**：异常会让循环以 0 等待空转刷
  日志 = 忙循环，否决；**宽容 validator**（Settings 层吞掉非法严格值）会让整机以默认
  值继续 spill，把「该线程不因配置抖动而死」扩大成「全局忽略非法配置」，否决。
- **#2086 用 `Field(gt=0)`**：把 0 变成「获取即失败」——对长驻 Agent 是更差的失败面
  （线程/启动带病），且与 #1710「非法配置不得拖垮 Agent」的既有裁决方向相反，否决。
  **调用点逐个 `max(..., 1)`** 亦可（#2086 给的另一选项），但同域 5 个读点分散、
  未来新增读点会漏，故选 Settings 单点。
- **#2036 改为「在 ack/死信/清理路径同步 pop」**：需穷举所有消费路径（含 recovery/sync
  等进程内其它入口），漏一处即复发；差集裁剪与消费路径解耦（与 #2036 评论口径一致）。
- **#2036 用 `get_pending_terminals()` 那一页做差集**：该查询 `LIMIT 20`，窗口外的延期
  条目会被误判为「已消失」而删除、退避失效——专门写了守卫用例钉住这一点，否决。
- **#2069 只挡 `\n`**：`\r` 在部分解析器下同样换行、NUL 可截断，一并挡。

## Verification

实际运行的命令（worktree `.wt/stp-2014-2036-2069-2086`，解释器
`/home/debian13/stability-test-platform/.venv/bin/python`）：

```bash
python -m pytest backend/agent/tests/test_local_disk_monitor.py \
  backend/agent/tests/test_agent_settings_heartbeat.py \
  backend/agent/tests/test_agent_settings_lease.py \
  backend/agent/tests/test_event_uploader.py \
  backend/agent/tests/test_terminal_outbox_dead_letter.py -q          # 全绿

python -m pytest tests/test_agent_priv_boundary.py -q                 # 全绿
DATABASE_URL='postgresql+psycopg://u:p@127.0.0.1:5432/stp_test' \
  python -m pytest tests/test_agent_priv_parser_contract.py -q        # 全绿

# 门禁同口径（AGENT_TEST_ENV）下的 Agent 全量套件
TESTING=1 JWT_SECRET_KEY=ci-test-secret-key \
DATABASE_URL='postgresql+psycopg://postgres:postgres@localhost:5432/stability_test' \
TEST_DATABASE_URL='postgresql+psycopg://postgres:postgres@localhost:5432/stability_test' \
systemd-run --user --scope -p MemoryMax=6G -p MemorySwapMax=0 -- \
  python -m pytest backend/agent/tests/ -q                            # 2077 passed

python scripts/run_gates.py check:quick    # [OK] 10 gates
python scripts/run_gates.py check:pr       # [OK] 18 gates
```

（注：不带上述 env 裸跑 `backend/agent/tests/` 会有 23 项环境性失败——该集合与干净
main `566f4f3d` 逐条 diff 一致，与本批改动无关；本 worktree 另缺未跟踪的
`backend/.env`，故根级 `test_agent_priv_parser_contract.py` 需占位 `DATABASE_URL`。）
```

新增守卫（每条均用「还原对应改动 → 重跑」构造反例，确认无修复时必失败）：

- `test_run_loop_survives_settings_validation_error`（旧实现：线程死亡 +
  `PytestUnhandledThreadExceptionWarning`）、`test_next_wait_seconds_falls_back_on_settings_validation_error`、
  `test_settings_validation_error_logs_warning`；正向对照
  `test_next_wait_seconds_still_uses_catchup_interval`。
- `test_retry_timer_deregisters_itself_once_fired`；
  `test_deferral_pruned_when_row_consumed_elsewhere`；
  `test_deferral_not_pruned_when_page_is_truncated`（守「不得用分页结果做差集」）。
- `test_sync_env_rejects_newline_in_override_value`、`test_sync_env_rejects_carriage_return_and_nul`、
  `test_sync_env_rejects_newline_in_secret_payload`、
  `test_write_env_preserving_owner_is_second_line_of_defense`；正向对照
  `test_sync_env_writes_normal_value`。
- `test_non_positive_pacing_clamped`（4 旋钮 × {0, -5}）、
  `test_min_max_both_clamped_stays_ordered`、`test_non_positive_renewal_interval_clamped`；
  正向对照 `test_non_numeric_renewal_interval_still_strict`、
  `test_heartbeat_pacing_reload_takes_effect`、`test_coordinator_pacing_reload_takes_effect`；
  静态契约 `test_main_reload_config_reapplies_pacing`（防 reload 分支回退）。

## Revisit

- 若 `AGENT_LEASE_TTL` 或其它**非节奏**旋钮也出现「0 = 静默失效」的事故 → 按同法收口
  （当前只覆盖 5 个节奏旋钮）。
- 若运行时调小 `COORDINATOR_MAX_PLAN_RUN_HOSTS` 引发意外的投影裁剪 → 重审 re-apply 面
  （当前把它纳入 re-apply 是为了避免同域分层，非本单诉求）。
- 若 #2014 的「线程存活但 spill 停摆」在真实运维中造成磁盘压力 → 再议「非法配置下用
  默认值继续腾退」是否可接受。
- #2086 第 2 条若后续需要「不重启即改心跳节奏」之外的更强热更新（如线程重建），
  应改为显式生命周期设计，而不是继续往 reload 分支加边。
