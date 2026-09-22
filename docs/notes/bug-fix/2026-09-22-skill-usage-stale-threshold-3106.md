# skill 用量陈旧阈值与生产周期对齐：48h → 2× 周级（14 天），判据改为从 timer 推导（#3106）

Status: implemented
Class: bug-fix

## Decision

`StabilitySkillUsageUntrusted` 的陈旧分支是 `time() - stp_skill_usage_last_run > 172800`（48h），
而它唯一的生产者 `stp-skill-usage.timer` 是**周级**（`OnCalendar=Mon *-*-* 09:30:00`）。48h 是
**0.29× 周期**：从周三 09:30 之后到下一个周一 09:30，差值必然超过 48h，于是这条告警**每周约
5 天持续 firing**。同一表达式还承担「探针崩了（broken）/ 强信号源不在场（unknown）/ 真停摆」
三种真信号 —— 一个恒响位把三者一起淹掉：这是告警疲劳，规则事实上失效。

两件事：

1. **阈值改成 2× 生产周期** = `1209600`（14 天），两个文件同时改（`alerts-stability-platform.yml`
   与站点子集 `site-alerts.yml`），并把文案里的「超过 48 小时」改为「超过 14 天（2× 周级周期）」
   —— 阈值改了文案不跟着改，就是下一条会骗人的注释。
2. **判据不再手写，改为推导**：新增
   `tests/test_staleness_threshold_vs_timer_period_3106.py`，从规则文件里抽出所有
   `time() - <metric> > <阈值>`，再**推导该 metric 的生产周期**，断言 `阈值 ≥ 2×周期`。

   周期的推导按生产者类型分家，且**不留第二份清单**：

   - systemd timer 驱动的 `stp_<x>_y_last_run` → 单元名直接推导为 `stp-x-y.timer`，
     周期取该单元的 `OnUnitActiveSec=`（如 pg-guard 的 `5min`）或 `OnCalendar=`（带星期字段
     ⇒ 周级，否则日级）。**新增探针不需要改判据**。
   - cron 驱动的（现在只有第五道闸账本 `stability_script_presence_sweep_timestamp`）周期取
     settings 默认值，用 `tools/dev/env_inventory.scan_reads()` 读
     `SCRIPT_PRESENCE_SWEEP_CRON`（复用既有扫描器，同一事实不留两套）。
   - **推导不出来即红**：新增陈旧判据却没人说清它的周期时，本文件报错而不是静默跳过 ——
     静默跳过正是「判据退化后仍报绿」的入口。

   现行四条一次核过：skill-usage `1209600 ≥ 2×604800` ✓、script-guard `172800 ≥ 2×86400` ✓、
   pg-guard `3600 ≥ 2×300` ✓、第五道闸账本 `172800 ≥ 2×86400` ✓。

## Alternatives

- **把 timer 改成日级**（与 `stp-script-guard.timer` 一致），阈值保持 48h：也能让不变式成立，
  但改的是**采集节奏**这个产品决定 —— `stp-skill-usage.timer` 的注释写明「HOLLOW 观察窗最小
  14 天，周级节奏足够」，且它决定的是「多久看一眼」，不是「多久算停摆」。本单要修的是
  后者与前者不一致，故只动后者。若 owner 想同时提高观测频率，那是另一件事（会连带改
  `for: 7d` 的语义解释）。
- **只改阈值不加判据**：否决。#2998 的教训就是「同一形态隔一窗复发」——阈值是手写的数字，
  下一个人按日级口径抄到周级探针上会原样重犯（本单正是这么产生的：timer 先改周级，
  阈值次日按日级口径抄来）。判据要从周期推导，数字才会自己保持正确。
- **把陈旧判断移出告警、只留指标**：否决。`absent()` 只能覆盖「指标整个消失」，覆盖不了
  「文件在场但数据陈旧」（第 #2973 的现场就是这种形态：崩溃在写之前，旧值继续被吐出）。
  陈旧分支是必要的，问题只在它的量纲。

## Verification

- `pytest -q tests/test_staleness_threshold_vs_timer_period_3106.py` → **3 passed**
- **变异自证（不碰工作树）**：把规则副本里的 `> 1209600` 改回 `> 172800` 后调用判据本体，得到
  `mutated.yml StabilitySkillUsageUntrusted: stp_skill_usage_last_run 阈值 172800s < 2× 周期
  604800s（0.29×）——恒响形态：真停摆会被淹没` ⇒ 判据确实能判红本单描述的形状。
- `pytest -q tests/test_prometheus_alerts_contract.py tests/test_site_alert_scrape_surface.py
  tests/test_staleness_threshold_vs_timer_period_3106.py` → **57 passed**（含结构层、覆盖棘轮、
  场景层解析、站点子集对拍）
- 文案一致性：YAML 折叠后平台文件与站点子集的 `description` **逐字相同**（`>-` 折叠断点保持
  原样，避免折叠差异造成的假不一致）；promtool 场景文件的两处 `exp_annotations` 同步。
- `scripts/run_gates.py check:quick` → OK；`check_governance_surface.py` → OK（S1–S15、S5x）。

## Revisit

- **promtool 场景层仍不覆盖陈旧分支**：#3106 指出场景文件故意喂 `99999999999x12100` 让
  `time() - last_run` 恒为负。本 PR 只加结构层判据，未加「陈旧分支真的会 fire」的场景用例
  （需要构造一个 last_run 明显落后的真实时间序列，属场景层增量）。若要做，宜同时给站点副本
  补场景（`site-alerts.yml` 侧无 promtool 覆盖）。
- **站点侧需要重放才生效**：规则文件的改动要经站点渲染/控制面重放 + `POST /-/reload` 才生效
  （人工副本，见 `docs/operations/README.md` §6）。本 PR 只保证仓库侧正确。
- **第五道闸账本的周期来自 cron 默认值**：若运维把 `SCRIPT_PRESENCE_SWEEP_CRON` 改成更低频率
  （如每 10 分钟），48h 仍是 288×，不受影响；但若改成 `0 9 * * 1`（周级），判据会红 —— 这是
  期望行为（阈值需同步）。
