# B6 v1.7 规划收口与裁决前置

Status: proposed
Class: process

## Decision

按交接包 `01-规划者提示词.md` 收口规划，未写业务代码。现行ADR-0061保持Accepted v1.0。
新增 [ADR修订提案](../../design/2026-adr0061-v11-proposal.md)、[完整v1.7候选正文](../../design/2026-b6-v17-planning-proposal.md)
与 [条件派单](../../design/2026-b6-v17-dispatch.md)。模型裁决 #3751，独立跟进U8 #3752；Owner裁决仍pending。
U3/U5保留原issue/PR；保守集成为U3→U5→U8→U4。部署继续冻结，不临时加入阶段B开关。

第二笔提交（原规划者，回应草案复核）补两处规划缺口：
- 旧登记包：三个旧 disarm 条目改为 `retired: true`，并按 git 树对象恢复发布。Owner 已于 2026-10-17 选定该方案 A（[裁决记录](https://github.com/DUElost/stability-test-platform/issues/3751#issuecomment-6234224273)）；方案 B（保留缺包诊断并改部署 SOP 预期）未采用。
- 保存入口：§3.0.2「新计划缺解除步骤必须拒绝」落到 U5，新增 v1.7 §3.6.5，写明入口、调用顺序、错误契约 `ONBOARDED_TEARDOWN_INVALID`（四种 reason，含旧合并版 finish）与真入口反例。

## Alternatives

不采用全WAITING豁免、无条件更新时间、忽略失租、把未知confirmed猜成已执行或删除冻结manifest登记。
不在Owner裁决前把候选稿写成Accepted/正式实施规范。#3723保留v1.6正文并增加裁决状态与候选稿入口；
裁决后必须整篇更新为v1.7，不能只在评论追加规则。
旧包不采用原稿「保留登记、不退役、不发布」：scan 每次都会报缺包并触发部署 SOP 的停止条件；日后若补发，旧缺陷版本会成为 active。
只退役不发布也消不掉缺包，因为 scan 对没有库内行的退役条目仍要读包建 inactive 行。
保存入口校验不放进 U8：U8 依赖模型裁决且排在最后；该形态由 U5 的拆分引入，同一 PR 封堵。

## Verification

- 当前origin/main完整SHA为945fcd4406cefa6299caa688376c9a54c2482868；U3为46ba633ee5e56afea836d9cb92bc2f16a68f95c4；U5为13a77c7fdd1097c70651a1961b20d0b8a063ded0。
- 通过GitHub现查确认U1/U2合入、U3/U5 Draft；读取批次/PR正文与评论、Accepted ADR、相关源码与相邻测试。
- 审阅交接给定三个隔离日志，不冒称本轮重跑；未运行 #3739 危险变异。
- 用项目入口调用确定性打包函数，在临时目录从原提交恢复disarm1.0.0/1.0.1/1.0.2与finish1.1.0，四项SHA均与原manifest相符；没有register/publish/scan或站点写入。
- 四份文档本地链接与必需规划字段核对通过；writing-conventions §5人工自查完成。
- `env -i PATH="$PATH" HOME="$HOME" ./scripts/project_python.sh scripts/run_gates.py check:quick`退出0，16 gates通过；schema-at-head因未配置DATABASE_URL明确跳过，未查业务库。日志 `/tmp/b6-v17-check-quick.log`。
- 本轮不跑业务/真机测试，不把未来有限验收标通过；required CI在文档PR创建后仍须现查。
- 第二笔提交核对（main `945fcd440`，只读）：`script_catalog.py` 退役分支对无库内行条目仍读包建行；
  `check_script_packages.py::publish_latest` 只发每族最新未退役条目；`check_tool_manifest.py` 允许 `retired` false→true；
  部署 SOP 阶段 B 第 4、5 步把 #3661 以外的 `package_missing` 定为停止条件。
- 从 U5 head 可达的三个族树对象（`9943ccae…`、`153c867d…`、`1fdf49a4…`）在临时目录重建，SHA 与 manifest 的 1.0.0/1.0.1/1.0.2 全部一致；未写站点。
- 除历史 alembic 迁移外，PlanStep 只在 `plans.py` 三个保存入口写入；U5 现有墙钟 API 测试的 init 不含 `powercycle_setup`，不受新校验影响。
- 第二笔提交的门禁：`git diff --check` 通过；四份文档本地链接全部可解析；
  `env -i PATH="$PATH" HOME="$HOME" ./scripts/project_python.sh scripts/run_gates.py check:quick` 退出 0（16 gates）；
  `./scripts/project_python.sh scripts/run_pytest.py tests/ -q -x -p no:cacheprovider` 退出 0（2406 passed、18 skipped）。新 PR head 的 required CI 待现查。
- 第三笔提交只回填 Owner 对 §3.6.3 的选择（方案 A）并删去方案 B 分支：`git diff --check` 通过；本地链接可解析；
  `check:quick` 退出 0（16 gates）；`tests/test_internal_ip_leak_allowlist.py`、`tests/test_adr_index_status_2989.py`、`tests/test_governance_m7_board_3205.py` 13 passed。
  `tests/` 根套件未重跑（与第二笔提交相比只改同四份文档的文字）。

## Revisit

Owner对预算、模型增量、核验策略或部署姿态提出修改时重订正文并复核交叉引用。
U3/U5新提交、原包恢复失配、共享文件重叠或实际协议落点超出scope时先刷新，再决定修订，不沿用旧快照。
#3661 若改用 `--publish-restored` 补包，需另行核对其历史版本能否按树对象重建。
新增 PlanStep 写入口（如步骤启停接口）时，§3.6.5 的校验必须同时接入。
