# 设备列表默认排序只由恒定身份列构成（#3123）

Status: implemented
Class: bug-fix

- 日期：2026-09-22
- 关联：`#3123`（本单）、`#537`（同主题前一次修复，只补了 tie-breaker）、
  `docs/notes/bug-fix/2026-08-29-device-list-ordering-tiebreaker.md`（部分取代，见 Revisit）、
  `#496`（列表分页/虚拟滚动，deferred，本单不涉及）
- 落点：`backend/api/routes/devices.py:351`（排序 + 注释）、
  `backend/tests/api/test_devices.py`（两条用例）、
  `frontend/src/utils/api/devices.ts:33`（分页对全序的依赖，注释）

## Decision

`GET /api/v1/devices` 的默认排序从

```python
.order_by(Device.last_seen.desc().nullslast(), Device.id.asc())
```

改为

```python
.order_by(Device.host_id.asc().nullslast(), Device.id.asc())
```

**判据是一条不变量：排序列只能由恒定身份列构成。** `Device.last_seen` 在每一次设备
心跳都被重写（`backend/api/routes/heartbeat.py:535`），所以以它为主键时，`id` 这个
tie-breaker 保证的只是「同一时刻的组内顺序」——列值一变，全序整体重排。`#537` 当时
断言「整个结果集全序确定」，在快照内成立、跨时刻不成立，本单取代的是这一点。

生产实测（2026-09-22 13:40 CST，只读）：

| 测量 | 值 |
|---|---|
| 设备数 / `last_seen` 不同取值 | 862 / **160**（同主机几十台时间戳完全相同） |
| 距最新 `last_seen` 10s / 60s 内 | 295 台 / 611 台 |
| 第 50 行（表格第 1 页边界）落后最新值 | **0.34 秒** |
| 两次快照（间隔 = 前端一个轮询周期 10s）前 50 行重合 | **0 / 50** |
| 位置移动（中位 / 最大） | 250 行 / 433 行 |

即：排序列表达的不是「活跃度」，而是「哪台主机的心跳包最后一个到达」。表格每页
50 行（`ExpandableDeviceTable` 的 `pageSize`），所以用户看到的是**整页换人**；触发面
是 10s 轮询（`DevicesPage.tsx` 的 `refetchInterval`）叠加 `useFleetDeviceUpdates` 的
事件失效。

取 `host_id` 而非只用 `id`：48 台主机 × 862 台，页面本身有「所属主机」列与主机筛选，
同主机设备成片才可扫读；`id` 唯一，作末级 tie-breaker 保证全序。

**不取业务字段做排序**：`status`（ONLINE/OFFLINE/BUSY）同样被心跳重写，按它排是同一种
病的另一种写法。

## Alternatives

- **只删 `last_seen`、退化为 `order_by(Device.id)`**：最小 diff，也确实稳定。放弃——
  丢掉了主机成片，48 主机 × 862 台的列表按登记先后散布，扫读性反而比改之前更差。
  注意 `admission_pump`、`ai_assistant/tools` 的 `order_by(Device.id)` 是内部取用，
  不承担展示职责，与本处不冲突。
- **把 `last_seen` 量化（如 `date_trunc('minute', last_seen) DESC`）**：抖动变慢而非消失，
  每分钟仍整页重排；且把「排序依赖遥测列」这条隐患留在原地。
- **加 `sort` 查询参数，把 `last_seen` 保留为可选**：本单无消费方（前端没有任何排序
  控件需求），属于为假想需求造 API 面。且真加时它会成为 offset 分页不安全的那条路径。
  真出现「最近活跃优先」需求再单独立项。
- **前端冻结顺序（记录上一次的顺序，只把新设备追加）**：与数据对抗——冻结后的行序
  不再对应服务端事实，翻页与筛选会放大不一致。

## Verification

- 两条新用例（`backend/tests/api/test_devices.py`）：
  - `test_list_devices_order_is_host_then_id`：跨两台主机、每台 3 台设备，**刻意让
    `last_seen` 与 id 顺序相反**，断言响应顺序 == 按 `(host_id, id)` 排序的结果，
    并检查全量响应无重复（offset 分页完整性）；
  - `test_list_devices_order_survives_heartbeat`：取一次顺序 → 把最后一台的 `last_seen`
    刷成最新（模拟一次心跳）→ 再取一次，断言顺序不变、该设备仍在末位。
- **变异自证**：把 `order_by` 临时改回旧实现，上述两例**均红**
  （`assert after == before` 与 `(host_id, id)` 顺序断言各命中一次）；恢复后绿。
  这排除了「用例只是恰好通过」。
- **生产数据 A/B**（同一份库、同一时间窗，只读；新语句即本次改动后的 SQL）：
  相隔 10s（= 前端一个轮询周期）两次快照的**前 N 行重合度**——

  | 深度 | 新 `(host_id, id)` | 旧 `(last_seen DESC, id)` |
  |---|---|---|
  | 前 10 行 | 100% | 0% |
  | 前 50 行 | 100% | 0% |
  | 前 100 行 | 100% | 0% |

  新排序下的 offset 分页（页大小 200、页间 150ms 往返）收集 862 台、**去重后仍 862，
  0 重复**（旧排序同期实测 5 轮中 3 轮各丢 14-20 台）。
- `pytest backend/tests/api/test_devices.py`（22 passed）、
  `+ test_host_retirement_read_filters_1804.py + test_project_routes.py`（110 passed）；
  另全量 `pytest backend/tests/api`：**1295 passed**（462s）。
- `scripts/run_gates.py check:quick`：14 门全绿（含 ruff / eslint / tsc / knip / gov-surface）。
- `vitest run src/utils/api/devices.test.ts src/pages/devices src/components/device`：31 + 3 passed。
- 未做：真机浏览器确认。改动是服务端排序，前端不消费排序语义（表格按后端给定顺序渲染），
  故按上面的生产库 A/B 判到位；`fetchAllDevices` 的分页分支在本机 862 台 < 1200 上限时
  不会被走到，其修复效果由上面的 SQL 级复现承担。

## Revisit

- **排序列的准入判据**：`devices.list_devices` 里任何新增排序键都必须问一句「它会不会被
  心跳重写」。`last_seen` / `battery_level` / `temperature` / `status` / `adb_state` 全会。
- **若确有「最近活跃优先」需求**：加显式 `sort` 参数，默认仍须是本单的稳定序；末级
  `Device.id` tie-breaker 不可去（沿用 `2026-08-29` 笔记 Revisit 的结论）。
- **设备数超过 1200 时**：`fetchAllDevices` 的 offset 分页才真正开始翻页。届时它是安全的
  （服务端全序），但页间若引入任何非全序排序就会静默丢设备——实测旧排序下 5 轮里 3 轮
  各丢 14-20 台（页大小 200、页间 150ms）。到那时应改为游标分页而不是继续赌全序。
- **`GET /plan-runs/{id}/devices`（`#83`）等其他设备列表**若将来也要默认排序，同一判据适用。
