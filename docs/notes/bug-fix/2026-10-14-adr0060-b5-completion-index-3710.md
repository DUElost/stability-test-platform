# ADR-0060 索引同步 B5 完结状态

Status: implemented
Class: bug-fix

## Decision

实施 [#3710](https://github.com/DUElost/stability-test-platform/issues/3710)，
仅更新 `docs/adr/README.md` 的 ADR-0060 主表行和 M7 条目。
两处统一记录 B5 已落地，2026-10-14 完成生产验收并关单，并链接批次单 #3653。
保留 Accepted v1.1、#3652 hard-max NULL 裁决、其它 ADR 条目与 M7“进行中”状态。
不修改 ADR 正文，也不将后续 #3705 UX 或 #3709 说明维护算成 B5 未完结。

基线 `origin/main@318b73d246bdd4c55b09dd9835e3559eee9a42de`；
已现读 #3653 正文 v1.6 和 [Planner 收口证据](https://github.com/DUElost/stability-test-platform/issues/3653#issuecomment-6150507694)。
GitHub `closedAt=2026-10-13T16:46:15Z`，对应北京时间 10-14 00:46:15。
本轮引用既有生产验收记录，没有重新部署或执行金丝雀。

## Alternatives

- 只改主表：派生看板仍误报“批次落地中”，放弃。
- 将整个 M7 标为完成或因后续维护重开 B5：改变其它交付范围，放弃。
- 补写未知的全历史退回/复核前合入次数：没有完整核定证据，保留 #3653“待核”。

## Verification

- 开放 PR 前检：#3705 与 #3711 均不修改 ADR 索引，无目标文件重叠。
- Diff 核对：主表与看板各一处、链接同指 #3653；ADR 正文无差异。
- `./scripts/project_python.sh tools/dev/check_governance_surface.py --check --base origin/main`：通过。
- `./scripts/project_python.sh scripts/run_gates.py check:quick`：16 gates / exit 0。
- Required CI：提交时 pending，最终 head 结果另回填 PR 与 issue。
- 本单不新增或运行 pytest/vitest；完整后端 CI 可能继承 #3709 的说明缺失失败，
  此处不搭车修复，也不冒称全量后端已绿。

## Revisit

ADR-0060 新的正式裁决或后续独立批次改变交付范围时，再同步主表与看板。
本单只记录已经完成的 B5 原范围；流程统计另由规划者核定回填。
