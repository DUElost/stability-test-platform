# Track B（#3514）规模验收报告：150 Host × 3,750 设备与 1,000 Job Minimap

Status: implemented
Class: feature
Related: #496, #3514, #4117, #83

## Decision

依据 [`docs/design/2026-11-24-list-pagination-and-facets-contract.md`](../../design/2026-11-24-list-pagination-and-facets-contract.md) §7 放量验收门槛，在完全隔离的临时 **PostgreSQL 17** 容器（`postgres:17-alpine`，`127.0.0.1:15439/stp_bench`，`TESTING=1`，零触碰生产库）+ 单 worker `uvicorn` + 实机无头 Chromium（`1440×900`）环境下完成全量规模基线采集与放量门禁核验，全部核心门槛通过：

- **有界设备列表载荷（`projection=list&limit=50`）**：
  - 常规 3,750 设备夹具（含 ~750 B/行旧 `extra`）：原始 UTF-8 JSON **`35.97 KiB`**（离线 gzip `2.33 KiB`），远低于 `100 KiB` 门槛；
  - 极端 50 条合法长标签夹具（每台 20 个 ~120 字节标签 + 长 `retire_reason`，对应 Q2 审查复现的 `120,885` 字节场景）：`DeviceListItemOut` 收敛后稳定低于 `100 KiB`，单机详情 `GET /api/v1/devices/{id}` 完整保留原文。
- **新版设备页 API c10 p95（`< 200 ms` 门槛）**：
  - 全部 6 项设备页场景在 **10 并发查看者 × 单 worker** 下均低于 `200 ms`、错误率 `0.0%`：
    - `devices_list_init`：p50 `78.39 ms` / **p95 `103.81 ms`**
    - `devices_list_page`：p50 `77.76 ms` / **p95 `98.15 ms`**
    - `devices_list_attention`：p50 `84.90 ms` / **p95 `104.39 ms`**
    - `devices_list_search`：p50 `102.59 ms` / **p95 `186.16 ms`**
    - `devices_facets_init`：p50 `81.96 ms` / **p95 `97.66 ms`**
    - `devices_facets_filtered`：p50 `79.19 ms` / **p95 `97.33 ms`**
- **PlanRun 1,000 Job Minimap 载荷与浏览器渲染（`≤ 384 KiB` 门槛）**：
  - 原始 UTF-8 JSON **`178,810 B`（`174.62 KiB ≤ 384 KiB`）**，离线 gzip `10.43 KiB`（不替代线上实测值）；
  - 实机 Chromium 完整渲染 **1,000 个唯一可达格子**（`unique_cell_count = 1000`），筛选切换（`1000 → 75 → 1000`）、悬停文案、并集 KPI（`status=lost_or_backoff`）、表格真分页（`limit=50`）与单 Job 抽屉网络隔离（零全量 `/devices` 回拉）均验证通过。
- **所有 GET 端点零 DML / 零 COMMIT**：全部 26 项只读接口测点 `dml_sql_count = 0`。

## Alternatives

- 曾评估在 `device` / `host` 表新增额外索引迁移；基于隔离 PG 17 `EXPLAIN (ANALYZE, BUFFERS)` 实测，仅靠应用层消除 `_scope_b_conditions` 整表加载 `Host` ORM、改用扁平 `COUNT(Device.id)` 及在 `projection=list` 延迟 `Device.extra` / `Host.mount_status` 大列后，设备首屏 SQL 总执行耗时已降至 **`1.98 ms`**（c10 p95 `103.81 ms`），遵守契约 §3.3 规则 5「无瓶颈证据不新增索引迁移」。

## Verification

### 1. 夹具与环境说明

- 代码基线：`fix/4117-trackb-p1-bounds-host-paging-and-scale`（基于 `origin/main@af8bea076`，已合入 `#4170`、`#4183`、`#4184`、`#4187`）。
- 数据规模：**150 Agent host × 3,750 物理设备**（含 4 退役主机、55 退役设备、~119 陈旧设备、25 无主机设备、4 项目 16 活跃型号映射）+ **1 个 RUNNING PlanRun 含 1,000 Job**（覆盖 40 主机，含 running/backoff/stuck/completed/failed/unknown 与 `StepTrace` / `JobLogSignal`）。
- 隔离基础设施：临时 `postgres:17-alpine` 容器 + 单进程 `uvicorn`（`lifespan='off'`）+ 构建产物静态服务 + Playwright 无头 Chromium（`1440×900`），跑完即销毁容器与临时进程。

### 2. PostgreSQL `EXPLAIN (ANALYZE, BUFFERS)` 与 SQL 次数 / 载荷实测

| 端点场景 | 业务 SQL 数 | DML 数 | 原始 UTF-8 | 离线 gzip | PG 执行耗时（ms，逐条 SQL） |
|---|---:|---:|---:|---:|---|
| `devices_list_default` (`projection=list&limit=50`) | 4 | 0 | 35.97 KiB | 2.33 KiB | `[0.136, 1.500, 0.295, 0.051]` |
| `devices_list_search_q` | 4 | 0 | 36.33 KiB | 1.85 KiB | `[0.141, 9.845, 1.725, 0.033]` |
| `devices_list_search_tag_q` | 4 | 0 | 36.84 KiB | 1.91 KiB | `[0.148, 10.435, 1.962, 0.038]` |
| `devices_list_need_attention` | 4 | 0 | 36.20 KiB | 2.21 KiB | `[0.143, 1.681, 2.771, 0.034]` |
| `devices_list_project` | 5 | 0 | 36.14 KiB | 1.87 KiB | `[0.008, 0.138, 1.218, 0.352, 0.028]` |
| `devices_facets_base` | 6 | 0 | 10.14 KiB | 1.51 KiB | `[0.150, 2.927, 2.044, 1.812, 1.906, 2.161]` |
| `hosts_list_priority` (`limit=50`) | 2 | 0 | 66.26 KiB | 1.63 KiB | `[0.221, 1.381]` |
| `hosts_facets` | 2 | 0 | 0.20 KiB | 0.14 KiB | `[0.208, 1.400]` |
| `planrun_minimap_1000` (1,000 Job) | 3 | 0 | **174.62 KiB** | 10.43 KiB | `[0.014, 2.180, 0.011]` |
| `planrun_table_p1` (`limit=50`) | 4 | 0 | 55.16 KiB | 1.76 KiB | `[0.024, 3.278, 0.014, 0.387]` |
| `planrun_table_lost_or_backoff` | 4 | 0 | 57.81 KiB | 2.00 KiB | `[0.012, 1.997, 0.009, 0.490]` |
| `planrun_single_job` | 6 | 0 | 1.36 KiB | 0.60 KiB | — |

### 3. 1 / 5 / 10 并发查看者 HTTP 基准（单 worker，错误率全为 `0.0%`）

| 场景 | c1 p50 / p95 (ms) | c5 p50 / p95 (ms) | c10 p50 / p95 (ms) | 字节（原始 UTF-8） |
|---|---:|---:|---:|---:|
| `devices_list_init` | 12.09 / 14.17 | 39.36 / 56.15 | **78.39 / 103.81** | 36,844 B |
| `devices_list_page` | 10.57 / 14.60 | 37.87 / 44.71 | **77.76 / 98.15** | 36,881 B |
| `devices_list_attention` | 12.93 / 15.79 | 40.13 / 51.52 | **84.90 / 104.39** | 37,070 B |
| `devices_list_search` | 21.84 / 25.63 | 55.15 / 66.64 | **102.59 / 186.16** | 37,211 B |
| `devices_facets_init` | 20.29 / 23.17 | 40.11 / 46.53 | **81.96 / 97.66** | 10,387 B |
| `devices_facets_filtered` | 14.50 / 16.57 | 40.88 / 47.33 | **79.19 / 97.33** | 7,385 B |
| `write_invalidation_cycle`（改标签→刷新列表+Facets） | 36.03 / 38.93 | 41.35 / 47.57 | **43.60 / 45.47** | — |
| `hosts_list_priority` | 20.29 / 25.26 | 89.81 / 112.84 | 181.23 / 222.23 | 67,854 B |
| `hosts_facets` | 14.33 / 16.82 | 76.41 / 106.26 | 169.69 / 266.92 | 201 B |
| `planrun_minimap_1000` | 25.87 / 27.45 | 121.18 / 215.19 | 248.07 / 393.32 | 178,810 B |
| `planrun_table_p1` | 32.27 / 39.01 | 114.04 / 142.35 | 217.04 / 308.26 | 56,486 B |
| `planrun_single_job` | 6.56 / 8.45 | 26.74 / 31.44 | 54.75 / 67.06 | 1,396 B |

### 4. 实机 Chromium（`1440×900`）浏览器核验结果

- **`/devices`（3,750 台夹具）**：首屏渲染耗时 `884 ms`，列表走 `projection=list`，独立 Facets 仅请求 `1` 次，虚拟表按可视区渲染 `23` DOM 行，总计范围正确展示，零横向溢出。
- **`/hosts`（150 台夹具）**：首屏渲染耗时 `820 ms`，列表走 `projection=list`；点击「下一页」自动透传第 1 页冻结的 `snapshot_id`（实测 `snapshot_id=c878693f5a534d8b`）；点击当前页主机行打开抽屉后按需请求 `GET /api/v1/hosts/{id}` 加载完整 `mount_status`。
- **`/execution/plan-runs/1`（1,000 Job Minimap 夹具）**：首屏渲染耗时 `1,154 ms`；完整渲染 **`1,000` 个唯一可达格子**（`minimap_cell_count = 1000`、`unique_cell_count = 1000`）；失联/退避 KPI 筛选切换 `1000 → 75 → 1000`；初始加载仅请求 `GET /devices/minimap` 1 次且零旧全量 `/devices` 请求；点击格子打开抽屉耗时 `104 ms`（仅触发单 Job 详情与相邻预取共 2 次，零全量回拉）；切至表格视图使用 `limit=50` 与真实并集参数 `status=lost_or_backoff`，零横向溢出。

## Revisit

- 若后续设备规模超过 10,000 台或主机规模超过 500 台，可复跑本隔离基准，并以新 `EXPLAIN (ANALYZE, BUFFERS)` 证据决定是否追加 `pg_trgm` / 部分索引，以及是否把主机动态排序快照从进程内 LRU 切换为 Redis 瞬时键以支持多 worker 共享。
