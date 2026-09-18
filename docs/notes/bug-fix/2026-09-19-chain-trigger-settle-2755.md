# 链触发最小稳定窗：即时路径父终态后 180s 内跳过，reconciler 补偿（#2755）

Status: implemented
Class: bug-fix

## Decision

采纳票面方向 **2（最小稳定窗）**，不动方向 1（就绪探测）：

- `trigger_next_plan` / `trigger_next_plan_sync` 新增 `respect_settle` kwarg——
  **只有即时路径**（`job_terminalization._post_aggregation_side_effects_*`，即父段
  最后一个 job 终态那一跳）传 True；reconciler 与 post-completion 补偿路径保持
  False。窗内跳过=不设 flag、不建 child、打 `plan_chain_trigger_settling`
  日志，交 `chain_reconciler`（60s tick）以默认参数补发——所以跳过不是丢失，
  实际触发时刻 ≈ settle + (0~60s)。
- 配置 `CHAIN_TRIGGER_SETTLE_SECONDS` 默认 **180**（owner 建议 2–5 分钟取中；
  0 = 关闭回退旧行为）。判据用 `parent.ended_at`，缺失不阻断（历史行为）。
- 幂等回放不吃窗：child 已存在时先返回既有（顺序刻意：status→next_plan→existing
  →triggered→settle→选设备→prepare），settle 只挡「新建 child」这一跳。

数据依据（票面对照表）：r431 间隔 2s → init 失败 40.6%；r427 同链型间隔 2.7h →
4.6%；r428 父段是开关机（设备新鲜）4s 也只 5.2%——问题只在「父段是长时间浸泡
（monkey 1h）后立刻接链」。稳定窗削的就是这个尖峰。

## Alternatives

- **方向 1（准入健康门）**：更本质（探测就绪而非固定等待），但改动面落在
  dispatch/claim 侧且预检脚本选型未定——票面自己标了「待裁决」，不抢跑；
  窗是兜底、探测是主菜的话，探测落地后窗可缩到 0 或留作第二道；
- **把 settle 做进 reconciler（调大其 interval）**：弃——reconciler 是通用补偿轨，
  为一种时序调全局周期，会拖慢所有真实中断的修复；
- **async 路径 `asyncio.sleep(settle)`**：弃——占住 SAQ worker 分钟级，
  post_completion 吞吐直接受害。

## Verification

- `test_plan_chain_trigger.py` → **20 passed**（新类 5 例：窗内跳过零副作用+日志、
  过窗照发、补偿路径不吃窗、`ended_at` 缺失不阻断、幂等回放不吃窗）；
- 既有 rollback 用例（#986 族）在即时路径带窗后需显式关窗直达 prepare 断言路径
  （`_settle_off`），用例意图未稀释；
- 相关回归批（plan_run_chain/schedules/aggregation/phase0）与 `check:quick` →
  见 PR。

## Revisit

- 若 r431 型事故在 settle=180 后仍现（长收敛设备 >3min），按票面方向 1 上
  **准入健康门**（init 前 adb state + check_device 轻量探测，未就绪延迟而非
  FAILED），窗降为兜底值——两向不互斥，探测落地后 settle 可归零；
- `test_missing_ended_at_does_not_block` 用的是「历史数据没有 ended_at」的
  兼容路径；若未来给 CHAIN 子 run 补 ended_at 语义时误清此列，本例是哨兵。
