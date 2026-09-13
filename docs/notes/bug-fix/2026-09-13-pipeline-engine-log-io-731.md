# Agent Note: step 运行日志改为每流持久句柄（#731）

Status: implemented
Class: bug-fix
Issue: #731

## Decision

把 step 运行日志的写入从「**每行** open/write/close」改为「**每流一个持久句柄**」：

- 新增 `_StepLogSink`（`pipeline_engine.py`）：懒开句柄、`write()` 时归一化换行（沿用
  #805-3 契约）、`close()` 做 flush+close 且幂等、`close()` 后写入变 no-op；
- `_handle_line` / `_reader` 的参数由 `log_path` 换为 `log_sink`；
- 句柄在**该流 reader 线程的 `finally`** 里关闭——正常完成、abort、timeout 三条路径
  共用同一个收尾点，CPython 在 close 时 flush，缓冲内容不会丢（issue 期望第 2/3 条）；
- `_pump_process` 按 `log_paths` 建两个 sink（`log_sinks`）。

**为什么是「每流一个句柄」而不是一个共享句柄**：stdout/stderr 由两个 reader 线程分别
写入两个不同文件（`{phase}_{step}.out.log` / `.err.log`），一个 sink 只被其所属线程使用，
**无需加锁**；合并成一个句柄反而要引入跨线程串行化，得不偿失。

### 实测到的关键约束：不能改动 `_append_log_line`

`test_agent_misc_805.py::TestAppendLogLine` 的四个用例在调用后**立即读文件**（换行归一化 /
追加语义 / 目录不存在时 OSError 被吞），因此该函数必须保持「调用返回即落盘」的一次性语义。
故保留 `_append_log_line` 为**薄封装**（内部建一个 sink、写一行、关闭），只把 drain 路径
换到持久 sink 上。这样改动面最小，且不触碰 #805-3 的既有契约测试。

## Alternatives

- **给 `_MQStepLogger._write` 也换成持久句柄**（同型第二处，issue 未提）：
  本次**不做**。它在 `_make_mq_logger`（`pipeline_engine.py:1441` 调用）里**按 step 构造，
  但没有任何 owner 侧 `close()` 钩子**。持有一个没人关的句柄 = 每个 step 泄漏一个 fd
  （长跑 agent 会累积到 fd 上限），**比每行一次 openat/close 更糟**。正确做法是先给它加
  `close()` 并在 step 收尾处调用——留给后续（见 Revisit）。
- **把 `self._log_file` 的写也并进同一个 sink**：不选。`_MQStepLogger` 写的是带时间戳的
  格式化行（`ts [LEVEL] message`），与脚本原始 stdout/stderr 是两种内容，混流会破坏
  「运行日志唯一副本」的行格式契约。
- **去掉 `_append_log_line` 只留 sink**：不选。会让 #805-3 的既有契约测试失去被守护对象
  （那些用例正是「一次性追加」语义的回归网）。
- **加锁共享单句柄**：不选。两个 reader 分写两个文件，本就不需要同步；加锁只会把两条
  独立管道串行化。

## Verification

- **新增 `backend/agent/tests/test_pipeline_log_sink_731.py`（9 例）**，把 issue 的性能声明
  固化成可回归断言（而非只留在微基准里）：
  - 写 50 行 → 目标路径上 `builtins.open` **只被调用 1 次**（回归成每行一次即失败）；
  - `close()` flush 掉无换行尾行；close 后写入为 no-op；
  - 换行归一化与 #805-3 一致；目录不存在时 OSError 被吞且**失败后不重试** open；
  - `_pump_process` 端到端：正常完成 20 行 stdout + 1 行 stderr 全落盘且**每流 1 次 open**；
    **超时（wall_clock=1.5s）被杀时，已写内容仍落盘**（覆盖 issue 期望第 3 条的 abort 路径）；
    `log_paths=None` 时不影响 pump。
- **既有测试未改一行**：`test_agent_misc_805.py`（#805-3 契约）与
  `test_step_stall_detection.py` / `test_pipeline_engine_process_group.py`（pump 行为）
  与本文件合计 **64 passed**。
- 全量 `pytest backend/agent/tests/ -q` → 见 PR 描述。
- `ruff check backend/agent/pipeline_engine.py` → All checks passed；`check:quick` → 见 PR 描述。

## Revisit

- **同型第二处（`_MQStepLogger._write`，`pipeline_engine.py`）**：同为逐行 `open/write/close`，
  且是**活代码**（`:1441` 每 step 构造）。转换的前置条件是**先有 owner 侧关闭钩子**
  （加 `close()` + 在 step 收尾时调用），否则 fd 泄漏。建议单独立项，不要与本改动混在一起。
- 微基准（issue 记录的 71.8×）是在「持久句柄 + 默认缓冲」下测的；本实现的**缓冲语义与
  Python 默认一致**，但**每步收尾必然 flush**，故磁盘可见性不弱于旧实现（旧实现每行即落盘）。
  若未来要求「崩溃时最多丢一行」，可把 sink 换成 `buffering=1` 的行缓冲（代价是每行仍有一次
  write syscall，但省掉 openat/close 两处）。
