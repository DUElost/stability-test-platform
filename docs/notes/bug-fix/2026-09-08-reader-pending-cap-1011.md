# R07-F10 落地：reader pending 按捕获预算截断（#1011）

Status: implemented
Class: bug-fix

## Decision

`_drain_polling`（POSIX 轮询 reader）的 `pending` 在出现换行前无界增长：
无换行大流 / 超长单行让 `pending += decoder.decode(chunk)` 每 chunk 累加，
整行只在 EOF 或换行边界才送 `on_line`；sink 层 `_MAX_CAPTURED_CHARS`
（8 MiB）只在完整行入缓冲时按总量检查，既不截单行也管不住组装阶段的
`pending`——可造成显著内存增长乃至 OOM（远端命令逐字节/无换行输出场景）。

修复（`backend/agent/pipeline_engine.py`，`_drain_polling` 单点）：

- 每次 chunk 组装后检查 `len(pending) > _MAX_CAPTURED_CHARS`：把预算内
  前缀按完整行送出（`MAX-1` + `"\n"` = 恰好预算，截断行本身不超限），
  超出的尾随部分保留续接下个 chunk；
- 每段 `pending` 由此压回 chunk 量级（峰值 ≈ 预算 + 64 KiB chunk）；
- 截断后的后续输出走 sink 层既有预算语义（`sink_sizes` 满即丢、警告一次）
  ——与「捕获前 8 MiB」的既有行为一致；EOF 尾行仍正常送达（不超过预算）。

## Alternatives

- **组行阶段直接丢弃超预算尾部（不留 tail）**——放弃：行可能恰在 chunk
  边界处出现换行，无尾随则丢内容；保留 tail 让正常行结构完整；
- **reader 层维护独立行预算计数器**——放弃：sink 层已有总量预算与单次
  警告，reader 只负责把「无界 pending」变「有界 pending」，两层职责清晰；
- **Windows 阻塞分支 `readline()` 一并改造**——放弃：文本模式 readline
  无法预算，需改字节读取重构；Windows 非生产运行面，代价不值（Revisit）。

## Verification

- **反例实证**：回退实现保留测试 → 2 用例失败（24 MiB 无换行流/21 MiB
  超长单行均超 8 MiB 上限）；修复版全绿；
- 新增用例（`test_step_stall_detection.py::TestCaptureLimit` +2）：
  `test_no_newline_stream_is_bounded_by_capture_budget`（24 MiB 无换行 →
  ≤ 预算 + rc 0）、`test_single_huge_line_is_truncated_then_remaining_lines_dropped`
  （21 MiB 超长单行 → 前缀保留 + 流读尽不阻塞）；
- `test_step_stall_detection.py` 全套 **29 passed**（含既有截断/PROGRESS/
  reader 语义回归）；
- `check:quick` 与 PR 门禁：见 PR 描述。

## Revisit

- 测试踩坑记录：`_spawn` body 内写换行字面须用双反斜杠 `\\n`——单反斜杠
  会在字符串值中变真换行，撕裂 body 行结构致 dedent 失效（子进程
  IndentationError）；既有用例均守此约定；
- Windows `_drain_blocking` 仍按行 `readline()`（无界单行内存）；生产面
  为 Linux（POSIX 轮询 reader），Windows 为遗留兼容分支——若未来需支持
  Windows 大输出场景，改字节预算读取（超出本单范围）。
