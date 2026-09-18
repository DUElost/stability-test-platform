# 孤儿 DLE 清理：根未配置早退 + 扫描方向逐 tick 交替（#2636）

Status: implemented
Class: bug-fix

## Decision

#2316 的键集推进把「跳过行占满批头导致空转」的悬崖从 100 抬到 1000
（`_ORPHAN_DLE_MAX_PAGES(10) × _ORPHAN_DLE_BATCH(100)`），但**结构没变**：游标是
函数局部的，每 tick 都从最老端重扫；恒被跳过的行（形态不符 / 目录删不掉）既不删
也不更新，于是累计 ≥1000 条时 `purged` 恒为 0，其后所有可清理的行**永久不可达**
（#2636 的诊断，与我 #2316 Note 里 Revisit 预判的同一条）。两处收口：

1. **共享根未配置时早退**：该形态下每一行都必然命中 `root_unset` → 本轮**无行可清**，
   进翻页循环只会白扫最多 1000 行。现在记一次可检索的 warning
   （`dle_orphan_skipped_root_unset_early_return`）后 `return 0`。
2. **扫描方向逐 tick 交替**（首轮升序 = 历史行为，之后每轮翻转）：
   - 升序轮：与旧行为一致（最老的先清，公平性不变）；
   - 降序轮：从**最新**端往回扫——恒跳过行占满最老端时，那一端**永远排在最前**，
     于是可清理行每两 tick 至少被检视一次。**任意数量**的恒跳过行都不再造成永久空转
     （不再有悬崖阈值）。
   - `dry_run` 恒升序且不消耗翻转（运维盘点保持确定性）。

**为什么不用「跨 tick 持久化游标」或「给跳过行一个终态」**：前者要么落 Redis（本仓
硬不变量：Redis 只承载队列与瞬时通信、不作业务事实存储），要么加一张状态表（要迁移）；
后者要对 `device_log_event` 加列/加语义（同样要迁移），且「文件先于行」的既有语义
（目录清不掉就不删行）会让终态的定义本身需要裁决。本次先做**不需要迁移**的结构修法，
把「永久空转」消掉；终态化的收益与路径见 Revisit。

## Alternatives

- **跨 tick 持久化游标（Redis / 状态表）**：否决（本轮）。Redis 用途受限；状态表要
  迁移且引入新的一致性面（进程重启、多实例并发清理）。方向交替用**零状态**达到
  同样的「不再永久不可达」效果。
- **自适应加大翻页页数**（`purged == 0` 时继续翻到硬上限）：否决。只是把悬崖从 1000
  推到 5000——问题结构没变，而单 tick 耗时随上限上升。
- **把 `path_invalid` 行从候选谓词里排除**：否决（本轮）。谓词里表达不了「形态不符」；
  真要排除就得先给行打标 → 回到迁移。且 `purge_failed`（目录删不掉）本就该重试。
- **整轮反向（恒降序）**：否决。放弃「最老的先清」的公平性；交替同时保住两者。

## Verification

- **反例构造（先证伪再采信）**：
  - A 去掉根未配置早退（`if False:`）→ `test_orphan_cleanup_early_returns_when_root_unset`
    **FAILED**（无早退告警）；
  - B 去掉方向交替（`descending = False`）→ `test_orphan_cleanup_alternates_scan_direction`
    **FAILED**（第 2 轮仍升序，清不到最新端的可清理行）。恢复后 **41 passed**。
- 两条新用例的构造要点：把窗口压到 1 页（`monkeypatch _ORPHAN_DLE_MAX_PAGES = 1`）
  来复现悬崖——120 条形态不符行（较老）+ 1 条可清理行（最新）：升序轮 `purged == 0`，
  降序轮立刻命中。
- 实测命令与结果：
  - `TESTING=1 JWT_SECRET_KEY=test-secret python -m pytest
    backend/tests/scheduler/test_retention_cleanup.py -q` → **41 passed**；
  - `python -m ruff check backend/scheduler/cron_scheduler.py` → All checks passed。

## Revisit

- **恒跳过行的终态化仍是终局**（#2316 Note 的 Revisit 未变）：方向交替消掉了「永久
  不可达」，但恒跳过行本身仍会**每次被检视一次**（每两 tick 一轮）。真正的收敛是给
  「形态不符且永远不会变好」的行一个显式终态（记 failed 后排除，或运维清理）——
  那需要迁移与语义裁决，另单。
- **进程内翻转的状态**：`_orphan_scan_from_newest` 是模块级变量，重启后回到升序、
  多实例各翻各的——都不影响正确性（只是两个实例可能同向扫），无需处理。
- **`purged == 0` 的可观测**：现在仍只靠 `dle_orphan_skipped_total{reason}`。若
  `path_invalid` 长期高位，建议按上面那条终态化，而不是加新告警。
