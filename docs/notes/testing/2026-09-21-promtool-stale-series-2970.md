# 夜间 backstop 红：`StabilityChainCoverageGap` 场景样本没覆盖第二个 eval_time（#2970）

Status: implemented
Class: testing

- 日期：2026-09-21
- 关联：`#2970`（本单，自动兜底单）、`#2909③`（该规则与场景的引入面）、`#2151`（promtool 场景层进夜间 CI）、
  `#2236`（场景覆盖清零）、`#2333`（失败 job 归因：重跑仍红 = 确定性）

## Decision

把 `alerts-stability-platform.test.yml` 里 `StabilityChainCoverageGap` **firing 块**的两条 series
从 `x60` 改成 `x70`（样本覆盖到 t=69m），两个 eval 点原样保留。规则、阈值、`for:` 与断言语义**一律不动**。

**根因**（CI 同款 promtool 3.13.3 复现）：

```
promtool test rules alerts-stability-platform.test.yml
  FAILED:
    alertname: StabilityChainCoverageGap, time: 1h5m, exp:[…] got:[]
```

该块的样本只有 60 个点（t=0..59m），而第二个 `eval_time` 是 **65m**——最后一个样本已过期 6 分钟，
超过 Prometheus 的 **5 分钟 staleness** 窗口 ⇒ 表达式取到空向量 ⇒ 期望有告警、实际什么都没有。
`for: 60m` 的规则在 65m 本该是「刚过窗口就亮」，样本却先于它消失。

**为什么合入时没人发现**：本机 promtool 是 **2.53.3**，对这个形态**容忍**（同文件同命令 `SUCCESS`），
而 CI 全量 job 固定 **3.13.3**；PR 阶段按 #2151 的成文理由**不装 promtool**（不引第三方二进制进 PR 门禁），
所以这条只在夜间全量里显形——正是 backstop 该拦的东西。

**并补一条不依赖 promtool 的结构层判据**（`test_scenario_eval_times_are_within_the_sample_window`）：
逐组取「组内**最长**的 series 末点 + 5m」，要求该组每个 `eval_time` 都落在里面。
理由：这条错误的**发现成本**与错误大小完全不成比例（写错 5 个字 ⇒ 夜间红 ⇒ 队列停摆一天），
而它本质是**结构问题**（数据窗口 vs 判据时刻），不需要 promtool 就能判——与 #2151 的分层一致
（结构层恒跑、语义层交给 promtool）。边界**有意**只判「整组样本先用完」；「只有某一条 series
提前过期」要读表达式语义（`absent()` 形态本就依赖序列消失），仍归 promtool 层，已在用例 docstring 写明。

- **只改数据、不加判据**：这条错误的发现成本（夜间红 + 队列停摆一天）与错误大小（少写 10 个样本点）
  完全不成比例，而它由结构层零成本可拦；不加等于把同类再犯留给下一次夜间。否。

## Alternatives

- **把 firing 的 `eval_time` 从 65m 挪到 62m**（样本仍在 staleness 窗口内）：能过，但把判据顶在
  staleness 边界上，任何一次窗口/间隔调整都会再犯；而 62m 与 65m 的判别力完全相同。否。
- **缩短规则的 `for: 60m`**：改的是产品语义（告警灵敏度），本单是场景数据错，不能反向迁就。否。
- **本机装/升级到 3.13.3 就好**：那是环境面，且换台机器又会漂；仓库侧要保证的是「样本覆盖判据里用到的时刻」。否。
- **让测试自己去下载 CI pin 的 promtool**：与 #2151 的成文约束冲突（PR 门禁不引第三方二进制依赖），
  且给每条断言加一次下载。否（记入 Revisit，取舍留给测试面整体裁决）。
- **只在 CI 装 promtool 的档位里跑该场景**：本来就是这么分流（`PROMTOOL_REQUIRED`），本单不涉及。否。

## Verification

- **修前复现**（CI 同款）：`/tmp/prometheus-3.13.3.linux-amd64/promtool test rules …` →
  `FAILED … time: 1h5m … got:[]`；与本单给的日志逐字一致（注解文本也逐字对上）；
- **修后**：同命令 **SUCCESS**；再用本机 `promtool`（2.53.3）跑同文件也 **SUCCESS**（不回归）；
- **变异（断言有牙）**：把 firing 块的 `gap_missing` 由 `90x70` 改成 `60x70`（比值 0.1，不满足 `> 0.1`）
  ⇒ 3.13.3 报 `got:[]`；还原即绿。即 65m 那条断言不是「只要 series 在就亮」的空转；
- **新结构层判据的边界实测**：以末点 t=59m 为基准，`eval_time` 63m/64m ⇒ 3.13.3 **SUCCESS**、
  65m ⇒ **FAILED** ⇒ 判据取「≤ 末点 + 5m」与 promtool 实际行为一致（含边界）；
- **新判据的红绿双向**：场景修好时 **1 passed**（0.09s，不需 promtool）；把场景改回 `x60` ⇒
  **1 failed** 且点名 `group#11 StabilityChainCoverageGap: eval_time=65m 超出样本末点 59m + 5m`；还原即绿；
- **59m 那条不报**仍是 `for: 60m` 的边界证据（样本自 t=0 起就满足比值，59m 未满 60m ⇒ 不亮）；
- 全量口径：`PATH=<3.13.3> python -m pytest tests/ -q` → 见 PR；`check:quick` → 见 PR。

## Revisit

- **本地 promtool 版本 ≠ CI pin 时，本地绿不代表 CI 绿**（本单就是这么漏到夜间的）。下次遇到
  「本地过、夜间红」，第一步先对版本（`promtool --version` vs `ci.yml` 的 `PROMTOOL_VERSION`）。
  若这类误判再出现一次，值得给测试加一条「版本不一致时发 warning」的提示（**不改判据**，
  仅让本地读数带上不确定性），取舍属测试面整体裁决；
- **staleness 是场景文件的通用陷阱**：任何 `eval_time` 都必须落在样本区间末点 + 5m 之内。
  本文件其它块已用 `x70`/`x12100` 之类长样本覆盖，本单把这一条写进块注释；
- 该规则的阈值/`for:` 语义未动——#2909 的「覆盖差 10%」是否合适仍归 #2909 的方向裁决，
  本单只让场景与规则重新对齐。
