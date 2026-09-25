# powercycle_setup v1.2.6：给「等系统就绪」加轮次上界，时钟失效不再等于永转（#3223 账 1）

Status: implemented
Class: bug-fix

## Decision

`wait_system_ready(deadline)` 原先只有**一个**退出条件：`deadline - time.time() <= 0`。
`time.sleep` 一旦不消耗真实时间，这个条件就退化为"必须等满墙钟"，而循环体每次迭代都调用
`system_ready()` → `adb(...)`，在测试/调用方的桩里还会往容器 `append` —— 于是表现从"慢"变成
**吃内存**（实测 ≈150 MB/s；09-23 13:42 控制面宿主卡死那次的元凶链最后一环就是这个形状）。

加**第二个**上界：`STP_ATT_READY_MAX_POLLS`（默认 400 ≈ 正常预算 90s/5s=18 轮的 22 倍冗余），
并把两种结束原因在报文里分开：`deadline_exhausted` vs `polls_exhausted=n/400`。

区分是刻意的：把"等待环失去了时间约束"和"设备真没就绪"写成同一条报文，会把前者归因到设备上
——本族 v1.2.4 曾因 `attempts=1/3` 把预算误指设备，教训同形（#3140）。

**不改** `_ATT_READY_POLL_SECONDS` 的值：那只改变燃烧速率，不改变"无上界"这个事实。

## Alternatives

- **只在测试侧修**（#3213/#3225 已做）：必要但不充分——引信在生产脚本里，任何新的 no-op sleep
  夹具或将来任何"睡而不等"的执行环境都会再点着。本单就是去拆那根引信。
- **加 `min_polls_interval`（强制每次真等）**：否决。循环不许依赖 `sleep` 会推进时钟这个假设，
  而那正是本次被打破的前提。
- **把上界设成 20 轮（贴预算）**：否决。push/安装耗时会计入调用方预算，20 轮可能误截断正常慢就绪；
  400 轮在"永不错杀"与"绝不永转"之间留了 20 倍余量，且它是**可覆盖旋钮**。
- **顺手给 `powercycle_finish._wait_device_online` 等同类循环也加**：否决于本单。那是另一个已发布族、
  要另走版本与发包链；混在一起会让"这次发了哪些包"说不清。清点与后续见 Revisit。

## Verification

```bash
V=/home/debian13/stability-test-platform/venv/bin/python
$V tools/dev/check_script_packages.py --register powercycle_setup 1.2.6
  → [OK] 登记 powercycle_setup@1.2.6 sha=c7ee0cd152d9（3 文件 / 17984 bytes）
$V tools/dev/check_script_packages.py     → [OK] 35 个族树与 tool_manifest.json 最新登记等价
$V tools/dev/check_tool_manifest.py --base origin/main
  → OK: tool_manifest 绿（38 族 / 214 版本条目；相对 origin/main append-only）

pytest backend/agent/tests/test_powercycle_setup_v126.py -q      → 4 passed
  · test_never_advancing_sleep_is_cut_off_by_poll_bound：deadline 在 1h 之后、sleep 不推进时钟
      ⇒ 400 轮截断、耗时 0.01s（此前该形状会跑满墙钟并按圈吃内存）
  · test_default_budget_never_hits_the_poll_bound：时钟正常推进 ⇒ 13 轮/12 次 sleep 后
      deadline_exhausted，绝不触碰 400（防误杀）
  · test_poll_bound_is_an_env_knob / test_ready_path_returns_immediately
pytest backend/agent/tests -q -k powercycle                     → 85 passed + 新增 4
pytest backend/agent/tests/test_powercycle_scripts.py -q        → 50 passed（同族测试未回归）
```

夹具纪律：新测试替换的是**模块属性 `mod.time`**（假对象），不用
`monkeypatch.setattr(mod.time, "sleep", ...)` 去改 stdlib —— 后者会改写**整个进程**的时钟语义，
正是 #3202/#3223 的失控形态；测试自己不能成为下一个引信。

未做（诚实标）：合入后的 `--publish` → `POST /scripts/scan` → fleet 拉包核验属部署动作，按
`script-version-lifecycle` §A.4/A.5 单独执行，本 Note 不声称已完成。

## Revisit

- **发包与生效链**：`check_script_packages.py --publish --packages-root /mnt/stp-aee/packages`
  → `POST /scripts/scan`（`package_missing` 必须空）→ Plan 派发前 `verify_scripts` 预热，或
  `POST /api/v1/script-presence/refresh?host_id=<id>` 期望 `missing=0 mismatch=0`。
  ⚠️ 旧版本仍可被 Plan pin：v1.2.6 只有在 plan_step 指到它（或改默认）后才真正被执行——
  "包已发布" ≠ "现场在用"，别把前者当后者。
- **同类循环清点**：`powercycle_finish`、`check_device` 等族里还有靠墙钟判退的等待环；逐个加迭代
  上界需各自新版本，避免一次发包涵盖多族导致核验面说不清。
- 若 `polls_exhausted` 在现场出现：那是"sleep 未生效"的**信号**而不是设备故障，先查执行环境的
  时间语义，不要去调大 `_ATT_READY_POLL_SECONDS`。
