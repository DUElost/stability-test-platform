# #3300 去 flake：TestReaperCompetition 竞争用例改断言「恰一方生效」而非「admission 必赢」

Status: implemented
Class: bug-fix

关联：[#3300](https://github.com/DUElost/stability-test-platform/issues/3300)（本单）、
#1525（flake 归「去 flake」不前移评估的规则）。

## Decision

`backend/tests/services/test_admission_queue_step4.py::TestReaperCompetition::test_concurrent_reaper_and_admission_never_corrupt`
在线程内把 `admission_transaction(...) is True` 写成硬断言——它假设两个并发 writer 中
admission 一定先拿到 PlanRun 行锁。这正是时序依赖：CPU 负载拉长调度间隔后，reaper 线程
先赢得 `FOR UPDATE` 行锁、把行重排回 QUEUED 的情形变多，`admission_transaction` 按 CAS
语义正确地返回 False（重读 status≠PRECHECK 即非 owner），旧用例却把这个**合法返回值**记进
`errors`，于是 `assert errors == []` 红。

修复=让断言随真实赢家走，不再赌顺序：
1. 线程内只**记录** `admission_transaction` 的返回值（不 assert），reaper 结果照旧进
   `reaper_summary`；
2. join 后断言**互斥推进点**：`admitted[0] is not reaper_won`——行锁串行化保证恰有一方
   生效（两真/两假都判为 bug）；
3. 按真实赢家核对持久态：admission 赢⇒RUNNING+3 jobs+reason 空；reaper 赢⇒QUEUED+
   `requeued==1`+0 jobs+`PRECHECK_STALE`。

未用 skip / retry / 放宽 join/barrier 超时掩盖——超时上界原样保留。

## Alternatives

- 加 `@pytest.mark.flaky(reruns)` 或 skip 高负载：**否**，违背 #1525「去 flake 不前移」且
  掩盖真实竞争窗口。
- 用显式 barrier 强制 admission 先提交、reaper 后跑（把并发拆成两阶段）：**否**——这会退化成
  同文件 `test_second_reaper_skips_row_already_requeued` 的确定序版本，丢掉本用例的端到端
  并发价值（真实争抢同一行锁）。
- 断言 `errors==[]` 改成只忽略特定 AssertionError：**否**，等于选择性吞异常。改为断言
  「恰一方生效」把根因（错误的赢家假设）直接消掉。

## Verification

- 复现（20 核机 + 40 busy-loop，load ~50）：修复前 `TestReaperCompetition` 10 轮 **4 failed**
  （FAILED nodeid = `test_concurrent_reaper_and_admission_never_corrupt`，:1093
  `assert errors == []`，与 issue 报错形态逐字一致）；修复后同负载 10 轮 **全绿**。
- 双向分支验证（临时插桩 `WINNER=` 打印，验后已撤）：同负载单跑本用例 20 次得
  **13 admission / 7 reaper** 两类赢家都出现——证明互斥断言两个方向都被真实走到，不是
  只在固定顺序下恒真（插桩行未入库）。
- 空闲机整文件 `test_admission_queue_step4.py` 40 passed。
- **issue 标题 nodeid 偏差**：标题写 `test_second_reaper_skips_row_already_requeued`，但该用例
  是单会话顺序 `_reap` 两次、无并发 writer、无 `admission_transaction` 调用；其报错形态
  （`assert [...] == []` + `admission_transaction(<Session>, 1, '<token>')`）只可能来自
  并发用例。修复针对真实 flake 的并发用例；顺序用例不受影响、实测也绿。
- 门禁/CI：见 PR。

## Revisit

- 若将来 admission/reaper 之外加入第三个抢同一行锁的 writer，互斥断言需从 `admitted[0]
  is not reaper_won` 升级为「至多一方生效 + 生效方与持久态一致」的枚举。
- 负载复现依赖慢机/满载，CI 常态不触发；如需回归护栏，可加一条「高并发同 PlanRun 准入+回收」
  的 soak 用例（本单不做，属新覆盖面）。
