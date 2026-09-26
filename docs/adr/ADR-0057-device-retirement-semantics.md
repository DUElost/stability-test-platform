# ADR-0057：设备退役语义（Device Retirement Semantics）

- 状态：**Accepted** v1.0（2026-09-26 owner 授权 Claude 裁决：E1–E5 全部采起草取向，见 §7；实施未开始）
- 优先级：P2
- 目标里程碑：M7
- 日期：2026-09-26
- 决策者：owner（2026-09-26 授权 Claude 裁决）；起草：平台研发组
- 归属域：semantic-ownership device-retirement
- 标签：生命周期, 设备, 退役, 数据保留, 容量口径
- 关联：[#2962](https://github.com/DUElost/stability-test-platform/issues/2962)（触发：OFFLINE 库存陈旧）、
  [ADR-0038](./ADR-0038-host-retirement-semantics.md)（主机退役；其 D7 明确「不做设备退役，独立议题，需求出现时单独提案」——本 ADR 即该提案）、
  [ADR-0055](./ADR-0055-schedule-device-selector.md)（`selector` 健康门）、#106（device 容量口径）、#3159（主机空置意图）
- 版本记录：v1.0（2026-09-26）**裁决**：E1–E5 全部采起草取向，转 Accepted，见 §7；v0.1（2026-09-26）首次起草，Proposed

## 1. 背景

### 1.1 问题定性

设备只有「在线状态」一个维度，没有「是否还在役」的维度。OFFLINE 同时表示「刚掉线」和「早已报废 / 送修 / 归还」，
于是库存数、容量口径与链选设备都被历史沉积污染。2026-09-26 已裁决先做**陈旧度派生**（#2962 方案 A：`OFFLINE` 且
`last_seen` 超过 7 天现算为「陈旧」）。陈旧度是自动、可逆、推断出来的；它回答不了「这台设备已经确认不再使用」。
本 ADR 补的是**人确认过的终态**。

### 1.2 事实（`main@8dc9d82` 读码 + #2962 实测）

| 事实 | 出处 |
|---|---|
| `DeviceStatus` 只有 `ONLINE / OFFLINE / BUSY / ERROR` | `backend/models/enums.py:46` |
| 心跳把没再出现的设备确定性改写为 `OFFLINE`（并写 `adb_state="offline"`） | `backend/api/routes/heartbeat.py:139-165` |
| 2026-09-20 实测：202 台 OFFLINE 中 188 台超过 7 天未上报，83 台超过 30 天 | #2962 正文 |
| 设备行被历史表以 FK 引用，硬删会破坏历史 | `models/job.py:24`、`models/plan_run.py:209`（`plan_run_target_device`）、`models/device_lease.py:27`、`models/resource_pool.py:40` |
| 设备列表已按**主机**退役过滤（`include_retired` 默认 false） | `backend/api/routes/devices.py:351-385` |
| per-host `adb_state` 计数不区分设备是否在役 | `backend/api/routes/metrics.py:106-134` |

### 1.3 约束

- 不删历史行、不改 FK 的级联语义（与 ADR-0038 D3/D7 一致）。
- 不在 `DeviceStatus` 增值：心跳每轮都会改写 `status`，新值会被覆盖，且租约 / 派发 / 告警多处按该枚举分支。
- 与陈旧度（#2962 A）互补而不重复：陈旧度继续承担「自动发现」，退役承担「人工确认」。

## 2. 决策

- **D1 单一真源**：`device` 表新增可空列 `retired_at`（TIMESTAMPTZ）/ `retired_by`（String(128)）/ `retire_reason`（Text，必填）。
  `retired_at IS NOT NULL` 即退役。additive 迁移，存量默认在役。事件真源是 `audit_logs`，退役写路径**审计失败即事务失败**（对齐 ADR-0038 D1）。
- **D2 入口与前置**：新增 `POST /devices/{id}/retire`、`POST /devices/{id}/unretire`（admin + 审计），以及批量退役
  `POST /devices/retire`（逐台返回结果，任一台失败不影响其他台）。退役前置：设备**无活跃 Job、无 ACTIVE 租约**，否则 409。
  unretire 无前置；清空 `retired_at`，`retired_by / retire_reason` 保留最近一次痕迹。均幂等。
- **D3 与心跳的关系**：退役不改写 `status`。已退役设备重新出现在心跳里时，**如实记录**事实列（`last_seen`、`adb_state`、`status`），
  **保持退役**，并发**一次**告警提示人工确认是否 unretire（去重用持久列，如 `retire_alerted_at`；禁 `Device.extra` 之类裸键）。
- **D4 收口面**：统一判据 = 「`device.retired_at IS NULL` ∧ 原判据」，覆盖下表全部面（允许扩充、不允许缩减）：

  | # | 面 | 锚点 |
  |---|---|---|
  | 1 | 派发快照与准入分类 | `backend/services/plan_dispatcher_sync.py` `_classify_dispatch_devices_sync`；`admission_pump.py` |
  | 2 | claim | `backend/api/routes/agent_api.py` `claim_jobs` |
  | 3 | 链选设备与定时 `selector` | `plan_chain_trigger._select_chain_devices`；ADR-0055 D2 健康门 |
  | 4 | 设备列表 / 前端多选 / 就绪判定 | `devices.py:351-385`（与主机退役同一 `include_retired` 开关）；`DeviceMultiSelect.tsx`；`planExecuteReadiness.ts` |
  | 5 | 统计、容量、指标 | `stats.py`；`metrics.py:106-134`（per-host `adb_state` 计数）；#106 容量口径 |
  | 6 | 设备面告警 | `StabilityHostAdbOfflineConcentration` 等以设备行计数为输入的规则 |
  | 7 | AI 助手读写面 | `backend/services/ai_assistant/tools.py` |
  | 8 | 标签、项目归属等管理写路径 | `PUT /devices/{id}/tags`、项目分配 |

- **D5 与主机退役正交**：主机退役 → 其设备按 ADR-0038 D5 被整体排除；设备退役只作用于单台。两者任一成立即排除。
- **D6 与陈旧度的关系**：陈旧度不自动转为退役。设备列表对「陈旧超过 N 天」的设备给出**退役建议**（只提示、不动作），由人确认后批量退役。
- **D7 显式不做**：不做硬删与 purge；不做「送修 / 外借」等更细的状态（出现第二种需求时再把退役原因枚举化）。

## 3. 备选与否决

| 方案 | 结论 | 理由 |
|---|---|---|
| `DeviceStatus` 增 `RETIRED` | 否决 | 心跳每轮改写 `status`；租约、派发、告警按枚举分支，全部要改 |
| 只靠陈旧度，不做退役 | 不作为终态 | 回答不了「已确认报废」；报废设备偶发上电会自动「复活」回库存 |
| 硬删无历史的设备、其余保留 | 否决 | 188 台陈旧设备几乎都有历史引用，覆盖不了主要对象；删除不可逆 |
| 并入 ADR-0038 增补 | 否决 | ADR-0038 D7 已明确设备退役是独立议题 |

## 4. 影响

- 迁移一次（三列 + 去重列）；API 与 `types.ts` 同步；D4 八个面各配回归测试与反例（去掉判据必红）。
- 存量处置：83 台超过 30 天的设备由人按清单确认后批量退役；清单由陈旧度视图导出。

## 5. 验收与 Revisit

- 验收：退役设备不出现在派发、claim、链选、默认列表、容量与告警计数中；退役设备重新上报时只告警一次；unretire 后全部面恢复。
- Revisit：出现第二种设备意图（送修 / 外借）时，把 `retire_reason` 升级为枚举；ADR-0055 `selector` 上线后复核健康门是否已接入本判据。

## 6. 裁决点（2026-09-26 已裁，结果见 §7）

| # | 问题 | 起草取向 | 备选 |
|---|---|---|---|
| E1 | 已退役设备重新上报 | 保持退役 + 单次告警 | 自动 unretire |
| E2 | 退役前置 | 无活跃 Job、无 ACTIVE 租约，否则 409 | 允许强制退役并中止其 Job |
| E3 | 存量 83 台如何处置 | 陈旧度视图导出清单，人工确认后批量退役 | 超过 30 天自动退役 |
| E4 | 陈旧设备的退役建议阈值 | 陈旧超过 30 天显示建议 | 不做建议 |
| E5 | 设备面告警是否按退役排除 | 排除（D4 第 6 面） | 保留计数、只在面板区分 |

## 7. 裁决记录（2026-09-26，owner 授权 Claude 裁决）

| # | 裁决 | 依据 |
|---|---|---|
| E1 | **保持退役 + 单次告警** | 报废设备偶发上电不应自动回到库存；是否恢复由人判断，告警只发一次避免刷屏（对齐 ADR-0038 D4） |
| E2 | **前置：无活跃 Job、无 ACTIVE 租约，否则 409** | 退役是账面动作，不应顺带中止在跑的测试；要退役在用设备，先按既有流程中止或等待 |
| E3 | **从陈旧度视图导出清单，人工确认后批量退役** | 自动退役会把「长期关机待修」与「已报废」混为一谈；清单由陈旧度（#2962 方案 A）现算，确认动作留痕 |
| E4 | **陈旧超过 30 天在设备列表显示退役建议**（只提示，不动作） | 与 #2962 实测「83 台超过 30 天」的分档一致；7 天陈旧阈值用于默认隐藏，30 天用于建议退役，两者分工不同 |
| E5 | **设备面告警排除已退役设备** | 已退役设备的 `adb_state` 恒为 offline，计入会让 `StabilityHostAdbOfflineConcentration` 等规则长期误报 |

实施在 #2962 领单：迁移 + API + D4 八面收口 + 陈旧度视图的退役建议与批量退役入口。
