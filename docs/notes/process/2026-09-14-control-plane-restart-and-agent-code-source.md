# 控制面重启与控制面检出：两个「隐性前提」写成文档

Status: implemented
Class: process

## Decision

在一次「后端修复已合入但界面没变化」的排查中，连续踩到两个**没有写在任何地方**的前提，
本次把它们落到 `deploy/control-plane/README.md`（与 `deploy/postgres/README.md` 同一形态）：

1. **控制面没有自动重载** —— 把检出推进到新的 `main` 之后，进程内仍是旧代码。
   实证：`git merge --ff-only origin/main` 后，`GET /plan-runs/{id}/watcher-summary`
   仍返回旧结果，重启进程才变化。此前只能靠"现象不对"反推，容易误判为修复无效。
2. **主机 Agent 的代码跟「控制面检出」走**（`host_updater._AGENT_SOURCE_DIR` 指向本检出的
   `backend/agent/`），不是跟远端分支走。因此正确顺序是「先推进检出 → 再 hot-update」，
   否则 hot-update 会把旧代码推回主机，且返回仍然是 `ok: true` / `service restarted`。

文档同时给出：启动命令（`venv/bin/uvicorn backend.main:app`）、配置来源（`.env.backend`）、
重启步骤与验证方式（进程与接口两层），并标注「本仓库当前没有启动脚本」。

## Alternatives

- **写一个 `restart.sh`**：更"一键"，但仓库里没有任何控制面脚本先例（`deploy/` 下都是
  README + compose/yaml），且脚本涉及 kill 共享进程、pid 管理、日志落盘等未验证细节 ——
  在不重启进程的前提下我无法实测它，所以先交付**可核对的文档**，脚本待需要时再补。
- **只在 PR/评论里口头说明**：信息会随 PR 沉底，下一个人仍会踩。
- **改代码让其自动重载**：生产环境自动重载本身有风险，且属部署方式变更，不在本次范围。

## Verification

- 文档里每条断言都对应一次实测：`ps -ef`（进程形态与 `PPID=1`）、推进检出后接口行为未变、
  hot-update 返回 `ok/deployed` 但 `agent_code_revision` 未翻转、推进检出后同一命令才翻转并
  附带 `artifact_digest` 变化（`b13c2183…` → `900e6ff3…`）。
- `gov-surface` S1–S13 通过（仅文档新增，无代码路径变更，故 `test_impact=none`）。
- **未执行**任何进程重启：文档中的重启步骤是「待运维按此执行」的说明，不是我已验证的操作。

## Revisit

- 若控制面改为 systemd 托管，应把「如何重启」收敛为 `systemctl restart <unit>`，并删除文档里
  的 `kill <pid>` 手工步骤。
- 若能接受，建议后续补 `deploy/control-plane/restart.sh`（带 pid 检查与健康探测），
  但需在有人可执行重启的窗口内验证后再合入。
- 「Agent 代码跟随检出」这一耦合本身值得评估：它意味着**控制面检出的状态会直接影响主机部署**，
  多会话并行时容易互相干扰（本次即遇到检出有他人未提交改动）。若要解耦，可改为按 commit
  归档 agent 树后从归档发布。
