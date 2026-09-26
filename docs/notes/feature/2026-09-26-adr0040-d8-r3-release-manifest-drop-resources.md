# ADR-0040 D8 R3：release manifest 去掉 host-resources 分量（2026-09-26）

Status: implemented
Class: feature

## Decision

按 ADR-0040 v1.2 D8 的 R1–R4 顺序（2026-09-26 owner 裁决）落第三步：发布物不再携带、也不再声明 `host-resources`。

- **`build_bundle`**：`backend/agent/resources` 不再是构建前置（删除 `bundle_resources` 拒绝），也不进 bundle——
  构建机仓根可能仍有退役前的资源副本（gitignored），`bundle_ignore` 按路径剔除，交付物与提交同形。
  manifest 分量改为 `agent-code` + `control-plane`。bundle 从此不依赖任何 `backend/agent/resources/` 外部物料，
  ADR-0051 D8「`check-deploy-source` 由结构替代（从提交构建）」的这一项前置随之解除。
- **`tools/site_config`**：`REQUIRED_COMPONENTS` 只剩 `agent-code`；新增 `RETIRED_COMPONENTS = {host-resources}`：
  旧 bundle 仍可声明它，照收但不核验——其内容已没有任何下发通道（R1 控制面、R2 Ansible 都不再推送），核验只剩成本。
  S0 不再计算 resources 分区；preflight 的 bundle 布局去掉 `backend/agent/resources`；plan / checks 文案同步。
- **S5「到齐」判据只看 Agent 自报的分量**（`AGENT_REPORTED_COMPONENTS = {agent-code}`）。这里顺带修了一个既有缺陷：
  原判据要求「清单声明的全部分量都等于上报值」，而 `control-plane`（ADR-0051 Phase 4 加入）由 S0 核验、Agent 从不上报，
  所以真实 bundle 永远到不齐，**每台 Agent 都要空等满 `digest_timeout`** 才放行。去掉 host-resources 后若不改判据，
  这个空等依旧存在，因此一并修正。

**刻意不动**：契约包与控制面镜像里的 `kind="resources"` 枚举。它们位于 `backend/agent/contracts/`，属于 agent-code 载荷，
改动会让全机队 digest 变化、触发一轮全量热更新与 Agent 重启；随 R4（本就要改 Agent 代码）一起退役，只付一次代价。

## Alternatives

- **S0 继续核验旧 bundle 的 host-resources**：必须保留 resources 枚举给安装器用，而被核验的内容已经没有消费方；弃。
- **旧 bundle 声明 host-resources 即拒绝**：违背 D8「旧 bundle 照常接受」；弃。
- **S5 的空等缺陷另开单**：R3 去掉 host-resources 时会原样保留这个缺陷，而且改的是同一段判据；一并修掉更小。

## Verification

- 相关 5 个测试文件（release bundle / site install / site agents / plan / preflight）**241 passed**
- **变异反证**（在新代码上逐项注回旧行为）：S5 判据改回「全部声明分量」→ 两条新用例红（0.5s 窗口内空转
  **28 万次**假 sleep，坐实空等）；S0 去掉退役豁免 → 旧 bundle 用例红；`bundle_ignore` 不剔除 resources →
  「不要求也不携带」用例红。另把 7 个源文件整体换回 origin/main 版本，新旧夹具均按预期大面积红
- `check:quick` **16 gates OK**；`tests/` 全量 **1916 passed / 18 skipped**

## Revisit

- **下一次部署**：新 rev 的发布根不再有 `backend/agent/resources/`。控制面自 R1 起不读它，Ansible 自 R2 起不推它，
  所以部署前须确认 R1 / R2 已在同一 rev 内。
- R4：Agent 心跳停报资源 digest、wrapper 删除资源子命令（ADR-0037 同 PR 回填）、DB 列停写、前端标弃用，
  并退役契约与镜像中的 `kind="resources"` 枚举。完成后结项台账 `host-resources-layer`（跟踪 #3288）。
