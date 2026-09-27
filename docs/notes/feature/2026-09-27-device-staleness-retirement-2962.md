# 设备陈旧度（#2962 A）与设备退役（ADR-0057 B）实施 Note

Status: implemented
Class: feature

- 日期：2026-09-27
- Issue：#2962（A 期陈旧度 + B 期 ADR-0057 E1–E5）
- 迁移：`b2c3d4e5f6a7_device_retirement_columns_2962`（device 四列，additive nullable）
- 关联：ADR-0057 v1.1（§8 实施记录）、`docs/notes/architecture/2026-09-26-tier-b-issue-decisions.md`（A 期裁决）、`.../2026-09-26-pending-decisions-round4.md`（B 期裁决）

## Decision

### A 期：陈旧度派生（7 天，现算不落库）

- **单一口径**：`backend/services/device_lifecycle.py` 的 `is_stale`（`OFFLINE` ∧ `last_seen` 早于 7 天 **或为空**）与 `is_retire_suggested`（陈旧 ∧ 最后上报早于 30 天；`last_seen` 为空不给建议——「超过 30 天」无从度量）。SQL 判据同源（`stale_condition` / `not_stale_condition`），八面不许各拼谓词。
- **消费面默认排除**：`GET /devices` 新增 `include_stale`（默认 false，与 `include_retired` 同为「默认隐藏 + 显式开关」）；容量口径按新鲜度计算——fleet `stability_device_online{status="offline"}` 只计近期掉线、陈旧单列新 gauge `stability_device_stale`；per-host adb 分桶剔除陈旧；`dashboard_summary` 拆 `offline` / `offline_stale`；低电量/高温告警计数只看新鲜度窗内设备。
- **链选**：陈旧是 `OFFLINE` 子集，链选既有规则已排除「过期 OFFLINE」（`offline_stale`），无需新增判据；退役设备另行排除（见 B 期）。
- **展示**：`DeviceOut` 增派生 `is_stale` / `retire_suggested`；设备页两个开关 + 行内徽标；`planExecuteReadiness` 增「设备陈旧（>7 天未见）」原因。

### B 期：设备退役（ADR-0057 D1–D7，E1–E5 全采）

- **D1**：`device.retired_at / retired_by / retire_reason / retire_alerted_at` 四列；`retired_at IS NOT NULL` 即退役；退役**不改写 status**、不删历史行；审计 fail-closed（`record_audit(strict=True)`）。
- **D2/E2**：`POST /devices/{id}/retire`、`POST /devices/{id}/unretire`、`POST /devices/retire`（批量，逐台独立事务 + 逐台结果/计数）；admin；前置 = 无活跃 Job、无 ACTIVE 租约否则 409；幂等；unretire 清 `retired_at`，`retired_by/retire_reason` 保留最近痕迹。
- **D3/E1**：心跳对退役设备如实记录事实列、保持退役；`retire_alerted_at` 去重单次告警 `DEVICE_RETIRED_HEARTBEAT`（恢复拍重计轮，与主机侧 #1806 同款）；退役设备不再派 `DEVICE_OFFLINE` 通知。
- **D4 八面收口**（判据 = `device.retired_at IS NULL ∧ 原判据`）：
  1. 派发/准入：分类器新增 `device_retired`，与 `host_retired` 并列 **fatal**（先于设备级暂态判定）；
  2. claim：设备查询过滤 `retired_at`；
  3. 链选：rows 增第 5 列，退役一律排除并记 `device_retired`（优先于 COMPLETED 无条件路径）；
  4. 设备列表/前端多选/就绪判定：列表 `include_retired` 同开关覆盖设备退役；就绪原因「设备已退役」；
  5. 统计/容量/指标：dashboard 排除退役、fleet/per-host gauge 排除退役、心跳 `online_healthy_devices` 两个端点同口径；
  6. 设备面告警：`StabilityHostAdbOfflineConcentration` 的数据源（per-host adb 分桶）在现算侧剔除退役；
  7. AI 助手：`_q_devices` 与 fleet 概览过滤退役；
  8. 管理写路径：标签与批量项目归属对退役设备 409。
- **D6/E4**：列表对「陈旧 > 30 天」给出「建议退役」徽标（只提示不动作）；**E3** 批量入口 = 列表（含开关查看）导出后人工确认，走 `POST /devices/retire`。

### 实现取舍（留痕）

1. `device_retired` 取 **fatal**（对齐 `host_retired`）：永久事实进 QUEUED 等于永远占位；静态 Plan/排程仍引用退役设备时 prepare/准入显式失败（detail 带 `reason=device_retired`），由人修清单而非静默缩目标。链选在触发侧剔除（不 fatal）。
2. ADR D2 未定义批量 unretire：批量 API 只做退役；前端「解除退役」逐台串行（低频人工动作、选择集小）。
3. `_mark_missing_devices_offline` 仍把退役设备标 `OFFLINE`（事实照记），只是不发通知——「退役 ≠ 事实冻结」。

## Alternatives

- **`DeviceStatus` 增 `RETIRED`**：心跳每轮改写 `status`，会用下一拍静默撤销退役决定（ADR-0057 §3 已否决）。
- **陈旧度落库/定时任务**：现算即可逆、回场自动恢复、零回填；落库要新增 staleness 任务与两处写路径，收益不足。
- **`device_retired` 非 fatal（排队等待）**：队列语义是「暂时不可用，等待重试」；退役是永久事实，等待只会让 run 无限 QUEUED。
- **陈旧与退役共用一个开关**：两者生命周期不同（陈旧自动可逆、退役人工终态），合并开关会让「找退役设备」与「找沉积库存」互相污染；故两个独立参数。
- **把 stale 也挡在 claim/派发**：陈旧是 `OFFLINE` 子集，status 检查已覆盖；重复加判据只增加一处可能与 status 语义漂移的谓词。

## Verification

| 命令 | 结果 |
|---|---|
| `pytest backend/tests/services/test_device_lifecycle_2962.py backend/tests/api/test_device_retirement_api_2962.py backend/tests/api/test_device_retired_heartbeat_2962.py -q` | **28 passed**（纯函数边界 / API 全链 / 心跳四边沿） |
| `pytest backend/tests/api/test_metrics_device_lifecycle_2962.py backend/tests/services/test_ai_tools_device_lifecycle_2962.py backend/tests/services/test_plan_dispatcher_device_validation.py backend/tests/api/test_agent_api_watcher.py -k "retired or lifecycle or dispatch or claim or adb or stale" -q` | **61 passed** |
| `pytest backend/tests/services/test_plan_chain_trigger.py backend/tests/services/test_chain_trigger_offline_filter.py -q` | **39 passed**（含新增退役排除用例） |
| `pytest backend/tests/migration/test_device_retirement_roundtrip_2962.py -q` | **2 passed**（docker postgres:16 真跑 upgrade→downgrade→upgrade；四列可空、无回填） |
| `pytest backend/tests/api/ backend/tests/services/ -q` | **2756 passed**（11m53s；前一次跑中 1 例为时序 flaky，重跑全绿） |
| **变异自证（5 条，全部被测试抓住）** | ① 从 `_FATAL_DISPATCH_REASONS` 移除 `device_retired` → 派发 prepare 用例红；② 撤 `agent_claim` 的 `retired_at` 过滤 → claim 用例红；③ 撤列表 `not_stale_condition` → 默认隐藏用例红；④ 撤链选退役分支 → 链选用例红；⑤ 撤心跳单次告警判据 → 首拍告警用例红 |
| 前端 `vitest run`（全量 136 文件） | **1143 passed**；`npm run type-check` + eslint 通过 |
| `scripts/run_gates.py check:quick` | **[OK] 16 gates**（含 C1 分层与 inner-imports 棘轮） |

**过程中被门禁挡下并修正的两处（留痕）**：
1. **C1 分层**：`api.schemas` 不得 import `services`——schema 的派生字段需要的纯函数下沉到
   `backend/core/device_lifecycle.py`（与 `core/device_serial.is_placeholder_serial` 同款），
   SQL 判据留在 `backend/services/device_lifecycle.py`（需要 Device 模型，core 层不得 import models）。
2. **inner-imports 棘轮**（610 → 617 超限）：新代码的函数体内 import 全部上提（schemas/dashboard/
   heartbeat/设备 SQL 判据共 6 处），并顺手移除 `devices.py::bulk_assign_project` 里与顶层重复的
   `ProjectModel` 内联 import；**通知派发保持内联**——测试靠 monkeypatch `notification_service`
   的晚绑定生效，上提会静默破坏既有测试的桩。

## Revisit

- **7 天阈值**：A 期上线满 30 天后按「陈旧后又回场」比例回看（#2962 原裁决）。
- **存量 83 台（E3）**：按陈旧度视图导出清单人工确认后批量退役；退役后若发现某台需回场，`unretire` 可逆。
- **ADR-0055 `selector` 落地后**：复核其健康门是否复用 `not_retired_condition()`（当前 selector 未实现，链选路径已先行接入）。
- **AI 读面是否也排除陈旧**：本轮只按 D4 排除退役；若助手把沉积库存报成在线容量造成误判，再把 `not_stale_condition` 接进助手读面（触发器 = 出现该类误判）。
- **批量 unretire**：若出现「一次性恢复大批设备」的真实需求，再补批量端点（当前逐台）。
