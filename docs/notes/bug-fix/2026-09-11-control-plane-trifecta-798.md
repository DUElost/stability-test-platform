# 控制面杂项 3 处（#798）

Status: implemented
Class: bug-fix

## Decision

同一审查批的 3 处独立缺陷（各自小面、互不耦合）：

1. **ai_assistant 轮次 TOCTOU**（`send_message`）：in_flight 检查与两条
   消息插入之间无锁——并发双击/前端重试下两个请求都通过检查，落库两条
   user + 两条 pending 占位，一条因 SAQ 同 key 去重永久 pending（注释
   自述的「孤儿化」正是竞态后果）。修复：检查/插入前对会话行
   `SELECT ... FOR UPDATE`（同事务串行化，commit 释放）——并发第二请求
   阻塞后见 pending → 409。
2. **merge_stderr 漏判**（`dedup_scan.merge_stderr_indicates_failure`）：
   仅匹配 `": error:"`/`"error: argument"`；行首 `ERROR:` 与 `Traceback`
   不命中 → 工具 exit 0 的报错被当成功、残缺报表注册入库。修复：扩行首
   `error:`/`error ` 前缀与 `traceback (most recent call last)` 匹配。
3. **console.log 只增不删**：`{LOG_BASE_DIR}/jobs/{id}/console.log` 与
   `log_writer._locks` 全仓无清理路径（retention 只删 DB 行）。修复：
   `log_writer.purge_job_log_files(job_ids)`（rmtree + 锁条目 pop，
   best-effort 失败仅告警）；`run_retention_cleanup` 在**删行前**收集
   job 清单、commit 后调用（与 #781 的 job_log_signal 孤儿行是两个
   对象：文件 vs DB 行）。

## Alternatives

- **ai_assistant 用占位唯一键 ON CONFLICT**——放弃：需迁移加唯一约束
  （pending 占位按会话唯一的偏索引），行锁零 schema 改动且与仓内
  「锁+复查」模式一致；
- **merge_stderr 改为完全依赖工具 exit code + 产物目录校验**——放弃：
  工具契约（exit 0 + stderr 报错）短期不会变，扩展匹配是最小闭环；
  产物校验可作为后续增强（Revisit）；
- **console.log 用 logrotate 类外部机制**——放弃：retention 是现成水位
  驱动点，就地清理与 DB 行删除同拍；外部轮转引入部署面。

## Verification

- **反例实证**：回退三实现文件保留测试 → 2 个新用例失败（漏判/文件不
  清理）；修复版全绿；
- 新增用例：`test_merge_stderr_detects_error_prefix_and_traceback`（命中
  ERROR:/Traceback 且非误报）、`test_console_log_files_purged_with_run`
  （目录与锁条目随 Run 删除被清理）；
- 三域全套 **95 passed**（dedup_scan_merge + retention + ai_assistant
  端点，含既有 `test_send_message_rejected_while_turn_in_flight` 顺序
  回归——行锁不改变其行为）；
- ai_assistant 并发真验证：行锁语义与 #987/#989/#993 同模式；并发
  TOCTOU 的确定性黑盒复现需要可控停顿注入（同 #792 讨论），当前以顺序
  回归 + 结构对齐背书；
- `check:quick` 与 PR 门禁：见 PR 描述。

## Revisit

- merge 产物目录校验（exit 0 但无新 xls）可作为 #798-2 的后续增强；
- console.log 清理随 retention 批（每轮 ≤100 run）滚动完成；若单 job
  目录异常巨大（>树删除耗时）可评估异步化。
