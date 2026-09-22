# ADR-0038 v0.3 草案——D9 设备面意图（空置/人工清空）（2026-09-22）

Status: proposed
Class: architecture

## Decision

把「主机被人工清空设备后、设备面告警常亮且无合规解除手段」这一现网缺口，落成
**ADR-0038 §7 的 v0.3 草案（D9，待 owner 裁决；裁决前 §7 不生效）**；本 PR **不改任何代码、
规则或生产状态**，只落草案文本与索引登记。实施单 #3159。

核心一句：给 host 增加**第三个生命周期维度**——设备面意图（本期唯一值「空置/人工清空」，
`emptied_at/by/reason` 三列，可逆、与 `retired_at` 正交、**不扩** D5 的 14 面矩阵），
并用新指标 `stability_host_device_intent` 让 `StabilityHostUsbBlind` 在规则侧 `unless` 豁免；
`usb_tree_empty` 等原始 reason **照常上报**（不隐藏数据）。

起草时做了三个取舍：

1. **增补而非新 ADR**（裁决点 A3 的取向）：「主机生命周期有几个维度、各自由谁写」属本 ADR
   的同一序位；分开立 ADR 会出现两个文件各自描述 host 状态的可分叉表述。
2. **只做规则侧豁免，不动派发/认领**（D9.6）：设备不在，派发自然落空；把「空置」塞进 D5
   的 14 面矩阵会顺带改变准入语义，超出本缺口所需。
3. **不设自动过期**（D9.5）+ 显式接受取舍（D9.7）：自动过期会再造闪断式豁免；长期置位让
   「已空置 + 真故障」静默是**明知**的代价，靠 reason 必填 + 徽标 + Revisit 2 兜底。

## Alternatives

- **复用 `maintenance_until`**：否决——实测不抑制 `health.reasons`（只作用于探针目标选择、
  派发门、升级门）；且维护窗必有过期，而空置并无自然到期。
- **复用 `retired_at`（或 `status=EMPTIED`）**：否决——D3 终态与本文可逆语义冲突；
  `status` owner 是心跳，下一次心跳会改写（同 §3 首条）。
- **`Host.extra` 裸键**：否决——D4 明文（主心跳每拍重建 extra）。
- **自动推断置位**：否决——候选集缺「人为有意操作」时自动判因必错（#3065 三例），
  且会覆盖真故障的可见性。
- **mute / ack**：否决——#2900 / #2754 教训；无意图留痕、会连未预测形态一起埋。
- **设备级 retired 当期做**：延后 B 期（#2962，78 行受 FK 封路）。

## Verification

- 引用锚点行号在 `origin/main @ 1210c084` 实测：`deploy/prometheus/alerts-stability-platform.yml:597`、
  `backend/core/metrics.py:191/955`、`backend/api/routes/metrics.py:83/178/304`、
  `backend/services/host_retirement.py:18-19`、`docs/operations/host-device-visibility-triage.md:18`。
- 现网数据来自 2026-09-22 只读实测（78 行陈旧设备 / FK 引用计数 4482·4902·4426 /
  相关 serial 全机队无一处 ONLINE），见 #3065 与 #2962 的当日评论。
- 守卫：`tests/test_adr_index_status_2989.py`（ADR 索引一致性 + M7 看板覆盖）+ `check:quick`。
- 本 PR 的 diff 仅含：ADR-0038 头部（状态/版本记录/标签/关联）+ §7 新增 + README 两处登记 + 本 Note。

## Revisit

- owner 裁决 §7.6 的 A1–A5 后：把 §7 提升为 §2 的 D9 正式条款、版本记 v0.3（Accepted），再按 #3159 实施。
- 若 owner 选「新 ADR」（A3 备选）：把 §7 整节搬出为新 ADR，ADR-0038 回到 v0.2 并在 §5 Revisit 留指针。
- 若 A2 选「全设备面规则豁免」：先按 §7.5-4 逐条评估 `StabilityHostAdbOfflineConcentration` /
  `StabilityHostUsbControllerDead` / `StabilityHostUsbLinkDegraded` 的误报形态，再动规则文件。
