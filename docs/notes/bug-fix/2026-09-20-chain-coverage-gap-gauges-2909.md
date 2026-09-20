# 链覆盖差可发现化：ONLINE∉schedule 三元组 gauge + 10% 告警（#2909 问题③）

Status: implemented
Class: bug-fix

## Decision

问题②（清单刷新策略）等 owner 裁决，**③ 无争议先行**——票面自证：
「当前断了 20% 都没人知道，是本轮靠人肉排查发现的」。度量落点选择：

- **权威源而非观测样本**：分母=未退役 host 上 ONLINE 设备；清单=`task_schedules.device_ids`
  的 **enabled 行并集**（链 run 的即时修剪不参与——那会把「结构性掉出」稀释成
  「此刻恰好没派」，与本告警要抓的失效不同物；同记忆锚「键集取权威源全集」）；
- 三元组 `stability_chain_coverage_devices{kind=online_total|scheduled_union|gap_missing}`
  落原子事实，ratio 归 PromQL——与 fleet gauge 同法拉取期现算（表小、无周期任务
  staleness）；退役/无主设备排除与 adb/fleet gauge 同族口径（D5/不硬造 (none)）；
- 阈值 10%/60m + 分母下限 50：**上线即 firing 是设计意图**（实测 gap=12.6%，
  #2909 诊断评论有数）——一只迟到三年的铃必须响；annotation 写明处置在问题②
  裁决、**禁止用调阈值消音**。
- promtool 正反例（0.15 fires@65m、exactly 0.1 不 fires）+ expr 用
  `sum without (kind)` 解除两侧 kind 标签的 1:1 匹配死锁（首版实弹教训：
  不 without 则比值恒空、告警永不响——promtool 测试把它当场抓住）。

## Alternatives

- **按链计划维度打标签（per-plan ratio）**：弃——问题要的是「fleet 级还有谁没进
  任何周期观测」，per-plan 会把整机断链（.63 型）摊薄成每台计划的中等缺口；
- **对比最新 CHAIN run 快照而非 schedule**：弃——run 快照被即时修剪污染（浮动
  529-553），且断链 host 的 run 缺席时读数会**虚假变好**；
- **阈值先设 20% 免得部署即红**：弃——红是对的指标不该为观感调高；annotation
  写明这是现状而非事故突发。

## Verification

- backend：新用例 2（三元组精确值；退役/无主排除）+ read_api_auth → 75 passed；
- 契约族（alert producers / prometheus contract / adb gauges）→ 47 passed；
- `promtool test rules` → SUCCESS（正反例）；ruff 全仓 clean。

## Revisit

- 问题②裁决落地（动态派生或清单再生成）后：gap 应回落至噪声水位；若把 10%
  阈值改小，需与 schedule 再生成周期一起定（再生成每 24h 跑，60m for 窗口够）；
- site 子集挂载：`stability_*` 指标生产者归控制面（#2643 的站点无生产者问题同样
  适用于本指标——告警规则归控制面副本，站点装配面按 #2731 的可见面机制走）。
