# ai_work READY 判据改用 required checks 权威（#1211）

Status: implemented
Class: bug-fix

## Decision

`tools/dev/ai_work.py::derive_integration` 原实现从 `gh pr view
--json state,statusCheckRollup` 取「**已完成**（`status == COMPLETED`）的检查
conclusion 集合」判绿：

```python
conclusions = {c.get("conclusion") for c in rollup if c.get("status") == "COMPLETED"}
ok = conclusions <= {"SUCCESS", "SKIPPED", "NEUTRAL"} and conclusions
```

于是只要已有若干检查完成且全绿，**仍处于 pending/queued/in_progress 的 required
check 被完全忽略**，直接派生 `READY`——与 Execution Contract §3.1「`READY` =
required checks 全绿」不符（pending ≠ 全绿）。这是实现相对既有契约的漂移，不改契约
语义。

改为以 branch protection 的 **required checks 集合**（GitHub 权威）为唯一判据：

1. `gh pr view <N> --json state` 只判 MERGED/CLOSED/开放；
2. 开放 PR 调 `gh pr checks <N> --required --json name,state`；
3. 新增离线纯函数 `required_checks_all_green(checks)`：集合非空且每项 `state ∈
   {SUCCESS, NEUTRAL, SKIPPED}` 才 `True`；任一 pending/queued/in_progress/
   failure → `PR_OPEN`；
4. required 查询失败/空集（分支未保护、API 不可达）→ 走「GitHub 不可用降级」
   （保留旧值 + `refreshed=False`），不虚报 READY。

影响面：`tools/dev/ai_work.py`（函数 + `--self-test`）。所有 `derive_integration`
调用点（status/update/heartbeat/resume 守卫/drift）共享新判据。

**复查 follow-up（2026-09-10，PR #1235 复查）**：上述「观测不可用 → 降级」在
「PR 刚创建、尚无任何 check 上报」的秒级窗口会命中（`gh pr checks` 对无上报分支
返回 `no checks reported on the '<branch>' branch`），此时 `finish --pr N` 会把
cached（新记录 = NO_PR）原样写回，违反 §3.3 T2「登记新号 → PR_OPEN」。修正：
新增纯函数 `seed_registered_pr(cache)`，`cmd_finish`/`cmd_update` 登记 `--pr` 时
先播种（NO_PR/空 → PR_OPEN，更精确的既有观测不覆盖），随后 `derive_integration`
在观测可用时照常升级/降级。降级路径因此不再产生「已登记 PR 却是 NO_PR」的记录。

## Alternatives

- 继续用 rollup 但排除非 required 检查：rollup 不携带 `isRequired`，无法区分
  required 与可选（如 backend-test/docker-build 常年 skipping），会误判。
- 硬编码 required 名单（lint/CodeQL/pr-typecheck/pr-compileall/pr-agent-tests/
  pr-migrate-empty-db）：与 CI 配置重复、易漂移，branch protection 才是权威源。
- 依赖 `gh pr checks` 退出码判绿/待定：`--json` 导出路径在退出码逻辑**之前**
  `return`（gh 2.98.0 `pkg/cmd/pr/checks/checks.go`；help 的「8: Checks pending」仅
  适用表格/watch 路径），故 FAILURE/PENDING 时退出码仍为 0——退出码只可用于
  「真错误」判定（无上报分支/网络/鉴权），不可作判据。

## Verification

- 离线红绿：`python tools/dev/ai_work.py --self-test`（新增
  `required_checks_all_green` 用例：全绿→True；空/None/PENDING/FAILURE/QUEUED→False）。
- 实网抽样（改后直接调用）：#1149(merged)→MERGED、#1207/#1208/#1209(全绿)→READY、
  #1205/#1202(required FAILURE)→PR_OPEN、**#1210(required IN_PROGRESS)→PR_OPEN**
  （修复前该例会被虚报为 READY）。
- `ruff check tools/dev/ai_work.py` 通过。
- 复查 follow-up 离线红绿：`--self-test` 新增 `seed_registered_pr` 用例
  （None/""/NO_PR→PR_OPEN；READY/CLOSED 原样保留）。
- 复查来源与证据：[PR #1235 复查评论](https://github.com/DUElost/stability-test-platform/pull/1235#issuecomment-5614891000)
  （gh 源码短路点 + 「无 check 上报」窗口推演）。

## Revisit

若 GitHub 端为 main 之外分支（或 fork）无 required checks，`gh pr checks --required`
报「no required checks reported」，derive 走降级回退**旧值**（fresh 记录为
NO_PR），不会宣报 READY；这些来源本就不进 auto-merge 队列，可接受。若将来需要
区分「无 required 配置」与「观测失败」，可再引入显式分支保护查询。
