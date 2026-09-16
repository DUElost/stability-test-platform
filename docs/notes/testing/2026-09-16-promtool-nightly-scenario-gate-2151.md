# promtool 场景层进夜间全量 CI：从恒 skip 变必备门禁（#2151）

Status: implemented
Class: testing

## Decision

采纳 issue #2151 的**选项 1**：只在夜间全量 `backend-test`（`main-ci-backstop` 每天
UTC 18:00 dispatch 的那条路径）装 promtool，并把「promtool 缺失」从 **skip 改成 fail**；
PR 路径保持原样（未装即 skip），不引入第三方二进制依赖。

三段改动，缺一不可：

1. **装 + 双 pin**（`.github/workflows/ci.yml` 的 `Install pinned promtool`）：从上游
   release 下载 `prometheus-3.13.3.linux-amd64.tar.gz`，`sha256sum --check` 校验
   `PROMTOOL_TARBALL_SHA256`，只解出 `promtool` 经 `GITHUB_PATH` 暴露给后续步骤。
   装完立刻 `promtool --version | grep -q "version ${PROMTOOL_VERSION} "` 做**版本自证**
   ——没有它，pin 只是一条注释：装错版本无人发现。
   升级方式：改 `PROMTOOL_VERSION` + `PROMTOOL_TARBALL_SHA256` 两处，摘要取
   `https://github.com/prometheus/prometheus/releases/download/v<X.Y.Z>/sha256sums.txt`
   里的 `prometheus-<X.Y.Z>.linux-amd64.tar.gz` 行（本机已用该摘要验证下载一致）。
2. **缺失即红**（`tests/test_prometheus_alerts_contract.py`）：`skipif` 换成
   `_promtool_path()` 分流——`PROMTOOL_REQUIRED` 为真时 `pytest.fail`，否则 `pytest.skip`。
   判据取「非空且不是显式假值」而非只认 `"1"`：只认字面量会让 `PROMTOOL_REQUIRED: "true"`
   这类改写**静默退回 skip**，正是本单要修的失效模式。
3. **接线锚成守卫**（新文件 `tests/test_ci_promtool_scenario_gate.py`，纯离线、PR 路径真跑）：
   与 `test_ci_test_db_guard_wiring.py` / `test_lock_order_pr_path_contract.py` 同模式。
   它守的方向包括「PR 路径**不得**装 promtool / 不得注入 `PROMTOOL_REQUIRED`」——issue
   否决的选项 2 需要机械守住，否则会被下一个 Agent 顺手加回去；以及安装步骤必须排在
   消费步骤之前（`GITHUB_PATH` 只对后续步骤生效，顺序反了等于没装）。方案自身此前
   零测试：删步骤、把 env 改成假值、把 pin 换成 `latest`，PR 全部放行。

**实测把判据②打穿了一次，据此多做了第四段**：

4. **覆盖棘轮**（`test_every_alert_rule_has_scenario_case`，恒跑结构层）：promtool
   **对未列规则不校验**——把没有场景用例的 `StabilityPlanRunAggregationFailed` 阈值
   `> 0` 改成 `> 500`、场景文件不跟，`promtool test rules` 仍 **SUCCESS**（8 passed）。
   17 条告警只有 7 条有 `alert_rule_test`。所以「装了 promtool」只把已列规则变成常量，
   未列规则依然零覆盖；而「新增告警必须带场景」这件事只能在恒跑层成立。存量 10 条记为
   `_SCENARIO_COVERAGE_DEBT`，清单双向收缩（新告警不补场景 → 红；补了场景不删条目 → 红；
   规则被删不删条目 → 红）。

另附一处实现自证：`test_promtool_gate_detects_threshold_drift`（负向对照）把**全部** 17 条
告警的比较阈值统一抬到 `1e12`，先在 tmp 沙箱跑未变异副本作控制组（必须绿），再跑变异组
（必须红且输出含 `FAILED`）。没有它，「场景层恒绿」与「接线坏掉的恒绿」无法区分。

## Alternatives

- **PR 路径也装**（选项 2）：否决——`pr-agent-tests` 的离线纯度由
  `tests/test_offline_subset_guard.py` 锚住，且第三方二进制进 required check 会把
  外部发布故障转成本仓红灯。该否决现已由守卫第 2 类用例固化。
- **apt 装 `prometheus`**：否决——Debian 包版本不可 pin（`2.53.3+ds1-2` 随 sid 漂移）、
  `+ds1` 重打包后与上游 `sha256sums.txt` 对不上，摘要校验失效。issue 允许两种，取可 pin 的那种。
- **场景测试再断言一次版本号 == ci.yml 的 pin**：否决——同一事实两处声明，升级要改
  两个文件；改由安装步骤内 `grep` 自证（版本对不上时 job 已经红，不需要测试再知道一次）。
- **只加 `PROMTOOL_REQUIRED` 不加覆盖棘轮**：不选。那样判据②只对 7/17 条成立，
  而 PR 正文会让人以为语义漂移已被全面拦住——正是「不标注的止血沉淀为技术债」。
- **本批顺手补齐 10 条场景用例**：不选。它是另一件事（逐条构造 input_series +
  注解逐字匹配），塞进本 PR 会让「CI 接线」的 diff 被 200 行测试数据淹没；已作为
  后续单范围记入 Revisit。

## Verification

本机（`/usr/bin/promtool` 2.53.3）与 pinned 3.13.3 双档：

- `pytest tests/test_prometheus_alerts_contract.py -q` → **9 passed**（含 2 条 promtool 层真跑）
- `PATH=<pinned 3.13.3> PROMTOOL_REQUIRED=1 pytest tests/test_prometheus_alerts_contract.py tests/test_ci_promtool_scenario_gate.py -q` → **29 passed**
  （nightly 档位等价模拟；`-s` 可见 `promtool, version 3.13.3` 与 `2.53.3+ds1` 两档均 SUCCESS）
- 红绿对照：
  - `PATH=/nonexistent PROMTOOL_REQUIRED=1` → **2 failed**（fail 而非 skip，夜间安装步骤丢失即红）
  - `PATH=/nonexistent`（无 flag）→ **6 passed, 2 skipped**（PR 档位不变）
  - 改已覆盖告警阈值（`StabilityDispatchGateFailed > 0` → `> 500`）→ **2 failed**
  - 新守卫接线破坏（删 `PROMTOOL_REQUIRED: "1"` 一行）→ **2 failed**（nightly 接线 + 双向锁）
  - 棘轮三向注入（新告警无场景 / 债条目已补场景 / 债条目指向已删规则）→ 各自 **1 failed**
- 安装步骤脚本单独执行：`env -i PATH=/usr/bin:/bin`（无 promtool）跑 ci.yml 里那段 run →
  **rc=0**，摘要校验通过、`GITHUB_PATH` 追加成功、版本自证通过（100MB 下载在本机 8.5s）
- `pytest tests/ -q` → **1083 passed**（合并 origin/main 后的最终 head；PR 档位（无
  promtool）复跑为 27 passed + 2 skipped，nightly 档位 29 passed）
- `python scripts/run_gates.py check:quick` → **10 gates OK**

未验证（pending）：夜间 `backend-test` 的真实一次成功运行——本 PR 合入后由
`main-ci-backstop` 下一次 dispatch 才见分晓；判据① 的「CI 日志可见版本」目前只有
本地等价模拟与脚本单测支撑。

## Revisit

- **10 条存量场景缺口**（`_SCENARIO_COVERAGE_DEBT`）需另开单消化：逐条补
  `input_series` + `alert_rule_test`，补完即从清单删除（守卫会强制同步）。在此之前，
  判据② 对它们不成立——这是显式标注的过渡态，不是终态。
- **promtool 升级**：随上游 patch release 手动 bump（版本 + 摘要两处），不引入自动
  更新——第三方二进制的动升级会把「夜间门禁红」的成因混入上游行为变化。若 3.x 某版
  改变 `test rules` 的输出契约，`test_promtool_gate_detects_threshold_drift` 会先红。
- **夜间下载失败**：`curl --retry 3` 之后仍可能因上游/CDN 故障让 `backend-test` 变红。
  先观察：nightly 不阻塞合入，红了只是「这一轮场景层没验证」。若连续 flaky，出口是把
  promtool 缓存进镜像或改 `actions/cache` + 固定摘要，而不是退回 skip。
- 若将来要求「场景层也进 PR 路径」，前提是先解决离线纯度约束（`test_offline_subset_guard.py`）
  与二进制来源，不是简单地把安装步骤复制到 `pr-agent-tests`——守卫会直接拦红。
