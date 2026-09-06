# ai_work P2 Adapter：whoami 与 heartbeat 集成指引

Status: implemented
Class: feature

## Decision

交付 ADR-0034 §2.7 P2 的 Adapter 基元（上下文供给，非路由）：

1. **`ai_work.py whoami`**（新命令，严格只读）：按 worktree（默认当前 worktree，realpath 匹配）定位自身 Execution，输出自身状态 + **入向 overlap**（其他在窗记录的 effective scope 覆盖本 worktree scope 的部分）——各 Harness 会话启动时由 agent 执行，即「启动时知晓自身 Role」的最小实现；
2. **heartbeat = `update` 的语义别名**（`aliases=["heartbeat"]`）：无 `--scope/--pr` 的 update 即纯心跳（刷自身 last_seen + GitHub reconcile）——不新增写路径，P2 wrapper 定时调用即满足契约 §2.5「wrapper 提供 heartbeat 后 last_seen 升格」的接入点；
3. **`harness-adapters.md` 增「P2 Adapter：会话启动动作」节**：四时机动作表（启动 whoami / 长会话 heartbeat / 开 PR update / 停止 finish）+ 只读语义 + #857 的人工过渡纪律（Claude 子目录改共享层前先读根 AGENTS.md）。

## Alternatives

- **独立 `heartbeat` 子命令**——放弃：与无参 `update` 完全同义，别名即可（避免两条写路径维护同一逻辑）。
- **whoami 自动刷新 last_seen**——放弃：违反 §2.5「观察不改变被观察状态」（R4）；启动自报身份应显式 `heartbeat`。
- **本 PR 顺带做 #857 根供给（Claude SessionStart hook）**——放弃：涉及 `.claude/settings.json`（共享元文件）且影响所有 Claude 会话行为，单独评审更稳；本 PR 先落人工过渡纪律。

## Verification

- `--self-test` 12 规则红绿双向通过（离线）；
- 端到端冒烟：declare（含 role）→ `whoami`（默认当前 worktree，drift 双清单正确）→ `heartbeat --id`（=update 无参，刷 last_seen+reconcile）→ 冒烟记录已清空；ruff 通过；
- 治理门禁 `--check` 全绿（S6 harness-adapters.md 100 行预算内）。

## Revisit

- P2b：#857 根供给（SessionStart hook 候选）、Cursor 双份加载去重评估、cwd 深度 × Harness 加载矩阵终验（ADR §2.7 P2 验收项）；
- wrapper 心跳的定时形态（hook vs 外部 loop）在 P2b 定。
