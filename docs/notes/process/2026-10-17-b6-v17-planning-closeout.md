# B6 v1.7 规划收口与裁决前置

Status: proposed
Class: process

## Decision

按交接包 `01-规划者提示词.md` 收口规划，未写业务代码。现行ADR-0061保持Accepted v1.0。
新增 [ADR修订提案](../../design/2026-adr0061-v11-proposal.md)、[完整v1.7候选正文](../../design/2026-b6-v17-planning-proposal.md)
与 [条件派单](../../design/2026-b6-v17-dispatch.md)。模型裁决 #3751，独立跟进U8 #3752；Owner裁决仍pending。
U3/U5保留原issue/PR；保守集成为U3→U5→U8→U4。部署继续冻结，不临时加入阶段B开关。

## Alternatives

不采用全WAITING豁免、无条件更新时间、忽略失租、把未知confirmed猜成已执行或删除冻结manifest登记。
不在Owner裁决前把候选稿写成Accepted/正式实施规范。#3723保留v1.6正文并增加裁决状态与候选稿入口；
裁决后必须整篇更新为v1.7，不能只在评论追加规则。

## Verification

- 当前origin/main完整SHA为945fcd4406cefa6299caa688376c9a54c2482868；U3为46ba633ee5e56afea836d9cb92bc2f16a68f95c4；U5为13a77c7fdd1097c70651a1961b20d0b8a063ded0。
- 通过GitHub现查确认U1/U2合入、U3/U5 Draft；读取批次/PR正文与评论、Accepted ADR、相关源码与相邻测试。
- 审阅交接给定三个隔离日志，不冒称本轮重跑；未运行 #3739 危险变异。
- 用项目入口调用确定性打包函数，在临时目录从原提交恢复disarm1.0.0/1.0.1/1.0.2与finish1.1.0，四项SHA均与原manifest相符；没有register/publish/scan或站点写入。
- 四份文档本地链接与必需规划字段核对通过；writing-conventions §5人工自查完成。
- `env -i PATH="$PATH" HOME="$HOME" ./scripts/project_python.sh scripts/run_gates.py check:quick`退出0，16 gates通过；schema-at-head因未配置DATABASE_URL明确跳过，未查业务库。日志 `/tmp/b6-v17-check-quick.log`。
- 本轮不跑业务/真机测试，不把未来有限验收标通过；required CI在文档PR创建后仍须现查。

## Revisit

Owner对预算、模型增量、核验策略或部署姿态提出修改时重订正文并复核交叉引用。
U3/U5新提交、原包恢复失配、共享文件重叠或实际协议落点超出scope时先刷新，再决定修订，不沿用旧快照。
