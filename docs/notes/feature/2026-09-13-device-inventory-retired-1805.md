# #1805 验收矩阵「设备派生库存」行：退役主机设备默认排除

Status: implemented
Class: feature

## Decision

`GET /devices`（`backend/api/routes/devices.py::list_devices`）新增
`include_retired: bool = Query(False)`，默认经 `Device.host_id` outerjoin `host`
排除退役主机上的设备；显式 `include_retired=true` 时恢复显示。补 3 例测试
（`test_host_retirement_read_filters_1804.py` 的 `TestDeviceInventory`，10 → 13）。

## 为什么这是缺口（实测确认，非矩阵比对推演）

ADR-0038 验收矩阵「统计、容量与设备库存」行要求：

> 活跃库存通过 Host 生命周期过滤，**包含设备派生统计**；保留历史查询和原始遥测。

③ 切片（#1804，`b22e9f0c`）覆盖了 `hosts.py` / `stats.py` / `metrics.py` /
`ai_assistant/tools.py` 四个读面，**未覆盖 `devices.py`**（该 PR 的 diff 文件清单可核）。

**实测探针**（临时测试，`GET /api/v1/devices` 列表断言）：

```
SERIALS: ['SN-ACTIVE', 'SN-RETIRED']
AssertionError: 退役主机的设备泄漏进活跃库存列表
```

即退役主机上的设备**实际出现在活跃库存列表**中——设备库存是经 `Device.host_id`
派生的容量，退役主机按 D5 不变量 1「不再是容量」，其设备不应计入活跃库存。

## 口径与 ③ 切片严格一致

- **默认隐藏 + `include_retired=true` 显式查看**：与 `list_hosts` 完全同形（含参数
  描述措辞），不新造第二套约定；
- **`total` 与列表同口径**：`query.count()` 位于过滤之后且共用同一 `query` 对象，
  故计数自动同步，不会出现「列表 8 条、total 10」的错配；
- **不删历史**：仅过滤列表查询，不改任何行、不删遥测（符合矩阵「保留历史查询和
  原始遥测」）。

## 为什么用 outerjoin 而非 `host_id.in_(...)` 子查询

`Device.host_id` **可为 NULL**（未归属主机的设备，见 `backend/models/host.py:82`
该列 `nullable=True`）：

- **outerjoin**：无主设备的 `Host.retired_at` 求值为 NULL → `IS NULL` 恒真 → **保留**；
- **子查询** `host_id.in_(未退役集合)`：NULL 不在任何集合中 → 三值逻辑下为 UNKNOWN
  → **误删无主设备**。

已加 `test_device_without_host_is_retained` 钉住该分支（若改用子查询形式，该用例
转红）。

## Alternatives

- **不过滤，仅在文档标注** → 否决：矩阵明确要求「活跃库存通过 Host 生命周期过滤，
  包含设备派生统计」；且设备库存是容量派生的直接来源，不过滤等于退役主机仍在
  活跃容量里计数，与 D5 不变量 1 冲突。
- **用 `host_id.in_(...)` 子查询** → 否决：见上，会误删无主设备（NULL 三值逻辑）。
- **无条件排除退役设备、不提供 `include_retired`** → 否决：与 ③ 切片已确立的
  「默认隐藏 + 显式查看」形态不一致，且运维需要能定位退役机上的设备做收尾。
- **同时在 `DeviceOut` 加 `host_retired` 字段** → 否决（本切片范围）：矩阵该行只要求
  **活跃库存过滤**；字段暴露属读面契约扩展，需独立裁决（且 ③ 切片对 hosts 详情是
  「保留可见 + 携带生命周期字段」，设备侧是否同样需要未在矩阵中要求）。
- **一并改 `stats.py` 的设备派生统计** → 否决（本切片范围）：③ 切片已处理 stats；
  本单只补矩阵中**未被 ③ 覆盖**的 `devices.py` 列表面，不重复改已覆盖面。

## Verification

- `python -m pytest backend/tests/api/test_host_retirement_read_filters_1804.py -q`
  → **13 passed**（原 10 + 新 3）；
- **红绿双向**：临时移除过滤 → `test_device_list_excludes_retired_host_devices`
  **失败**（`SN-RETIRED` 泄漏）；还原 → 13 passed；
- **探针实证（修复前）**：`SERIALS: ['SN-ACTIVE', 'SN-RETIRED']` → 断言失败，
  确认缺陷真实存在而非矩阵比对推演；
- **NULL 安全分支**：`test_device_without_host_is_retained` 通过（无主设备保留）；
- **回归**：`test_devices.py` + `test_host_retirement_read_filters_1804.py`
  + `test_host_retirement_api_1801.py` + `test_agent_api_watcher.py`
  → **68 passed**（既有设备列表行为无回归）；
- `ruff check` 两文件 → All checks passed；
- `python tools/dev/check_governance_surface.py --check` → S1–S13、S5x 全绿。

## Revisit

- **设备侧是否需暴露 `host_retired` 字段**：当前仅做列表过滤。运维若要「列出退役机
  上的设备做收尾」，`include_retired=true` 已可用，但返回体不含退役事实——是否需要
  在 `DeviceOut` 增生命周期字段，属读面契约扩展，需独立裁决。
- **其它设备派生面**：矩阵「统计、容量与设备库存」行还点了 `stats.py:286` /
  `metrics.py:48`（③ 已覆盖）。若后续发现**其它**经设备派生的容量口径（如按项目
  聚合的设备计数），应按同一判据核查是否遗漏——本单只修 `devices.py` 列表面。
- **#1805 剩余待领项**：182d4e-F7 十场景全量 mutation、`logs.py` 日志尾读审计面
  仍未覆盖（见 issue 切片台账），本单不认领。
