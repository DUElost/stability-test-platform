# 内存塌陷场景在 promtool 3.x 下不触发（#3322 后续）

Status: implemented
Class: bug-fix

## Decision

`deploy/prometheus/alerts-host-resources.test.yml` 的内存/swap 联合余量场景：把高位平台从 8m 延到
8m30s（两条序列都是第一段 `x16 → x17`、第二段 `x2 → x1`），10m 之后的值不变。

原因：promtool 3.x 的区间选择器左开。`StabilityHostMemoryHeadroomCollapsing` 在 10m 处的
`[2m:30s]` 子查询从 8m30s 起算；平台止于 8m 时峰值在窗外，差值恰为 −6 GiB，不满足
`< -6442450944`，场景不响。2.x 左闭、包含 8m，所以本机（promtool 2.53）通过、CI（钉 3.13.3）失败。
main 上的原文件在 3.13.3 下即失败，夜间全量的 repo 级测试（`PROMTOOL_REQUIRED=1`）会因此变红。
与 #3269 修过的舱壁场景是同一类坑。

## Alternatives

- 把断言时刻改到 9m30s：场景头注释说明塌陷是瞬时判据，断言时刻是和 `for: 1m`、评估周期一起排的，
  挪时刻会打乱另外三条的配合；不取。
- 改规则（子查询窗 2m → 2m30s）：窗口来自真机回测（09-23 13:38 首次满足），为迁就测试改规则
  本末倒置；不取。

## Verification

- `promtool test rules alerts-host-resources.test.yml`：2.53.3 与 3.13.3 均 SUCCESS（改前 3.13.3
  在 10m 处 FAILED）。
- 判别力：把该规则阈值收紧到 −14 GiB（场景构造的塌陷是 −13.5 GiB）→ 3.13.3 下该场景 FAILED。
- `PROMTOOL_REQUIRED=1`、PATH 用 3.13.3：`tests/test_host_storage_alerts_3233.py`、
  `tests/test_host_rule_scenario_coverage_3322.py` 共 8 passed。

## Revisit

本机 promtool（2.53）与 CI（3.13.3）的大版本差已两次让场景「本地绿、CI 红」（#3269、本单）。
若再出现，考虑在开发机脚本或 `check:quick` 里钉 3.x promtool，让本地验证与 CI 同口径。
