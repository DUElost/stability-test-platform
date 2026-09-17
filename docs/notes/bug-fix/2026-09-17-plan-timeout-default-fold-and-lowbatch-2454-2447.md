# 步骤墙钟「未配置」不再被折成 30s + 24h 审计低危批（#2454 / #2447）

Status: implemented
Class: bug-fix

## Decision

**#2454 步骤超时读回折默认值（#2382 的前端侧残留）**：`rebuildLifecycleFromPlan`
把 `s.timeout_seconds ?? 30` 写进编辑器表单，而保存是整体替换 PlanStep 行——「打开 +
保存」一次就把「未配置」钉死成显式 30，压掉后端
`STP_STEP_WALL_CLOCK_SECONDS` → 300s 的回落链。**改法与三行之下的 `stall_seconds`
同一条规则**（那里注释已写明「无值就不写键」）：

```ts
...(s.timeout_seconds != null ? { timeout_seconds: s.timeout_seconds } : {}),
```

保存侧 `?? null` 不动（值不再被折成 30 后，null 兜底才真正生效 = 未配置落库）。
UI 侧无需改：`stepTiming.ts` 已支持三态展示（`0` → ∞、`null` → 「默认」/未配置提示、
`n` → `ns`），此前只是读回路径永远喂不进 `null`。

**#2447 24h 审计低危批（4 项，逐条核实后处置）**：

| 项 | 结论 |
|---|---|
| ① 摘要推送失败自续无退避 | **修**：失败分支改走 `_schedule_retry()`——指数退避（1×/2×/4×…，上界 30s）、连续失败超 `_MAX_RETRY_STREAK=5` 后**停到下一次真实变更**（正常入口清零 streak）；`_flush_task` 改为运行期保留 + done 回调清理（此前只写不读，串行化判据恒为假）；新增 `shutdown_dashboard_summary_publisher()` 并接入 `main.py::_lifespan_cleanup` |
| ② DLE 忽略分支不刷 `updated_at` | **已在 main 上正确，未改**：`row.state = target_state`(:2086) 与 `row.updated_at = now`(:2120) 同在 `else` 块内；真正「什么都没改」的忽略分支（:2062 `dle_stale_replay_ignored`）**本就不该**刷 `updated_at`——刷了反而会让「末次写入时间」说谎 |
| ③ 设备事件前缘节流丢事件 | **修**：`useFleetDeviceUpdates` 改为「前缘立即 + 尾部补一次」。不做纯 debounce——持续密集推送会让失效永远推迟（饥饿），而纯前缘正是原缺陷（窗口内事件整条丢弃） |
| ④ 风险卡文案前缀拼歪 + 忽略 `retryable` | **修**（这条是我 #2364 引入的）：`notFound` 由完整句改为「接口不存在（…）」，卡片前缀统一为「风险分布加载失败：」（此前网络/服务端分支同样被拼成病句）；`retryable === false`（404）时不再挂重试按钮，与 `PlanRunDetailPage` 同口径 |

## Alternatives

- **#2454 A. 反过来收紧后端**（把「未配置」也当 30s）：否决。`0`/未配置/`n` 是引擎
  三态（`_resolve_step_wall_clock`），把未配置折成 30 等于给所有没配墙钟的步骤拆掉
  `STP_STEP_WALL_CLOCK_SECONDS` 这条运维通道——正是 #2382 定案时否掉的。
- **#2454 B. 在 UI 加「未配置」输入态**：不做（#2382 已判定属单独一单）。本单只让读回
  不再伪造值；现有 `stepTiming` 展示已经能表达三态。
- **#2447-③ A. 纯 trailing（`clearTimeout + setTimeout`）**：否决。设备事件可能持续密集，
  trailing-only 会让失效无限推迟；`leading + trailing` 既有上界（≤1 窗口）又不饥饿。
- **#2447-① B. 只加退避、不接 shutdown**：否决。关闭窗口里的一次 flush 会在引擎/DB
  正在关闭时跑全量聚合 + 广播，日志噪声之外没有收益；teardown 已经有了（模块自带的
  reset 只是没接线）。
- **#2447-② 顺手把 `dle_stale_replay_ignored` 也刷 `updated_at`**：否决，见上表。

## Verification

- **红绿差分（先证伪再实现）**：
  - #2454：新增三例（NULL 不折 30 / 保存仍发 null / `0` 原样往返）在基线实现上红
    （基线的 `?? 30` 会让第一例拿不到「无该键」），新实现 15 passed；
  - #2447-③：新用例在基线实现上红——基线是纯前缘节流，窗口内第二次推送后
    **尾部永不补**，`expect(invalidate).toHaveBeenCalledTimes(2)` 拿不到第 2 次；
  - #2447-①（退避接线）：把失败分支改回 `schedule_dashboard_summary_push()`（恒定间隔）
    → `AssertionError: 失败路径没有退避：[0.01, 0.01, … ×13]`——**150ms 内重试 13 次**，
    正是 issue 描述的「1 Hz 永远重试」形态；恢复后 6 passed；
  - #2447-①（串行化）：把 `_flush_task = None` 放回 flush 开头（还原旧行为）→
    `AssertionError: 上一次 flush 未完成时不应叠加新任务`；恢复后通过。
- **测试**：后端 `test_dashboard_summary_publisher.py`（6 passed，新增 4 例）+
  `test_dashboard_summary.py` + `test_stats.py` → 38 passed；lifespan 相关
  （`test_health_saq.py` / `test_agent_secret_guards.py` / `test_saq_tasks.py`）→ 57 passed；
  前端全量 `vitest run` → **119 files / 952 tests 全部通过**（新增
  `useFleetDeviceUpdates.test.tsx` 4 例、Dashboard 3 例合并后的 9 例）；
  `tsc --noEmit`、`eslint --max-warnings 0` 通过；`check:quick` → 10 gates OK。
- **#2447-② 的「未改」也有据**：`grep -n "row.state = \|row.updated_at = "` 显示两者同块
  （2086/2120），忽略分支（2062）不产生任何状态变更——**没有可观测错行为**，按 issue
  自己写的「今天没有可观测错行为」不动。

## Revisit

- **`_flush_task` 的串行化语义**：现在是「上一个未完成就跳过本次武装，脏标记留着，
  等下一次变更/失败再武装」。若将来出现「变更稀疏 + flush 很慢」的组合，脏数据可能
  等到下一次变更才被推——届时应改成「完成后若仍脏则立刻再跑一次」而不是本次的跳过。
- **#2447-② 的时间戳语义**：本单确认「无变更不刷 `updated_at`」是对的；若将来有人按
  「末次接触时间」使用该列（例如做 lease/心跳判定），要先区分「末次写入」与「末次接触」。
- **#2454 的 UI 三态输入**：目前编辑器仍无法**主动**表达「未配置」（只能保持既有 NULL）。
  要放开成三态输入（未配置/不限/自定义）属单独一单（#2382 同判）。
- **我引入的 #2364 文案缺陷**（本批 ④）说明：给 `loadErrorCopy` 这类「返回完整句子」的
  helper 拼前缀很容易拼出病句——新增调用点时优先传**片段式**文案，或用整句 + 无前缀。
