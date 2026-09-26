# ADR-0040 D8 R1：控制面对 host-resources 层停判停推（2026-09-26）

Status: implemented
Class: feature

## Decision

按 ADR-0040 v1.2 D8（2026-09-26 owner 裁决采纳 R1–R4 顺序）落第一步——控制面不再判定、不再下发 `host-resources` 层：

- `plan_convergence` 只剩 `agent-code` 一层：`ConvergencePlan` 去掉 `resources_*` 三字段，判定过程不再计算 resources 身份，
  也不读主机的 `agent_resources_digest`；`force` 只强制 `agent-code` 全量；no-op 结果不再带 `resources_digest`。
- `execute_hot_update` 去掉 `resources_drift` / `resources_tarball` / `resources_digest` 参数，删除 `_build_resources_tarball`；
  远端脚本删掉资源层整段（不解包资源 tar、不调 `apply-resources`、不写 `ARTIFACT_DIGEST_RESOURCES`）。
- 能力判据 `_REQUIRED_PRIV_SUBCOMMANDS` 不再要求 `apply-resources`：R4 会从 wrapper 删掉这个子命令，届时装了新 wrapper
  的主机仍须能热更新。R1 在前，这个顺序才不会卡住。
- 三个入口（UI/API、`batch_hot_update --direct`、precheck 回退）同步改为单层。

**刻意保留**（属于后续步骤，或按 D8 永久保留）：

- wrapper 对 `resources/***` 的 protect-only 与 `ARTIFACT_DIGEST_RESOURCES` 的元数据保护：退役不做主机清理（D8-2）。
- `_iter_payload_files` / `collect_artifact_entries` 仍支持 `kind="resources"`：它和契约枚举对拍，而 Ansible（R2）与 bundle
  （R3）仍在产出该身份，随 R3 与契约一起退役。
- 心跳仍把 `agent_resources_digest` 写入库，但任何判定都不读它（R4 停报停写）。

上线效果：稳态主机两层原本都 matched，改成单层后仍是 converged，**不会触发全机队重推**。主机上的 `resources/` 原样保留；
D7 激活后已没有消费方读它。回滚方式：部署上一个 rev，旧代码恢复双层判定。主机上的 `ARTIFACT_DIGEST_RESOURCES` 没被改动，
只要 desired 没变就不会被重推。

## Alternatives

- **保留参数、只在调用方传 `resources_drift=False`**：死参数会让调用方仍能请求资源层，D8 的「不再下发」就没有结构保证；弃，
  改为删参数，再用签名守卫钉住。
- **本步一并删除 `kind="resources"` 枚举**：会连带改 Ansible / bundle 的对拍测试（R2/R3 的面），超出本步范围；弃。
- **本 PR 同步改 control-plane-deploy SOP 的资源段**：SOP 自己的规则是「校准来自实跑，不来自推演」。生产在部署 R1 前仍跑旧码，
  这些段落在那之前对生产仍然成立。所以只修因代码移位而失效的行号引用，语义段落等上线实跑后按「实跑→改文→§7 追加行」更新。

## Verification

- 相关测试：`test_artifact_digest` / `test_host_updater` / `test_hot_update_noop_gate_1907` / `test_precheck_sync` /
  `test_remote_script_privilege_paths`（PATH shim 沙箱真跑远端脚本）/ `test_agent_priv_parser_contract` /
  `test_agent_priv_apply_code_protection` **93 passed**；hosts / abort / upgrade-gate / version-info / precheck / heartbeat /
  Ansible digest 契约 / release bundle / site install / site agents / wrapper 边界 **369 passed**
- **变异反证**（在新代码上逐项注入回退，确认守卫会红）：M1 能力判据重新要求 `apply-resources` → 3 红（含「缺该子命令的
  wrapper 仍能收敛」沙箱真跑）；M2 路由向 `execute_hot_update` 传 `resources_drift` → 红；M3 `plan_convergence` 重新读取
  resources 身份 → 2 红；M4 批量入口传资源参数 → 红
- `check:quick` **16 gates OK**

## Revisit

- **上线实跑**（部署本 PR 的 rev 后）：canary 单台 hot-update 应回 `converged`（稳态）或 `deployed`；`--force` 的审计
  `phases` 里不应再出现 `build_resources`；主机的 `resources/` mtime 不变。随后按 SOP 规则改 §2/§3 资源段，并追加 §7 校准行。
- R2（Ansible 撤资源推送与 #2166 断言）→ R3（release manifest 去分量）→ R4（Agent 心跳 / wrapper 子命令 + ADR-0037 回填 /
  DB 列 / 前端）。跟踪 #3288，台账 `host-resources-layer`。
- 实施中发现的更正：D8 R4 文中「资源相关告警」在仓内不存在。`alerts-host-resources.yml` 是控制面宿主内存告警，与本层无关；
  仓内没有资源 digest 告警规则。已在本 PR 就地勘误 ADR 该句（不升版本：更正事实，不改决策）。
