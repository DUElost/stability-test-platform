# R09-R03 落地：nfs_quota_mb 落实为单 Job 写入配额（#1053）

Status: implemented
Class: bug-fix

## Decision

`WatcherPolicy.nfs_quota_mb` 可从环境解析（policy.py），但全仓**无消费点**：
puller 只有单文件 `pull_max_file_mb`，目录拉取路径完全绕过字节限制——配额
是死旋钮（大目录/异常风暴无字节上限，审查列为设计风险）。

修复（落实配额，`backend/agent/watcher/puller.py` + manager 接入）：

1. `LogPuller` 增 `nfs_quota_mb`（0=不限，兼容旧调用）；实例内累计
   `_nfs_written_bytes`（Puller 每 Job 构造，实例生命周期 = 单 Job 配额面）；
2. 文件超余量：删除已拉副本 + 回元数据（`artifact_uri=None`、size 保留）
   + 置 `_quota_exceeded`；
3. 目录按**内容总字节**计量（`_tree_bytes`——目录节点 stat 不代表内容，
   正是原实现绕过的口径）；超限整树删除 + 同上；
4. 配额耗尽后：后续事件**直接回元数据、不再发起 adb pull**（硬上限，
   顺带省带宽）；
5. `PullerStats.pulls_quota_exceeded` 观测；`manager` 从 policy 传参
   （配置到行为的闭环）。

## Alternatives

- **拉取前 adb shell stat 预检**——放弃：每事件多一次远端往返；拉后检查
  与既有 oversized 模式一致（最多浪费一次带宽，且与 sha256/首行管线同点）；
- **删除 `nfs_quota_mb` 旋钮（issue 的「或删除死旋钮」分支）**——放弃：
  单 Job 字节上限是真实需求（目录风暴隔离），实现成本可控；
- **仅限制单文件之和（不做耗尽短路）**——放弃：耗尽后继续拉取会持续
  浪费带宽与 adb 往返，短路才闭环。

## Verification

- **反例实证**：回退 puller/manager 保留测试 → 3 用例失败（无配额参
  数/无行为）；修复版全绿；
- 新增用例（`test_puller.py::TestNfsQuota` +3）：配额内累计（written 断
  言）/ 超限文件丢弃且**后续事件不再 adb pull**（pull_calls 计数=1）/
  目录超限整树删除；
- `backend/agent/tests/` 全套 **1550 passed**（2m19s，含既有拉取/溢出/
  证据链回归）；
- `check:quick` 与 PR 门禁：见 PR 描述。

## Revisit

- 配额粒度=进程内实例；若未来 Puller 复用于跨 Job 共享（当前非），需
  改为持久层计量；
- 单文件 `pull_max_file_mb` 与配额的关系：超大文件（>单文件上限）不落盘
  也不计配额 ✓（顺序上 oversized 先判）——两旋钮职责不重叠。
