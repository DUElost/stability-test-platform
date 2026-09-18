# 站点抓取面与已装告警规则做生产者对拍：结构性不可触发变成可判真的债务清单（#2643 建议 3）

Status: implemented
Class: testing

- 日期：2026-09-18
- 关联：`#2643`（本单，**仍 OPEN**——本次只落建议 3）、`#2488`（「规则装了但不加载」那层缺口）、
  `#2197`（站点本地监控栈）、`#2151`/`#2236`（场景层与覆盖棘轮）、`#2663`（同思路：可派生量不许抄成文字）、
  `#2639`（同思路：grep 型计数会静默少算）

## Decision

**#2488 修完之后仍然成立的一层**：安装清单把整份仓库规则文件渲到站点，而站点 Prometheus 只有
本机 node-exporter 一个抓取 job ⇒ 引用控制面 `stability_*` 的规则在站点**恒无样本**（不触发也不报错），
installer 只看「服务起来 + `/-/ready` 通」就报 `monitoring_ready`。于是「监控就绪」与
「规则结构性不可触发」同时成立。

**本单只落建议 3（机械对拍 + 登记存量），不替 owner 裁决方向**：建议 1（站点只装「站点可见面」规则）
与建议 2（站点补控制面 `/metrics` 抓取）都是方向级决策，且依赖仓库不可知的事实（生产机
`/etc/prometheus` 是运维手改的）。两个终态都会让本单的债务清单**清零**，所以清单先行：
清零前它不许扩大，也不许原地蒸发。

### 复核修正：真值是 19 条，不是 18 条

#2643 自述「18/21 条引用 `stability_*`」来自 `grep 'expr:' | grep -c 'stability_'`。实测少算一条：

- `StabilityPatrolStall` 的 `expr: >-` 是**折叠标量**，指标名在续行上（`alerts-stability-platform.yml:117-119`），
  行级 grep 只匹配 `expr:` 那一行 ⇒ 看不见；本单派生结果把它正确判为控制面域。
- 该单三个分类 `18 + 2 + 1 = 21` 里的 `absent(` 命中与 `stp_script_guard` 命中是**同一条规则**
  （`:280` 的 `... or absent(stp_script_guard_last_run)`），属重复计数，凑巧把总数对上了。

⇒ 站点侧结构性不可触发的规则是 **19 条**，站点可见的是 2 条（`StabilityScriptGuardRetirementDue`、
`StabilityScriptGuardUntrusted`，都只引用 `tools/dev/script_guard_probe.py` 写的 textfile 指标）。
这与 #2639 那次 `git grep 'tests/**/*.py'` 静默少算 81 个文件同形：**计数必须派生，不能 grep**。

### 落地形态

- 新增 `tests/test_site_alert_scrape_surface.py`：四条轴全部派生——
  ① 站点装哪些规则文件 = AST 读 `tools/site_config/stages.py` 的 `MONITORING_RULES` 字面量；
  ② 站点能抓什么 = `deploy/prometheus/prometheus.yml` 的 `scrape_configs`；
  ③ 规则引用哪些指标 = yaml 解析 `expr`（含折叠多行）后与指标全集求交；
  ④ 指标有没有生产者 = `tests/metrics_registry.py` 的控制面注册表 ∪ 本仓 textfile 产物。
  `_SITE_INERT_RULES`（19 条）双向对拍：新增即红（`grown`）、修掉却没删清单也红（`stale`）。
- **前提钉子**：断言站点抓取面仍是「单 job → `127.0.0.1:9100`」。任一方向落地都会让它先红，
  逼来人同步本文件——不留「清单悄悄失去前提」的形态。
- `alerts-stability-platform.yml` 头部补去向提醒：本文件整份进站点、引用控制面指标的规则在站点恒无样本、
  存量由该守卫逐条登记、方向由 #2643 定。**不写条数**（#2663 刚立的口径：抄下来的计数必然过期）。

### 判据边界（不担保的部分）

- 只对**站点**这个安装目标成立：中心/内网 Prometheus 的抓取面不在仓库里，无从判定。
- 不列 `node_*` 命名空间为「站点可见」：结构层（`test_prometheus_alerts_contract.py`）只认
  「注册表 ∪ textfile」，`node_*` 现在根本进不了规则文件。真要放开得先改那一层——不留没人走的分支。
- 「结构性不可触发」判的是**指标有没有生产者**，不是「规则逻辑对不对」；后者由场景层（#2151/#2236）管。

## Alternatives

- **直接做建议 1（站点不装控制面域规则）**：被否（本单）。它会改变生产站点的告警承载面，
  且 #2643 自己写明「站点本就不该承载控制面域规则」这个前提从仓库不可知——属 owner 裁决，不是修复。
- **直接做建议 2（站点补控制面 `/metrics` 抓取 job）**：被否（本单）。要先定端口与网络面，
  并过 ADR-0024 的 secure/内网约束，同样是方向级。
- **把结论只写进 issue 评论 / 文档**：被否。没有机械约束的结论会腐烂——#2663/#2640 两次都是这么坏的。
- **用 `grep -c` 数条数**（issue 的取证方式）：被否并**已实证少算**（见上）。派生还顺手纠正了 `absent(` 的重复计数。
- **把判据合进 `tests/test_prometheus_alerts_contract.py`**：被否。① 该文件在另一条在窗 Execution 的
  scope 里（`promtool 逐条漂移`，CODING）；② 它是「规则 ↔ 场景断言覆盖」那一轴，本单是「规则 ↔ 部署目标
  抓取面」另一轴，合成一个文件会让两条棘轮的清单混在一起，红的时候判不出因。
- **清单放 `tools/dev/` 做成门禁**：被否。根 `tests/` 已在 required check `pr-agent-tests` 里，
  效力相同；而门禁注册要动 `scripts/run_gates.py`/`ci.yml`（`ci.yml` 上有三条在窗记录）。
- **给每条规则加一个 `site_visible: true/false` 注解**（把结论写进规则文件）：被否。那是把派生量
  再抄一遍（同 #2663 的反面），而且 Prometheus 规则文件的注释面不受任何 schema 约束。

## Verification

只列实跑命令与结果。

- `pytest tests/test_site_alert_scrape_surface.py -q` → **6 passed**。
- 派生结果与 #2643 自述的差异：`inert_rules_at_site()` 给出 **19** 条（清单双向对拍全绿 ⇒ 登记的 19 条
  与派生完全一致）；`StabilityPatrolStall` 的折叠 expr 被单独一条用例钉住（`:117` 的 `expr: >-` 里
  不含指标名，解析后含 `stability_patrol_failure_streak_observed_count`）。
- 邻居：`pytest tests/test_site_alert_scrape_surface.py tests/test_alert_count_claims_are_live.py
  tests/test_prometheus_alerts_contract.py -q` → **45 passed**（规则文件头只加注释，promtool 场景层不受影响）。
- 判别力变异 **8 条全部变红**，还原后基线 6 passed：V1 清单摘掉一条（修掉却没删）/
  V2 清单加入不存在的告警名 / V3 规则文件新增一条未登记的控制面规则 / V4 站点抓取面多了第二个
  job（前提变了）/ V5 安装清单换掉这份规则文件 / V6 分类判据恒不触发 /
  V7 只读 `expr` 首行（退回 grep 形态）/ V8 站点可见面丢掉 textfile 轴。
  过程记录：**第一轮 V7 存活**——折叠夹具把目标指标放在了 expr 值的首行（`>-` 折叠是 YAML
  键行与值行的错位，不是 expr 内部的换行），改成「唯一指标在续行」后 V7 才判红；
  这条改法本身也说明了「18 vs 19」为什么容易被行级 grep 漏掉。
- `python -m ruff check tests/test_site_alert_scrape_surface.py`、`tools/dev/check_governance_surface.py --check`、
  `scripts/run_gates.py check:quick` / `check:pr`：结果写进 PR 描述。
- pending：**#2643 保持 OPEN**（建议 1/2 待裁决）；本单不触碰站点安装行为，生产侧零动作。

## Revisit

- **本单是「半单」**：建议 3 落地后，#2643 的核心问题（19 条规则装到站点却恒无样本）仍然存在。
  owner 选定方向后，落地单的判据就是把 `_SITE_INERT_RULES` 清空 + 拆掉前提钉子，二者都必须同步。
- **`assert refs` 对「不引用任何已知指标」的规则直接判红**：今天无此类规则。若将来出现合法的
  `absent(up{...})` 型规则，`PROMETHEUS_BUILTIN` 是它的落点——但那是**新增站点可见命名空间**，
  应连同结构层一起改，不该只在本地放行（这条就是防那种改法的）。
- **指标全集来自导入期注册表**（`metric_registry_index()` 会 `import backend.core.metrics`）：
  沿用既有共享助手（`tests/metrics_registry.py`），与该助手在 #1257/#2237 的既有先例一致；
  本单不新增导入期副作用。
- **同一形态是否还有第三处**（Grafana 面板装到站点？`deploy/grafana/` 的抓取面归属）：
  本单不预先泛化，等第二次真实漂移出现再抽（#2640 的 Revisit 是同一条教训）。
