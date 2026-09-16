# RunConsole 回放游标语义与 start 失败兜底（#2070 / #2039 / #2045）

Status: implemented
Class: bug-fix

## Decision

三单同面（同一文件 `backend/services/run_console.py` 的 replay/生命周期语义），
一个 PR 收口，避免两两之间的「顺带覆盖」依赖悬空。

### 1. `read_log` 的 `seq` 改为**交付游标**（#2070 + #2039 同一处判据）

旧返回体把三件事混成一个字段：`seq = max(文件行数, owner 快照 seq)`。前端把它当作
「已交付到 seq」的游标用（`LiveConsole.tsx`），于是：

- 命中 `_replay_max_lines`（默认 2000，`#1124` 引入）时，游标直接跳到文件末尾
  → 第 2001..N 行永不请求（#2070）；
- 跨实例 `STP_RUN_CONSOLE_LOG_ROOT` 未共享/落后时，游标被推到 owner 报告值
  → 未交付行与其后**实时行**一并被丢弃，且 `replay_unavailable` 无任何消费方（#2039）。

新契约（响应形状本身是 `ApiResponse[dict]` 透传，无 Pydantic 模型改动）：

| 字段 | 语义 |
|---|---|
| `seq` | 本次响应**最后一行**的行号 = `from_seq - 1 + len(lines)`；一行未交付即等于起点，绝不前进 |
| `total_seq` | 已知末端 = `max(文件行数, owner_seq)` |
| `truncated` | `文件行数 > seq`：上限截断，调用方应以 `from_seq = seq + 1` 续拉 |
| `replay_unavailable` | 语义不变（文件缺失/落后），但现在**有人消费** |

后端消费侧同时收口：`_on_jira_run_complete` 原先复用 `read_log(from_seq=0)` 解析
`issue_keys`，等于把「HTTP 显示层上限」搬到落库路径 —— 超上限日志尾部的 issue key
静默丢失。新增 `RunConsole.iter_log_lines()`（逐行流式、无响应上限）供服务端内部
消费者使用，`parse_issue_keys` 本就吃 `Iterable[str]`，无额外内存代价。

前端 `LiveConsole`：游标只由 `applyLines` 按**已写行**推进；`truncated` 时按页续拉
（一页 = 一次响应，上限 50 页防自旋）；`replay_unavailable` 时渲染
「日志不完整」横幅并对补拉请求退避 5s（否则修复后每个 live 批次都会打一次注定
读不到的请求 —— 这是把静默丢行改成可见后必须一并处理的副作用）。

### 2. `thread.start()` 失败与 Popen 失败同构收口（#2045）

`start()` 在 `Popen`/`thread.start()` **之前**就登记 owner、发布 RUNNING 快照，
而 `_finalize` 由 reader 线程的 finally 调用。线程起不来时异常直接冒泡，run 停在
默认状态 `RUNNING` 且没有执行体：`_renew_registrations_once` 只按
`status not in _TERMINAL_STATUSES` 过滤 → **永久续租 run_key**（进程重启前该
run_key 谁也起不来）、快照停在假 RUNNING、已 spawn 的子进程无人读 stdout（管道写满
即悬挂）。

抽出 `_abort_failed_start(run, *, error, proc=None)`，由 Popen 失败（#1931 既有路径）
与 `thread.start()` 失败共用；后者额外经 `_kill_orphan_proc` 收敛进程组
（复用 `_resolve_pgid` / `_await_group_exit`，与 `cancel()` 同构但不改状态）。

### 3. 删除 `configure()` 内重复的初始化块

`#1124` 那次提交把 8 行 replay/retention 赋值写了两遍（无行为影响）。属同一处
replay 有界逻辑的提交内重复，随手删除，不扩大为重构。

## Alternatives

- **只改前端**（`res.seq > seqRef + len(lines)` 时循环续拉）：能盖住 #2070，但盖不住
  #2039 —— 前端无法区分「还有行可读」与「永远读不到」，跨实例落后时仍会自旋重试。
  且 `seq` 字段语义本身是错的，留着一个说谎的字段会咬下一个消费者。
- **`replay_unavailable` 时仍把游标推到 `total_seq`**：等于保留丢行，只是加了横幅。
  否决：横幅会让人以为「内容在别处」，而实际上实时行也在被丢。
- **#2045 用「`thread.start()` 前先不登记」**：改变 P1/P2 的登记时序（快照必须先于
  可能的跨实例查询存在），影响面大于本次必要性；且登记后失败仍要收口，兜底无论如何
  都要写。否决。
- **拆三个 PR**：三处改动落在同一文件的同一语义面（回放/生命周期兜底），拆开只会
  让 review 在同一函数里反复跳。合并为单 PR，#2070 的落库影响与游标影响同源。

## Verification

红绿自证（新语义 / 旧实现各跑一遍，`git checkout --` 回退源文件后重跑）：

- 后端 `backend/tests/services/test_run_console.py` + `test_run_console_registry.py`
  对**旧实现 8 failed / 34 passed**，对**新实现 42 passed**；
  其中 4 条是本次新增/改写的用例（分页覆盖全量、`iter_log_lines` 无上限、
  本地与注册表两个面的僵尸收口），4 条是旧契约断言被改写（`seq` 语义变更必须显式
  反映在断言里，否则等于悄悄改掉契约）。
- 前端 `src/components/console/LiveConsole.test.tsx`：对**旧实现 2 failed / 4 passed**
  （新增两条正是 #2070 分页与 #2039 保实时行），对**新实现 6 passed**。
- `python -m pytest backend/tests/api/test_dedup_jira_runs.py`（`iter_log_lines` 的
  消费方）与 `scripts/run_gates.py check:quick`、`ruff`、`eslint`、`tsc`：见 PR 检查。

## Revisit

- 若将来要支持「从任意时刻起完整回放超大日志」（如导出/回放 2000 行之外的中段），
  现在的 `total_seq` + `truncated` 已足够表达，不需要新字段；但**分页拉取的
  `GAP_FILL_MAX_PAGES = 50`**（≈10 万行）是显示层的经验上界，若日志规模再上一个
  量级需改为虚拟滚动或后端分页窗口。
- `_replay_max_lines` 目前仍是「响应体上限」而非「滚动窗口」：客户端续拉是串行
  往返（每页一次 HTTP）。若 2000+ 行日志的补齐延迟成为抱怨点，出口是给
  `read_log` 加 `limit` 查询参数并让前端按可见区域惰性拉取，而不是抬高默认上限。
- `replay_unavailable` 的退避是**组件内**状态（5s）；多实例部署下真正的修复是
  `STP_RUN_CONSOLE_LOG_ROOT` 共享（ADR-0027 的部署前提），本单只负责让前提被破坏时
  不再静默丢行。#1737（多实例 owner 路由）落地后应复核本处是否仍需要退避。
