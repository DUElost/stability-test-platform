# 收口判据单页速查表（registry / worktree / 本地与远端分支）

Status: implemented
Class: process

## Decision

新增 [`docs/development/closure-cheatsheet.md`](../../development/closure-cheatsheet.md)：
把四层收口判据（A registry 记录 / B worktree / C 本地分支 / D 远端分支）压成一页表格，
并在 [`docs/README.md`](../../README.md) 的任务表登记一行、在
[`repository-workflow.md`](../../development/repository-workflow.md) 的「Registry 批次收窗」
段加交叉链接。

三条定位原则（写进文档头部）：

1. **不新增规范**：每行都标「权威源」列（execution-contract §3.1–§3.4 /
   repository-workflow 各节），冲突以权威源为准——速查表只是**索引与顺序**，不是第二份契约；
2. **顺序固定**：`update --all`（+ 单条 `update --id` 兜底 >400 PR）→ `drift --strict`
   → `status --risk` → 才动本机资源；顺序反了会把「已合入」误判成待清理；
3. **共享态与自留地分开**：远端删除先出清单+判据交人工确认；本地 worktree/分支自行处置；
   registry 记录按 A 收口——三者判据不互相顶替。

内容取自 2026-09-14/15/20 三次实测收口（68→6 worktree、62→5 本地分支、远端 4→1）与
本会话的两个实例（fix-2706 的 class ② 等价核验、远端补删三例外）。

## Alternatives

- **只留在会话记忆（`project_local_tooling.md` §11/§12）**：弃——记忆是单 harness 的
  私有面，其他 harness/新会话看不到；而收口是**跨会话的共享卫生**。文档为正本、记忆留
  操作细节（本机路径、代理试路等）。
- **把速查内容并进 `repository-workflow.md`**：弃——该文已承载 PR/CI/Note/远端四大主题；
  再塞四层判据会让「按需入口」膨胀，违反 S6 的启动上下文预算取舍。
- **并进 `execution-contract.md`**：弃——契约只管 Registry 与 scope 语义，worktree/分支/
  远端删除是操作面，写进契约会把「权威语义源」和「操作速查」混在一处。
- **新开一个 gate 校验速查表与权威源不漂移**：暂弃——两处都是表格文本，机械对拍成本高、
  收益低；用「冲突以权威源为准」声明 + S2 链接存在性校验兜底，出现漂移事故再升级。

## Verification

- `python scripts/run_gates.py check:quick` → 见 PR（含 S2：新增的两条相对链接必须解析成功）；
- 结构自证：速查表每条判据与 `execution-contract` §3.2 真值表、`repository-workflow`
  「远端分支生命周期与补删」逐条对应（人工核对，本次会话的两个实例即端到端样例）。

## Revisit

- 若出现「速查表与权威源不一致」的实际事故，按 ADR/契约优先修正速查表，并考虑加机械对拍；
- 若远端删除改由自动化执行（脚本化补删），把「人工确认」一条改写为「dry-run 输出 + 确认」。
