# #1805 ④ claim 切片：退役主机显式不认领 + 可区分信号

Status: implemented
Class: feature

## Decision

在 `backend/api/routes/agent_api.py` 的 `_claim_jobs_for_host` 中，主机行锁之后、
维护窗口检查之前，新增**显式**退役判据：

```python
if host_row.retired_at is not None:
    logger.info("claim_skipped_host_retired host=%s", host_id)
    await db.rollback()
    return [], {}
```

补 2 例测试（`backend/tests/api/test_agent_api_watcher.py`）：退役+ONLINE、
退役+OFFLINE。

## 为什么不能依赖既有的 `status != ONLINE` 检查

既有代码在锁后已有 `if host_row.status != HostStatus.ONLINE.value: return [], {}`，
看似能挡住退役主机——**这是本切片的核心陷阱**：

- **ADR-0038 D1 明定：退役不改写 `status`**（status 归心跳所有）。故退役主机的
  `status` 仍可能是 `ONLINE`，此时既有检查**完全不拦**，退役机照样认领作业；
- 反之若退役机恰好离线，`status` 分支**先短路**，使「因退役而跳过」这一可区分
  信号永远落不下来——与 `claim_skipped_host_maintenance` 的可观测性先例不一致。

**实测确认（非推演）**：临时移除本判据后跑新增测试，断言
`assert result.data == []`（退役主机不得认领到作业）**失败**——即修复前退役且
ONLINE 的主机会真实认领到作业。这同时证明该缺陷不是纸面推演，而是可达路径。

## 为什么判据放在 status 之后、maintenance 之前

- **放在 status 之后**：`status != ONLINE` 的语义是「主机不在线，无法接活」，与退役
  正交；两者都拦，顺序不影响「是否认领」的结果。本判据的价值在**归因与信号**，
  不在改变放行集合。
- **未把 retired 前移到 status 之前**：那会让「离线 + 退役」的日志从 status 归因
  改为 retired 归因——看似更精确，但**改变了既有 status 分支的可观测语义**，超出
  本切片范围（#1805 的 claim 面只要求「叠加 retired_at + 可区分信号」）。
  此处保留现状并在测试中记录该顺序事实，待后续切片需要时再独立裁决。

## 活读 `retired_at`（不缓存）

退役可发生在**准入与认领之间**。若认领侧读缓存快照，则「检查完 → 退役 → 认领」
窗口内仍会派作业给退役机。本判据在行锁内直接读 `host_row.retired_at`，与派发侧
`plan_dispatcher_sync` 的 `host_retired_at` 活读同构（④ 切片一已落地）。

## 与其它切片的边界

本单只做 #1805 列出的 claim 面（issue 原文：「**claim**：叠加 `retired_at IS NULL` +
**可区分信号**（log/metric，对照 `claim_skipped_host_maintenance` 先例；非 ONLINE
分支现为静默 `return [], {}`）」）。**未做**的其余 claim 相关面（scan/archive 扇出、
Socket.IO、reload-config、hot-update/install/upgrade-gate 拒绝、预检与准入前置副作用、
10 场景全量 mutation）仍归 #1805 的后续切片——本单不认领、不暗示已覆盖。

## Alternatives

- **只加 `retired_at` 判据、不加日志信号** → 否决：issue 明确要求「可区分信号」；
  且静默返回空正是本切片要修的形态之一（运维无法区分「退役跳过」与「无作业可认领」）。
- **把 `retired_at` 合并进既有 `status != ONLINE` 条件**（如
  `if status != ONLINE or retired_at`）→ 否决：两者语义不同（离线 vs 退役），合并后
  日志只能给出一个笼统归因，丧失可区分性——这正是 issue 点名的先例要求。
- **在 status 分支之前判 retired** → 见上「为什么放在 status 之后」，属独立裁决项，
  本切片不做。
- **改用 metric 而非 log** → 部分采纳但本单不做：issue 原文允许「log/metric」二选一；
  选 log 是**与 `claim_skipped_host_maintenance` 同形**（同文件既有先例，形制一致、
  改动最小）。若后续需要面板化统计，应作为独立事项统一为两个信号一起加 metric，
  而非只给 retired 单独加（否则两个同类跳过信号的可观测性不对等）。
- **同时在派发侧再校验一次** → 否决（本单范围）：④ 切片一已在派发侧落
  `host_retired` fatal（`_FATAL_DISPATCH_REASONS`），无需重复。

## Verification

- `python -m pytest backend/tests/api/test_agent_api_watcher.py -k retired -q`
  → **2 passed**；
- **红绿双向（缺陷真实性）**：临时移除判据 → `test_claim_skipped_when_host_retired_and_online`
  **失败**，失败断言为 `assert result.data == []`（退役主机不得认领到作业）——
  证明**修复前退役且 ONLINE 的主机会真实认领作业**；还原 → 2 passed；
- **可区分信号断言**：断言 `claim_skipped_host_retired` 出现在 caplog（用
  `r.getMessage()` 取值，避免 `%`-格式串手工拼接）；
- 退役相关全量回归：`test_agent_api_watcher.py` + `test_host_retirement_api_1801.py`
  + `test_host_retired_heartbeat_1806.py` + `test_host_retirement_read_filters_1804.py`
  + `test_host_retirement_schemas_1800.py` + `test_host_retirement_roundtrip_1800.py`
  → **64 passed**（含 ① ② ③ ⑤ 各切片既有用例，无回归）；
- `ruff check` 两文件 → All checks passed；
- `python tools/dev/check_governance_surface.py --check` → S1–S13、S5x 全绿。

## Revisit

- **`retired` 与 `status != ONLINE` 的归因顺序**：当前 retired 判据在 status 之后，
  故「离线 + 退役」主机归因到 status（不落 retired 信号）。若运维面需要「退役机
  无论如何都归因到退役」，应把判据前移到 status 之前——但那是**既有 status 分支
  可观测语义的变更**，需独立裁决，不在本切片内顺手做。
- **metric 面**：本单只加 log（与 maintenance 先例同形）。若要把「跳过认领」做成
  可统计指标，应两个信号（retired / maintenance）一并加，保持可观测性对等。
- **#1805 剩余 claim 相关面**：scan/archive 扇出（回收类 `skipped_retired` 不虚报完整）、
  Socket.IO 与 reload-config、hot-update·install·upgrade-gate·批量工具拒绝、
  预检与准入前置副作用（SSH 推脚本在终检前）、10 场景全量 mutation——
  均**未**在本单覆盖，归 #1805 后续切片。
