# 租约校验退避预算分钟化 + 中断/丢锁分级（#1881）

Status: implemented
Class: bug-fix

## Decision

生产实证（2026-09-13）：控制面约 1 分钟不可用期间，设备侧 24h/45h 长跑 job 成批
被 `lease re-verification failed: lock_verification_http_502 / lock_verification_unreachable`
判死（run 374 7/7 ABORTED、run 371 5/14 ABORTED）。原因是 `_verify_device_lease`
的退避预算 `[1,2,4]≈7s` 只挡得住秒级抖动；几百个 job 每 300s 各有一次校验窗口，
任何一次 >7s 的中断都会与大量窗口重叠——中断越短越像「恰好命中一批」。

按 issue 的两条方向同时落（互补而非二选一）：

1. **退避预算分钟化**（`_LEASE_VERIFY_RETRY_DELAYS = (1,2,4,8,15,30)`，≈60s）：
   模块常量 + 注释说明依据；`_verify_device_lease` 的 5xx 与连接异常两条重试路径
   共用该预算（连接异常此前同样只退避 3 次）。
2. **中断 / 丢锁分级**（`_lease_verify_outage_decision(error_message, streak)`，
   纯函数、可单测）：
   - `lock_verification_*`（我方不可达 / 5xx）→ **累计**连续失败窗口，达到
     `_LEASE_VERIFY_OUTAGE_ABORT_STREAK = 3`（间隔 300s ⇒ ≈15 分钟）才终止；
     未达阈值只记 WARNING 并继续跑；
   - `device_lease_not_held`（409，服务端明确拒绝 = 真丢锁）与
     `lock_verify_auth_failed`（401）→ **立即终止**（非目标：不改该语义），
     并清零中断计数；
   - 校验成功 → 计数清零。

patrol 循环据此改造（`lease_verify_outage_streak` 局部计数），WARNING 文案带
`%d/%d` 便于事后把「中断导致」与「真丢锁」分开统计；判死时仍以
`lease re-verification failed: <原始错误串>` 收敛，错误串保持可判别。

## Alternatives

- **只加长预算、不分级**：弃——预算再长也只是推迟判死；分钟级中断期间 job 仍会
  在「预算耗尽」时被终止，且预算越长单次校验阻塞越久。分级让「短暂中断」与
  「长期失联」有不同处置；
- **只分级、不加长预算**：弃——单次窗口内 7s 就放弃，会把「几秒抖动」也算作一次
  中断；分钟化预算让单次窗口自身具备抗抖动能力，两者叠加才覆盖 issue 的两个
  失效场景；
- **不可达时无限重试/永不判死**：弃——控制面长期失联（如网络分区）时 job 会
  永远跑在无租约保护状态；阈值 3×300s≈15 分钟给了明确的终态出口；
- **把 `device_lease_not_held` 也纳入累计**：弃——那是服务端明确拒绝（别人已持有
  租约），继续跑只会写冲突数据，必须立即终止（issue 非目标）；
- **新增配置开关（env 调阈值）**：暂缓——阈值先以常量落地并注释依据；若现场
  出现不同网络形态（跨区高延迟），再评估 env 化（见 Revisit）。

## Verification

- **新增 8 例**（`backend/agent/tests/test_lease_verify_outage_budget_1881.py`）：
  预算 ≥60s；持续 502 时重试次数 = 预算长度且逐次按预算退避、错误串
  `lock_verification_http_502`；连接异常同样用满预算（`lock_verification_unreachable`）；
  分级判死参数化（409/401 立即终止、`lock_verification_*` 累计 1→3 才终止、
  真丢锁插入时立即终止并清零）；**patrol 集成**：一次中断窗口下 patrol 继续跑，
  日志出现 `lease verify outage 1/3` 且**无** `lease lost during patrol`；
- **反例实证**：
  - 预算退回 `(1,2,4)` → `test_budget_covers_minute_scale_outage` **failed**；
  - 移除分级（不可达也立即判死）→ **3 failed**（累计判死、混合原因、patrol 集成）；
  恢复后 8 passed；
- **回归**：`backend/agent/tests/` 全量 → **1898 passed**（163s）；
- `ruff` → All checks passed；`check:quick` → **7 gates OK**。

未做：真机/生产复现（依据为代码路径 + 单测/集成测试；生产证据由 issue 提供）。

## Revisit

- **阈值与预算的现场调参**：3×300s 与 ~60s 预算是依据「nginx 502 / 重启级别中断」
  选的；若跨区控制面 RTT 高或中断常超 15 分钟，应把
  `_LEASE_VERIFY_OUTAGE_ABORT_STREAK` 与 `_LEASE_VERIFY_RETRY_DELAYS` env 化
  （当前刻意不加开关，避免半调参状态）；
- **可观测面**：目前只有 WARNING 日志与错误串前缀。若要把「中断导致 vs 真丢锁」
  做成看板，需在 `_end_patrol` 或上传侧加分类计数（属观测面增强，独立评估）；
- **其它租约校验调用点**：`_execute_pipeline` 开场的 `_verify_device_lease`
  （非 patrol 路径）仍按「预算耗尽即失败」处理——它没有 patrol 的 300s 周期可
  分摊，语义上属「起动即校验」，本单未改（issue 非目标）；若现场出现开场即被
  中断打穿，可复用同一分级（把 streak 放入实例状态）；
- **与 #872 的边界**：本单只管 patrol 期租约校验；barrier 续期信任执行态的硬顶
  兜底仍归 #872。
