# 生产只读诊断：目标取自站点权威配置、身份只用 `stp_ro`，不可确认即停（#3538）

Status: implemented
Class: bug-fix

## Decision

**问题本质是权威文档自相矛盾，不是缺一道 gate。** ADR-0051 D6（2026-09-25 实切）之后，
生产 env 真身是站点文件，仓根 `.env.backend` 降级为纯 dev 配置，`control-plane-deploy`
skill 已写明「生产改值 / 诊断取数一律经站点文件」；但 `prod-db-readonly-diagnose` skill
与 `production-diagnostics.md` 的凭据来源表、可复跑核对说明仍把仓根 `.env.backend` 当生产
源。执行者按 skill 走，读到的是可能与生产漂移的 dev 配置，诊断结论可能落在另一个库上。
同一文档内还自相冲突：红线要求手工查询只用 `stp_ro`，来源表却指向 `stp` 所在的连接串。

本单统一口径（只改措辞，不改代码 / schema / 应用运行账号）：

1. **目标**取自站点当前权威配置（ADR-0051 D6），只取库地址 / 库名，不取其中应用账号口令；
   skill 与 SOP 只**引用** canonical source（ADR-0051 D6、`control-plane-deploy` skill §1、
   `production-diagnostics.md`），不再复制易漂移的路径 / DSN 事实；
2. **身份**：人工 SQL 诊断只用 `stp_ro`（口令由操作者在库侧显式提供），连接带
   `application_name`（沿用既有 `diag-<用途>` 约定）；
3. **fail-closed**：目标无法从站点配置确认、或 `stp_ro` 不存在 / 无法登录，即停止并报缺，
   不回退 `stp` / `postgres` / 仓根 env；
4. 管理 API 的 `AGENT_SECRET` 与管理员凭据同样改指站点配置。

涉及文件：`.claude/skills/prod-db-readonly-diagnose/SKILL.md`（frontmatter 不变）、
`docs/operations/production-diagnostics.md`（§安全边界、§凭据来源、状态机核对说明）、
`docs/operations/2026-09-19-inotifyd-only-e2e-procedure.md`（仅 §1 选机的只读 SELECT 一处）。
`tests/test_diag_readonly_role_contract.py` 钉住的红线句式（「手工查询不得用」段、
`ON_ERROR_STOP=1`、「不要退回」、`stp_ro` 出现次数）保持不变。

另 3 份文档（`device-log-event-recovery.md`、`center-storage-hardlink-dedup.md`、
`honor-flash-runbook.md`）逐份判定为受控写操作面，**本单不改**，判定见 PR 正文；相关
前序：[诊断只读角色 `stp_ro`（#2632 缺口②）](./2026-09-19-diag-readonly-role-2632.md)。

## Alternatives

- **新增通用扫描 gate（grep「仓根 `.env.backend` 当生产源」）**：放弃。该字串在开发、
  测试、部署构建与受控写操作文档里大量合法出现（本仓 docs / tools / tests 命中数十处），
  规则要么误报泛滥、要么白名单比规则还长；而且本单要治的是「取源口径写错」这一类语义
  问题，字串扫描抓不到「站点配置」被换个说法写错。最小方案是让权威文档一致 + skill 只
  引用不复制，由已有的 `test_diag_readonly_role_contract.py` 守红线核心句式。
- **把 4 份命中文档一律机械替换成 `stp_ro` / 站点配置**：放弃。其中 3 份是受控写操作
  （DLE 补录 INSERT、硬链接去重执行、管理 API 建 Plan）——写操作需要应用侧凭据，
  `stp_ro` 是 `default_transaction_read_only=on`，直接替换会让流程跑不通，或诱导人为
  绕开只读闸；写路径的来源口径是否对齐 D6 属另一类问题（凭据与目标确认），应单独判定，
  不在「只读诊断」需求内顺手改。
- **在 skill 里写明站点 env 的具体路径与 DSN 形态**：放弃。路径与取数方式会随站点 / 发布
  形态变化（ADR-0051 后续多站点续集），复制一份必然再次漂移，正是本单要消灭的形态；
  引用 D6 与 deploy skill §1 即可。

## Verification

- `python3 tools/dev/check_governance_surface.py --check --base origin/main` 与 `--self-test`：
  见 PR 正文实际结果。
- `tests/test_diag_readonly_role_contract.py`：云端容器无 pytest，仅手工 import 并调用其中
  `test_sop_points_at_this_script_and_names_the_role_where_it_matters`（唯一读取本次改动文档的
  用例）通过；整文件 pytest 运行 **pending**（需在有依赖的环境复跑）。
- 残留复扫：对改动范围内 6 份文档 grep `.env.backend`，改前 / 改后逐处对照，结果见 PR 正文；
  剩余处均为「说明它不是生产源」的否定 / 禁令表述，或受控写操作面的未改动引用。
- `python scripts/run_gates.py check:quick`：云端无依赖，**pending**。
- 未在生产上执行任何命令；本单不含真实 DSN / token。

## Revisit

- 写路径文档（`device-log-event-recovery.md` 的 DLE 补录、`honor-flash-runbook.md` §2 的
  管理 API 鉴权）与 `docs/development/testing.md`「生产唯一 env 源是仓库根 `.env.backend`」、
  `tools/ansible/README.md` 等处仍是 D6 之前的口径，宜另单按「受控写的凭据与目标确认」统一，
  而不是套用只读诊断规则。（`center-storage-hardlink-dedup.md` 已在发布根下 source，
  经树内 symlink 即站点 env，口径本就一致。）
- 若站点权威配置出现单一、可引用的机器可读入口（多站点续集，ADR-0041），skill 与 SOP 应
  改为直接引用该入口。
