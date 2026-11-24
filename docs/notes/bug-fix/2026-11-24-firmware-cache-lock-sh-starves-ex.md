# Agent L1 固件缓存消除并发冷拉下 `LOCK_SH` 饿死 `LOCK_EX` 等待者（#4086）

Status: implemented
Class: bug-fix

## Decision

在 2026-11-24 P532AA 49 台批量刷机实测（PlanRun 1655/1656，`.71`/`.72`/`.74` 三台高密主机 100% 复现）中，每台主机首批并发进入 `ensure_firmware` 的 5 台设备同时遭遇 cache miss：第 1 台拿到 `.entry.lock` 的 `LOCK_EX` 写盘完成并降级为 `LOCK_SH` 进入刷机，其余 4 台在 `_populate_locked` 中调用 `_flock_wait(fd, LOCK_EX)` 阻塞等待 `LOCK_EX`。随后每 3–4 分钟进场的第 6–16 台设备走 `_try_hit` 直接获取 `LOCK_SH`（与在刷设备共享），持续维持至少 1 个活跃 `LOCK_SH`，导致首批等 `LOCK_EX` 的同伴发生经典 reader-starves-writer 饥饿，单步 `firmware_cache.elapsed_seconds` 高达 `2825s`–`3587s`（47–60 分钟）。

在 `backend/agent/firmware_cache.py` 的 `_populate_locked` 中修复该等待路径：

- 将单向 `_flock_wait(fd, fcntl.LOCK_EX)` 改为轮询循环：每次 `LOCK_EX` 未获批时，若条目 `.stp-fw-ready.json` 已就位，立即非阻塞尝试 `fcntl.LOCK_SH` 并在持 `LOCK_SH` 状态下执行 `_verify_ready_under_sh` 校验；校验通过即更新 `last_used_at` 并以 `hit=True` 返回（保持 `LOCK_SH` pin 住条目）；校验未通过则立即释放 `LOCK_SH` 继续等待 `LOCK_EX`。
- 不改变 ADR-0064 D2 的任何并发锁、降级复核、空间守卫或 `LOCK_SH` pin 语义。
- 涉及文件：
  - `backend/agent/firmware_cache.py`
  - `backend/agent/tests/test_firmware_cache_b10_u2.py`

## Alternatives

- **改用文件锁排队目录或外部公平锁**：弃——复杂度高、增加跨进程崩溃清理面；实际上其余 4 台在首台写完后根本不再需要 `LOCK_EX`（写盘已完成），直接转取 `LOCK_SH` 才是本质最小解。
- **在刷机开始前释放 `LOCK_SH`**：弃——违背 ADR-0064 D2 第 5 条（刷机全程必须持 `LOCK_SH` 防止被 LRU/TTL 清理误删正在使用的固件）。

## Verification

- `env -i PATH="$PATH" PYTHONPATH=. /home/debian13/stability-test-platform/venv/bin/python scripts/run_pytest.py backend/agent/tests/test_firmware_cache_b10_u2.py -q`
- 新增并发回归测试 `test_concurrent_cold_pull_waiter_switches_to_sh_without_starving`：在首台冷拉完成后持续有 reader 持有 `LOCK_SH` 的场景下，断言等待中的冷拉同伴在数个 poll 周期内立即以 `hit=True` 返回，不再被 `LOCK_SH` 饿死。

## Revisit

- 本次仅影响 Agent 运行时代码（`agent-code` digest 变更），合入后随下一次控制面部署与 Agent fleet 热更新生效；若后续引入除 `LOCK_EX`/`LOCK_SH` 外的新缓存写者状态，复查等待环中的 `READY_MARKER` 判定。
