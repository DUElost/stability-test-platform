# 汇总审计写入失败不得改写「已提交的删除」：返回值/指标/日志三方自洽（#2789 顺带项）

Status: implemented
Class: bug-fix

## Decision

**本单只修 #2789 里不需要裁决的那半条。** #2789 的主诉（`install_agent*` 落 business
默认桶 90d，与 ADR-0044 D3「审计=持久证据」未对齐）是**三选一的裁决题**，本 Note 不代裁；
它尾部顺带记录的读数不一致是纯代码事实，且与裁决无关，所以单独收口。

缺陷形态（`backend/scheduler/audit_log_cleanup.py`，tip `1788749e`）：

```python
with session.begin():        # 裁剪事务：到这里删除已提交，是既成事实
    ...
for name, count in pruned.items():
    audit_retention_pruned_total.labels(layer=name).inc(count)   # 指标：已自增
if sum(pruned.values()):
    record_audit(...); session.commit()                          # ← 这一步抛错
...
except Exception:
    logger.exception("audit_log_cleanup_failed")
    return {"session": 0, "business": 0, "security": 0}          # ← 读数改成「没删」
```

三方各说一套话：**行已删、指标已加，而返回值与日志说零**。ADR-0049 D5 写的是
「汇总审计失败只丢这一条审计，不回滚已完成的裁剪」——旧实现遵守了后半句（确实没回滚），
却用返回值把前半句抹掉了：看起来像整件事没发生。对账时会得出「指标说删了、任务说没删」
的矛盾，而这类矛盾一旦被人拿来当证据，代价是**下一次真故障时不再信指标**。

修法（顺序与边界，不扩权）：

1. 出了 `with session.begin()` 之后先固化 `committed = dict(pruned)`，
   **后续任何失败都不再改写它**；
2. 汇总审计的写入单独 `try/except`：失败 → `session.rollback()`（只回滚这一笔审计）
   + `logger.exception("audit_retention_summary_audit_failed")`——**自己的失败有自己的名字**
   （与 `agent_runtime_snapshot_stale` / `unisoc_reconciler_missing_field` 同一条纪律），
   且 `audit_log_cleanup_failed` 保留给「裁剪事务本身没提交成功」这条真失败；
3. 汇总 INFO 行加 `summary_audit_failed=0|1`，使「删了多少」与「审计有没有落」
   在同一行里可读，不必靠翻异常栈区分；
4. 外层 `except` 的报零语义**保持不变**并加注释钉住：事务未提交时报零才是诚实的。
   这条是防过度修正——把新加的就地捕获推广成「任何失败都报已删」就是反向失真。

`record_audit` 并非只吞「缺表」：它只在 `audit_logs` 不存在且 `strict=False` 时降级，
其余异常原样上抛（`backend/core/audit.py:131-146`），加上 `session.commit()` 本身可能
失败——所以这条路径不是假想敌。

## Alternatives

- **把汇总审计挪进裁剪事务里一起提交**：否决。那正是 ADR-0049 D5 刻意分开的两件事——
  「一条审计写不进去」不该回滚已完成的清理（清理是有界的、每 tick 自然推进的活儿）。
- **给汇总失败新增一个 Counter 指标**：不在本单。加指标要同步 `tests/metrics_registry.py`、
  生产者守卫与 Grafana 口径，而「有没有写进审计」目前在这条一次性 job 的日志里已经可读
  （`summary_audit_failed=` + 独立异常名）；真要做，判据应是**日报**而不是单点计数，另议。
- **顺手裁掉 `install_agent*` 的保留期分歧**：不做，那是 #2789 的裁决题（三选一）。

## Verification

- `pytest backend/tests/scheduler/test_audit_log_cleanup.py -q` → **8 passed**
  （原 5 条 + 本单 3 条：①失败时返回值/`REGISTRY.get_sample_value` 指标增量/DB 实际剩余
  行数三方自洽，且日志出现 `audit_retention_summary_audit_failed` 而**没有**
  `audit_log_cleanup_failed`；②失败的那笔审计不落库、删除仍在；③真·事务失败仍报零，
  两条失败路径可区分）
- **变异自证**：把内层 `except` 改回「直接上抛」（= 修复前形态）→ 用例①红，日志里
  重新出现 `audit_log_cleanup_failed`；还原后 8 passed。用例③是**反向钉子**：
  若有人把就地捕获推广成「吞掉一切并照报已删」，它就红
- 未跑（标 pending，不当作通过）：本 job 在真实调度下的下一个 tick（`_instrumented`
  包装 + leader 单例），以及生产 `audit_logs` 上的实际行数对账——修复属读数口径，
  不改删除谓词，故不影响数据

## Revisit

- #2789 主诉仍在 owner 手上（`install_agent*` 提层 / 改事实源 / 明示接受 90d 视界，三选一）；
  本 Note 不为它背书「已解决」。
- 若将来要观测「汇总审计写入失败率」，判据应是「连续 N 个 tick 都失败」而不是单点计数——
  单点计数会把「一次抖动」和「审计面长期坏掉」混成同一个数字。
- 同一族形状（「失败被改写成成功」或反之）在本仓已出现三次（#2286 恒真豁免、#2641
  注释与陈旧 step 满足判据、本单）。若要根治，值得有一条「返回值/指标/日志三方自洽」的
  结构性检查，而不是每处各自加断言——那属测试与质量面（#2639/#2642 同族）的立项题。
