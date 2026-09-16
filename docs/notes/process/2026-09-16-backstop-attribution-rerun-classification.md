# 兜底单归因：失败 job 重跑一次 + 缺陷/flake 分类（#2333）

Status: implemented
Class: process

## Decision

`main-ci-backstop.yml` 的 `notify-failure` 增一节**归因**，由
`scripts/ci/backstop-attribution.sh` 机械产出（不引入 LLM 分诊）。它补的是
[`2026-09-12-pr-gate-promotion-rule-1525.md`](./2026-09-12-pr-gate-promotion-rule-1525.md)
那条前移规则缺的一维输入：规则的前提是「红灯 = 确定性缺陷」，而 `backend-test` 类红灯里
**混有 flake**（#2333 事实 3：同命令两次运行失败集合不同、隔离跑全绿）——对 flake 前移
等于把 flakiness 引进合入路径，与两分钟注意力窗口的取舍相反。

**决定的两件事**：

1. **红灯时对失败 job 自动 rerun 一次**（`gh run rerun --failed`），按重跑结论机械分类：
   转绿 → **flake**（进"去 flake"，**不进入**前移评估）；仍红 → **确定性缺陷**（才进入
   前移评估）；未在预算内完成或重跑请求失败 → **未能分类**（按缺陷处置，但**不得**作为
   前移评估的样本）。
2. **失败用例名**从 job 日志机械 grep（`FAIL|FAILED|ERROR`，锚定 GHA 时间戳前缀，
   先剥 ANSI 色码）；取不到时**逐 job 显式写出原因**，不静默省略。

**三条硬约束（在实现里编码，不只是注释）**：

- **顺序**：红灯 job 清单与失败用例名都必须在 **rerun 之前**取——`gh run rerun` 会重置
  job 结论（转绿后清单直接变空），job 日志端点也随手指向**新**一次尝试；先重跑再取等于
  把红灯证据擦掉。三要素①因此改由归因步骤快照回传，开单步骤不再自己查 jobs 端点。
- **「一次」是幂等的**：`run_attempt > 1` 时**不再重跑**，直接判为确定性缺陷——避免
  workflow 重入或人工重跑后再叠一次。
- **归因不得打掉通知**：该步骤 `continue-on-error: true`，失败时正文**显式**写
  「归因步骤未成功（outcome=…）」。#1548 就是这个形态（一个步骤报错让红灯彻底静默）。

**影响面**：`.github/workflows/main-ci-backstop.yml`（`notify-failure` 权限增
`actions: write`；新增 checkout + 归因步骤；正文增「归因」节）、
`scripts/ci/backstop-attribution.sh`（新）、`tests/test_backstop_attribution.py`（新）、
`docs/development/repository-workflow.md`「CI 分层」节、
`docs/design/2026-08-governance-surface-protection.md` §6。
**不改**：PR 侧 required 集合、敞口 ≤24h、不引 Merge Queue、不引 LLM 分诊。

**顺带纠正的事实**：#2333 原文把「job logs 接口对本仓库返回空」当作现实理由——那是
`gh api` 的**终端转义序列守卫**（exit 1、stdout 0 字节；stderr 明说要
`--allow-escape-sequences`），不是平台限制。该单正文已更正；本实现的日志取用即带该标志。

## Alternatives

- **只重跑、不分类**（把重跑结论原样贴进单子让人判断）：放弃。分类是零判断成本的机械
  动作（结论相等/不等），正是这一维要自动化的东西；贴原始结论等于把判断再推回给人。
- **只附失败用例名、不重跑**：放弃。用例名能定位缺陷，但给不出「缺陷还是 flake」——
  而后者才是前移规则缺的那一维。
- **引入 LLM 分诊**：放弃（与治理面设计一致）。扩展位留在 job 注释。
- **把归因内联在 workflow 里**：放弃。分类与抽取是多分支逻辑，内联**无法在本机验证**
  （workflow 跑不起来），也会踩到 `pipefail` 下 `head` 提前退出触发 SIGPIPE 那类坑；
  抽成脚本后可用 `DRY_RUN=1` 只换掉取数层、跑真代码。

## Verification

- `tests/test_backstop_attribution.py`（9 例，`DRY_RUN=1` 跑真脚本）：转绿→flake、
  仍红→deterministic、`run_attempt>1` 不再重跑（反例设计：误走重跑分支会取到空结论）、
  取不到日志时逐 job 显式写出原因、ANSI 着色下的 `FAILED` 可抽且正文同名词
  （`FAILEDX`、时间戳后非摘要词）不误配、红灯清单含失败步骤名且绿 job 不进清单；
  另钉三条结构不变量（`continue-on-error`、归因步骤先于开单步骤且开单步骤不再查
  jobs 端点、`actions: write` 在场）。
- 命令与结果：`python -m pytest tests/test_backstop_attribution.py -q` → **9 passed**；
  `python -m pytest tests/test_main_ci_backstop_guards.py -q` → **5 passed**（既存守卫未破坏）；
  `python scripts/run_gates.py check:quick` → **10 gates [OK]**。
- **未覆盖**：真实红夜的端到端（需等一次红灯）。#2333 的验收允许「构造一次红夜或走
  dry-run 路径验证」，本项走 dry-run 路径；真实红灯下 `gh run rerun` 与日志端点取用
  尚未在线上跑过。

## Revisit

- 重跑若多数转绿（flake 占红夜多数）→「去 flake」需要真正的沉淀去向：本项只产出**标签**，
  没有队列条目（#2333 已指出 flake 无沉淀路径）。
- 等待预算（`WAIT_MAX` × `WAIT_INTERVAL`，默认 30s × 120 = 1h）若在慢夜不够 →
  「未能分类」会增多，应上调；注意未分类**不参与**前移评估。
- `FAIL|FAILED|ERROR` 的机械抽取若噪音过大 → 收窄锚点。当前只在 GHA 时间戳之后取词
  （已排除正文同名词），但应用侧以 `ERROR` 开头的日志行仍可能被收进来。
