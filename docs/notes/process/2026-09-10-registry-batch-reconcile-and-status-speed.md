# Registry 批量 reconcile（update --all）+ status 提速与在窗视图（#1234）

Status: implemented
Class: process

## Decision

契约 **v1.11** + 实现同 PR，三项（承接 #1232 的正确性修复，消除其根因）：

1. **`update --all` 批量 reconcile（契约 §3.3）**：**一次** `gh pr list --state all
   --json number,state` 取全部 PR 终态，**一次九步原子写**刷新各记录
   `integration_cache`（只处理 **MERGED/CLOSED** 终态）。
   - **不刷任何 `last_seen`**：批量命令没有 execution identity，不冒充心跳（§3.3
     identity 语义）；只写 `integration_cache`/`observed_at`/`updated_at`。
   - **不在批量侧重算 READY**：`gh pr checks --required` 是逐 PR 调用；READY 派生
     归单记录 T5（#1211 正在改的正是该判据）——避免同一判据出现**两套实现**。
   - GitHub 不可达 → 整体跳过（沿用 §3.3 降级：不猜测、不推进终态）。
2. **`status` 提速（纯实现细节，§10 豁免）**：`derived_paths` 按记录 memoize。
   成对 overlap 循环此前对**每条记录反复重算**（O(n²) 次 git 子进程），是强制前检
   的主要时延来源。**默认输出逐字节不变**（同一 registry 下与 v1.10 版 diff 为 0 行）。
3. **`status --risk`（附加视图）**：只列在窗记录 + overlap 提示；**默认行为与退出码
   不变**（不带 `--risk` 时与 v1.10 输出完全一致）。
4. **契约 §2.1 命令清单改为「以 `--help` 为准」**：原清单只写
   `declare/status/update/finish/resume`，早已漏掉 `whoami`/`heartbeat`/`drift`
   ——本次要加 `--all` 时发现清单本身是漂移的，故不再逐个罗列，改为「清单看 `--help`，
   契约只约束语义」。

## Alternatives

- **逐条 `update` 刷全部记录**：123 条 = 123 次 `gh pr view`（分钟级）；批量单次调用
  实测 1.8s、整条命令 2.77s（含九步写）。
- **批量也重算 READY**：需要逐 PR `gh pr checks --required`，等于没省调用；且会与
  #1211 的 required-checks 判据修复形成双实现——**拒绝**，READY 仍归单记录 T5。
- **把 `--all` 做成独立子命令 `reconcile`**：语义与 T5 完全重合，多一个子命令只增加
  契约面；改为 `update` 的选项，且与 `--id` 互斥（批量无 identity）。
- **`update --all` 顺带刷 `last_seen`**：会让批量命令冒充 123 条记录的心跳，
  `liveness`/STALE/僵尸判据全部失真——**拒绝**，并在契约 §3.3 明文固定。
- **改 `status` 默认只显示在窗记录**：是 CLI 输出语义变更，会影响所有既有读法
  （含脚本与 Harness 前检）；改为**附加** `--risk`，默认不变。
- **为腾预算而抬 S6 或改无关章节**：v1.9 Revisit 已定「再次逼近 26KB 优先契约分层
  而非抬预算」；本次只**压缩本系列自己新增的文本**（把理由移入 Note），并在
  #1234 请求「契约分层 vs 抬预算」的用户裁决。

## Verification

- **批 reconcile 实测（真实 registry，124 条记录）**：`update --all` 2.77s 刷新
  **22 条终态**（12×`READY→MERGED`、10×`PR_OPEN→MERGED`），GitHub 侧共 400 个 PR
  一次取回；
  - **不变量核对**：前后快照对比 `last_seen` **改动数 = 0**（必须为 0），
    `integration_cache` 改动数 = 22、`observed_at` 更新数 = 22（与刷新集完全一致）。
- **`status` 实测**：5.55s → **2.05s**（同 registry、同快照；44 条在窗时为 32.3s）；
  `status --risk` 296 行 → **51 行**、**0.61s**——强制前检因此可读可用。
- **输出等价性**：v1.10 版与 v1.11 版 `status` 全量输出 diff **0 行**（memoize 透明、
  默认视图未变）。
- `python tools/dev/ai_work.py --self-test` → 通过；新增批量纯函数断言：
  终态刷新集正确（`stale-merged`/`stale-closed` 命中）；**PR 仍 OPEN 不入集**（READY
  不在批量侧重算）；已终态**幂等**；无 `pr_number` / 未出现在映射中**跳过**；
  `fetch_pr_states` 在不可用 cwd 下返回 `None`（降级，不触网）。
- `python tools/dev/check_governance_surface.py --check` → S1–S12 全绿；契约
  **25951/26000 bytes**（余 49）、211/260 行；`ruff` 通过（过程中 ruff 抓到一处
  `--risk` 路径的 use-before-define，已修）。

## Revisit

- **契约空间告急（余 49 bytes）**：#1234 的待裁决项（a 契约分层 / b 抬预算）仍是
  下一次契约改动的**前置**。本版能落地是因为把新增文本压到了纯规范口径（理由进
  Note）；再压缩会开始伤及规范精度，届时必须分层。
- **`status` 仍有优化余量**：当前对**每条被显示记录**计算 derived（124 条 ≈ 2s）；
  若 registry 继续增长，可改为「--risk 默认路径只算在窗记录」或引入一次性
  `git worktree list` 批量取 diff——属性能面，不改变语义。
- **批量 reconcile 与 #1211 的分工**：若将来要求「批量也刷新 READY」，须先让
  required-checks 判据收敛为**单一实现**（当前在单记录路径），再谈批量化。
- **`drift` overlap 仍为顶层目录粒度**（ADR §2.7 P3 明文）：本版未动，收窄需独立裁决。
- **`--self-test` 是跨 PR 的合并热点（实测）**：#1235（#1211）与 #1233/#1236（#1232/#1234）
  合并时在 `ai_work.py` 冲突，**冲突面只在 self-test 内**（源码区不冲突）——双方都把断言插在
  「`derive_liveness` 之后」与「尾部 `if failures:` 之前」这两个最自然的落点，另加同一行成功
  消息字符串。本次把断言组移到按主题就近的位置以降低耦合；若再次出现同类冲突，下一步应把
  self-test 拆成**按主题的独立断言函数**（`_selftest_xxx()` + 单行调用），而不是继续挪位置。
  解析配方留痕于 [#1235 评论](https://github.com/DUElost/stability-test-platform/pull/1235#issuecomment-5614227757)。
- **规则 cron / git hook 化**：本版只是把批量核销变便宜（秒级），仍由人/会话触发。
  若实测「收窗时仍常忘记跑」，再议自动触发（属新机制，需独立裁决）。
