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

## 更正记录（2026-09-20，同日核对轮）

首版合入后做了「速查表 × 权威源」逐条核对，发现 6 处不一致（上节「结构自证」的措辞
过宽，实际只覆盖了 A 的真值表与 D 的骨架）。按「冲突以权威源为准」原则**先修权威源、
再对齐速查表**：

| # | 问题 | 处置 |
|---|---|---|
| 1 | 头部称「每条判据的权威源见『权威源』列」，四表均无该列 | 头部改为「各节的权威源见**节尾引用**」 |
| 2 | A 漏契约的缓存失效前置 `risk = 真值表 ∧ ¬landed`（`status` 会标 `stale-cache` 并提示 `update --id`） | A 补「缓存失效（`landed`）」行 |
| 3 | B（worktree）/C（本地分支）判据只存在于会话记忆，而 `repository-workflow` 已引用「本地清理规程」——该规范在仓库中**不存在**（悬空引用） | **上收**为新节 `repository-workflow.md` §worktree 与本地分支清理（两表 + 保留面 + 前置顺序）；速查表 B/C 改为指向该节；悬空引用改指该节锚点 |
| 4 | D ②（改写合入）写「判据同 C（`+`=0）」——squash/amend 会改 patch-id，`git cherry` 为 `+`；**权威源 :79 也把「改写合入」列进 `+`=0 分支**（同一混淆） | 两处同修：rebase 合入（patch-id 不变）⇒ `+`=0 可删；squash/amend 改写 ⇒ 核 PR 已 merged + 逐文件等价 |
| 5 | A 引用范围 §3.1–§3.4 过宽（§3.4 是 declare 查重） | 改引 §3.1–§3.3 |
| 6 | 权威源陈旧：`repository-workflow` 称 `update --all` 刷「**全部**已登记 PR」，实测只扫最新 400 | 文档补「覆盖面边界：最新 400，更早用 `update --id` 兜底」 |

更正后两侧一致：速查表 A–D 的每条判据都能在契约 §3.1–§3.3 或 `repository-workflow`
（§worktree 与本地分支清理 / §远端分支生命周期与补删）找到对应原文。
