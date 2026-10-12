# absent 告警的逐条变异判别力（#3679）

Status: implemented
Class: testing

## Decision

采用 [#3679](https://github.com/DUElost/stability-test-platform/issues/3679) 的守卫侧修复，
承接 [#3678](https://github.com/DUElost/stability-test-platform/issues/3678) 的 backstop 红灯。
本单是 [B5 #3653](https://github.com/DUElost/stability-test-platform/issues/3653) 的外部验收依赖，
不是 U3/U4/U7 单元；只修改本 Note 和 `tests/test_prometheus_alerts_contract.py`。

既有 `_CMP_THRESHOLD_RE` / `_HUGE_THRESHOLD` 数值阈值变异路径及双参数调用保持兼容。
可选第三参数只服务 absent 轴；absent 未显式提供目标切片时失败，不使用默认时长。
对于没有比较阈值的单一 `absent(selector)`，只在临时副本中延长目标规则的 `for`：
复用 `_duration_minutes`，取原 `for` 与全部目标切片 `eval_time` 的最大值，向上取整后加一分钟。
当前 NoData 原值为 `15m`，最大评估时刻为 `40m`，故副本为 `41m`。

每条规则先运行未变异规则与该告警的场景切片，控制组必须通过；随后只变异目标规则，
要求返回非零、有 `FAILED`，且存在点名目标告警的 `alertname/time/exp/got` 失配块。
仅有失败码、规则名或语法/路径报错不构成有效证据。
目标规则缺失/重复、不可解析或非正 `for`、不可解析 `eval_time`、无目标场景、
全部目标场景 `exp_alerts` 为空均显式失败。无阈值且非支持的单一 absent 形态交人工分类。
不扩大时长解析器，不支持任意 PromQL；selector 的语法合法性由真实 promtool 控制组验证。

保留 `_alert_exprs()` / `_ALERT_NAMES` 的全部告警参数化、覆盖棘轮与
`_raise_all_thresholds` 整文件对照。生产规则的 expr/for/labels/annotations、
生产场景的输入与期望以及 CI 接线均未修改。

前检基线为 `43dfc764a8881fabfed7979b3a40e6662f3c6120`。
Cursor 历史在窗记录“promtool 逐条漂移：场景过滤加速（保 #2236 判别力）”仍为
FINISHED/STALE/NO_PR；其 worktree 与本地/远端分支均不可得，历史 diff 无法核实。
按 [派单 v1.1 裁定](https://github.com/DUElost/stability-test-platform/issues/3679#issuecomment-6112114440)
保留该记录原样及风险可见性，本单正常 declare 自己的两文件 scope，不使用 `--force`。
缺少分支证据不等于历史工作已合入、无价值或已放弃。

## Alternatives

- 改生产规则为数值比较：无法保持 absent 的“序列从未出现”语义，且超出本单 scope。
- 过滤无阈值规则、skip 或豁免失败：会让新增规则绕过逐条判别力与覆盖棘轮。
- 只断言变异后红：控制组错误和语法/路径错误也能造成红灯，无法证明变异轴有效。
- 扩建通用 PromQL 解析器：超出有限输入形态与威胁模型；新形态先人工分类。

## Verification

使用项目 Python 入口与既有内存保护 runner；runner 自证内存上限 6 GiB、swap=0。
`TEST_DATABASE_URL` / `DATABASE_URL` 均从测试命令环境移除，完整根套件的数据库测试
使用 Docker/testcontainers，不加载生产配置。Docker Server 为 `26.1.5+dfsg1`。
promtool 为当前 CI pinned `3.13.3`，下载归档 SHA256 与 ci.yml 一致：
`b349c732d8a853e657d0e7ae1bbad4d11b586615fb65fdc59d896b9f869c001e`。
二进制仅放在本单 `/tmp/stp-3679-promtool/`，通过 PATH 使用，未覆盖生产安装。

[未修改基线证据](https://github.com/DUElost/stability-test-platform/issues/3679#issuecomment-6111938894)：
目标用例 exit 1，`1 failed in 0.10s`，原第 707 行 `assert hits` / `assert 0`。
同 SHA 的基线复现按 v1.1 直接引用。

有限清单及既有调用兼容性实跑 `36 passed, 60 deselected`，exit 0，无 skip
（33 条本单用例及 #3233 的 3 条既有数值规则用例）：

- NoData 真实切片：control=0、mutation=1，目标 `exp/got` 失配；原 15m→临时 41m。
- 数值规则仍抬到 1e12；absent 只改 for；非目标告警、录制规则及其它字段值保持一致。
  原 for 大于 eval_time 及秒数非整分钟的向上取整边界均通过；
  absent 缺少显式切片拒绝，既有 host-storage 数值规则的双参数调用仍通过。
- 六种无阈值不支持形态、四种无效 for、三种无效 eval_time、缺失/重复目标、
  无场景/仅不触发场景均捕获预期断言失败，反例测试通过。
- 控制组失败、变异仍绿、无 FAILED、其它告警失败、仅目标名的语法报错使用受控返回值
  检验拒绝路径；缺二进制且 REQUIRED 时捕获 pytest.fail，均通过。
- 路径、YAML、PromQL 三种错误另用真实 promtool 实跑，均被控制组断言拒绝。

首个 PR head `f043235b` 的专项全量 `89 passed`、exit 0；该 head 的 required
`pr-agent-tests` 日志同时暴露新增必需第三参数导致 #3233 三条既有调用失败
（Agent 套件 2505 passed，根离线子集 3 failed / 2327 passed / 71 skipped）。
这一回归已在本单文件内修复并以原调用用例实跑验证；未修改其它测试文件。
最终 head 重新执行下列专项、完整根套件与 quick，不沿用旧 head CI。

交付验证使用下列命令，精确 head、退出码、完整汇总与 skip 明细由本单 Draft PR
及 #3679 / #3653 的交付评论记录；PR 的六项 required CI 另查 GitHub 最终状态。
下面的专项与根套件命令均在私有 promtool PATH 下设置 `PROMTOOL_REQUIRED=1`：

```bash
PROMTOOL_REQUIRED=1 ./scripts/project_python.sh scripts/run_pytest.py 'tests/test_prometheus_alerts_contract.py::test_promtool_gate_detects_per_rule_threshold_drift[StabilityDeviceLeaseReconcilerNoData]' -q
PROMTOOL_REQUIRED=1 ./scripts/project_python.sh scripts/run_pytest.py tests/test_prometheus_alerts_contract.py -q
PROMTOOL_REQUIRED=1 ./scripts/project_python.sh scripts/run_pytest.py tests/ -q -rs
./scripts/project_python.sh scripts/run_gates.py check:quick
```

运行期告警语义仍由 `deploy/prometheus/alerts-stability-platform.test.yml` 的真实场景承担。
本修复不证明生产采集器健康，PR required CI 不替代带 REQUIRED 的本地场景证据。
既有 [#2236 场景清零 Note](2026-09-16-alert-scenario-debt-clearance-2236.md) 的场景覆盖职责不变。

## Revisit

威胁模型是防正常维护时误改复发，不证明对刻意绕过或任意 PromQL 改写的完备性。
新无阈值形态或时长形态先退回 Owner/规划者分类；不得以复核新发现扩建解析器、修改
生产规则或降低验收。若历史工作恢复或出现涉及本单文件的活动工作面/PR，暂停重叠修改并协调。

本 PR 保持 Draft 等独立复核，Ready/FIFO 由 Owner 决定。
合入后仍需回填 merge SHA 与 main/backstop 证据；#3678 按既有工作流成功后自动收口。
B5 单元须各自在同步后的精确 head 补验，不能由本单通过推导生产激活或 U3/U4/U7 已通过。
