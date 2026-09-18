# 步骤日志落盘失败不再静默（latch + 一次性告警）

Status: implemented
Class: bug-fix

## Decision

`_MQStepLogger` 的两处 `except Exception: pass`（构造期的 `os.makedirs` 与逐行写入）
让「本地日志写不进去」与「这一步本来就没有输出」**不可区分**——而落盘失败恰恰最可能
发生在**事故时**（坏盘/满盘/权限/路径被占），那时本地副本正是最要紧的证据。
这是 #739b 静默吞咽分诊里点名的、253 处中「真正值得单独处理」的两处。

修法：**一次判定 + latch**。

- 失败原因是**持久性**的，逐行重试既刷不出日志、又按行放大 syscall；
- 首次失败记一条 warning（带路径与底层错误），置 `_file_failed`，此后本步不再尝试
  本地写入 → 不刷屏；
- 异常类型从 `Exception` 收窄到 **`OSError`**（文件 I/O 的现实失败面）；收窄后若真有
  非 OSError 的意外，会照常冒泡，而不是被吞掉；
- MQ 那一路不受影响（本类的作用是「MQ + 本地副本」，本地没了仍要走 MQ）。

与既有先例一致：`_StepLogSink`（#2285 族）在写失败后也是「置位 + 不再重试」，只是它
把日志压到 debug；本类选择 warning 一次——因为这里的失败**不阻断**主流程，操作员若
不看日志就不会知道本地副本缺了。

## Alternatives

- **逐行 warning**：否决。步骤日志可以上千行，坏盘场景会变成 warning 风暴，反而淹没
  现场。
- **保持静默（现状）**：否决。这正是本单要消除的形态（#739b 分诊里已列为待处理）。
- **失败即抛**：否决。日志落盘是 best-effort 旁路，抛出去会打断刷机/测试主流程——
  代价与收益不成比例。
- **每次打开都重试但限速（如每分钟一次）**：更复杂且没有额外收益：同一进程内坏盘不会
  在一分钟内自愈；跨步骤的 logger 实例本来就会各自重试一次。

## Verification

- 新增 `backend/agent/tests/test_pipeline_engine_steplog_file.py` 三条：
  1. 构造期 makedirs 失败（父路径被**文件**占位）→ 告警一次 + 置位；
  2. 首次写入失败（路径被**目录**占位，确定性、不依赖权限）→ 告警一次 + 置位，且
     **latch 后 20 次写入只发生 1 次 open**（用计数包装的 `builtins.open` 证明）；
  3. 反向：可写路径不被误伤（每行落盘、无告警）。
- **反例构造（先证伪再采信）**：
  - A 退回静默版（两处 `except OSError: pass`）→ 前两条 **FAILED**；
  - B 保留告警但去掉 latch（逐行重试）→ 第 2 条 **FAILED**（告警数 >1 / open 计数 >1）。
  恢复后 3 passed。
- 实测：`env -i PATH="$PATH" PYTHONPATH=. python -m pytest backend/agent/tests/ -q` →
  **2148 passed**；`python scripts/run_gates.py check:quick` → **[OK] (12 gates)**。

## Revisit

- **其余静默吞咽**：#739b 的分诊结论未变——253 处里绝大多数是有意降级；若要继续治理，
  按子系统分批，别按计数从大到小。本单只收了分诊里点名的那两处。
- **可观测性**：本单只加日志（agent 侧无指标面）。若坏盘反复出现，出口是给 agent 增一个
  「本地日志落盘失败」计数（textfile 或 MQ），而不是把 warning 升成 error。
- **`_StepLogSink` 与本类口径不同**（debug vs warning）：两者面向的读者不同（前者是
  单步日志文件，后者是 MQ+本地副本）。若将来统一，先回答「谁会在什么时候读这条日志」。
