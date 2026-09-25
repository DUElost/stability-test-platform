# 夜间 CI 后续：promtool 3.x 左开区间下舱壁场景失效 + 一步红遮蔽其余套件（#3247）

Status: implemented
Class: bug-fix

关联：[#3247](https://github.com/DUElost/stability-test-platform/issues/3247)（夜间 backstop）、
[四红修复 Note](./2026-09-25-nightly-red-and-vacuous-capacity-criteria-3247.md)（PR #3266，本单前序）、
#3248 / #3240（`StabilityTerminalBulkheadRejected` 规则与场景的来源）、
#2151（promtool 场景层只在全量 job 跑，CI 固定 promtool 3.13.3）。

## Decision

PR #3266 修掉四红后，对同一提交手动触发的全量 CI 仍然是红的：`Run repo-level tests` 里
`test_alert_scenarios_fire_with_promtool` 与 `test_promtool_gate_detects_threshold_drift` 两项失败，
都指向 `StabilityTerminalBulkheadRejected` 在 12m 的预期告警没有出现。两个成因，分别修：

1. **场景采样与生产抓取不一致**。规则是
   `count_over_time((increase(x[1m]) > 0)[10m:1m]) >= 8`，场景却按 **1m** 采样。Prometheus 3.x 的区间
   选择器改成左开区间，`increase(x[1m])` 的窗口里只剩 1 个样本、恒无值：
   - 正向组在 CI 固定的 promtool 3.13 下失败；
   - 负向组恒真通过（规则拿不到数据）——即使把阈值放宽到 `>= 0` 也不红，属于空过。

   本地 promtool 2.53 是闭区间，两组都「对」。生产是 **15s 抓取 + Prometheus 2.53.3**，规则本身在
   生产没有问题，所以**只改场景**：两组都按生产抓取间隔 15s 采样。负向组的单次跳变刻意放在一分钟
   窗口**内部**（75s→90s）：放在 60s→75s 这种步长边界上时，3.x 下每个 `increase[1m]` 都看不见这一跳，
   负向断言会再次空过。
2. **一步红遮蔽其余套件**。`backend-test` job 里，`Run backend tests` 一旦失败，后续的
   `Install pinned promtool` / `Run agent tests` / `Run repo-level tests` 都会被 `if: success()` 跳过。
   09-23 与 09-24 两夜正是如此：本问题因此藏了两夜，直到 PR #3266 修好第一步才暴露。
   改为依赖装好（`steps.deps.outcome == 'success'`）即各自独立跑（`!cancelled()`）。backstop 归因本来就
   按 job 汇总**全部**失败步骤（`join(", ")`）与全部失败用例，无需改动。

## Alternatives

- **改规则**（例如 `increase(x[2m])`，使 1m 采样下也有两个样本）：规则在生产（15s 抓取）上没有缺陷，
  为迁就一个不符合实况的采样去改判据，会顺带改变「≥8 个一分钟窗口」的语义（窗口互相重叠）。改场景才
  对准问题。
- **把 CI 的 promtool 降到 2.x**：只是掩盖版本差异；将来生产升级到 3.x 时同一问题会在生产出现。
- **负向组维持 1m 采样**：3.x 下是空过（本次用 `>= 0` 变异实证），留着等于没测。
- **遮蔽问题另开单**：它正是这两个失败藏两夜的直接原因，与本修复同源；一行条件、行为可预期，同批修。

## Verification

- 本机下载 CI 固定的 promtool **3.13.3**（按 CI 同样的 sha256 校验）复现：修前 `StabilityTerminalBulkheadRejected`
  在 12m 失败，与 CI run 36093610580 一致。修后在 3.13.3 与本机 2.53.3 下 `promtool test rules` 均为 **SUCCESS**。
- **判别力**：把规则阈值放宽到 `>= 0` 的变异，在两个版本下都让负向组在 6m 变红（修前的 1m 采样下 3.x 不红）。
- `tests/test_prometheus_alerts_contract.py` 等 CI / 告警契约 7 个文件，在
  `PATH` 优先 promtool 3.13.3 + `PROMTOOL_REQUIRED=1`（与 CI 同口径）下 **105 passed**，其中包括 CI 上失败的两项。
- `backstop-attribution.sh` 的 `failed_jobs_tsv` 已按 job 汇总全部失败步骤名，本改动不影响归因。

## Revisit

- **生产升级到 Prometheus 3.x 前**须复核本规则：15s 抓取、1m 步长的子查询下，3.x 左开窗口使跨步长边界的
  那一对样本（约四分之一的增量）对 `increase(x[1m])` 不可见。持续削峰仍能触发，间歇削峰会少计。
  届时评估改用 `increase(x[1m15s])` 或等价写法，并同步场景。
- 场景层采样间隔应跟随生产抓取间隔（`deploy/prometheus/prometheus.yml`）；抓取间隔变更时回扫全部场景组。
- 本地 promtool（Debian 2.53）与 CI（3.13.3）行为不同：改告警规则或场景时，用 CI 固定版本本地复跑
  （按 `ci.yml` 的 sha256 下载到 `/tmp`，不装进系统）。
