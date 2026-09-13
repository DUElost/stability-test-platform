# barrier 续期改为「信任执行态」+ 默认硬顶兜底（#872 Part 1）

Status: implemented
Class: bug-fix

## Decision

#872：`_peers_are_progressing` 旧判据「`EXECUTING_STEP` 且
`last_progress_at` 新鲜（< 120s）才算活」在**打戳覆盖率不齐**的现实下会误杀
合法长步骤——run 338（484 台）里 4 台 MTK init 已完成却在 barrier 等
11.5min 后被 `barrier_timeout` 判死，原因是慢 peer 处于长步骤
（AutoTestTool 15MB push+install 数分钟）期间不刷戳 → 不算 progressing →
早完成者的 600s 滑窗不续期。

**止血（本批，平台侧单点改动）**：

1. **执行态即活性证据**：`EXECUTING_STEP` 无条件计入续期（`WAITING_BARRIER`
   仍排除，防互相续期死锁；`WAITING_EXECUTION_SLOT` 维持原判）。打戳新鲜度
   降级为**诊断信号**——抽出 `_peer_stamp_is_fresh` 供超时日志区分，
   不再作为唯一判据（覆盖「陈旧戳」与「无戳」两形态，均有回归用例）。
2. **默认硬顶兜底**：信任执行态后必须有上限，否则真卡死的 peer 会被无限
   续期。新增 Agent 侧默认 `_DEFAULT_BARRIER_MAX_WAIT_SECONDS`
   （env `STP_BARRIER_MAX_WAIT_SECONDS`，默认 **1800s**；0/负值 = 显式不设
   上限，保留 #174 调试语义）。优先级：Plan 级 `barrier_max_wait_seconds`
   > env 默认；到期 reason=`barrier_max_wait`。

**评估过的证据**：20+ 个最新版脚本 0 处打戳（含 gpu_setup/powercycle_setup/
fill_storage/push_resources/install_apk 等长耗时路径），说明「长步骤必须打戳」
契约短期无法兑现——平台活性判据不能建立在脚本纪律上。

**Part 2（契约兑现，长期跟进）**：已建 #1690——首批给 gpu_setup /
powercycle_setup / fill_storage 三个最常用脚本补戳（新版本），其余逐批收敛。
平台侧放宽不代表 stall 钟问题消失：启用 `stall_seconds` 的 Plan 仍会误杀无戳
长步骤，两处修复互补。

## Alternatives

- **脚本侧全量补戳（先做 A 不做平台）**：契约内但周期长、20+ 版本发布；
  且未来新脚本仍会漏——平台在该契约上无强制手段（#1690 承接长期收敛）。
- **语义降级（超时不判死、直接进 PATROL）**：彻底消除误杀但弱化「整波齐进」
  保护（慢 peer 的 init 会与快 peer 的 patrol 并发），改动面也更大；未采用。
- **只调大 `STP_BARRIER_PROGRESS_STALE_SECONDS`**：治标——阈值仍是猜数
  （长步骤多长算长？），且换一批更慢步骤又复发。
- **信任执行态但不给默认硬顶**：真卡死 peer 会拖死整波（无限续期）——
  与「不能无限等」的既有 review 原则冲突，必须成对落地。

## Verification

- `pytest backend/agent/tests/test_coordinator_peers.py`：**19 passed**——
  矩阵反转（陈旧戳/无戳的 EXECUTING_STEP 均算活）+ 集成回归
  （陈旧执行态 peer 越过原 0.2s 滑窗不超时；真停滞=无状态 → 按原滑窗超时；
  默认硬顶 0.3s 命中 reason=barrier_max_wait；显式硬顶优先路径不回归）；
- `pytest backend/agent/tests` 全量：**1770 passed**；
- `ruff check backend/ tools/ scripts/` 全绿。

## Revisit

- **#1690**（Part 2）：三脚本优先补戳 + 其余批次收敛；`stall_seconds` 启用面
  与打戳覆盖率的匹配关系在那单持续维护；
- 默认硬顶 1800s 的取值：按「1.5× 最长单步骤预算 × 队列深度」估算，随补戳
  进展与实际观测（`barrier_max_wait` 日志）再校准；
- barrier 超时日志已带 peer 状态快照与 staleness 诊断——若 `barrier_max_wait`
  在正常场景频繁命中，说明默认值或续期语义需要再评估。
