# 批次方法模块

- **状态**：Living v1.0（2026-10-26，随 ADR-0058 v1.6 D12 建立；内容自 `batch-planning.md` v1.3 与 `batch-review.md` v1.2 原样迁出，规则无改动）
- **读者**：规划者、实施者、复核者，按下表的触发条件读取
- **权威**：职责、闸门与判据以 [ADR-0058](../../../adr/ADR-0058-planned-batch-execution.md) 为准；各角色常驻的门禁见 [`role-cards.md`](../role-cards.md)

本目录只放「怎么做」的方法，不放门禁。规划者和复核者所在的 Web 工作面读不到 `.claude/skills/`（ADR-0058 §4），
因此方法模块放普通文档，由派单和复核提示词按路径点名，不依赖 Harness 自动发现。

| 模块 | 触发条件 | 主要读者 |
|---|---|---|
| [`family-scan-by-source.md`](family-scan-by-source.md) | 规划者做形态压缩；复核者回答 B 问题 | 规划者、复核者 |
| [`target-state-design.md`](target-state-design.md) | 修法需要新表、新状态、新 env、权威冲突、生命周期变化或跨族共享运行时 | 规划者 |
| [`guard-unit-acceptance.md`](guard-unit-acceptance.md) | 单元是 checker、门禁或静态分析工具 | 规划者、复核者 |
| [`negative-evidence.md`](negative-evidence.md) | 复核者给出结论之前 | 复核者 |
| [`batch-activation-acceptance.md`](batch-activation-acceptance.md) | 批次进入激活与验收 | 规划者、激活执行者 |

规划者对实施档位为 flash 的单元，必须在派单提示词中按路径点名需要的模块；对高阶档单元，规划者可以不点名（D12 第 5 条）。
