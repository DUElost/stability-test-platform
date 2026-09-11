# 停滞钟 PROGRESS seq 单调校验（#804）

Status: implemented
Class: bug-fix

## Decision

本质问题（#804，B｜contract）：契约（`docs/design/2026-08-step-stall-detection.md` §3）
规定「`seq` 单调递增是唯一判据；重复打同一句话 seq 不涨 → 被判停滞」，但实现
（`backend/agent/pipeline_engine.py` 的 `_handle_line`）只做
`startswith("PROGRESS ")` 前缀匹配即刷新 `state["last_progress"]`，**从不解析/
校验 seq**。阶段 2 打开 `stall_seconds` 且步骤 `timeout_seconds=0` 时，卡在循环里
重复打同一戳（seq 不涨）的脚本会被判「在推进」→ 永不触发停滞钟 → 死循环无限
占用 permit（正是设计文档 §4 警示的形态）。

修复（对齐契约）：

- 新增 `_parse_progress_seq(line)`：解析 `PROGRESS {json}` 的 `seq`（仅 int，
  排除 bool）；缺失/非法返回 None；
- `_handle_line`：**仅当 seq 单调递增（或首戳）才刷新** `state["last_progress"]`；
  重复/回退 seq 不刷新——进程活着 ≠ 在推进；
- 无 seq 字段的旧戳按阶段 1 行为兼容（仅刷新），不引入对存量脚本的误杀；
- `on_progress` 回调保持「每个 PROGRESS 行都调用」（观测语义与 seq 无关）。

## Alternatives

- **无 seq 旧戳告警**——放弃（本批）：契约允许「兼容或告警」二选一；存量脚本
  多数未打 seq，告警会成噪音；
- **只防重复、seq 回退仍刷新**——放弃：回退同样不是推进，契约要求单调；
- **解析失败按停滞处理**——放弃：会把格式手误升级为误杀；按旧戳兼容更稳。

## Verification

实际运行（worktree `/tmp/stp-804`，基于 `origin/main`）：

- `pytest backend/agent/tests/test_step_stall_detection.py -v` → **32 passed**
  （新增 3 例：重复 seq 判停滞 / seq 回退判停滞 / 无 seq 旧戳兼容仍刷新；
  既有 6 个 PROGRESS 用例全绿——它们本就使用递增 seq）；
- **反向验证**：mutation 回退校验（恒刷新）→ 重复/回退 2 例失败、兼容例仍过；
  恢复后 3 passed；
- `check:quick` → 7 gates 全绿。

未完成（pending）：

- 阶段 2 真实长跑场景（脚本重复打戳 + stall_seconds 开启）的真机验证——本机
  无设备环境；行为由进程内 harness 单测覆盖（与既有测试同模式）。

## Revisit

- 若契约升级为「无 seq 视为违规」，把兼容分支改为告警/判停滞；
- seq 语义如扩展（多 step 各自 seq），解析与状态键（`last_progress_seq`）需同步。
