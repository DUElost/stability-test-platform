# merge 全平台失败改 raise（#1527）

Status: implemented
Class: bug-fix

## Decision

`merge_task` 在 `result != "ok"`（merge 全平台失败，`run_merge_all_platforms_sync`
返回 `""`）时仅 `logger.info` 后 `return`：

- SAQ 视该 job 成功（无异常即成功）→ 无重试；
- `extract_task` 永不入队 → PlanRun **永久 RUNNING**、无 extract 产物、
  无失败标记（无法聚合、归档完备性判定失真）；
- 日志级别 INFO → 监控不告警；与夜间 backstop 无交集（纯运行期静默）。

修复（issue 修复方向 1）：该分支对齐**同函数异常路径**（`:756-758` 本就
`logger.exception + raise`）——改 `logger.error` + `raise RuntimeError`，
让 SAQ 标记失败并触发重试。**不引入新契约**（异常即失败是既有行为，静默
return 才是偏离），无需 owner 额外裁决；方向 2（显式 FAILED 终态 +
`run_context.archive` 记录）可作为后续增强（Revisit）。

## Alternatives

- **方向 2：写显式 FAILED 终态 + archive 记录**——暂缓：task 链现有惯例是
  「失败即 raise」（同函数异常路径、extract enqueue 失败路径均 raise），
  直接写 Run 终态会引入与链上其它 task 不一致的语义；且「谁最终把 Run 置
  终态」是链级契约，适合独立评审。

## Verification

- **反例实证**：回退 saq_tasks 实现保留测试 → 用例失败（旧实现无 raise）；
  修复版全绿；
- 新增用例（`test_saq_tasks.py` +1）：`_run_sync_exclusive` 返回 `""` →
  `pytest.raises(RuntimeError)` 且 `_enqueue_extract_task` **未被调用**；
- `test_saq_tasks.py` 全套 **30 passed**（含既有 merge mark/水位线回归）；
- `check:quick` 与 PR 门禁：见 PR 描述。

## Revisit

- 方向 2 增强：merge 失败后 PlanRun 何时收敛 FAILED（链级契约）值得单独
  评审——当前 raise 使 SAQ 失败可见/可重试，但若重试耗尽，Run 仍依赖
  上层收敛路径（同其它 task）；
- 若出现「merge 部分成功」语义需求（当前二分 ok/""），需要引入 result
  枚举并明确 partial 的 extract 行为。
