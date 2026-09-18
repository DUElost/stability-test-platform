# settle 窗补偿路径同吃：修「有效窗≈60s」缺口（#2755 跟进）

Status: implemented
Class: bug-fix

## Decision

`reconcile_chain_trigger_sync` 末尾的补发改传 `respect_settle=True`——settle 窗
锚定 `parent.ended_at`，即时路径与补偿路径（reconciler 60s tick / post-completion /
recycler 重试）过**同一道窗**。

背景：#2755 的实现 PR #2760 把窗做成「仅即时路径」，补偿路径默认 False。但
`plan_chain_reconciler.py:36-43` 的扫描条件（terminal + next_plan_triggered=False +
无 child）**没有 ended_at 年龄过滤**，即时路径刚跳过的 parent 下一 tick 就是命中项，
于是窗内 parent 会在 ≤60s 内被补发——**有效稳定窗 ≈ 一个 reconciler tick，与
`CHAIN_TRIGGER_SETTLE_SECONDS=180` 的取值无关**（评审评论
pr#2760/comment/5733663008；该 PR 在评论 35s 后被 FIFO 合入，缺口进入 main）。

本修正后「实际触发时刻 ≈ settle + (0~60s)」的既有注释承诺**成为事实**（settings 与
`.env.example` 两处措辞已同步）；过窗的老 parent 窗口已自然耗尽，补偿/修复语义零损失：
窗内 skip 不设 flag、不建 child，reconciler 下一 tick 重试直至窗过。

## Alternatives

- **reconciler 扫描加 `where(ended_at <= now - settle)`（评审里的方案 A）**：效果
  等价，但把窗语义复制到扫描 SQL 里，与 `_settle_wait_left` 形成两处判定源；改动点
  在调用处（方案 B）让窗判定只剩 `_settle_wait_left` 一个事实源，且天然覆盖
  post-completion / recycler 这两个不走 reconciler 扫描的补偿入口——方案 A 只堵
  reconciler 一条路，post_completion 在窗内的重试仍会绕过。

## Verification

- 新增 3 例 + 翻转 1 例（`TestChainTriggerSettleWindow`）：补偿 helper 窗内不建
  child/不设 flag（caplog 可观测）、过窗照发、orphaned-flag 修复形态窗内只清 flag
  不借道补发；原「补偿路径照发」用例改写为「函数默认 False（直接调用方行为不变）」。
- `test_plan_chain_trigger.py` → **23 passed**；`-k "chain or reconcile or settle"`
  跨 services+scheduler → **67 passed**。
- 既有 `test_orphaned_true_flag_is_reset_before_redispatch` 因补发调用新增 kwarg
  而签名失配，`**_kw` 适配（其「先清 flag 再重派」断言不变）。

## Revisit

- 方向 1（准入就绪探测）仍是更本质方案：落地后窗可归零或留作第二道；本窗是
  最小可逆止血，`CHAIN_TRIGGER_SETTLE_SECONDS=0` 即回退。
- settle=180 的取值依据仍是 r431 单点证据（失败集中前 5 分钟、2.7h 健康），
  积累多窗数据后应复核。
