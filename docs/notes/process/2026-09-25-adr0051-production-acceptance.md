# ADR-0051 生产验收与剩余边界（2026-09-25）

Status: implemented
Class: process

## Decision

ADR-0051 **尚未落地完结**。现站「按内容寻址包执行脚本」已达到生产验收；原 ADR 的「全发布单元统一包化、独立站点可复现、过渡机制退出」尚未达到。不能把 #3222 issue 关闭或 48 台 Agent digest matched 当作全 ADR 关闭。

本次只把已合入 main 的 #3265 生产激活。现网原为 `7fee7cf`（#3263 初版）；实际 Agent ACK 不携带 `package_sha256`，初版 sweep 用 ACK 侧该字段筛选，48 台全被记为 `unknown`。修复提交 `37a56b41` 相对现网仅改变 `backend/services/script_presence.py` 与对应测试，判据改取 expected 侧 `sha_keys`。没有中止运行中的 PlanRun，也没有 force 热更新 Agent。

## Alternatives

- 直接沿用初版汇总：拒绝；`unknown=48` 不是包模式成功证据。
- 为更新控制面中止活跃计划：拒绝；修复只需控制面切换与只读型 Agent 核验 RPC，运行计划并非阻塞条件。
- 部署更新的整个 `origin/main`：本次未选；使用已合入 main 且相对现网只有 #3265 两文件差异的提交，减少与包模式验收无关的变更。

## Verification

- 构建独立发布根 `stp-releases/37a56b41`；真实目录 venv，依赖 `pip freeze` 与原发布根逐项一致，`python -m pip check` 无破损；Agent code digest 与 host-resources digest 和原发布根相同，schema target 仍为 `c7d2e5f8a1b3`。
- 隔离测试：`python -m pytest -q backend/tests/services/test_script_packages_mode_3222.py`，2 passed。发布根内用真实 ACK 形态导入验证 `derive_packages_mode(...)=package`。
- 切换后 `stability-backend` active，`/health` healthy、SAQ ready、alembic revision/head 均 `c7d2e5f8a1b3`，进程 cwd 与 `current` 均为 `stp-releases/37a56b41`。
- 生产逐台 `POST /script-presence/refresh?host_id=…`：48/48 成功，均无 missing/mismatch/unknown。随后 `GET /script-presence/summary`：`fleet_packages={package:48, tree:0, mixed:0, unknown:0}`；`present=1249, missing=0, mismatch=0, unknown=0, n_a=1247`，`hosts_total=48, hosts_with_gap=0, stale=false`。账本仅覆盖 52 个被引用版本，`uncovered_active_versions=50`，故在位零缺口**不代表**全部活跃版本逐台核验。
- 切换前后 RUNNING 的计划均为 3 个（#558/#555/#554）；本次没有调用 abort。#3258 的空库首扫/strict 默认已有隔离测试与此前生产 scan 幂等证据，不能把本次 48 台刷新误称为空库实测。
- #3262 已合入：manifest 当前 211 条脚本版本（102 未退役、109 显式退役）与 3 条工具版本；独立空库 bootstrap 活跃脚本集 102 条的结果来自该 PR 的隔离验收，本次未重跑空库迁移。

## Revisit

关闭 ADR 前至少逐项验收：

1. 新站 `default_params` 与生产参数面的差集归零；不得原地改已有版本的默认参数，需确定性、版本化数据迁移与隔离空库重放证据。
2. D6：生产 `.env.backend` 不再经 symlink 依赖开发检出；site install/回滚在独立发布根可重放。
3. D7：flashtool 与 aimonkey 从 host-resources 迁入 `tool_manifest` 包；控制面 dedup 工具及 Agent legacy 路径键/回退窗口退出，#3075 与 `docs/governance/transitions.json` 中对应活跃项逐项销账。
4. D8：`check_new_script_family.py`、`check-deploy-source.sh` 等仅在替代门禁生效后按 ADR 顺序退役；例外声明格式收敛。

当前 48 台包模式观测已闭环，后续常设 sweep 应持续维持 `package=48` 且 `unknown=0`；出现回落先区分 RPC/账本陈旧与真实执行路径漂移。
