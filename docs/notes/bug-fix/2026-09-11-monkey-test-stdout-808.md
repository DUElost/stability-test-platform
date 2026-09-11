# monkey_test stdout 纯 JSON 契约（#808）——v1.2.1

Status: implemented
Class: bug-fix

## Decision

本质问题（#808，结构已确认，严重度 A）：`monkey_test/v1.2.0:186` 的资源推送
日志 `print(...)` 打向 **stdout**；引擎（`pipeline_engine.py:1504-1510`）对
stdout **整份** `json.loads`——一旦部署 bundle 含 `resource/` 目录即触发 →
解析失败 → `payload={}` → 引擎按 rc==0 仍报成功，但 metrics / route 审计全空
（#507 三要素静默丢失）。

修复 = **v1.2.1**（版本目录不可变）：

1. 资源推送日志改 `file=sys.stderr`（普通行，非 PROGRESS 前缀格式，不与
   stderr 打戳契约冲突）；
2. 成功 metrics 增 `resource_push`：`disabled`（未开启）/ `missing`（目录
   缺失——原静默 no-op 现在留痕）/ `pushed:N`。

## Alternatives

- **直接删除该 print**——放弃：丢失推送数量可观测性；stderr 是引擎允许通道；
- **改走 `_progress_stamp` 打戳**——放弃：推送计数不是进度语义，会污染
  PROGRESS 契约；普通 stderr 行足够（进错误日志）；
- **原地修改 v1.2.0**——禁止（AGENTS.md 版本目录不可变硬不变量）。

## Verification

实际运行（worktree `/tmp/stp-808`，基于 `origin/main`）：

- `pytest backend/agent/tests/test_monkey_test_stdout_contract.py -v` →
  **2 passed**（含资源目录：stdout 为合法 JSON + `resource_push=pushed:1` +
  日志在 stderr；缺目录：`resource_push=missing`）；
- **反向验证**：测试指向 v1.2.0 → **2 failed**（stdout 被日志行污染、
  metrics 缺字段）；恢复后 2 passed；
- `ruff check backend/` → All checks passed；
- `check:quick` → 7 gates 全绿。

未完成（pending）：

- 真机侧：bundle 含 `resource/` 的完整 monkey 流程（验证 stderr 日志被
  agent 收集且 metrics 完整）——需真机 / 隔离环境。

## Revisit

- **范围外发现（同类风险，建议另开单）**：脚本族扫描显示
  `monkey_launch/v4.0.0` 在正常执行路径（admission / monitor / restart）向
  **stdout** 打多行 `[STP_MONITOR]` 日志（:146/:223/:243 等）——同样会在
  引擎整份 `json.loads` 下丢 metrics（rc=0 报成功）。本次未处理（保持
  #808 范围）；建议按「stdout 只允许最终 JSON」纪律另立一单（其余脚本已扫：
  oobe_skip/noop/flash_preflight/flash_firmware 的 print 均为 JSON 输出，无风险）；
- 若引擎后续支持 stdout 行过滤，本约束可放宽——在此之前「stdout 只允许最终
  JSON」应作为脚本族通用纪律。
