# 冲刺期结构护栏：结构日报、needs-decision 分流、门禁预算

Status: implemented
Class: process

## Decision

临近节点时按 ADR-0034 并行消解积压，合入量成倍上升（9 月 1–24 日 1,264 个 PR，峰值单日 135）。
现有「并行消解 → FIFO 串行合入 → CI 兜底 → 每日审计」不改；它只覆盖行为面（修复是否落地），
本次补结构面，不设合入上限：

- 新增 `tools/dev/structure_digest.py`：只读、纯 git 的结构日报（合入构成、`.importlinter`
  基线变化、三次法则热点、新增门禁与根契约测试、过渡登记、兜底/兼容标记行数），`--self-test`
  离线自证，`tests/test_structure_digest.py` 覆盖；不设阈值、不判红，不进 `run_gates`；
- `repository-workflow.md` 新增「冲刺期的结构护栏」：CI 结构门禁、`needs-decision` 领单前分流
  （标签约定）、每日结构日报、冲刺后按数据结算；
- `dependencies-and-quality.md` 增加门禁预算：新增门禁写明合并或替代了哪一条，每月复看拦截记录。

## Alternatives

- **每日合入上限**：弃——ADR-0034 §2.6 v1.9 已以同一理由撤销会话上限，节点压力下会被常态突破；
- **把结构日报做成阻塞门禁**：弃——热点与标记数是讨论输入，不是二值缺陷；阻塞只留给
  `.importlinter` 这类能明确判定的结构规则；
- **在 `ai_work declare` 中机械拦截 `needs-decision`**：本次不做，改变执行语义须先修订 ADR-0034。

## Verification

- `python tools/dev/structure_digest.py --self-test` 通过；
- `python tools/dev/structure_digest.py --since "2026-09-24 00:00"` 真实运行：21 个合入、
  合约基线 54、29 个热点文件、6 个新增门禁/契约测试、兜底标记 685 → 674；
- 治理面结构检查 S1–S15、S5x 通过。

## Revisit

- 连续两次冲刺结算后，热点清单仍以同一批文件为主：说明结算没有触及根因，重看结算方式；
- `needs-decision` 标签实际使用中若常被绕过，再评估是否修订 ADR-0034 做机械拦截。
