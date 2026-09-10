# RunConsole replay 有界 + 终态运行记录淘汰（#1124）

Status: implemented
Class: bug-fix

## Decision

#1124（R11-F17，设计风险）：`read_log` 即使增量请求也 `f.readlines()` **全量**读
入内存再切片——大日志 + 长期运行下内存与响应体无界放大；`self._runs` 只进不出，
进程生命周期内随运行次数无界增长。

修复（保持 API 形态不变，纯内部语义收紧）：

- **replay 流式化**：逐行迭代，跳过 `from_seq` 之前的行，命中后收满
  `_replay_max_lines`（env `STP_RUN_CONSOLE_REPLAY_MAX_LINES`，默认 2000）即不再
  收集但仍**继续计数**——`seq` 必须精确等于全文件行数（断线补齐协议依赖它）；
  收集的单行再做 `_REPLAY_MAX_LINE_CHARS`（100k）截断，防止单条超大行撑爆响应。
  内存占用从此与日志大小无关（时间仍 O(n)，无行号索引下的必要代价）。
- **终态记录淘汰**：`status()` 惰性清扫 `_runs`——终态且 `ended_at` 距今超过
  `STP_RUN_CONSOLE_TERMINAL_RETENTION_SECONDS`（默认 3600s）的条目移除。淘汰只删
  内存条目：replay 走既有的 `log_root/{run_id}.log` 文件回退路径（status 退化为
  UNKNOWN，调用方按需从 jira_run 表补全——docstring 已有的既有约定）；`ended_at`
  解析失败不淘汰（宁多留不误删）。reader 线程持有 run 对象自身引用，淘汰字典
  条目不影响进行中的 finalize。

## Alternatives

- 后台定时清扫线程：多一个常驻线程只为低频整理，惰性清扫挂在既有的高频调用
  （status 轮询）上零成本达到同一上界；
- replay 建行号索引（offset 文件）：把 O(n) 时间降为 O(1)，但引入索引一致性
  （进程中断时的 partial index）问题——先解决内存/响应放大这个主诉，索引留给
  实测成为瓶颈时再立单；
- 行截断放 reader 线程（落盘就截）：会改 replay 文件内容，超出「显示层有界」的
  范畴，放弃。

## Verification

- `pytest backend/tests/services/test_run_console.py`：15 passed，新增 3 例——
  2100 行日志按上限只返回 10 行而 `seq=2100` 精确、增量 `from_seq` 边界正确 /
  150k 单行被截断到 ≤100k 且后续行不丢 / 终态 run 超保留期被淘汰且 replay 走
  文件回退（status=UNKNOWN、行内容完整）；
- 消费方全绿：dedup jira/scan 端点 + main lifespan + agent_installer 合计
  71 passed；ruff 干净。

## Revisit

- `seq` 仍需读全文件（O(n) 时间）：若大日志 replay 实测成为响应瓶颈，再立单做
  行号索引（partial-write 一致性需一并设计）；
- 淘汰后 `status` 为 UNKNOWN 是既有回退语义；若前端需要「历史 run 的终态」，
  应从持久化层补全而非延长保留期；
- 落盘日志文件本身的清理不在本单（replay 依赖它）；磁盘侧已由 #1123/#1078 一族
  的 staging 回收覆盖，log 文件留存策略如需收紧另立单。
