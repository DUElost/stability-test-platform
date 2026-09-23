# install_apk 用例只 stub sleep 不推时钟：60–90 秒忙等按圈吃内存，冻结控制面宿主（#3202）

Status: implemented
Class: bug-fix

## Decision

（夹具：#3210 已合入失败路径；本 PR 补齐瞬态 push 路径 + 守卫）

`backend/agent/tests/test_powercycle_scripts.py::TestInstallApkV103` 的两条用例曾把
`v103.time.sleep` 换成 `lambda s: None`，但**没有同时替换 `v103.time.time`**。被测
`install_apk` 的等待环靠真时钟判 deadline：

- `wait_system_ready(deadline)` = `while True: system_ready() … time.sleep(min(_ATT_READY_POLL_SECONDS=5, remaining))`，
  deadline 来自 `time.time()`；
- 默认 `_PCS_RETRY_DEFAULT_READY_SECONDS=60`、`_PCS_RETRY_DEFAULT_WAIT_BUDGET_SECONDS=90`、
  `max_attempts=3`。

⇒ sleep 失效后每圈耗时 ≈0，而退出条件只能等真墙钟走完 ⇒ **60–90 秒的忙等**；又因为 adb 桩
`calls.append(list(args))` 每圈留痕 ⇒ 内存按圈单调增长，实测 **≈150 MB/s**。

改法是**用同文件已有的夹具**（不新造机制、不改已发布脚本、不动等待语义）：

```diff
-        monkeypatch.setattr(v103.time, "sleep", lambda s: None)
+        _patch_advancing_clock(monkeypatch, v103)
```

落地节奏：PR #3210（`b74f579f`）先把 `test_pm_install_failure_output_preserved` 改成
`_patch_advancing_clock`；本 PR（#3213）在与 main 合流后保留该改动，并把同形的
`test_transient_push_failure_recovers_on_retry` 一并改掉，避免留下第二条 busy-wait 路径。

配套一条**函数粒度**的守卫 `tests/test_agent_clock_stub_guard_3202.py`：某函数若把
`<mod>.time.sleep` 换成 no-op，同一函数内必须推进时钟。粒度必须是函数——该文件本来就有
`_patch_advancing_clock`，**按文件判会把真凶正好放过**（写这条时实测到的假阴性）。守卫自带
判别力自证（坏形态必红、三种安全形态必绿）与锚点在位断言（#2639 的纪律）。

## Alternatives

- **只加大 `MemoryMax` 让它跑完**：否决。被顶杀死是结论不是障碍（`testing.md` §2）；跑完即等于
  在生产宿主上复现今天的冻结。
- **给 `wait_system_ready` 加迭代上界**（本单最初的设想）：否决于本轮。它落在
  `backend/agent/scripts/powercycle_setup/_lib.py` —— 已发布脚本行为变化须走新版本（ADR-0039），
  且修 test 侧即可消除本次危害；生产侧兜底属另一件事（#3202 尾账）。
- **给整个 `backend/agent/tests` 上同一条守卫**：暂缓。目录里另有 8 个文件存在 no-op sleep 形态
  （`test_device_script_misc_fixes.py` 6 处、`test_sleep_scripts.py` 4 处等），但它们被测的循环
  是否靠墙钟判退**需要逐个证明**；未经证明就登记为"豁免"，等于把判据换成一张自制的免责清单。
  故本守卫先钉住已炸过两次的文件，其余留 #3202 尾账。
- **靠 `pr-agent-tests` 拦住它**：拦不住。修复前该文件在 CI 上是**绿的**（5m50s）——峰值 ~9 GB
  在空闲 runner 上能扛，在只剩 3–8 GiB 余量的生产控制面宿主上不能。**门禁绿 ≠ 宿主安全**，
  这正是加静态守卫的理由。

## Verification

```bash
# 修复前（同一棵树，cgroup 顶内，不影响宿主）
pytest backend/agent/tests/test_powercycle_scripts.py -q -k "not test_pm_install_failure_output_preserved"
  → 46 passed in 0.06s                         # 排除那一条：干净
pytest "…::TestInstallApkV103::test_pm_install_failure_output_preserved"
  → 60s 未结束并被顶杀：kernel: Memory cgroup out of memory: Killed process (python)
    … anon-rss:714660kB                        # 16:43 与 17:09 各复现一次
# 修复后
pytest "backend/agent/tests/test_powercycle_scripts.py::TestInstallApkV103" --durations=5
  → 2 passed in 0.06s（单用例 <5ms，此前 >60s）
pytest backend/agent/tests/test_powercycle_scripts.py -q
  → 47 passed in 0.07s（修复前同一文件在顶内 800 MB 被杀）
pytest tests/test_agent_clock_stub_guard_3202.py -q → 3 passed（含判别力自证）
```

版本对应（防误报）：`64d0ef93`（复现点）与 PR #3208 已推 head `2f47ad96` 之间该文件
`git diff --numstat` 为空 ⇒ 缺陷确已进入 main（`8bc6bc1e`）。随后 #3210（`b74f579f`）修了
失败路径一处；合流后本 PR 仍保留守卫与瞬态 push 路径的同形修复。

## Revisit

- 生产侧兜底：给 `wait_system_ready`/重试预算加**迭代上界**需走脚本新版本（ADR-0039），
  在 #3202 上继续；本轮只做 test 侧。
- 守卫扩面：先逐个证明其余 8 个文件的被测循环不靠墙钟，再把 `TARGET` 换成目录扫；
  证明不了的那些要么改夹具、要么在文档里写明为什么安全。
- 若 CI runner 未来内存变小，这类"门禁绿但宿主死"的形态会先以 runner OOM 形式暴露——
  届时本守卫的作用会由 CI 反证，但不要因为 CI 绿就回退它。

