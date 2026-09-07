# Harness 摄取矩阵探针（#855 方向 b 落地 / ADR-0034 P2 验收工具化）

Status: implemented
Class: feature

## Decision

实现 `tools/dev/harness_probe.py`（加载矩阵探针，挂 `check:gov` 手跑）——**#855 三选一的 b 方向（每 Harness 确定性摄取自检）落地**。触发背景：#857 上游确认（09-07）定性为上游 bug 且短期难修，缓解转为长期形态后，唯一真实缺口=「裸奔不可检测」——L0 全绿但加载层断裂（结构性缺上下文）无机制发现。

**第一原理推导的设计**（详见 ADR-0034 附录 A 协议源流）：

1. **黑盒观测加载结果**，不白盒推断加载机制——读 harness 源码/文档断言行为会随版本失真（#88405 实例：文档与行为矛盾）；
2. **双题探针是最小完备的**：P0b/G2 之后契约层只有两个语义对象（根启动契约 + scoped 真身），探针覆盖两对象 = 覆盖契约体系全部加载面；
3. **确定性结构判卷**（唯一探针串 + 正则），零 LLM judge——结构层稳定，语义层随模型漂移（R1 实证）；
4. **EXPECTED 显式编码 = 行为漂移检测器**：每形态的预期结果（来自实测）写死，实际偏离即红——**即使偏离「变好」**（#857 上游修复 → `claude-subdir-plain` 对照行变绿 → 输出提示 wrapper 退役）。被动跟踪上游变主动发现；
5. **不进常规 CI**：每家一次真实非交互 LLM 会话（分钟级+外部依赖）——挂 `check:gov` 手跑，与 ADR §2.7 P3 同款注意力预算纪律。

七形态（六家自动化 + Zcode 人工指引）：claude-with-root / **claude-subdir-plain（#857 对照组——上游修复监测行）** / codex / cursor / opencode / codebuddy / zcode(manual)。

**与 `invariant-diff` gate（并行会话已落地，进 check:pr）的分工**——#855 的两条腿：invariant-diff 守**差异面**（声明/不变量的 diff 偏离，静态可判、可进 CI）；harness-probe 守**能力面**（各 Harness 实际摄取了什么，只能黑盒实测）——互补不重复。#857 类结构性缺上下文由后者覆盖（前者对其盲）。

## Alternatives

- **a 方向：行为 eval 复活（LLM 语义问答）**——放弃：语义层随模型漂移（R1）、成本高；摄取存在性检测已覆盖 #857 类缺陷，语义级残留人工分诊；
- **c 方向：并入 drift gate**——放弃：对象不同（drift 查声明与 diff，probe 查 harness 能力），混建互相稀释；
- **进 check:quick/pr**——放弃：外部 LLM 调用分钟级，违背确定性门禁与注意力预算；check:gov 手跑定位不变；
- **白盒断言（读 harness 源码/文档判定行为）**——放弃：#88405 实证文档与行为矛盾；黑盒观测是唯一可信源。

## Verification

- `--self-test`（离线红绿）：判卷正则（含全角冒号容错）、compare 偏离检测（含「变好也是漂移」——#857 修复监测语义）、FORMS 完整性（expected 二键/command-manual 二选一）、prompt 双题+禁令；
- FORMS 与 2026-09-06/07 实测矩阵一一对应（六家 expected 来自 live 实测，zcode=manual 形态）；
- 治理门禁 S1–S12+S5x 全绿（S5x：harness-ingest 登记 None+理由，与 invariant-diff 交叉引用）；ruff 通过；
- pending（如实标注）：真矩阵全量跑需逐家真实会话（分钟级×6）——首次全量跑在批次开工时执行并 `--json` 留档，作为基线时间序列起点。

## Revisit

- **#857 上游修复监测**：`claude-subdir-plain` 行 Q1 变「是」即上游修复信号 → 评估 wrapper 退役；
- harness 版本升级后重跑矩阵（升级安全网）；
- 结果 JSON 时间序列积累后，评估接入 P4 观察位的数据面。
