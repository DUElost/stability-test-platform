# 计数与版本型陈述必然过期：去写死 + 与派生真值对拍的防复发门禁（#2663）

Status: implemented
Class: bug-fix

- 日期：2026-09-18
- 关联：`#2663`（本单）、`#2236`/`#2151`（这两处「17/17」文本的来源）、`#2488`（规则从未安装
  那次事故，其 `7/10` 细节留在 `docs/notes/process/2026-09-17-prometheus-rules-never-installed-2488.md`）、
  `#2639`（同一思路的前一单：把「静默失效的守卫」变成可判因的守卫）、`#2252`/`#2640`（同族
  「文字与真值之间没有约束」）

## Decision

**本质不是三处文字陈旧，而是「可派生量被抄成文字」这一类陈述没有任何约束**：加一条告警
规则、发一个小版本，抄下来的数字立刻变成谎话。#2663 登记的是本窗口内**已经发生的两次**
（规则 12→21 而三处仍写 17/17；`flash_firmware` v1.3.17 已于 09-16 发布而 runbook 仍写 v1.3.16），
所以按原样再改一次数字只是把同一颗雷重新埋回去。

### 复核后的真值（全部当场派生，不抄）

| 事实 | 值 | 派生方式 |
|---|---|---|
| 规则条数 | **21**（distinct `alert`） | yaml 解析 `deploy/prometheus/alerts-stability-platform.yml` |
| 场景覆盖 | **21 个 distinct `alertname` / 24 条 `alert_rule_test` 条目** | yaml 解析同目录 `.test.yml` |
| 覆盖缺口 | 无（A-only、B-only 均空集） | 两集合互差 |
| `flash_firmware` 最新 | **v1.3.17**（runbook 停在 v1.3.16） | `backend/agent/scripts/` 下最大版本目录 |
| `monkey_test` 最新 | v1.2.2（**未漂移**，但同一形态） | 同上 |
| 告警条数与 21 的差 | 棘轮本体是**动态计算**的，`_SCENARIO_COVERAGE_DEBT` 为空 frozenset | 读码——与本单结论一致：不是门禁漏洞，是文字陈旧 |

### 落地形态：6 处文本 + 1 个门禁

- 6 处**去掉写死的数字**（不改成 21/21）：`docs/operations/README.md`、
  `deploy/prometheus/alerts-stability-platform.test.yml` 头、`tests/test_prometheus_alerts_contract.py`
  docstring、`deploy/prometheus/alerts-stability-platform.yml` 头的历史叙述（数字改指 #2488 的 Note）、
  `tests/test_alert_metric_producers.py` 的「17 条告警」→「当时全部告警」、runbook 两行脚本版本表。
- 新增 `tests/test_alert_count_claims_are_live.py`：扫「现状口径」文本（`docs/operations|development|design|prd`、
  `deploy`、`scripts`、`tests`、`tools`，279 个文件），认五类形态（`N 条告警[规则]`、
  `N 条(场景)?断言`、`N 条场景用例`、`N/M 全覆盖`、`最新 active 版本 … vX.Y.Z`），逐条与派生真值对拍。
  **写等于真值的数字是允许的**——判据反的是过期，不是数字。
- 三处设计取舍有实测支撑，不是审美：
  1. **不做「带日期即豁免」**——runbook 那两行本来就写着「2026-09-15 复核 = v1.3.16」，带日期
     并没有让它变得可用（运维今天照它核对仍被误导）。时点记录的正确落点是 `docs/notes/**`
     （整目录不在扫描面内），所以门禁不需要任何绕过位。
  2. **不设「扫描文件数 ≥ N」魔法下限**——实测把 `MIN_SCANNED_FILES` 从 200 改成 0 仍然全绿
     （N 可被随手调小）。换成两条可判真的结构约束：每个面必须存在且至少贡献 1 个文件 +
     6 个已知文件必须在扫描面里（变异 M5/M6/M7 各抓一次红）。
  3. **真值不留可硬编码的中间层**——第一版有 `live_counts()` 包一层，变异 M8（把它的返回值
     写成常量）存活；改成 `counts_from_text` / `counts_from_files` 两层纯函数 + 文本级/文件级
     两个扰动探针（往输入里加一条规则 → 输出必须 +1；喂一份假的 tmp 文件对 → 输出必须跟着假文件走），
     M8a/M8b 才都变红。

### 判据边界的诚实说明

- 只认上表五类字面形态：`N 条规则`（不带「告警」）、`7/10 的手工拷贝`、`自测 7 条规则全绿`
  （治理面 S 系列）都**不认**——`docs/operations/mtbf-api.md:142` 的「改 1 条用例」第一版被误判过
  （那里的「用例」指测试用例），故把裸 `N 条用例` 从形态里去掉，并留一条绿样例钉住这个边界。
- 版本形态只认「最新 active 版本 + 同一句反引号点名的脚本」；点不出脚本名也判红（要求写清楚），
  脚本 pin 版本（`mtbf_*` 那类）不在射程内。

## Alternatives

- **只把 17 改成 21**（issue 的次选）：被否——下次加规则即再次过期，本单就是第二回。
- **在 `tools/dev/` 做门禁 + 注册进 `run_gates.py`/`ci.yml`**：被否。落点需要改 `ci.yml`，而
  cursor 有三条在窗记录（`Main CI 墙钟…`、`压墙钟→…`、`撤销 agent∥tests/ 并行`）都在
  `.github/workflows/ci.yml` 上；根 `tests/` 已在 required check `pr-agent-tests` 里，判据效力相同、
  碰撞面小得多。
- **把「告警条数」做成一个生成文件/由脚本注入文档**（真正的单一事实源）：被否（本期）。它要求
  引入 docs 生成步骤并把人写散文与生成段落分离，成本远大于「禁止抄数字 + 抄了就红」。
- **顺手把 #2643（站点抓取面只有 node-exporter）一起修**：被否。那是方向级裁决（分层承载
  还是补抓取），且与本单的「文字口径」不是一件事；混做会让本单 PR 变成两件事的合订。
- **给 runbook 补一个 `v1.3.17` 再刷新日期**（issue 建议）：被否，改抄真值等于埋下一颗雷，
  见上「不做带日期即豁免」。
- **把 `deploy/prometheus/alerts-stability-platform.yml:7` 的历史数字留着、加豁免位**：被否，
  改为指向 #2488 的 Note——细节没丢，丢的是「在现状文件里抄一份会过期的数」。

## Verification

只列实跑命令与结果。

- 先立门禁再改文本，取「改前」证据：`pytest tests/test_alert_count_claims_are_live.py -q` →
  **FAIL，恰好 6 条 finding**（README:83 / 场景文件头 :3 两条 / 规则文件头 :7 /
  `test_alert_metric_producers.py:49` / `test_prometheus_alerts_contract.py:20`），
  与 #2663 列举的三处 + 我复核出的两处历史叙述 + 一处版本行完全对应，无一漏、无一多判。
- 文本正规化后同命令 → **8 passed**；`ruff check` → All checks passed。
- 判据变异 **12 条全部变红**，还原后基线 8 passed：M1/M2/M3 三处文本回潮、M4 条数判据削弱、
  M5 摘掉一个扫描面、M6 目录名打错、M7 `MUST_SCAN` 指向不存在文件、M8a/M8b 两层真值硬编码、
  M9 判据恒返回空、M10 版本判据被摘、M11 全覆盖比值判据被摘。
  过程记录：第一轮 M5（改魔法下限）与 M8（改 `live_counts`）**存活**，据此删掉魔法下限、
  把真值拆成两层纯函数并加扰动探针；M8b 第一次因变异自身语法错而「假红」，已改成合法变异复跑。
- 邻居回归：`pytest tests/test_prometheus_alerts_contract.py tests/test_ci_promtool_scenario_gate.py
  tests/test_source_scan_anchor_ratchet.py tests/test_site_install.py -q` → **123 passed**；
  连带 `tests/test_alert_metric_producers.py` → **14 passed**。
- 新文件不参与 #2639 棘轮（它不做「读源码 + 否定断言」，是非判定是 `violations == []` 的正向形态），
  棘轮用例仍绿。
- `scripts/run_gates.py check:quick` / `check:pr`：见 PR 描述。
- pending：无生产侧行为变更 ⇒ 不动 Prometheus 规则本体（只动头部注释），站点侧无需重放安装。

## Revisit

- **形态驱动 ⇒ 天然假阴性**：换个说法（「全部 21 项告警均已覆盖」不含上表任一形态）就扫不到。
  缓解是绿/红样例把边界钉在测试里，扩展形态时先加红样例。真正的兜底是评审面——门禁只保证
  「抄了数字又过期」这一类不再静默通过。
- **真值绑定在单个规则文件对上**：若 #2643 落地成「按部署目标分层的规则文件」，本门禁的
  `RULES_REL`/`SCENARIO_REL` 必须变成**按目标各一对**，否则会把中心/站点两套条数误判成同口径。
- **`SELF_EXEMPT` 是本文件自己**：把过期数字写进本文件的注释里不会被抓住。这是所有
  「判据自身豁免」的共同代价（同 #2639），不做额外处理。
- **是否把同类判据推广到别的可派生量**（端点数量、脚本版本表、ADR 条数）：等第二次真实
  漂移出现再泛化，不预先抽象（#2640 的 Revisit 是同一条教训）。
