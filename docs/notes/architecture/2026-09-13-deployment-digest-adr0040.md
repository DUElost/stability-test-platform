# 部署摘要协议（ADR-0040 立项）：热更新从「全量打包」改为内容寻址收敛（#1900 / #1901）

Status: implemented
Class: architecture

## Decision

把热更新的部署单元从「每次物化全量 tarball」改为**内容寻址的收敛协议**，立
[`ADR-0040`](../../adr/ADR-0040-deployment-artifact-digest-protocol.md)（Proposed）：

**1）事实基线（2026-09-13 只读实测 + 控制面复测）**：

| 指标 | 值 |
|---|---|
| 单台端到端 p50（542 条审计 `duration_ms`，08-15~09-03） | **20.1s** |
| 当天 48 台 `--direct` run 单台间隔 | median 21s（min 20s） |
| 全量 tarball：252 MB 源 → 125.7 MB，打包（level 9） | **16.6s** |
| 其中 `resources/`：229 MB / 108 文件 → 125.0 MB（level 6） | 5.9s |
| **剔除 resources 的代码树 + schema：468 文件 → 1.0 MB** | **0.31s** |
| 每台重复打包（`--direct` 循环） | 48 台 × 16.6s ≈ 13 min 纯 CPU |

**2）本质问题**：传输成本 ∝ 状态总量（而非差量）、副作用与「是否需要变更」无关（无条件重启）、
没有「已收敛」判定（写 VERSION 但不比较）。

**3）决策要点**：D1 双 artifact 内容寻址（`agent-code` 1MB 级 / `host-resources` 大件独立判定，
digest 双侧镜像实现 + 字节级等价测试，先例 `script_catalog_version`）；D2 远端单点上报
（`ARTIFACT_DIGEST` + 心跳字段 + 显式列，禁 `Host.extra` 裸键）+ 控制面现算 desired；
D3 digest 相等即全链路 no-op（保留 `--force`），变更走分层载荷、**不做** code 差量协议（带复议触发器）；
D4 restart 三类判据显式化；D5 四入口统一收敛语义与记录；D6 per-phase 计时；D7 显式不做清单。

**4）同时闭合的既有分叉**（同日核验）：审计 `hot_update_result` 只覆盖 UI/API（最后一条 09-03）；
`agent_code_deployed_at` 只有 UI/API 与 `--direct` 写；Ansible 不写该字段；precheck 连远端 VERSION
都不刷。D5 把四入口记录语义统一，no-op 结果同样留痕。

**5）裁决（2026-09-13，owner）**：D1–D7 全部采纳，ADR-0040 转 **Accepted**（v1.0），
优先级维持 P2 / M7；四项取舍（D2 信任模型、D3 不做差量、`deployed_at` 语义修订、
资源变更默认不重启）显式接受并保留 §7 复访触发器；P1/P2 实施切片另开 issue。
裁决记录见 ADR §9；P0 过渡项已由 [#1904](https://github.com/DUElost/stability-test-platform/pull/1904) 落地。

## Alternatives

- **P0 只做过渡项**（一批一次打包 + 压缩级 9→6）：省 ~16s/台，但不解决传输/重启/入口分叉 →
  保留为过渡，终态出口 = digest 缓存键，不留双轨；
- **单 artifact + 差量协议**（rsync / 变更集）：分层后收益边际小，且需远端持久 staging 或
  wrapper 新增带删除语义子命令（提权面扩大，ADR-0037）→ 否决，列为复议触发器下的首选备选；
- **每次收敛全树重算 digest**：可识别带外漂移，但 229MB 级 CPU 与目标矛盾 → 否决，列 Revisit；
- **artifact 存储 / 增量分发（ADR-0033 轨道）**：工程量大 → 前瞻保留，身份复用本协议 digest。

## Verification

- 只读基线已复现：生产库 `audit_logs.hot_update_result.duration_ms` 542 条；48 台
  `host.extra.agent_code_deployed_at`；控制面 `_build_tarball()` 与 gzip 分级基准实测；
- 本 ADR 初版为 Proposed（**未改任何代码**）；2026-09-13 owner 裁决转 **Accepted**（v1.0），
  本单仅改 ADR 状态与裁决记录 + README 索引，无代码/契约语义变更；
  实施切片的验证口径（digest 双侧等价、五象限集成、灰度、per-phase 计时复核）已在 ADR §6 列明；
- 文档影响：`docs/operations/agent-version-and-hot-update.md` 待 **P1 实施落地**时同步修订
  （避免先行描述未实现的语义）。

## Revisit

- 分层收益被证伪（`host-resources` 变更频率 >1 次/周）→ 回单 artifact + 差量评估；
- `agent-code` >5MB 或传输耗时占比 >20% → 差量协议提回表决；
- 出现带外手工改动导致 digest 未感知的事故 → 升级低频全树校验；
- 新增第五入口 → 必须复用本协议，否则先修订 ADR；
- 资源动作扩展落地时 → ADR-0037 同 PR 回填子命令白名单与校验模式。
