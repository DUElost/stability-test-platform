# DLE 归档判据定案：无 `merge_result_xls` ⇒ `REMOTE` 是事实终态（#2150 裁决 B）

Status: implemented
Class: architecture

## Decision

**裁决 B 成立**：DLE 行的 `REMOTE → ARCHIVED` 由 run 级 extract 唯一驱动，而 extract 以
本轮存在 `merge_result_xls` 为前置——**该 run 无 merge 产物时，`REMOTE` 是事实终态**。

判据与依据写入 [`docs/design/2026-scan-upload-merge-contract.md`](../../design/2026-scan-upload-merge-contract.md)
的「DLE 归档（`REMOTE → ARCHIVED`）的触发与判据」一节（含排查口径）。

### 证据（2026-09-16 生产只读：`SELECT` + `ls`）

| run | status | `merge_result_xls` | `jira/{run}` | 该 run 的 UNIVIEW 行 |
|---|---|---|---|---|
| 400 | SUCCESS | **有** | **存在** | **2 行 ARCHIVED**（链路对 UNIVIEW 是通的） |
| 401 / 410 / 411 / 412 | FAILED | 无 | 不存在 | 停在 REMOTE（**410/411/412 是当天新 run**） |
| 393 | — | 有 | — | 1 行 REMOTE（`updated_at` 晚于其 extract ⇒ late-arriving） |
| 394 | — | 无 | — | 3 行 REMOTE |

代码链（与本单三个待查问题的答案）：

1. **触发条件**：`mark_events_archived` 全仓只被 `dedup_extract.run_extract_sync` 调用
   （`dedup_extract.py:348`）；extract 虽由 merge 链 best-effort 入队
   （`saq_tasks.py:914`），但**无 `merge_result_xls` 时第一步就返回**
   （`dedup_extract_skip_no_merge`），归档调用到不了。时间窗口不参与；
2. **run 401 未归档的原因**不是窗口边界，而是「该 run 的归档面根本没开」（无 merge 产物）
   ——与 410/411/412 同型；
3. 「late-arriving 本就不该归档」只覆盖 393 一类（extract 已跑完后才变 REMOTE）。

### 判据写成「无 merge 产物」，不写成「`REMOTE` 即终态」

粗口径会掩盖**真卡住**：run 400 证明有产物时归档是通的。排查口径因此是两段式：
先看该 run 有无 `merge_result_xls`（DB 产物行或中心存储 `jira/{run_id}/`），
**有产物而未归档才算异常**。

存量（核对时点）：10 行 REMOTE 中，393 属 late-arriving、其余属「无 merge 产物」；
**没有一行**属于「有产物却未归档」——即不存在需要紧急 replay 的样本。

## Alternatives

- **(A) 让 UNIVIEW 事件也进 extract bundle**：需要先定 bundle 语义——无 scan 产物
  （也就无 merge）的轮次里，`jira/{run_id}/` 应当放什么？这不是「补个回填工具」的量级，
  且会与「extract 只复制 merge 引用到的事件」这一既有契约冲突。未采纳，保留为 Revisit。
- **粗口径「`REMOTE` 即终态」**：见上，会把真卡住一并掩盖，否决。
- **给所有 `REMOTE` 行加自动补归档的定时任务**：等于绕过 merge 前置，把「归档」变成
  「上送即可归档」，会让 `jira/{run_id}/` 的 bundle 内容失去「本轮 merge 引用」这一语义，
  否决。
- **只改代码注释**：判据是**跨进程契约**（控制面链 + 运维/界面口径），注释承载不了，
  故落权威链文档。

## Verification

- 文档改动为纯契约记录，无行为变化；`test_impact=none`。
- `python scripts/run_gates.py check:quick` → [OK] 10 gates（文档门禁：DOC-MAP 登记、
  链接、治理面 S1–S14）。
- 生产证据为一次性只读核对（命令见 #2150 评论；全部 `SELECT` / `ls`，未改动任何数据）。
- **界面口径**（追加提交）：`LogEventsCard.test.tsx` 新增两条用例——`REMOTE` 单元格的
  `title` 必须含「无 merge 产物」与「不是卡住」、`ARCHIVED` 说明落点、**其它状态不带 title**
  （不硬塞口径）；**反例**：还原 `LogEventsCard.tsx` → 两条用例失败，恢复后 15 passed。

**界面口径（已落地，2026-09-16 追加）**：`LogEventsCard`（DLE 终态视图）的状态单元格带上
口径说明——`REMOTE` 行提示「归档由该 run 的 extract 执行；无 merge 产物则不会归档，此处即
终态，不是卡住」，`ARCHIVED` 行说明落点（`jira/{run_id}/`）；其余状态不硬塞说明。
（初版因 `components/plan-run/` 看似被在窗占用而搁置——复核后确认那批只声明了
`PlanRunEventStream.tsx`，`LogEventsCard.tsx` 未被占用。）

口径文案面向**运维**（「不是卡住」），判据的权威表述仍在
`docs/design/2026-scan-upload-merge-contract.md` 那一节。

## Revisit

- **(A) 若将来被选中**：先定 bundle 语义（无 merge 轮次的 `jira/{run_id}/` 放什么、
  与 `dedup_extract` 的「只复制 merge 引用事件」如何共存），再谈回填工具。
- **界面口径**：已完成（见 Verification 一节）。若将来引入「无 scan 平台也归档」的路径，
  这行提示要同步撤掉——它宣称的正是「无产物 ⇒ 不归档」。
- **新平台接入**（如 QCOM）同样适用本判据：只要该轮无 merge 产物，其 DLE 行停 `REMOTE`
  即为正常；若将来引入「无 scan 平台也归档」的路径，本判据需同步修订。
