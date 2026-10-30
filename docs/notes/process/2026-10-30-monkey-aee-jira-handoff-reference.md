# Monkey AEE 到 Jira 交接参考（文档新增）

Status: implemented
Class: process

## Decision

新增 `docs/operations/2026-10-30-monkey-aee-jira-handoff-reference.md`，记录 2026-10-30
对 NX2 / NX3 / NX3Carrier 三个机型的实际交接流程：归档扫描完整性、FAILED run 的手动
merge、报告行到事件目录的映射、证据导出到中心存储、表单生成与计数回填，以及 Jira
第一阶段与 dry-run 的入口。

该文档是经验参考与建设输入，不是契约：日志链语义仍由
`docs/design/2026-log-chain-global-semantics.md` 与
`docs/design/2026-device-log-chain-contract.md` 定义；本文只写「这次怎么做、哪里会错、
下一步该建什么」。同时更新 `docs/README.md` 与 `docs/operations/README.md` 的索引。

文档明确记录了三类事实：三份最终表单的来源 run 与计数（NX2 77 条 / 2151 / 339；
NX3 12 条 / 17 / 15；NX3Carrier 39 条 / 399 / 137）；Count 的唯一事件口径；以及本轮
未覆盖的缺口（仅 dblog、手工脚本、无 manifest、无校验账本、Jira 段未执行等）。

## Alternatives

- 写进 ADR：本轮没有新的方向级裁决，写 ADR 会把经验记录拔高为规范，过重。
- 并入设备日志链契约：契约应稳定，实战步骤与缺口会随批次变化，混写会让契约漂移。
- 只保留会话记录：不可检索，下一次交接仍要重新踩坑。
- 直接实现导出 API 与 manifest：跨控制面、存储、权限与 Jira 适配，超出本轮文档任务；
  本文把它们列为 P0 建议，供后续单独立项。

## Verification

- 文档中的三份表单、来源 run、目录规模、Count/DeviceCount 与 2026-10-30 实测一致
  （共享根目录、dedup 产物、表单回填结果三方交叉核对）；
- 新增入链在 `docs/README.md` 与 `docs/operations/README.md` 中可解析；
- 本地运行 `scripts/run_gates.py check:quick`，结果见 PR 描述。

## Revisit

- 控制面导出 API 与 manifest 落地后，把第 4 节的手工步骤替换为 API 调用，本文降级为
  历史参考；
- Jira dry-run、真实提交与回归跑通后，补齐第 6 阶段的证据与失败处理；
- 若唯一事件计数口径进入 ADR 或契约，本文只保留口径链接，不重复定义。

