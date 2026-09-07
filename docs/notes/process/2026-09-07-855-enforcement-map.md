# #855 收口：强制力覆盖图替代行为验证重建

Status: implemented
Class: process

## Decision

#855（L1 移除后的残余缺口）按第一原理与复利口径裁决为**不重建任何行为
验证层**，改以「强制力覆盖图」收口：

- 覆盖图落 `2026-08-governance-surface-protection.md` §7.1：AGENTS.md
  硬不变量逐条标注强制力来源（运行时拒绝 / gate / 结构自证 / residual），
  证据带行内引用；差集（context-only 项）显式列管；
- 差集收缩：Pydantic v2 only、表名单数两条可 lint 项走**差异面检查**
  （后继 PR，advisory 起步）；
- residual（Redis 边界、types.ts 同步、python -m 形式）：显式声明依赖
  review 兜底，配棘轮决策树（`repository-workflow.md` §不变量违规处置）：
  任何违规事故 → 补文档 / 加差异面模式 / 记 residual，三选一，检查面只增不减；
- 原三缺口归宿：语义传导=消解（测量不产生约束力）、分诊=三步决策树、
  多 Harness 摄取=维持 ADR-0034 附录 A 手工验收矩阵（连续金丝雀否决：
  负复利 + 为低频事件建常驻设施）。

## Alternatives

- **重建行为 eval（引擎可插拔版）**：否决——测量不产生约束力，结果随模型
  版本作废（09-06 供应商切换即基线重置实证），且「写了没传导」失效类在
  两个月审计史中零记录（#855 解冻条件 3 未触发）；
- **连续摄取金丝雀矩阵**：否决——附录 A 手工矩阵已是正确的低频验收形态，
  脚本化为常驻设施违背「按需探针」裁决；CLI 自报已加载经 G2 实测不可行
  （无一家提供该能力）；
- **独立分诊协议 + 留档格式**：否决——归因的终局是选择修复端，决策树
  三步即可，协议化是流程剧场；
- **差异面检查直接 BLOCK**：否决——backend 现存 `.dict(` 用例证明噪声面
  未测，advisory 起步收噪声数据后再裁决（同 pr-agent C-G2 观察模式）。

## Verification

- 覆盖表每行证据经本机实测：`pipeline_engine.py:793/:1325`（运行时拒绝）、
  `backend/core/security.py:83`（RuntimeError guard）、
  `tools/dev/check-script-version-immutability.py`（gate 在位）、
  `grep -rn '\.dict(|parse_obj(' backend/`（context-only 项现存用例实证）、
  `frontend/package.json` 无 schema 生成器；
- `venv/bin/python tools/dev/check_governance_surface.py --check` /
  `--self-test` 全绿（S1–S12）；
- `check:quick` 全绿（本 PR 为纯文档）。

## Revisit

- 差异面检查 advisory 运行收噪声数据后，裁决是否转 BLOCK（约两周或首个
  真实命中）；
- residual 项出现首次违规事故时按棘轮收缩（加模式或加结构防线），此为
  #855 的长期维护姿态；
- 新增硬不变量时同步维护覆盖图（S11 锚点表同模式——有意改写必须连锚/行
  一起改）。
