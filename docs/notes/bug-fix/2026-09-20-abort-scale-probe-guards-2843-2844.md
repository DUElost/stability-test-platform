# abort_scale_probe：seed/cleanup 目标库 fail-closed 守卫 + 去已删列（#2844 / #2843）

Status: implemented
Class: bug-fix

- 日期：2026-09-20
- 关联：`#2844`（破坏性腿零守卫）、`#2843`（引用已删列致 seed 崩）、
  `#703①`（本工具的出处）、`#2734`/ADR-0048（删 `failure_threshold` 的迁移）、
  `#2790`（同型调用点的 CI 侧修复）、`#2632`/`#1300`（`db_url_guard` 先例）

## Decision

两条都是**我交付的工具有缺口**，同批修（同一文件）：

### 1. #2844：seed/cleanup 先过 dev 目标守卫（fail-closed）

新增 `_require_dev_db_target()`，作为 `cmd_seed` / `cmd_cleanup` 的**第一条语句**（先于任何
ORM import——`backend.models.*` 会连带建引擎）：

- **必须显式导出 `DATABASE_URL`**：缺了就直接拒。理由见下；
- 目标须是 **dev 形态**：库名 `stp_dev` **或** PG 端口 `15432`（compose 映射）之一；
- 其余一律拒，错误信息写明「生产/未知库上一律不动手」。

**为什么必须有这道闸**：`backend.core.database` 的 DSN 经 `env_source.resolve_database_url()`
解析——ambient 没有 `DATABASE_URL` 时**静默回退仓库根 `.env.backend`（本仓文档化的生产 env 源）**。
而 seed 写 30 host / 510 job、cleanup 直接 DELETE 同规模：忘一条 `export` 就打在真生产库上
（AGENTS.md 红线「禁止在生产库试跑测试」）。同文件的 run 腿早有 `_require_local`（拒绝非回环
控制面），**守卫不对称**是这条缺陷的形态。

### 2. #2843：删掉两处 `failure_threshold=0.0`

该列已随 ADR-0048/#2734 从 `Plan`/`PlanRun` 移除（模型 grep = 0），探针仍给它绑值 ⇒ `seed`
**当场崩**（`ArgumentError: Unconsumed column names`），整条 seed→run→cleanup 链不可用。
CI 侧同型调用点已在 #2790 修掉，探针这个调用点被漏。

## Alternatives

- **复用 `backend/core/db_url_guard.py`（#2632 系）原样**：它的第一道闸要求库名含 `test`，
  而本探针的目标是 dev 栈（`stp_dev`）⇒ 合法目标会被误拒。故取它的**判据形态**
  （fail-closed + 显式豁免面）而不复用函数。
- **加 `--target-dev` / `--i-know-this-destroys-data` 两个 flag**：比环境变量更显眼，但要把
  「哪个库」这份事实拆到两处（flag 说「是 dev」、env 说「连哪里」），不如直接校验目标本身；
  且当前调用形态就是「export DSN 再跑」（README/Note 里的用法）。
- **只写文档提醒**：静默回退 + 破坏性操作 + 守卫不对称，三者叠加时「靠记得 export」正是
  #2706 那类「提示在场仍出事」的形态。否。
- **#2843 只改代码不加判据**：该列是**被删除**而非改名，同类风险（removal 类迁移的消费面漏查）
  会复发——故补一条结构判据（源文件不得再出现该列名）钉住。

## Verification

- `python -m pytest tests/test_abort_scale_probe_guards.py -q` → **8 passed**（守卫三态 +
  端到端入口 + 两条结构判据）；
- `python -m pytest tests/test_source_scan_anchor_ratchet.py::test_offenders_equal_baseline_no_growth_no_staleness -q`
  → **passed**（#2860 CI：`pr-agent-tests` repo_rc=1 曾因本文件裸 `assert … not in source`
  增长棘轮 offender；已改为 `SourceGuard.of_repo_path(...).anchored(...).assert_absent(...)`）；
- **4 条定向变异逐条回退即红**：去掉 `cmd_seed` 的守卫调用 → **2 failed**；守卫空集分支失效
  → **1 failed**；守卫提前 `return` 放行一切 → **3 failed**；重新引用 `failure_threshold`
  → **1 failed**；
- `python scripts/run_gates.py check:quick` → **12 gates 绿**（含 ruff）。

**一条自纠（写测试时的隐患，已修）**：我最初把「生产形态」的假 DSN 写成
`postgresql+psycopg://stp:secret@127.0.0.1:5432/stp`——而**本机 5432 就是生产 PG**。变异测试
（去掉守卫）时它真的去连了那个地址（因口令不符被拒，未触及数据）。已改为**未监听端口 5599**：
守卫失效时得到的是「连接被拒」，而不是「打到疑似生产库」。**破坏性工具的测试夹具本身也必须
遵守红线**——这条与「变异的锚点必须有判别力」同源，都是「测的是不是那件事」。

**CI 自纠（#2897 / #2860）**：否定结构判据最初写成裸 `read_text` + `assert "failure_threshold" not in
source`，触犯 #2639 棘轮（锚点漂移时恒真空守）。未放宽守卫、未扩 BASELINE——只把该断言迁到
`SourceGuard.assert_absent`。

## Revisit

- **dev 形态判据是 dev 栈特定的**（库名 `stp_dev` / port 15432）：dev 栈改端口或换库名时，
  这道闸会开始误拒——判据集中在 `DEV_DB_NAME` / `DEV_DB_PORT` 两个常量，改一处即可；
  若将来出现第二个合法的压测目标，应引入显式的「目标白名单」而不是放宽到「非生产即可」。
- **探针仍未进 CI**：本单的两条判据都是**可离线跑**的（守卫是纯函数、结构判据读源码），
  但 seed→run→cleanup 的**端到端**路径仍需真 dev 栈（issue 建议「让探针 seed 步进 CI」——
  那需要 testcontainers + 一次性库，是独立决策，未纳入本单）。
- **同型消费面核对**：removal 类迁移（删列/删表）目前靠人工 grep 消费面。若再现「漏修一处调用点」，
  应在迁移检查里加一步「新旧列名在 `tools/` 与 `backend/` 的引用计数」——本单的
  `test_probe_does_not_reference_removed_failure_threshold_column` 只是这一个列名的钉子。
