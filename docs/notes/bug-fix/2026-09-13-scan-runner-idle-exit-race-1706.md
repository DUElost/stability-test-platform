# ScanRunner 空闲退出与入队交错窗口丢单修复（#1706）

Status: implemented
Class: bug-fix

## Decision

`backend/agent/scan_runner.py` 的 `_worker_loop` 把「队列已空」的判定与
`_worker_started` 复位拆在了两个临界区里：worker 先释放 `_worker_lock`/`_queue_lock`
再 `return`，复位由 `finally` 完成。入队线程可恰好落在「判定空 → 复位」之间——
它写入 `_pending` 后 `_ensure_worker` 见到仍为 `True` 的标志直接返回，worker 随即
复位退出，留下「有 job、无 worker、标志 False」的队列，直到下一次 scan_now 或控制面
重试才被拾起（#754 的 try/finally 重构引入，原实现复位在锁内）。

修复（`scan_runner.py:181-231`）：

1. 排空判定与复位移入**同一临界区**（`_worker_lock` → `_queue_lock` 内先
   `_worker_started = False` 再 `return`）。此后入队侧要么被本次 `_pending` 检查看到
   并 `continue`，要么在释放后见到 `False` 并拉起新 worker，不存在中间态；
2. `finally` 的复位只在**非正常排空**退出时执行（新增 `stopped` 标记）。这是必须的
   护栏而非洁癖：正常排空的复位移入锁内后，入队线程可在旧 worker 的 `finally` 之前
   就拉起新 worker，无条件复位会把新 worker 的标志清成假死，此后每次入队都会重复
   拉起线程（见 Verification 的朴素修复对照）。

锁序不变（worker 侧 worker→queue；入队侧 queue 释放后才取 worker），无环。异常路径
（`_dequeue_next`/`_any_scan_runner_configured` 等逃逸异常）仍由 `finally` 保证复位，
#754 的「队列永久停摆」防护不回退。

## Alternatives

- **只把复位移入临界区、`finally` 照旧无条件复位**（issue 建议稿的写法）：弃——已用
  反例证明它会把丢单换成另一种破坏：新 worker 已启动时旧 worker 的 `finally` 覆盖标志，
  下一次入队重复拉起 worker 线程（`test_old_worker_exit_does_not_clear_restarted_worker_flag`
  在朴素修复下红）；多个 worker 并存不丢 job（`_dequeue_next` 在锁内 popitem），但线程
  会随每次交错累积；
- **代际 token（`_worker_generation`）代替 `stopped` 局部标记**：弃——同样能防覆盖，但
  要为「谁拥有标志」多引入一个类级状态与清理点；本处 worker 循环是一线程一实例，
  局部标记即可表达「本次退出是否已在锁内复位」，语义更小；
- **worker 不退出、改用条件变量常驻等待**：弃——能绕开重启竞态，但改变 Agent 的线程
  生命周期（常驻线程数、daemon 语义、现有 `_reset_for_tests` 契约）远超本单必要范围；
- **只补注释不修**：弃——窗口虽窄，机制确定且已有确定性反例，修复面仅一个方法。

## Verification

- **红绿对照（确定性，非 sleep 竞跑）**：新增
  `backend/agent/tests/test_scan_runner_idle_exit_race_1706.py`，用一把「释放后回调」
  的锁把入队精确钉在空判定与复位之间：
  - 修复前 → `2 failed`（第二个 job 永不被消费）；
  - 仅临界区复位（朴素稿）→ `1 failed, 1 passed`（丢单已修，但旧 worker `finally`
    覆盖新 worker 标志的用例红）；
  - 最终版 → `6 passed`（含既有 `test_scan_runner_worker_guard.py` 的 #754 守卫）；
- `TESTING=1 JWT_SECRET_KEY=test-secret python -m pytest backend/agent/tests/ -q`
  → **1825 passed**（166s）；
- `python -m ruff check`（两个改动文件）→ All checks passed；
- `python scripts/run_gates.py check:quick` → `[OK] check:quick (7 gates)`。

未做：生产/真机现场观测。本竞态窗口极窄（释放锁到 `finally` 取锁之间），未在 Agent
日志中取得「queue_depth>0 且长期无 worker」的现场证据——修复依据是机制与确定性反例，
不是现场复现（issue 置信度亦标注为中）。

## Revisit

- **同型竞态未普查**：本单只修 `ScanRunner`。仓内其他自管理 worker 标志的组件
  （`artifact_uploader.py`、`watcher/puller.py` 各有 `_worker_loop`）未逐一比对是否
  存在「决策与复位分离」的同型窗口；如需普查可作为独立 Requirement，不在本单顺手扩大；
- **启动失败面仍在**：`_ensure_worker` 里 `Thread.start()` 抛错（线程资源耗尽）会留下
  `_worker_started=True` 的假活标志，与本单修的窗口无关但同属该标志的健壮性面，留待需要时处理；
- `stopped` 语义假设一次 `_worker_loop` 调用对应一条线程（现有测试有直接调用
  `_worker_loop()` 的用法）；若将来引入复用同一循环体的第二条执行路径，需要复核该假设。
