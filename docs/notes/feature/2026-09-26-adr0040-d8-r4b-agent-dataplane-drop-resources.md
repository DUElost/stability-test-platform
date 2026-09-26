# ADR-0040 D8 R4b：Agent 停报资源身份 + kind 退役 + 停写列（台账结项）（2026-09-26）

Status: implemented
Class: feature

## Decision

按 ADR-0040 v1.2 D8 的 R1–R4 顺序（2026-09-26 owner 裁决），本片（R4b）收掉数据面，`host-resources` 层至此在代码中
不再有判定、下发、上报与写入。

- **Agent 停报**：`send_heartbeat` / `HeartbeatThread` / 心跳绑定删除 `agent_resources_digest`；
  `version_info._ARTIFACT_DIGEST_FILES` 只剩 `code`。主机上残留的 `ARTIFACT_DIGEST_RESOURCES` 不再有读取方，
  `read_artifact_digest("resources")` 与其他表外 kind 一样按缺失处理并告警（#2016 语义不变）。
- **kind 退役**：契约 `collect_artifact_entries` 与控制面镜像 `_iter_payload_files` 删除 `full` / `resources`
  两个分区，`resources/**` 从此只是主机本地树的排除项。形参保留、只接受 `code`，其余值 fail-closed——旧 kind
  若被静默当成 `code`，会得到一个看似合法的错身份。**agent-code 身份逐项不变**：用 R4 之前的契约模块对同一棵
  fixture 树现算后钉住（`sha256:e5f84e35…`）。
- **停写列**：心跳路由不再写 `host.agent_resources_digest`。`HeartbeatIn` 仍显式声明该键（旧 Agent 照收忽略），
  `HostOut` 字段标弃用（OpenAPI `deprecated`），只回显停写前的存量值；前端类型加 `@deprecated`。
- **台账**：`host-resources-layer` 结项，evidence 列出 R1–R4 的 PR。R4 原文要求「删列另起迁移，留一个版本窗口」，
  这个窗口本身是过渡态，登记为新条目 `host-resources-digest-column`（exit = ADR-0040#D8，due 2026-12-31）。
  ADR-0040 §11 补实施记录。

## Alternatives

- **本片直接删列**：旧 Agent 在全机队热更新之前仍会上报，外部读者（站点工具、旧前端）也可能还在读这个字段。
  删列必须走迁移，而迁移回滚需要 downgrade。按 D8 原文保留一个版本窗口，另起迁移处理；弃。
- **连同 `kind` 形参一起删掉**：`tools/release`、`tools/site_config`、`tools/ansible` 的调用方都传 `kind="code"`，
  删形参要跨三个工具面同步修改，而收益只是少一个单值形参；弃，改为只接受 `code` 并 fail-closed。
- **`HeartbeatIn` 现在就删掉该键**：模型默认忽略多余字段（实测多余键不报错），删了旧 Agent 也不会 422；但保留显式声明
  更清楚地表达「照收忽略」的兼容语义，并且能和删列迁移同批撤掉，一次收口；弃，留到删列时一起撤。

## Verification

- `backend/agent/tests/` 全量 **2206 passed**；控制面相关 8 个测试文件 **177 passed**
- **旧源码反证**：把 8 个源文件换回 origin/main 版本后，新守卫全红：心跳不带资源身份（线程 kwargs 与真实 POST 载荷两层）、
  `read_artifact_digest("resources")` 读不到、agent-code 身份钉值、退役 kind fail-closed、资源内容不影响 code 身份、
  心跳不写列（存量值保留）、HostOut 对新上报回 `None`
- `check_transitions`：10 条 / 5 在途；`check:quick` **16 gates OK**；`tests/` 全量 **1928 passed / 18 skipped**

## Revisit

- **上线激活**（运维，按序）：① 控制面部署含 R1–R4b 的 rev（部署后 bundle 不再带 `backend/agent/resources/`）；
  ② `update_agent.yml` 给 fleet 装新 wrapper（R4a；必须在 ① 之后）；③ 全机队热更新，agent-code digest 变化，
  Agent 停报资源身份。另外，control-plane-deploy SOP 中资源层判定的段落按 SOP 规则随这次实跑校准。
- 台账 `host-resources-digest-column`：版本窗口过后另起迁移删列，同 PR 撤掉 `HostOut` / `HeartbeatIn` 字段与前端类型。
