# 后端/Agent 低危批：sink 竞态收口、热更新归因分流、去重键定形、jsonb_set、回填有界（#2285）

Status: implemented
Class: bug-fix

## Decision

五项（PR 内），第 6 项（`monkey_teardown` 引号化）属**已发布脚本版本**，按脚本版本
SOP 另起版本另开 PR——本 PR 不动已发布目录。

**1. `_StepLogSink` 跨线程 close/write 竞态（#2061 修复的未闭合段）。**
`write()` 的「判 `_closed` → 惰性 `open()`」两步无锁，被主线程的
`_close_sinks_for_abandoned` 插在中间就会**重开**一个此后无人关闭的句柄（fd + 尾部日志
泄漏）。加 `_lock` 串行化 `write` / `close` / `_discard`：`close()` 等在做写入完成后
再关，**返回后 `_fh` 必为 `None`**；`write()` 在持锁后复查 `_closed`。同步改掉类
docstring 里「no lock is needed」的错误断言（它与本仓 #2061 的跨线程收口自相矛盾）。

**2. 热更新失败归因分流（原一律 `ssh_connect_failed`）。**
外层 `except (OSError, IOError)` 覆盖的 try 从建包起，含 `sftp.putfo`（上传）与
`client.exec_command`（远端执行）——paramiko 把它们也抛成 OSError，于是「目标机 /tmp
满 / 传输中断」被报成「查可达性与 SSH 端口」。改为 `stage`（connect/upload/exec）+
errno 双信号分流：

| 判据 | reason | 日志锚点 |
|---|---|---|
| errno ∈ {ENOSPC, EDQUOT} | `remote_disk_full` | `hot_update_remote_disk_full` |
| stage=upload 其它 | `remote_upload_failed` | `hot_update_upload_failed` |
| stage=exec 其它 | `remote_exec_failed` | `hot_update_exec_failed` |
| 其余（默认） | `ssh_connect_failed`（不变） | `hot_update_connection_failed`（不变） |

异常原文仍**只进日志、不外泄**（code-scanning #80 口径不变），message 各自给出对应
排查方向。

**3. UNIVIEW 去重键定形为恒三段。** 两项身份皆缺时旧实现退化为 `nfs:{dir}`——与
AEE / VENDOR_AEE 家族的键**同形**，同目录的 AEE 行与 UNIVIEW 行会被并成一条（#2010
「签名变化就再发射一条」在消费侧的反向残留）。恒带两个占位后两类键不可能相等。

**4. `write_run_context_section` 改库端 `jsonb_set`。** 原「读整段 → 改一个键 → 整段
写回」会抹掉并发写者（abort host 时钟 / `dispatch_state` / 各 SAQ 任务 / admission_pump）
刚落下的键，表现为该阶段回落为「无记录 / unknown」。改法与既有两个先例
（`plan_run_abort._patch_run_context` / `dedup_scan.record_scan_archive_state`）同款；
该 helper 的**四个调用点一并受益**（upload_mark / extract / merge_platforms / 通用段）。
单段路径不涉及 `jsonb_set`「只创建末段」的限制。

**5. 回填工具有界化。** ① `--limit` 下推到 SQL（默认 **1000**，`0` = 不限）：此前先
物化全部候选行再在 Python 里截断，大表上是一次无界加载；② 新增 `--apply` 的
`--max-rows`（默认 500），超出即拒跑（rc=2）——工具脚本化、无交互，故取行数上限而非
交互确认；③ `CANDIDATE_STATES` 改从 `device_log_event._AWAITING_UPLOAD_STATES`
**import 复用**，不再复制字面量（与模块自述「不复制任何判据」一致；等待态新增第三态
时不会静默漏掉）。

## Alternatives

- **1-a 只把 `open()` 移出竞态窗口（不加锁）**：否决——「close 后重开」的形态仍在，
  只是窗口变窄；且类注释的「无需锁」断言仍不成立。
- **2-a 只看阶段不看 errno**：否决。`ENOSPC` 是「腾空间」的强信号，比阶段更具体；
  **2-b 只看 errno**：更差——绝大多数传输失败没有可区分 errno。两者并用、errno 优先。
- **3-a 「无字段就并入同目录全部行」**：否决。会把同目录的**不同异常**并成一条
  （欠计数），与 #2080「宁多勿并」的取向相反。「无字段 / 有字段 = 两个身份」是信息
  缺失下的保守选择，已在 docstring 写明。
- **4-a 只改 `merge_platforms` 调用点**：否决。同一 helper 的四个调用点都在独立会话里
  写同一行，治标不治本；**4-b `SELECT ... FOR UPDATE`**：更重且不解决「ORM 快照 +
  整段回写」的根因（仍会把旧快照写回）。
- **5-a keyset 分页**：否决（当前规模不需要；SQL LIMIT 已消除无界加载）。
  **5-b `--apply` 交互确认**：否决——工具在运维链路里是非交互调用，行数上限更可控。

## Verification

- 五项各自**红绿双向**（旧实现载入后对新用例断言）：
  - 1：新用例在旧实现下 `_fh` 是**已重开的 TextIOWrapper**（断言失败），新实现下
    `close()` 后 `_fh is None` 且「在途行不丢」；
  - 2：上传失败 / ENOSPC 两条在旧实现下都报 `ssh_connect_failed`，新实现分报
    `remote_upload_failed` / `remote_disk_full`，且 message 均不含异常原文；
  - 3：四条新用例中三条在旧实现下红（退化键与 AEE 键相等）；
  - 4：并发用例（A 持旧快照 → B 写键 → A 写另一段）在旧实现下 B 的键**被抹掉**，
    新实现保留；
  - 5：`--limit` 触界标志与判据复用断言（旧实现返回 list、无上界概念）。
- 测试批次：`test_pipeline_log_sink_731.py` 10 passed；`test_host_updater.py` +
  `test_upload_state_backfill_1956.py` + `test_plan_run_context_concurrency_2285.py` +
  `test_plan_run_dedup_key_2285.py` + `test_watcher_summary_uniview_1956.py`
  **45 passed**；`test_dedup_extract.py` + `test_dedup_scan_endpoints.py` +
  `test_plan_run_aggregation_endpoints.py` **115 passed**。
- `ruff` 全绿；`python scripts/run_gates.py check:quick` → **OK (10 gates)**。
- **未跑**：backend 全量 pytest 与 Agent 全量（改动面已由上述五组覆盖；其余由 CI
  required checks 复核）。
- **未做**：第 6 项（`monkey_teardown` 计划参数引号化）——已发布脚本版本不可原地改，
  按 `script-versioning.md` 另起版本 + catalog 登记，单独 PR。

## Revisit

- **锁的代价**：`close()` 现在可能等一次在途写入（一行 IO 量级）；被放弃线程的收口
  路径因此从「立即返回」变为「等一行」，实测无影响。若将来 sink 承载大块写入，需重新
  评估（可改为「置标志 + 由持锁方关」的无等待形态）。
- **新 reason 的消费面**：`remote_disk_full` / `remote_upload_failed` /
  `remote_exec_failed` 是新增字符串；当前无前端/文档按 reason 分支（已 grep 确认仅
  `ssh_connect_failed` / `ssh_auth_failed` 出现在测试与文档），后续若要给 UI 文案需
  同步登记。
- **去重键的剩余不对称**：同目录「有字段 / 无字段」仍是两个身份（见 Alternatives 3-a）。
  若将来 Agent 侧统一保证写入身份字段，可考虑把它们收敛为一条并在 Note 里记「何时
  可以合并」的判据。
- **回填工具默认值变化**：`--limit` 默认从「不限」改为 1000，老用法在大表上会**只处理
  前 1000 行**——工具会打印触界提示并说明重跑幂等；这是有意的（无界加载是本次要修的
  问题）。若某次现场需要全量，显式 `--limit 0`。
- **jsonb_set 的 PG 专属**：测试库固定 testcontainers Postgres（无 SQLite 退路），
  与既有两个先例口径一致。
