# RunConsole 用例改有界轮询：消掉「裸 sleep 等 run 起来」（#2595，#2577 同族）

Status: implemented
Class: bug-fix

## Decision

`backend/tests/services/test_run_console.py` 里 5 处「起长命子进程 → `time.sleep(0.3)` →
动作（`cancel` / 查 `_pgid` / `shutdown`）」改为**有界轮询** `_wait_running()`：

```python
def _wait_running(rc, run_id, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = rc.status(run_id)
        if st and st.get("status") == "RUNNING":
            return
        time.sleep(0.02)
    pytest.fail(f"run {run_id} 未在 {timeout}s 内进入 RUNNING：{rc.status(run_id)!r}")
```

**判据与 #2577 一致**：等待异步状态用「可观测条件 + 上界」，不用固定时间窗。差别在于本单
的窗口不是亚毫秒级而是 0.3s —— 同样的病，只是发作频率低一些；而**该套件只在夜间
`backend-test` 跑**（PR 路径 `--ignore`，见 #2551 的事实表），红了也没人当天看到。

**没动 `sleep(0.8)` 那处**（同文件 flush 串行化用例）：它的作用不是「等一个可观测条件」，
而是「给 reader 一个机会去**制造**竞态」——不断言时序，最坏情况是这一轮没真的并发、断言
`max == 1` 仍然通过（即**测试强度**问题，不是 flake）。要确定化它需要「谁在等这把锁」的
观测面（如锁获取计数），属另一类工作，记在 Revisit。

## Alternatives

- **A. 把 sleep 调大（0.3 → 1s）**：否决。同 #2577：只降概率、拖慢夜间套件，且掩盖
  「我们其实在赌机器速度」这件事。
- **B. 用 `_wait_terminal` 式的固定状态轮询（等终态）**：不适用。这些用例的前提是
  **RUNNING**（长命子进程要活着才能 cancel/查 pgid），等终态就错过了要测的时点。
- **C. 只改出现失败的那一处**：否决。5 处同形，只修一条等于把下一次随机红留给邻居
  （与 #2577 的处置同一理由）。
- **D. 顺手把另 3 个文件的同形一起改**：本单不做（`test_phase0_closure.py` 等涉及的
  等待对象各不相同——线程 `join` 型、SAQ 重试型、回调同步型——需各自核判据），
  已在 #2595 登记为后续。

## Verification

- **测试**：`backend/tests/services/test_run_console.py` → **23 passed / ~7.2s**，
  连跑 **3 轮全绿**（每轮 7.0–7.3s）。
- **自证用例**：`test_wait_running_fails_loudly_when_never_running` —— 用一个不存在的 run id
  触发超时路径，断言 `pytest.fail` 真的抛出。**没有这条，把 sleep 换成 `_wait_running`
  就只是换了个名字的空转**（条件永不满足时测试会一路往下跑，竞争照旧）。
- **扫描面**：#2595 记录了 18 处命中的逐条分类（5 真竞争 / 9 子进程脚本字符串假阳性 /
  1 测试强度 / 3 其它文件），以及给下一个人的判据（`time.sleep` 只应出现在**循环内**或
  **子进程脚本字符串**里）。
- `ruff check` 通过。

## Revisit

- **另 3 个文件的同形**（`test_phase0_closure.py` / `test_saq_tasks.py` /
  `test_dedup_scan_merge.py`）：等待对象各不相同，改动触及时按同一判据处理（#2595）。
- **`sleep(0.8)` 那一类（制造竞态）**：需要「锁/临界区等待者」的观测面才能确定化；
  在那之前它只能靠时间边距。若该用例将来出现**假绿**（漏测到并发）而非红，应优先给它
  加观测面而不是加 sleep。
- **同类扫描可复用**：本单用的「亚秒 sleep + 邻近断言 + 是否在循环内」三判据（#2595 有
  结果表）可直接搬到 `backend/agent/tests/`（PR 路径，同样只在特定 job 跑）。
