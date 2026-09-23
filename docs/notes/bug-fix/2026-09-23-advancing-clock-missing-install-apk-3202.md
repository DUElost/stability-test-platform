# install_apk 用例只 stub sleep 不推时钟：60–90 秒忙等按圈吃内存，冻结控制面宿主（#3202）

Status: implemented
Class: bug-fix

## Decision

（夹具：#3210 已合入失败路径；本 PR 消掉瞬态 push 路径的同形脆形态 + 守卫）

`backend/agent/tests/test_powercycle_scripts.py::TestInstallApkV103` 的两条用例曾把
`v103.time.sleep` 换成 `lambda s: None`，但**没有同时替换 `v103.time.time`**。被测
`install_apk` 的等待环靠真时钟判 deadline：

- `wait_system_ready(deadline)` = `while True: system_ready() … time.sleep(min(_ATT_READY_POLL_SECONDS=5, remaining))`，
  deadline 来自 `time.time()`；
- 默认 `_PCS_RETRY_DEFAULT_READY_SECONDS=60`、`_PCS_RETRY_DEFAULT_WAIT_BUDGET_SECONDS=90`、
  `max_attempts=3`。

⇒ 若 sleep 失效且等待环**没立刻拿到就绪**，每圈耗时 ≈0 而退出条件只能等真墙钟走完 ⇒
**60–90 秒的忙等**；又因为 adb 桩 `calls.append(list(args))` 每圈留痕 ⇒ 内存按圈单调
增长，实测 **≈150 MB/s**。

**实测分界（勿写成"两处都会炸"）**：

- `test_pm_install_failure_output_preserved`：**会炸**。修复前 cgroup 顶内 >60s 未结束并被
  顶杀（anon-rss≈714 MB，16:43 / 17:09 各一次）。已由 **#3210 / `b5dab6b3`**（再经
  `b74f579f` 进 main）改成 `_patch_advancing_clock` ⇒ **main 当前没有活的失控体**。
- `test_transient_push_failure_recovers_on_retry`：**当前不炸**。退回未修复态单跑仍
  `1 passed in 0.06s`、无内存增长——因为 mock 的 `results` 序列让 `wait_system_ready`
  立刻拿到"就绪"并返回，**不是**判据结构安全。把 `results` 改短就会变成同形的 60–90 s
  忙等 + 每圈 `calls.append` ⇒ 属**脆形态**。本 PR 对它的改动是**消掉脆形态**，不是
  "修第二个 bug"。

改法是**用同文件已有的夹具**（不新造机制、不改已发布脚本、不动等待语义）：

```diff
-        monkeypatch.setattr(v103.time, "sleep", lambda s: None)
+        _patch_advancing_clock(monkeypatch, v103)
```

落地节奏：#3210 先修失败路径；本 PR（#3213）在与 main 合流后保留该改动，并对瞬态
push 路径消掉同形脆形态，再加守卫。

配套一条**函数粒度**的守卫 `tests/test_agent_clock_stub_guard_3202.py`：某函数若把
`<mod>.time.sleep` 换成 no-op，同一函数内必须推进时钟。粒度必须是函数——该文件本来就有
`_patch_advancing_clock`，**按文件判会把真凶正好放过**（写这条时实测到的假阴性）。守卫
**按形态判、不按当前 mock 返回值判**：对瞬态 push 路径在补夹具前判红、补后判绿，即其
有效性的自证——不要因为"当前不炸"把守卫当成误报关掉。另有判别力自证（坏形态必红、
三种安全形态必绿）与锚点在位断言（#2639 的纪律）。

## Alternatives

- **只加大 `MemoryMax` 让它跑完**：否决。被顶杀死是结论不是障碍（`testing.md` §2）；跑完即等于
  在生产宿主上复现今天的冻结。
- **给 `wait_system_ready` 加迭代上界**（本单最初的设想）：否决于本轮。它落在
  `backend/agent/scripts/powercycle_setup/_lib.py` —— 已发布脚本行为变化须走新版本（ADR-0039），
  且修 test 侧即可消除本次危害；生产侧兜底属另一件事（#3202 尾账）。
- **给整个 `backend/agent/tests` 上同一条守卫**：暂缓。目录里另有 8 个文件存在 no-op sleep 形态
  （`test_device_script_misc_fixes.py` 6 处、`test_sleep_scripts.py` 4 处等）。修复后整目录
  正控制（见 Verification）已证明它们**当前没有**同类失控——扩守卫是**防脆化**，不是在追
  已存在的火。未经逐个证明就登记为"豁免"，等于把判据换成自制免责清单；故本守卫先钉住
  已炸过的文件，其余留 #3202 尾账。
- **靠 `pr-agent-tests` 拦住它**：拦不住。修复前该文件在 CI 上是**绿的**（5m50s）——峰值 ~9 GB
  在空闲 runner 上能扛，在只剩 3–8 GiB 余量的生产控制面宿主上不能。**门禁绿 ≠ 宿主安全**，
  这正是加静态守卫的理由。

## Verification

```bash
# 会炸的那处（修复前，同一棵树，cgroup 顶内，不影响宿主）
pytest backend/agent/tests/test_powercycle_scripts.py -q -k "not test_pm_install_failure_output_preserved"
  → 46 passed in 0.06s                         # 排除那一条：干净
pytest "…::TestInstallApkV103::test_pm_install_failure_output_preserved"
  → 60s 未结束并被顶杀：kernel: Memory cgroup out of memory: Killed process (python)
    … anon-rss:714660kB                        # 16:43 与 17:09 各复现一次
# 脆形态那处：退回未修复态仍不炸（mock 立刻就绪）
pytest "…::TestInstallApkV103::test_transient_push_failure_recovers_on_retry"
  → 1 passed in 0.06s                          # 无内存增长；改 results 才会变忙等
# 修复后 / 正控制
pytest "backend/agent/tests/test_powercycle_scripts.py::TestInstallApkV103" --durations=5
  → 2 passed in 0.06s
pytest backend/agent/tests/test_powercycle_scripts.py -q
  → 47 passed in 0.07s（修复前同一文件在顶内 800 MB 被杀）
pytest tests/test_agent_clock_stub_guard_3202.py -q → 3 passed（含判别力自证）
# 整目录正控制（MemoryMax=3G、MemorySwapMax=0）
pytest backend/agent/tests -q
  → 2117 passed in 246.24s / RC=0，全程一次都没被顶杀
```

版本对应（防误报）：`64d0ef93`（复现点）与 PR #3208 已推 head `2f47ad96` 之间该文件
`git diff --numstat` 为空 ⇒ 缺陷确已进入 main（`8bc6bc1e`）。随后 #3210（`b5dab6b3` /
`b74f579f`）修了失败路径 ⇒ main 无活失控体；合流后本 PR 保留守卫并对瞬态 push 路径消掉
同形脆形态。

## Revisit

- 生产侧兜底：给 `wait_system_ready`/重试预算加**迭代上界**需走脚本新版本（ADR-0039），
  在 **#3223** 上继续（本 Note 原写「在 #3202 上继续」，而 #3202 已随 PR #3213 关闭 ⇒ 指针悬空，
  现已由 #3223 承接两条尾账：迭代上界 + 存量 33 处收敛）。
- 守卫扩面：**已做**（`tests/test_agent_clock_stub_guard_3202.py` 改目录级扫描 + 函数粒度豁免
  登记，实测 11 文件 / 33 处，每条带 cgroup 顶内的 `passed/耗时/峰值` 证据）。反证方式也记录在案：
  往**干净文件**注入一条新违规 ⇒ 判红并点名；往**已在豁免里**的函数注入 ⇒ 不新增红（这是我第一次
  做反证时踩到的设计错误，别用那种方式自证）。登记不缩短即为未完成，收敛跟 #3223。
- 若 CI runner 未来内存变小，这类"门禁绿但宿主死"的形态会先以 runner OOM 形式暴露——
  届时本守卫的作用会由 CI 反证，但不要因为 CI 绿就回退它。
