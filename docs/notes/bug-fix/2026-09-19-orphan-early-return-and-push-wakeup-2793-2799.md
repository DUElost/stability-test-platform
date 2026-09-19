# 确定性小修两处：孤儿清理早退范围 + dashboard 推送丢唤醒（#2793 / #2799）

Status: implemented
Class: bug-fix

## Decision

1. **#2793（孤儿 DLE 清理的「根未配置早退」不是语义等价变换）**：早退范围**收窄**——
   早退前先按同一批资格谓词（抽出的 `_orphan_dle_scan_predicates`，与常规键集翻页共用）
   清理 `remote_path` 为空/NULL 的合格行（它们在 #2636 之前就不需要共享根即可删），
   返回该批清理数；带 path 的待清行仍不出循环（保持 #2636 ① 的「不白扫满页」），但按
   「本 tick 检视上限」**封顶计数**进 `stability_dle_orphan_skipped_total{reason="root_unset"}`
   ——此前该分支不可达、计数在根未配置期间归零（issue 的次要项）。不做全表 COUNT。
2. **#2799（dashboard_summary 推送丢唤醒）**：两处收口——① `_clear_flush_task`
   （done callback）在收尾时若 `_dirty and _flush_handle is None` 就补一次
   `_arm_flush(delay=0.0)`（`_arm_flush` 自身幂等，是安全网而非第二触发路径）；
   ② compute 加墙钟上界 `_COMPUTE_TIMEOUT_SECONDS=30.0`，超时走失败路径（退避重试）并记
   error 日志；同时把会话创建/关闭**移进线程**（`_compute_and_close`）——超时后线程仍在跑，
   外层 `finally: db.close()` 会让它撞上已关闭连接。`shutdown_dashboard_summary_publisher`
   补 `_dirty = False`：被取消的任务不得在收尾回调里再武装一次。

## Alternatives

- **#2793 直接删掉早退（回到全程翻页）**：弃——#2636 ① 的收益（根未配置时不白扫
  `MAX_PAGES × BATCH = 1000` 行/tick）是实测换来的；收窄到「空 path 这一类」既恢复语义
  又不退性能。
- **#2793 只记录、不清理空 path 行**：弃——那是把「少删」固化成长期债务：这些行没有
  任何后续路径会清（不关联 run/job，retention 谓词也覆盖不到）。
- **#2793 用全表 COUNT 恢复可观测性**：弃——成本不可控且观测精度不是本单目标；
  封顶计数（`LIMIT limit`）足以表达「本轮有 ≥N 条在等根配置」。
- **#2799 超时后在外层 finally 关会话**：弃——`to_thread` 无法取消，关掉会话会让仍在
  执行的查询刷错误日志；会话交给线程自持自收。
- **#2799 只加重武装、不加超时**：弃——只解决「在飞期间变更丢唤醒」，无法解「compute
  永久挂起 ⇒ 一切后续武装被跳过」的冻结形态（issue 的第二半）。

## Verification

- `backend/tests/scheduler/test_retention_cleanup.py` + `backend/tests/services/
  test_dashboard_summary_publisher.py` → **49 passed**；
  - 新增 `test_orphan_cleanup_root_unset_still_purges_empty_path_rows`（根未配置 + 空 path
    行 → 必须被删，返回 1）；反向验证：恢复裸早退 ⇒ 该用例失败。
  - 既有 `test_flush_is_serialized_no_overlapping_task` 的 `await_count == 1` 断言按 #2799
    改为「在飞期间到达的变更最终落地」（`== 2`）；新增
    `test_compute_hang_times_out_and_retries`（超时 → `_failure_streak ≥ 1` 且
    `_flush_task` 已收尾）；反向验证：去掉重武装 + 去掉超时 ⇒ **3 failed**
    （两处均为预期断言）。
- `python scripts/run_gates.py check:quick` → 见 PR。

## Revisit

- #2793：若将来出现「共享根长期不配置 + 大量带 path 孤儿行」的生产形态，需要把
  「等根配置」的积压量做成真实指标（当前封顶计数的精度只够告警「有积压」）。
- #2799：`_COMPUTE_TIMEOUT_SECONDS=30.0` 是常量（未走环境变量，避免扩环境变量清单）；
  若 compute 的正常耗时接近该值，先查聚合慢查询再调常量，别把它当性能开关。
