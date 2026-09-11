# /logs/query 分页 cursor 改 (job 序, 行号)（#794）

Status: implemented
Class: bug-fix

## Decision

多 job 日志查询的 cursor 是**全局行偏移**，但逐文件循环里用**本文件 idx**
比较（`idx < offset: continue`）、offset 又全局累加（`offset + total_scanned`）：

1. 翻页时第一个文件断点之后的**其它文件头部行段被永久跳过**（第一页从未
   扫到它们，第二页 offset 已越过其本地 idx），同文件断点后段也不可达；
2. `if len(items) >= limit: break` 只跳出内层 for，外层 jid 继续 → 后续每个
   文件至少再追加 1 条，响应可超 limit。

修复（`backend/api/routes/logs.py`）：

- cursor 结构改为 `"<job 序>:<文件内行号>"`（`_encode/_decode_log_cursor`），
  精准表达跨文件位置；
- 达 limit 立即停止扫掠并对当前未处理的下一行出 cursor，外层不再追加；
- 旧纯数字 cursor（历史客户端持有）解析失败 → **降级从头开始**（宁可重复
  一页，不丢段——安全方向），附 warning 日志。

## Alternatives

- **保持全局 offset、改为「已扫描字节/行」全局游标**——放弃：需保证每页
  扫描路径与上页完全一致（过滤器/文件集合变化即错位），结构性表达更稳；
- **给每行生成稳定 stream_id 作为 cursor**——放弃：需要全局有序 id 生成
  与跨文件字典序映射，改动面大于收益；`(job 序, 行号)` 已足够。

## Verification

- **反例实证**：回退实现保留测试 → 全量用例与旧 cursor 用例失败（丢段/
  超限）；修复版全绿；
- 新增用例（`test_logs_query_pagination.py` 3 例）：600 行 × limit 200
  **翻页恰好全量、单页 ≤ limit、无重复、顺序正确**；旧数字 cursor 降级
  从头；单页完成 `next_cursor=None`；
- `check:quick` 与 PR 门禁：见 PR 描述。

## Revisit

- 前端/外部消费者若缓存了旧数字 cursor：升级后首次请求会从头拉一页（重复
  而非丢段），随后由新 cursor 接管——无需前端改动；
- 单文件 >1000 行 + limit 上限 1000 的极端单文件仍需多页（新结构正常
  支持）。
