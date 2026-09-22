# 告警面自述与计数固化收口：#3098 项 2/3（06-realtime「草案」+ stages.py 计数）

Status: implemented
Class: bug-fix

关联：[#3098](https://github.com/DUElost/stability-test-platform/issues/3098)（本单——承接已关单
[#2985](https://github.com/DUElost/stability-test-platform/issues/2985) 关单时未落地的三处备注项中的两处）、
[#3040](https://github.com/DUElost/stability-test-platform/pull/3040)（#2985 的关单 PR：只修了检测器侧）、
[#2663](https://github.com/DUElost/stability-test-platform/issues/2663)（运维面计数陈旧的口径来源）、
[#2643](https://github.com/DUElost/stability-test-platform/issues/2643)（站点只装站点可见面，方向 1）。

## Decision

本轮只改两处**自述与事实不符**，不碰规则内容、不动安装清单：

1. **`docs/design/06-realtime-and-background.md`** 的告警段原句
   「告警草案：`deploy/prometheus/alerts-stability-platform.yml`（ADR-0011 待运维挂载）」
   有三点已过期：① 该文件是活文件（实测 35 条规则）；② 控制面宿主上已有**人工副本**，
   改规则需重放 + `POST /-/reload`（`docs/operations/README.md` §6 已写）；
   ③ 站点只装 `site-alerts.yml` 子集（`#2643` 方向 1 已由 `fefeb019` 落地）。
   改为指向 `operations/README.md §6` 的真实口径，并**保留仍成立的那半句**——
   ADR-0011 的正式挂载确实仍未落地。
2. **`tools/site_config/stages.py`** 的 `MONITORING_RULES` 注释把两侧条数固化为
   「`site-alerts.yml` 的 **2 条** / 平台其余 **19 条**」；实测 **site=4 / platform=35**。
   按 `#2663` 之后的口径**去掉数字**，改为「需要规模时用两份文件的 `alert` 名做集合差当场算」。

## Alternatives

- **把数字更新成 4 / 35**：否。`#2663` 的结论正是「抄进正文的计数会随下一次增删说谎」，
  而该形态（`N 条 <非告警名词>`）当前**没有护栏**——更新即重犯。
- **同批扩 `tests/test_alert_count_claims_are_live.py` 的识别形态**（覆盖「N 条 <名词>」）：
  是治本方向，但属护栏形态变更（需自证、且要确认不会触发存量命中），不与本次「尾账收口」同批；
  留给该 gate 的持有单。
- **同批改平台规则文件头（`#3098` 项 1）**：不做。`.wt/stp-3077` 当时正持该文件的未提交修改
  （`#3077` OPEN），按撞单纪律让出。
- **删掉注释里 `#2488` 的历史计数段（「17 条规则一条没上线」）**：保留。那是有 Note 指针的
  **历史陈述**，与本条「当前规模」性质不同，删了会丢 #2488 的成因线索。

## Verification

- `venv/bin/python -m pytest tests/test_alert_count_claims_are_live.py tests/test_monitoring_asset_drift.py
  tests/test_site_alert_scrape_surface.py tests/test_prometheus_alerts_contract.py tests/test_site_install.py
  tests/test_env_inventory.py -q` → **176 passed in 249.70s**
  （覆盖规则逐条登记面、监控资产漂移、promtool 契约与站点安装链——本 PR 改的两处都在其守望范围内）
- `venv/bin/python scripts/run_gates.py check:quick` → **`[OK] check:quick (14 gates)`**
- `venv/bin/python tools/dev/check_governance_surface.py --check` → `[OK] 治理面结构检查通过（S1–S15、S5x）`
- 计数实测（改前口径核对）：`grep -c '^\s*- alert:'` → platform **35** / site **4**
- 被改文本无判据依赖：`grep -rn '告警草案\|待运维挂载\|19 条' tests/ tools/` 的命中均为无关项
  （env_inventory 的「19 条假红」说明、gate 自身的 fixture 串）

## Revisit

- `#3098` 项 1（`deploy/prometheus/alerts-stability-platform.yml` 的 `:2` 与 `:10-14`）
  待 `#3077` 合入后收口——届时该文件的「整份装到站点」自述与 #2643 方向应一并改掉。
- 若「计数类自述陈旧」再复发（第三次），应把 `test_alert_count_claims_are_live.py` 的形态面
  扩到「`N 条 <任意名词>`」，而非继续逐处删数字。
