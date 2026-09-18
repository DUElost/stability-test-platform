# promtool 逐条漂移：场景按告警切片（保 #2236 判别力）

Status: implemented
Class: process

## Decision

`test_promtool_gate_detects_per_rule_threshold_drift` 每次只跑**含该
``alertname`` 的场景 group/用例**（``_scenarios_for_alert``），不再每次重放完整
`alerts-stability-platform.test.yml`（内含 145h/200h ``eval_time`` 组）。

**核验不变**：

- 仍只抬这一条阈值 → ``promtool test rules`` 必须红，且输出点名该告警；
- 整文件正向：``test_alert_scenarios_fire_with_promtool``；
- 整文件负向（全抬）：``test_promtool_gate_detects_threshold_drift``；
- 覆盖棘轮：``test_every_alert_rule_has_scenario_case``。

**墙钟**（本机 promtool）：21 条逐条控制跑 全文件 **~240s** → 切片 **~18s**；
含变异的相关子集 pytest 约 **53s**（先前同类墙钟主导 `tests/` 的 ~4–8min）。

## Alternatives

- **缩短 evaluation_interval / eval_time**：易与生产 ``for:`` 语义错位，否决。
- **删逐条用例、只留整批负向**：丧失 #2236「假覆盖可见」判据，否决。
- **pytest-xdist 并行逐条**：收益有，但引入隔离面；切片已足够。

## Verification

```bash
TESTING=1 JWT_SECRET_KEY=ci PYTHONPATH=. PROMTOOL_REQUIRED=1 \
  python -m pytest tests/test_prometheus_alerts_contract.py \
  -k 'per_rule or threshold_drift or scenarios_fire or every_alert' \
  -q --durations=15
# → 全绿；逐条用例单条不再普遍 ~9s（长尾组除外）
```

## Revisit

若再增 100h+ ``eval_time`` 场景组，切片仍会让「只测该告警」变慢——那时再考虑
对该组单独降采样（须证明与 ``for:`` 等价）。
