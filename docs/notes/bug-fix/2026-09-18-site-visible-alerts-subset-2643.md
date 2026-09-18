# 站点只装「站点可见面」告警规则（#2643 方向 1）

Status: implemented
Class: bug-fix

## Decision

站点 Prometheus 只有一个抓取面（本机 node-exporter），而安装清单把**整份**平台规则
（21 条）渲到站点——其中 19 条引用的都是控制面进程指标，在站点上**结构性无样本**：
既不触发也不报错，却让 installer 的 `monitoring_ready` 与「规则装好了」同时成立。

按 owner 裁决走**方向 1**：站点只装站点可见面子集。

- 新增 `deploy/prometheus/site-alerts.yml`：**逐字段复制**平台文件里的
  `StabilityScriptGuardRetirementDue` / `StabilityScriptGuardUntrusted`（textfile 面，
  站点那唯一 job 能产生样本的两条），带 installer 的归属标记（`<deploy-root>` 占位符）
  与说明头；
- `tools/site_config/stages.py` 的 `MONITORING_RULES` 源改为该子集，**dest 路径不变**
  ——存量机升级时按同名覆盖，`rules/` 里不会残留旧副本；这也让
  `check-monitoring-assets.py` 的 `LEGACY_FALLBACKS`（按 dest 键）继续有效；
- `tests/test_site_alert_scrape_surface.py`：#2717 的债务清单 `_SITE_INERT_RULES`
  **清零**（终态），判据保留「新增即红」方向；新增**子集漂移对拍**——站点文件里每条
  规则（除 `alert` 外）必须与平台文件同名规则逐字段相等，且不得出现平台文件里没有的
  规则（防止分层后两份定义各改一半）；
- 文档：`docs/operations/installation.md` 的 S2b、`docs/operations/README.md` 的目录树
  与可观测性一节、`prometheus.yml` 模板注释同步（**不写条数**——#2663 的计数门禁只认
  派生真值，写死数字必被甩下）。

**推荐理由的一处更正**（供后续核对）：我最初的理由是「控制面自己已有监控栈」，实测
**不成立**——全仓 `rule_files` 只有站点这一条，平台规则文件在 `docs/operations/README.md`
里标注「ADR-0011 待挂载」。因此那 19 条在**任何地方**都没有生效（站点上无样本 ⇒ 不触发），
方向 1 不损失任何真实告警，只是把「装了什么」变得诚实；它们的生效路径仍是控制面挂载。

## Alternatives

- **方向 2（站点补控制面 `/metrics` 抓取 job）**：需定端口/网络面并过 ADR-0024（生产类
  环境的 secure cookie/CSRF/TLS 约束），且把「站点」变成跨主机抓取点——本轮不选；
  若将来要站点看控制面指标，走独立单 + ADR。
- **安装期过滤整份文件**（YAML 解析后按指标生产者筛规则再渲染）：否决。安装器要长出
  一套规则筛选逻辑，且「站点到底装了什么」不再能直接看仓库文件（可审计性下降）。
- **保持现状 + 只在文档里说明**：否决。19 条死规则会继续让「监控就绪」看起来更完整，
  而这正是 #2488/#2643 一路要消除的那类假象。
- **把子集文件也加进 promtool 的 `rule_files`**：不做。子集规则与平台文件逐字段相同、
  而平台文件已被 promtool 全覆盖（含场景断言），重复加载同一条规则会让「每规则都有
  场景」的判据面对重复项；一致性由子集对拍守（本文件新增的那条）。

## Verification

- **反例构造（先证伪再采信）**：
  - ① 往站点子集加一条控制面域规则（**原样复制**平台定义，故漂移对拍不该管）→
    `test_installed_rules_without_site_producers_stay_registered` 与
    `test_debt_register_is_not_hollow` **双双 FAILED**；
  - ② 把子集里 `StabilityScriptGuardRetirementDue` 的 `expr` 改成 `> 1`（定义分叉）→
    `test_site_subset_is_verbatim_from_the_platform_file` **FAILED**。恢复后 7 passed。
- **两条被改写的既有测试**（它们钉的正是本单的旧前提）：
  - `test_site_install.py::test_monitoring_installs_alert_rules_and_guard_units` 与
    `…::test_shipped_distro_defaults_do_not_block_the_monitoring_stack` —— 新文件起初
    漏了 installer 的**归属标记**（`Rendered by the site installer for <deploy-root>`），
    第二条因此以 `install_conflict` 红：这正是共享路径归属守卫在防「第二站点静默覆盖」，
    补上标记后两条转绿；
  - `test_monitoring_asset_drift.py::test_legacy_distro_path_is_accepted` 把源文件写死成
    平台文件 → 改指子集（dest 未变，老路径仍要被认出这一点反而更值得钉住）。
- 实测：`pytest tests/ -q -k "site or prometheus or alert or monitoring"` → **615 passed**；
  `pytest tests/test_site_install.py -q` → **70 passed**；
  `python scripts/run_gates.py check:quick` → **[OK] (12 gates)**。

## Revisit

- **那 19 条的生效路径**：控制面挂载（ADR-0011）仍待落地；本文改动不改变它们的定义，
  只改变「装到站点」这一层。若将来到站点的机器上出现控制面指标（例如反向抓取落地），
  站点子集可以按同一份对拍重新扩表——先把指标纳入站点可见面，再进子集。
- **`_SITE_INERT_RULES` 重新非空 = 分层被破坏**：往站点文件加控制面域规则会红；若判定
  确实要加，正确出口是同时更新子集判据与该清单（而不是把清单条目加回去当豁免）。
- **告警条数别写进文字**：本单文档一律不写条数（#2663 的计数门禁只认派生真值）。
