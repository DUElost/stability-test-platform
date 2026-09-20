# skill 用量探针落 textfile 指标 + 告警 + 登记（#2881）

Status: implemented
Class: bug-fix

- 日期：2026-09-20
- 关联：`#2881`（本单）、`#2785`（探针多源化 + 接 timer）、`#2851`（缺源 ≠ 零调用）、
  `#735`/`script_guard_probe.py`（同形缺口的先例）、`#2632`/`pg_error_guard.py`（第二处 textfile 生产者）

## Decision

「有 HOLLOW」此前唯一出口是 unit failed，而 node-exporter 无 systemd collector、探针也不落
任何指标 ⇒ **探针跑了没人看**（#2785 的立项动机「防建而不用」换了一层重演）。本单补齐指标出口，
形态照 `tools/dev/script_guard_probe.py`：

1. **新增 `tools/dev/skill_usage_probe.py`**：跑判据端 `--json`、落 4 条 textfile 指标
   （`stp_skill_usage_{hollow,unknown,broken,last_run}`）、原子写 `/var/lib/prometheus/node-exporter/stp-skill-usage.prom`；
2. **判据端加 `--json` 与 `--transcript-dir` / `--codex-dir`**（机器可读输出**只**打 JSON ✓，
   混着人类表格的 stdout 没法解析）；
3. **`stp-skill-usage.service` 改为不降权 + 显式指路**：`--home /home/<deploy-user> --deploy-root <deploy-root>`；
4. **两条告警**（平台文件 + 站点子集，逐字段一致）：`StabilitySkillUsageHollow`（`hollow > 0`，`for: 7d`）
   与 `StabilitySkillUsageUntrusted`（broken/unknown/last_run>48h/absent 合成一条）；
   **并补 promtool 场景用例**（`alerts-stability-platform.test.yml` 三块，照 script-guard 同族形状）：
   hollow=3 卡 `for: 7d` 两侧（145h 不报 / 170h 报）、`broken=1` 触发 Untrusted、全健康两项都不报。
   数值刻意选在「`> 0` 上方一点、任何被抬高的阈值下方」，让逐条阈值变异仍能被归因到本告警；
5. **登记**：`tests/metrics_registry.py` 的 `_TEXTFILE_PRODUCERS`、
   `tests/test_site_alert_scrape_surface.py` 的 `_PRODUCER_SITE_UNIT`（站点清单含该单元 ⇒ 映射 `stp-skill-usage`）、
   `tests/test_alert_metric_producers.py` 的 `_SELF_OWNED_METRIC_RE` 前缀集；
6. **顺手抽 `tools/dev/textfile_metrics.py`**（`render_gauges` + `write_atomic`）：渲染/落盘在
   script-guard 与 pg-guard 里已逐行重复两份，本单的探针是**第三处** —— 按仓内惯例（第三次出现
   抽公共 helper）收敛；两个旧调用点保留同名薄包装，它们的契约测试因此不用改。

### 三态语义（本单的核心判据，不是实现细节）

| 情形 | 任务状态 | 指标 |
|---|---|---|
| 有洞（`hollow>0`） | **exit 0**（数据交给人裁决，不是任务失败） | `hollow=N` |
| 强信号源不在场（#2851） | exit 0 | `unknown=1`（此时 hollow 读数无意义） |
| 探针自身异常 / 指标写不出去 | **exit 1**（systemd failed ⇒ 既有告警面） | `broken=1` |

理由与先例一致：让 timer 因「存在 HOLLOW」天天 failed，会把真故障淹死在告警疲劳里；而
`hollow=0` 必须能与「探针没跑」区分 ⇒ 另出 `last_run`，且指标写不出去即当场失败。

### 为什么改 root + `--home`（此前是降权）

降权是为了读部署用户的转录（`~/.claude`、`~/.codex`）；但 textfile 目录是 root:root，
**降权没有写权限**（邻居 script-guard/pg-guard 都不降权正是为此）。两者只能二选一：
改用 root 跑 + 把源目录显式传下去。走 **CLI 而非 env**：新环境变量要进
`environment-variables.md` 与 env 门禁，而 unit 里直接写进 `ExecStart` 就够（先例的成文理由）。

## Alternatives

- **打开 node-exporter 的 `--collector.systemd`**：能拿到 `node_systemd_unit_state{state="failed"}`，
  但那是**全机所有单元**的粗信号（噪音面大），且要在每个站点改宿主配置；本仓既有惯例是
  每个事实一个专用 textfile 生产者（#735/#2632 两处先例）。否。
- **探针继续降权 + 让它写别处的指标文件**：node-exporter 只认一个 textfile 目录，改变它
  就是改宿主配置（同上）。否。
- **探针直接 import 判据模块**（省一次 subprocess）：判据端 import 会连库/读会话转录，
  崩了会与探针一起炸成不可解释状态；先例同样刻意 subprocess ✓。否。
- **`--json` 与人类表格一起打**：消费者要在一堆表格行里找 JSON ✗（实测第一次就踩到）。
  否（JSON-only）。
- **让 json 分支复用表格循环**：那要改循环体那几行——它们正被**在队的 #2851（PR #2878）**修改，
  两个 PR 会在同一区域撞冲突、卡 FIFO。故 JSON 分支独立成环（判据仍走同一个 `is_hollow`，
  不复制判断），与 #2878 的改动面完全区隔。

## Verification

- `python -m pytest tests/test_skill_usage_probe.py -q` → **14 passed**（三态映射 6 组参数化、
  源路径推导、指标形状、端到端 3 条、只读纪律 1 条）；
- 合面：`test_skill_usage_probe + test_skill_usage_report + test_script_guard_probe +
  test_pg_error_guard + metrics_registry + test_site_alert_scrape_surface +
  test_alert_metric_producers` → **全绿**（22 passed 于最后一组）；
- **定向变异**：去掉「缺源折 unknown」→ **3 failed**；删掉站点单元登记 → **3 failed**；
  扩 `_SELF_OWNED_METRIC_RE` 前缀集之前 `test_alert_metric_producers` 本来就是红的
  （那两条断言即该登记的**正向守卫**，本轮实测踩到并修）；
- **CI 首轮红（`pr-agent-tests`，3 failed）——两处都是本单的漏项，已修，记账**：
  ① `test_prometheus_alerts_contract.py::test_every_alert_rule_has_scenario_case` 判红：
  新增两条规则却没补 promtool 场景 ⇒ **「加规则」与「加场景」是同一件事**，漏后者等于阈值/时间窗
  漂移不可见；② `test_alert_count_claims_are_live.py::test_detector_accepts_live_claims` 判红：
  该判据的绿样例用**派生真值**构造「N/M 已全覆盖」句，本单使覆盖变成 25/23 ⇒ 句子不再是真话
  ——**判据自身正确工作**，不是误伤（修法只能是补齐覆盖，不能改夹具）；
  ③ `test_source_scan_anchor_ratchet.py` 判红：新增的 `test_skill_usage_probe.py` 里那条
  `forbidden not in source` 是未走 `SourceGuard` 的源扫描否定断言（锚点漂移时恒真）⇒ 已改为
  `SourceGuard.of_repo_path(...).anchored("def run_report(")` + `assert_absent(..., why=...)`；
- 修后本地复跑：`test_prometheus_alerts_contract + test_alert_count_claims_are_live` → **43 passed**
  （152s，含 promtool）；`test_skill_usage_probe + test_source_scan_anchor_ratchet` → **21 passed**；
  全量 `tests/` → 见 PR；
- `python scripts/run_gates.py check:quick` → **12 gates 绿**。

## Revisit

- **阈值 7d / 48h 是类比取的**（照 script-guard 的成文口径）：HOLLOW 是周级事实、探针每周一次，
  故 7 天持续 + 48h 停摆。若采到基线（洞数的实际变动周期）应复核这两个数，而不是沿用。
- **`render_gauges` 的第三个消费者已出现**——本单就地抽了 helper ✓；若将来出现**第四个**
  textfile 生产者，它应当直接用 `tools/dev/textfile_metrics.py`，不要再复制。
- **root 读部署用户转录是新的权限面**：本单元只读（`ProtectHome=read-only`、`NoNewPrivileges`），
  且只把**计数**写进指标；若将来探针要输出转录内容（例如会话摘要），必须先过一遍凭据/隐私面。
- **`--home` 是人写的路径**：站点若把部署用户家目录放在非 `/home` 下，unit 模板需同步（模板里
  是 `/home/<deploy-user>`）。这与其它 unit 里 `<deploy-root>` 的占位形式同源，集成时一并核对。
