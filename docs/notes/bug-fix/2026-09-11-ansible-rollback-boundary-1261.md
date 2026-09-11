# Ansible 升级回滚边界文档化（#1261 / R14-F15）

Status: implemented
Class: bug-fix

## Decision

本质问题（R14-F15 设计风险）：`update_agent.yml` 的 rescue 只恢复三项（agent
代码、`agentctl`、service unit），而升级过程中 `.env`（`API_URL` 回写）已修改、
Python 依赖已原地更新、schema / VERSION 工件已刷新——配置或依赖不兼容时，旧代码
回滚后仍可能无法运行。

裁定（2026-09-11 用户裁决）：**文档化回滚边界**（验收两条路径中的文档路径）。
runbook §5 `update_agent.yml` 写明：

- 自动回滚范围：代码 / `agentctl` / service unit（与 rescue 段逐项对应）；
- 不自动回滚项与理由：
  - **`.env`**：host-local 配置，只按需回写 `API_URL`；整份回滚会覆盖运维在本机
    的本地修改，且旧 API 地址未必正确；
  - **Python 依赖**：venv 原地 `pip install`，回滚需 venv 快照级方案（未实现）；
    纪律 = 依赖变更保持向后兼容，不兼容变更走版本化路径；
  - **schema / VERSION 工件**：与 API 热更新同语义、有意不回滚（#1247）；极端场景
    按 #1247 的 Revisit 人工处置；
- 人工介入入口：rescue 失败消息（`rolled back to <backup_dir>`）+ 错误日志尾部。

## Alternatives

- **实现 `.env` 快照 + rescue 恢复**——放弃：`.env` 是 host-local 语义（整体回滚
  会覆盖运维本地修改），且"旧代码 + 旧 API_URL"未必优于"旧代码 + 新 API_URL"；
  在窗 #1342（PR）正改 API_URL 回写路径（同文件同段），并行实现有真实冲突面；
- **完整回滚（含依赖 venv 与 schema）**——放弃：venv 快照成本高，且与 #1247
  「schema 有意不回滚」的既定裁决冲突，需先修订 ADR，不适合本批；
- **仅保留现状、不写文档**——放弃：验收明确要求"文档写明回滚边界"，运维无法据此
  判断兼容性责任边界。

## Verification

实际运行（worktree `/tmp/stp-1261`，基于 `origin/main`）：

- `check:quick` → 7 gates 全绿；
- 文档 ↔ 行为逐项对照：runbook §5 的三项回滚对应 `update_agent.yml` rescue 段
  （:559–620）；"不自动回滚"三项与 playbook 实际行为一致（`.env` 仅 lineinfile
  `API_URL`；依赖原地 pip；schema copy 不回滚）；
- 引用核对：#1247 的 schema 不回滚裁决在主干（note + playbook 注释），本 note 与其
  Revisit 呼应。

未完成（pending）：

- 纯文档改动，无真机验证项；若未来转向实现路线（见 Revisit），需隔离环境做
  失败注入演练。

## Revisit

- 若出现"依赖不兼容导致回滚后仍不可运行"的真实事故，升级为实现路线：优先
  requirements 双版本兼容策略，其次 venv 快照；
- 若 `.env` 回写路径后续变化（#1342 已在改），同步复核本文档的 `.env` 描述。
