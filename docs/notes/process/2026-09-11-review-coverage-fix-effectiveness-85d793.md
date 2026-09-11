# 覆盖、修复有效性与组合治理独立审查落地

Status: implemented
Class: process

## Decision

按用户要求把两轮结论写入
[`REVIEW_COVERAGE_AND_FIX_EFFECTIVENESS_2026-09-11_85d793.md`](../../reviews/REVIEW_COVERAGE_AND_FIX_EFFECTIVENESS_2026-09-11_85d793.md)。
后缀来自本 Codex 会话真实编号，不借用其他会话名称。

区分根因修复、合理防御、过渡止血、修复不完整、风险接受与待验证；固定源码和
GitHub 统计口径，记录 #901/#1123 隔离反例及10个候选组合治理主题。报告是独立
Proposal，不改变执行契约、不预占 ADR 编号、不派单，不修改业务代码或 Issue 状态。
本 note 的 implemented 仅指审查文档已落地，不指报告建议已经实施。

不改共享 DOC-MAP/README/审查总纲或其他 Harness 报告，避免干扰在窗的综合整理。

## Alternatives

- 仅保留会话答复：后续 Harness 难以核对基线、反例和证据边界。
- 把所有小补丁都判作止血并重构：混淆合理的 CAS/背压/超时与根因未解决，增加成本。
- 自动创建治理 Epic/ADR：未经用户裁决，并可能重复已有 Accepted/Proposed 决策。
- 为每个 Issue 分别新增规则：强化症状切片；改为按共同不变量提出可裁剪的组合建议。

## Verification

- 第一轮5份 Agent 定向测试 73 passed；refresh 诊断 1 passed / 1 failed，失败揭示缺陷。
- 本轮真实线程取消诊断 1 passed / 1 failed，证实同 key 工作在旧线程未退出时重入。
- 本轮固定 `d00273d0` 的隔离 Agent 重跑：73 passed，0.98s。
- 两份文档的7个相对链接、源码路径/行号、会话后缀、Note 四节与 whitespace 核对通过。
- 独立 detached worktree 的 `python scripts/run_gates.py check:quick`：7 gates 通过；既存 compileall SyntaxWarning 记录于报告 V06，未修改无关代码。
- 验证仅使用隔离源码/临时资源，未读取生产配置、连接生产库或操作真实设备。

## Revisit

综合审查时按报告 F/CG 编号逐项反证、去重和人工裁决；不以投票替代源码/测试。
修复后以新 SHA 和回归证据关联收口，不能继续把旧反例当作当前缺陷。
若新增方向与既有 ADR 冲突，先形成单一裁决并更新既有权威；本文保留为历史证据，
不演化为第二套执行契约或永久问题台账。
