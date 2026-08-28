# 复核远端树必须 fetch 后验证 origin/main，不能用本地 HEAD 断言

Status: implemented
Class: process

## Decision

**复核/审查类工作断言「某文件在 main 上已（未）修复」时，一律基于
`git fetch origin && git show origin/main:` 的树内容，禁止用本地
`HEAD:` 或工作树代替。**

背景事件（2026-08-27~28，FRONTEND_UI_REVIEW_2026-08-27.md 复核轮）：

- 第三方复核用 `git show HEAD:frontend/src/pages/issues/IssueTrackerPage.tsx`
  断言「C2 页签未修复、期间无任何新提交改变这些文件」——但本地 HEAD
  落后 `origin/main` 5 个提交（auto-merge 仓库合入速度快于本地 fetch 频率），
  其中 `2b84573`（PR #487）正是 C2/C3 的合入提交（`add7f24` 一致性收敛第三批）。
- 同一轮还以「`App.tsx` 仍整 App 包裹」判 H5'（ErrorBoundary 下沉）未落地——
  实际 origin/main 的 `AppShell.tsx` 已有 5 处路由层 ErrorBoundary，
  `App.tsx` 顶层保留是双层兜底的设计本意（Provider/Shell 级最后防线），
  并非未下沉。两层误判同源：**基于过期本地树下结论**。

规则（适用于任何「与 main 对比」的断言）：

1. 先 `git fetch origin`（可顺带 `git rev-parse HEAD origin/main` 对比指针，
   落后即停下手头断言）；
2. 文件内容以 `git show origin/main:<path>` 为准，行号以该树为准；
3. `git log --oneline HEAD..origin/main` 先列出本地缺失的提交，
   把「报告条目 → 合入提交」对上再下结论；
4. 断言「无新提交」这类负向表述必须包含 fetch 动作，否则不可信。

涉及文件：`docs/reviews/FRONTEND_UI_REVIEW_2026-08-27.md`（复核轮记录，
第三次复核已按 origin/main 修正并采纳）。

## Alternatives

- **`git pull` 后再查**：会动工作树/工作区，复核场景（只读、可能有未提交
  改动）不宜；`git fetch` 只更新远端引用，零副作用。
- **只信 GitHub 网页 / gh CLI**：对单个文件可行，但对「逐文件逐行核验」
  场景效率低，且 gh 的树也需与本地提交 SHA 对齐——不如 fetch 后本地化。
- **复核前不 fetch、依赖「我上周拉过」**：被本事件直接证伪，auto-merge
  仓库的合入节奏不可假设。

## Verification

- `git fetch origin` → `git log --oneline HEAD..origin/main` 列出缺失提交；
- `git show origin/main:frontend/src/components/ui/state-tabs.tsx` 等逐文件
  存在性 + `git show origin/main:frontend/src/pages/issues/IssueTrackerPage.tsx
  | grep -c StateTabs` 引用计数；
- 与 PR 合并记录（`gh pr view 487`）核对提交归属。

## Revisit

- 若仓库改为禁止 auto-merge 或本地全量镜像 origin（fetch 频率≈0），
  本规则可放宽；否则维持。
- 建议后续复核轮把「fetch + origin/main 验证」写入 §7 方法局限段落，
  与 B 轨「重建复跑证伪」并列为本项目复核 SOP 两条硬性动作。
