# Execution Contract v1.4：实现与契约的先后纪律（语义先行）

Status: implemented
Class: process

## Decision

契约 v1.3 及此前只定义了两层权威边界——ADR ↔ Contract（冲突以本文为准，契约
§10）与事实来源分层（Registry/Git/GitHub/CI，契约 §2.3）——但缺第三层：
**实现（`ai_work.py`）与 Contract 的先后关系**。「实现不得静默重新定义
Contract 语义」此前只是事实惯例（#978 v1.2、#946 v1.3 均契约与实现同 PR
收口），无成文规则。本次在契约 §10 增补（措辞经用户 2026-09-08 确认）：

- 实现需要新增/删除/改变**项目级可观察语义** → 先改 Contract 再改实现；
  不改变项目级可观察语义的纯实现细节，无需改 Contract；
- 「项目级可观察语义」最小判据 = registry 持久字段与校验规则、三维状态与
  transition、overlap/issue 查重/drift 判定结果、CLI 输出与退出码；
- 「先改」是**语义先行**：契约修订与实现可同 PR 原子落地（#978/#946
  先例）；禁止的是实现先于任何契约修订独自合入、或实现合入后契约无
  版本化收口；
- 与 AGENTS.md「冲突时以代码与测试为准」的分工：后者是**事后事实裁决**，
  不豁免本条；发现既成漂移须按契约 §10 版本化收口，不得以「代码已如此」
  静默追认。

动机实据：#804（停滞钟不校验 seq 单调，实现 vs step-stall-detection 契约）
证明「实现静默偏离契约」在本仓库已实际发生；本条把防线从「事后审计发现」
前移到「事前过程纪律」。

同 PR 收口一处存量索引漂移：DOC-MAP 执行契约行仍钉 Living v1.1（#978 v1.2、
#946 v1.3 两版未同步），随 v1.4 一并修正并改为列增量摘要（#867 同类教训）。

## Alternatives

- 写入 ADR-0034 而非契约：放弃——细则级变更，契约 §10 明文允许直接在本文
  版本化，且本条不推翻任何 ADR 裁决；
- 在契约中枚举「纯实现细节」白名单：放弃——白名单随实现膨胀会成为新
  漂移源；反向定义（只钉可观察语义判据，其余默认免改）更稳定；
- 不成文、维持惯例：放弃——惯例对并行 Harness 新会话无约束力，#804 类
  漂移在无规则时既无可违也无从拦。

## Verification

- 变更仅三文件：`execution-contract.md`（状态行 v1.4 + 日期 + §10 增补）、
  `DOC-MAP.md` L85、本 note；`git diff` 核对无其他段落改动；
- 本地实跑：gov-surface（S1–S12）全绿、ai-work 自测全绿、ruff 绿、
  compileall 绿；check:quick 中 eslint/tsc/knip 属 frontend 检查，/tmp
  worktree 无 node_modules 未本地跑（零 frontend 文件改动，CI required
  checks 覆盖）；
- Registry dogfood：declare（scope 与实际 diff 一致）→ finish 全周期登记。

## Revisit

- 若出现「实现先行是唯一可行路径」的场景（如生产热修），复核本条与 PR
  流程的相容性——届时应以「实现 PR 显式声明契约欠账 + 紧随版本化收口」
  处置，再评估是否为该例外路径成文；
- P2 Adapter 落地引入新可观察面（heartbeat 供给等）时，扩展「项目级
  可观察语义」判据清单；
- 契约版本递增时 DOC-MAP 行需随版同步——若再次发生漏同步，考虑把
  执行契约行纳入 S12 门禁检查面。
