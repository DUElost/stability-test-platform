# 告警规则文件头归属收口：#3098 项 1（平台文件自述 + 站点子集固化计数）

Status: implemented
Class: bug-fix

关联：[#3098](https://github.com/DUElost/stability-test-platform/issues/3098)（本单末项）、
[#3113](https://github.com/DUElost/stability-test-platform/pull/3113)（项 2/3，已合入）、
[#2643](https://github.com/DUElost/stability-test-platform/issues/2643)（站点只装站点可见面，方向 1 已裁决）、
[#2985](https://github.com/DUElost/stability-test-platform/issues/2985)（同名文件在两种宿主上不是同一样东西 → 漂移检测按落点选源）、
[#2663](https://github.com/DUElost/stability-test-platform/issues/2663)（计数陈旧口径）、
[#2488](https://github.com/DUElost/stability-test-platform/issues/2488)（历史成因，本 PR 保留其陈述）。

## Decision

1. **`deploy/prometheus/alerts-stability-platform.yml` 头部（原 `:1-14`）三处自述修正**：
   - 删掉「Rendered by the site installer for `<deploy-root>`」——它**不在** `MONITORING_RULES`
     （该清单只含 `site-alerts.yml`），这个标记在本仓是「站点安装资产」的判据，留在平台文件上
     会继续让读者与工具把它当站点资产。
   - 「本文件**整份**被安装到站点…属方向裁决，由 #2643 的 owner 定」→ 改为方向 1 的终态
     （站点只装 `site-alerts.yml` 子集，`tests/test_site_alert_scrape_surface.py` 钉死该清单）。
   - 补上控制面宿主的真实落点与性质：`/etc/prometheus/rules/alerts-stability-platform.yml` 是
     **人工副本**（原样拷贝、不渲染），改完需**人工重放 + `POST /-/reload`**；并点明**同名文件
     在两种宿主上不是同一样东西**（站点那份是 `site-alerts.yml` 渲染产物），故
     `tools/dev/check-monitoring-assets.py` 按**落点所属宿主角色**选事实源（#2985）。
   - 保留 `#2488` 历史段与 promtool 提醒（历史陈述不追改）。
2. **`deploy/prometheus/site-alerts.yml` 头部同族的固化计数去掉**：「那 **19** 条 / 装了 **21** 条」
   → 不写数字（实测 platform=**35** / site=**4**），与 `#3113` 对 `stages.py` 的处理同口径。
   **这一处不在 #3098 的三项之内**，属同族第 4 处，本 PR 一并收口并在此披露。

## Alternatives

- **保留 `<deploy-root>` 标记、把平台文件也纳入站点清单**：方向相反（#2643 已裁决），且会把
  「装了但恒不触发」的假象固化，否决。
- **只改「整份装到站点」一句、保留 `Rendered by the site installer`**：该标记是归属判据，
  留在平台文件上读者仍会误判归属，必须删。
- **同批修 `tests/test_site_install.py::test_alert_rules_file_and_installed_copy_stay_in_sync`
  的 docstring**（其「规则文件带占位符头 ⇒ 安装的是渲染产物」在 #2643 之后同样过期）：
  不在本 PR——改测试面会扩大爆炸半径，列入 Revisit。
- **严格只动平台文件、不碰 `site-alerts.yml` 的计数**：可，但会留下同族第 4 处陈旧计数，
  与 `#3113` 已确立的口径不一致，故一并改。

## Verification

- `venv/bin/python -m pytest tests/test_site_install.py tests/test_site_alert_scrape_surface.py
  tests/test_prometheus_alerts_contract.py tests/test_monitoring_asset_drift.py
  tests/test_alert_count_claims_are_live.py tests/test_staleness_threshold_vs_timer_period_3106.py -q`
  → **165 passed in 256.18s**
- `venv/bin/python scripts/run_gates.py check:quick` → **`[OK] check:quick (15 gates)`**
  （本次多出的 `tool-manifest` 是期间合入的新门禁，同批绿）
- `venv/bin/python tools/dev/check_governance_surface.py --check` → `[OK] 治理面结构检查通过（S1–S15、S5x）`
- **语义核验（删掉标记的影响面）**：`tests/test_site_install.py:888` 的
  「缺归属标记：第二站点会静默覆盖本站规则」断言对象是**安装产物**
  （`etc/stp/prometheus/rules/alerts-stability-platform.yml`，由 `site-alerts.yml` 渲染），
  该文件的标记**保留**、断言未受影响；`:915-920` 的 `<deploy-root>` 替换在平台文件上成为
  **no-op**，`yaml.safe_load` 仍通过（以上均由 165 passed 覆盖）。
- `tools/verify_control_plane_templates.py` 的 `<deploy-root>` 唯一性校验只覆盖
  `deploy/control-plane/**` 模板 → 与本次改动无关。
- 计数实测：`grep -c '^\s*- alert:'` → platform **35** / site **4**。

## Revisit

- **运维副作用（预期且由设计承接）**：平台文件字节已变 ⇒ 控制面宿主上那份人工副本在**重放前**
  会被 `tools/dev/check-monitoring-assets.py` 报该资产 `drift`——这正是该 checker 的存在理由
  （「改了仓库、没重跑安装」）。合入后按 `docs/operations/README.md` §6 重放一次即可消解。
- `tests/test_site_install.py::test_alert_rules_file_and_installed_copy_stay_in_sync` 的
  名称与 docstring 前提（平台文件带占位符头 ⇒ 被渲染）在 #2643 后已过期，建议随下次测试面改动修正。
- 若「计数/归属类自述陈旧」再复发，应扩 `tests/test_alert_count_claims_are_live.py` 的形态面
  （`#3113` 的 Alternatives 已记录该方向）。
