# #1792 CodeQL 聚合 check 的 NEUTRAL 误判为失败

Status: implemented
Class: bug-fix

## Decision

**把 `NEUTRAL` 计为「满足」**（而非失败或 pending），两个工具同步：

1. `scripts/ci/pr-automerge-queue.sh`：通过态判据由 `conclusion == SUCCESS` 放宽为
   `conclusion in (SUCCESS, NEUTRAL)`；
2. `tools/dev/queue_head_telemetry.py` 的 `blocking_checks()`：`conclusion in
   ("SUCCESS", "NEUTRAL")` 即 `continue`。

补 6 例回归测试（bash 侧 +3、telemetry 侧 +3）。

## 为什么这不是 #1761 的重复

#1761 修的是「**进行中**（`status=IN_PROGRESS`）被当成 missing」；#1792 是
「**`COMPLETED`+`NEUTRAL`** 被当成 failed」——两者是不同的字段形态。#1764 合入后
误报仍在继续（08:20:58–08:41:12 六条：#1784/#1785/#1786/#1787/#1788/#1790），
正是这个残余源。

## 证据链

**`NEUTRAL` 是 CodeQL 聚合 check 的固有形态**：`CodeQL` 是 GitHub 默认 setup 的父
check，其三个子分析（`Analyze (actions)` / `(javascript-typescript)` / `(python)`）
未全部完成时父 check 为 `COMPLETED`+`NEUTRAL`。实测 #1775：

| 时刻 | Analyze 子项 | CodeQL |
|---|---|---|
| 告警时（08:41） | 1 SUCCESS + **2 IN_PROGRESS** | `COMPLETED/`**`NEUTRAL`** |
| 稍后 | 3 SUCCESS | `COMPLETED/`**`SUCCESS`** |

**关键实证——终态 NEUTRAL 亦存在且被分支保护放行**：#1772 三个子分析**全 SUCCESS**、
父 check 停在 `COMPLETED/NEUTRAL`，而该 PR **已合入**（mergedAt 08:01:49Z）。
分支保护 `strict=true` 且 required contexts 含 `CodeQL`——**GitHub 自身视 NEUTRAL 为
满足态**。

这两条共同决定了判据方向：**我们的 FIFO 不得比分支保护更严**。若把 NEUTRAL 判为失败，
会出现「GitHub 认为可合入、我们的 FIFO 拒绝 `update-branch`」的**队首伪停摆**——比单纯
的告警噪音更严重（会实际阻塞队列）。

## Alternatives

- **NEUTRAL 视为 pending（等它转 SUCCESS）** → 否决：与上表第二条冲突——#1772 的
  NEUTRAL 是**终态**，永不会转 SUCCESS。视为 pending 会让队首**永久阻塞**
  （`!= SUCCESS` 恒真 → `update-branch` 永不执行）。这是本单最先排除的方向，
  也是修复前必须先查清「是否存在终态 NEUTRAL」的原因（结论：存在）。
- **只修 bash 不改 telemetry** → 否决：#1792 的成因之一是「两个工具共享同一盲点」；
  只修一个会让口径再次分叉（#1761 的教训），且 telemetry 会继续给出
  `REQUIRED_CHECK_FAILED` + `actionable: true` 的错误人工指引——这比日志噪音更危险，
  会诱导人去「修」一个正常运行的检查。
- **把 `SKIPPED` 一并视为满足** → 否决（本单范围）：当前 REQUIRED 六项内无按路径跳过
  的 check（`backend-test` 等 skipping job 不在 REQUIRED 内）；且「按设计不跑」与
  「聚合未就绪」语义不同，放开需独立裁决。已在脚本注释与本 note 标注边界。
- **仅放宽告警而不放宽 update-branch 判据** → 否决：二者共用同一 `failed_checks`
  分类；且真正的问题是 FIFO 比分支保护更严，只改告警会留下伪停摆。

## Verification

- `python -m pytest tests/test_automerge_queue_alerts.py -q` → **15 passed**（原 12 + 新 3）；
- `python -m pytest tests/test_queue_head_telemetry_bootstrap.py -q` → **5 passed**（原 2 + 新 3）；
- **红绿双向（bash）**：新测试在 #1764 版脚本上 → **2 failed**
  （`test_codeql_neutral_does_not_open_alert` / `test_codeql_neutral_does_not_block_branch_update`）；
  修复后 → **15 passed**；
- **telemetry 分类实测**：`COMPLETED/NEUTRAL` → `blocking_checks` **不含 CodeQL**（瞬态与
  终态两种均验）；`COMPLETED/FAILURE` → **仍计入**（未被一起放过）；
- `python -m tools.dev.queue_head_telemetry --self-test` → **8 组红绿样例双向通过**；
- 全量离线子集：`python -m pytest tests/ -q --ignore=（两个容器文件）` → **268 passed**；
- `bash -n scripts/ci/pr-automerge-queue.sh` → 语法通过；
- `python tools/dev/check_governance_surface.py --check` → S1–S13、S5x 全绿；
- `ruff check` 两文件 → All checks passed。

## Revisit

- **`SKIPPED` 的语义**：本单只放开 `NEUTRAL`。若将来某个 required check 出现按路径
  `SKIPPED`（设计上不跑），需独立裁决是否视为满足——它既非「未就绪」也非「失败」。
- **`NEUTRAL` 覆盖子分析失败的场景**：当前无 CodeQL 子分析失败的本地样本，无法实测
  「子分析 FAILURE 时父 check 是否仍为 NEUTRAL」。按 GitHub workflow 语义，子 job 失败
  应使父 workflow 失败（父 check 为 FAILURE），故本单按 NEUTRAL=满足处理；
  **若首次出现「子分析红而父 check NEUTRAL」且被误放行，应立即回退本单判据**
  并改为「同时校验子分析」（需 `Analyze (*)` 前缀的额外读取）。
- **两工具的判据仍是两份实现**（bash + python）：本单已同步，但第三次口径分歧出现时，
  应让告警脚本直接消费 telemetry 的判定（受 telemetry 抬头「reason_code 不得据以分支」
  约束，脚本内复用需独立裁决）。与 #1761 的同类 Revisit 合流。
