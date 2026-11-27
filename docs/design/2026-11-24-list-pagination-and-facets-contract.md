# 列表服务端分页、只读投影与 Facet 聚合契约（v1.0）

> **最后更新**：2026-11-25  
> **状态**：Accepted v1.0（已吸收独立架构复核全部 P0/P1 与 Q1–Q7 裁决；承接 [#4117](https://github.com/DUElost/stability-test-platform/issues/4117) D5 Phase 2、[#496](https://github.com/DUElost/stability-test-platform/issues/496)、[#3325](https://github.com/DUElost/stability-test-platform/issues/3325)、[#83](https://github.com/DUElost/stability-test-platform/issues/83)）  
> **代码基线**：`origin/main@c170f42ad`（独立复核基线 `origin/main@dede8f1cc`；增量 `#4146` 已移除 Minimap 异常红点并保留悬停精确计数，其余结论全部适用）  
> **关联文档**：[`2026-11-23-frontend-ui-governance-contract.md`](./2026-11-23-frontend-ui-governance-contract.md)、[`ADR-0057-device-retirement-semantics.md`](../adr/ADR-0057-device-retirement-semantics.md)、[`writing-conventions.md`](../development/writing-conventions.md)（ADR-0059）

本文的规则句按 ADR-0059（`docs/development/writing-conventions.md` §3）书写，只使用「必须 / 不得 / 建议 / 可以」并写明执行者。本文约束控制面面向 **150 Agent host / 3,750 物理设备 / 单 PlanRun 1,000 设备** 规模的三个高频列表域（物理设备、Agent host、PlanRun 设备矩阵）的前后端查询契约、只读投影边界、跨页操作门槛与向后兼容迁移顺序。

**范围边界（非规则）**：

- 阶段 B1 只认领 `#3325` 的库存（物理设备与 Agent host）读写路径，不代替 `/results/summary` 无界聚合治理。
- 阶段 B3 不宣称 `#496` 的 `PlanExecutePage` 设备选机池已完成；`PlanExecutePage` 独立作为后续专题处理。

---

## 1. 背景与待解瓶颈（非规则）

基于 `origin/main@dede8f1cc` 源码核查，当前实现存在四项待解瓶颈：

1. **后台对账未覆盖物理设备与静默主机（`#3325`）**：`backend/tasks/session_watchdog.py` 默认每 15 秒运行，但仅查询心跳过期的 `ONLINE` Agent host，把 Agent host 置为 `OFFLINE` 并将符合条件的 `RUNNING` Job 转为 `UNKNOWN`，不更新 `Device`；`NULL` 心跳、`DEGRADED` 及已 `OFFLINE` 的 Agent host 均不在该查询内。`backend/api/routes/heartbeat.py` 的缺席设备对账仅在收到心跳时执行，静默 Agent host 下的物理设备长期不落库。
2. **四个库存 GET 端点带同步回写（`#3325`）**：`GET /api/v1/devices`、`GET /api/v1/devices/{id}`、`GET /api/v1/hosts`、`GET /api/v1/hosts/{id}` 在读路径调用 `_ensure_host_online_for_device` / `_ensure_host_status_up_to_date` 并在状态翻转时执行 `db.commit()`；`GET /api/v1/devices` 逐行调用 helper 并在返回循环中懒加载 `device.host`，且先按账面 `status` 过滤与计数、再逐行修正状态，可能出现「记录返回为离线却查不进离线筛选」的口径错位。
3. **库存全量拉取与旧调用方双形态（`#496`）**：`GET /api/v1/devices` 与 `GET /api/v1/hosts` 均存在「不传分页参数返回裸数组 / 传 `skip` 或 `limit` 返回分页包络」的历史双形态契约；`DevicesPage` 通过 `fetchAllDevicePages` 每 10 秒分页拉满全集并本地算候选与批量全选；`HostsPage` 同样存在按筛选全选执行安装的入口。
4. **PlanRun 设备接口先构造重对象再过滤（`#83`）**：`GET /api/v1/plan-runs/{run_id}/devices` 无分页参数，每次先为全部设备构造约 30 个字段的 `DeviceMatrixItem` 再过滤；且实算 1,000 条 8 字段裸 JSON 数组（即使串号与 `host_id` 全为 `null`）已达 165,787 字节，证明 Minimap 原始 UTF-8 JSON 预算必须基于实测设定，且不得将 Minimap 截断为单页。

---

## 2. 读端点纯只读不变量与后台对账接管（解 `#3325`）

### 2.1 原子接管与读路径纯只读

1. 后端实施者必须在同一个发布单元内先交付后台对账接管，再移除四个库存 GET 端点（`GET /api/v1/devices`、`GET /api/v1/devices/{id}`、`GET /api/v1/hosts`、`GET /api/v1/hosts/{id}`）中的状态回写与 `db.commit()`。
2. 后端实施者不得先删除 GET 端点回写再等待后续 PR 补齐后台对账。
3. 后端实施者必须确保四个库存 GET 端点保持严格纯只读事务，不得在读路径执行任何数据库 `UPDATE`、`INSERT`、`DELETE` 或 `COMMIT`。
4. 后端实施者必须在库存列表、详情与 Facets 查询中采用批量联表或显式预加载，不得在结果遍历循环中逐行懒加载关联对象（SQL 查询数不得随返回行数线性增长）。

### 2.2 后台对账与统一有效状态谓词

以下各条均为要求（针对后端实施者）：

1. 扩展 `session_watchdog`：在落库前复验 Agent host 心跳时间戳，并将缺失心跳（`NULL`）或心跳过期的 `ONLINE`、`DEGRADED` 及存量已 `OFFLINE` Agent host 下的关联物理设备幂等对账为离线。
2. 在物理设备与 Agent host 的列表、详情、筛选、计数与 Facets 查询中，复用同一套 SQL 有效状态谓词与同一请求内时间基准，消除筛选口径与返回字段的错位。
3. 缺失或心跳过期的 Agent host 判定为离线；心跳新鲜的 `DEGRADED` Agent host 不得判定为离线。
4. 无所属 Agent host（`host_id IS NULL`）的物理设备不得受 Agent host 离线级联影响。
5. 后台对账与只读投影保留既有 Job 状态机与设备租约规则，不得改写最后观测到的 ADB 字段，也不得改写 Agent host 或物理设备的退役事实（ADR-0038 / ADR-0057）。

在默认调度健康时，后端实施者建议以心跳超阈值后两轮（30 秒）作为后台对账落库目标并监测对账延迟。

---

## 3. 物理设备服务端分页、独立 Facets 与跨页操作契约（解 `#496`）

### 3.1 响应形态冻结与有界列表 DTO

1. 后端实施者必须冻结 `GET /api/v1/devices` 既有双形态（未传显式分页参数 vs 传 `skip` / `limit`）的返回结构、缺省值与默认排序，仅做向后兼容增量扩展。
2. 任何人不得以「前端已切换」作为废弃 `GET /api/v1/devices` 旧响应形态的许可；旧路径退出必须独立完成调用方盘点与废弃审批。
3. 后端实施者必须为新版物理设备列表提供有界列表 DTO（例如显式参数 `projection=list`），不得将 §7 的载荷字节上限套用到包含任意 `extra` 字段的旧 `DeviceOut`。

### 3.2 查询参数定义与排序约束

`GET /api/v1/devices` 的增量查询参数定义如下表（定义表，非规则句）：

| 参数 | v1 语义定义 |
|---|---|
| `q` | 不区分大小写的字面子串匹配（`%`、`_` 按字面转义），覆盖串号、展示型号、展示 Product（含别名及「未上报」）、`build_display_id`、Agent host 展示名、任一标签 |
| `status` | 保留旧大写状态值（`ONLINE` / `BUSY` / `OFFLINE` / `ERROR`）；支持哨兵 `need_attention`（等价于有效状态 `OFFLINE OR ERROR`）；全部状态时不传 |
| `host_id` / `model` / `product` / `version` | 四者均为单选精确匹配；`version` 对应 `build_display_id`；`host_id` 定义无主机桶对应 `host_id IS NULL`（与项目未归属无关）；`product` 参数值使用数据库原值而非展示别名 |
| `tags` / `has_tags` | 保留既有 `tags=a,b` 逐项 AND 的 JSONB 包含语义；`has_tags` 为三态（省略不限、`true` 非空数组、`false` 无标签，历史非数组按空处理） |
| `include_stale` / `include_retired` | 线上查询参数沿用 `include_stale` / `include_retired`；退役开关同时覆盖物理设备与所属 Agent host；陈旧判定保留 VBUS 已关机但在槽例外 |
| `project_key` / `unassigned` | 项目归属按活跃型号映射；未知项目返回 `404`，与 `unassigned=true` 同传返回 `400`；未归属保留既有 `USER` / `SEED` 边界 |
| `sort_by` / `sort_dir` | 首版白名单仅开放 `serial`、`host_id`，固定 `NULL` 次序并以 `id` 为尾键，缺省排序为 `(host_id NULLS LAST, id)`；`sort_dir` 为 `asc` / `desc` |

针对上表参数，实施者遵守以下规则：

1. 后端实施者必须在 `q` 匹配中按字面转义 `%` 与 `_`，不得将其解释为 SQL `LIKE` 通配符。
2. 后端实施者不得新增单数 `tag` 参数，也不得新增 `show_stale`、`show_retired` 或独立布尔 `attention` 同义键。
3. 前端实施者必须将 URL 中的 `stale` / `retired` 映射为 `include_stale` / `include_retired`，将 `attention` 映射为 `status=need_attention`，并将 `idle` / `testing` / `offline` / `error` 归一为 `ONLINE` / `BUSY` / `OFFLINE` / `ERROR`。
4. 当 `sort_by` 不在 `{serial, host_id}` 白名单内时，后端实施者必须返回 `422` 校验错误，不得拼接动态 SQL 字段名。
5. 前端实施者在服务端分页首版必须下线心跳遥测列（`last_seen` / `status` / 电量 / 温度）及主机显示名的前端排序入口并在变更说明中记录差异，不得改为仅对当前页本地排序。
6. 前端实施者在 `DevicesPage` 切换服务端分页后，必须默认请求单页（`limit=50`，页容量选项 `50 / 100 / 200`），不得继续每 10 秒轮询调用 `fetchAllDevicePages`。

### 3.3 独立 Facets 端点、三级作用域与 SQL 构造

1. 后端实施者必须新增只读端点 `GET /api/v1/devices/facets`，接收与 `GET /api/v1/devices` 相同的筛选参数（不含 `skip`、`limit`、`sort_by`、`sort_dir`）。
2. 后端实施者必须按以下三级作用域生成 `GET /api/v1/devices/facets` 响应：
   - **作用域 B（基础集）**：仅应用 `project_key` / `unassigned`、`include_stale`、`include_retired`；`status_counts`（`all`、`ONLINE`、`BUSY`、`OFFLINE`、`ERROR`、`need_attention`、`tagged`）必须从作用域 B 计数；
   - **作用域 H（主机候选集）**：在作用域 B 上叠加 `status`、`tags`、`has_tags`；`options.hosts` 必须从作用域 H 生成，不得被已选 `host_id` 锁死；
   - **作用域 D（明细候选集）**：在作用域 H 上叠加有效 `host_id`；`options.models`、`options.products`、`options.versions` 必须从作用域 D 生成，不得受 `q` 或同级下拉选择影响；
   - `GET /api/v1/devices` 的分页 `total` 必须应用全部查询条件。
3. 后端实施者首版不得在 `GET /api/v1/devices/facets` 中输出前端未消费的 `options.tags`。
4. 后端实施者必须以「一台设备一行」的窄投影构造设备列表与 Facets SQL（单次 `LEFT JOIN` 保留无主机设备，项目归属采用 `EXISTS` / `NOT EXISTS`，状态计数采用条件聚合，候选项采用分组去重，标签搜索采用受数组类型保护的 `EXISTS`），不得因展开标签数组造成物理设备重复计数。
5. 后端实施者在阶段 B1 与 B2 默认不得新增数据库索引迁移；仅当隔离 PostgreSQL `EXPLAIN (ANALYZE, BUFFERS)` 证实存在查询瓶颈时，后端实施者才可以另开独立 PR 添加针对性索引。
6. 前端实施者仅可以在筛选条件变更、设备写操作成功失效缓存或低频后台刷新时请求 `GET /api/v1/devices/facets`，不得在单纯翻页或切换列排序时重复请求 Facets。
7. 前端实施者仅可以在匹配当前筛选条件且成功返回的 Facets 证明某选项已失效时归一该筛选值；处于加载中、请求失败或空页状态时，前端实施者不得清空 URL 参数。

### 3.4 跨页批量选择与 `PlanExecutePage` 边界

1. 前端实施者与后端实施者必须在 `DevicesPage` 保留两条显式批量选择路径：
   - **当前页勾选**：普通勾选仅作用于当前页已选 `device_ids[]`，并在 `DeviceBulkActionBar` 中明确标注当前页已选数量；
   - **跨页筛选预览与确认**：点击「选中全部筛选结果」必须调用独立的 `filter_query` 预览接口，冻结匹配设备 ID 集合并展示命中总数与写操作影响（含归属项目受影响型号及 ADR-0057 E3 陈旧度退役确认清单），由授权用户确认后复用既有显式 `device_ids[]` 写接口执行。
2. 后端实施者必须为每类跨页写操作设置确认上限，并在写接口执行时重验操作者权限与目标设备资格；超过上限时后端实施者必须拒绝请求，不得静默部分执行，且确认执行时不得重跑筛选扩大影响范围。
3. 前端实施者在跨页复制串号与导出 CSV 时，必须走用户主动触发的全集读取路径，不得仅导出当前页。
4. 在跨页预览路径未交付前，前端实施者不得将 `DevicesPage` 切换至服务端分页。
5. 前端实施者不得在阶段 B3 修改 `PlanExecutePage` 设备选机池的拉取与准入策略，也不得删除共享全量 helper（`fetchAllDevices`、`fetchAllDevicePages`）。

---

## 4. Agent host 服务端分页、独立 Facets 与跨页操作契约（解 `#496`）

1. 后端实施者必须冻结 `GET /api/v1/hosts` 既有双形态契约（不显式传 `skip` 或 `limit` 时返回至多 50 条裸数组；传任一参数时返回 `{items, total, skip, limit}`，单页上限 200），保护 `site_config`、部署 SOP 与 Agent 安装脚本等既有调用方。
2. 后端实施者必须新增独立只读端点 `GET /api/v1/hosts/facets`（不采用随列表返回 `summary` 的方案），按 `include_retired` 基础全集返回 10 个计数字段：`all`、`online`、`degraded`、`offline`、`usb_anomaly`、`upgrade_pending`、`health_anomaly`、`mount_anomaly`、`aligned_agent_count`、`trackable_agent_count`，不受搜索词与翻页影响。
3. 后端实施者必须按以下口径计算 Agent 同步指标：`trackable_agent_count` 统计满足 `ONLINE && agent_installed` 的 Agent host，`aligned_agent_count` 在该集合内统计 `matched`，`upgrade_pending` 在该集合内统计 `drift`，`unknown` 保留在分母 `trackable_agent_count` 中。
4. 后端实施者必须在 `GET /api/v1/hosts` 中支持 `status`（连通态）、`health_status`、同步快捷筛选、USB 与挂载快捷筛选、`include_retired`、`q`（保留分词 AND，覆盖主机名、IP、健康原因、Agent 版本）、`sort_by`、`sort_dir`、`skip`、`limit`，并令分页 `total` 返回过滤后精确条数。
5. 后端实施者与前端实施者必须使 `upgrade_pending` 快捷点击采用与其计数完全相同的谓词（`ONLINE && agent_installed && drift`），不得使用更宽的原始 `sync=drift`；USB 异常必须沿用既有优先级与维护意图规则，挂载无数据不得计为挂载故障。
6. 后端实施者必须在分页切片前完成全局 USB 优先级与显式排序并以 `id` 为尾键；前端实施者不得在动态排序中混用不同刷新代次的页面结果。
7. 前端实施者在 `HostsPage` 新分页视图中建议默认页容量为 `50`；前端实施者必须在 `HostDetailDrawer` 打开时按需加载完整挂载列表与脚本在位明细，后端实施者不得在 `GET /api/v1/hosts` 中重新塞回全量脚本明细。
8. 前端实施者在 `HostsPage` 切换服务端分页前，必须显式区分当前页勾选与跨页确认（覆盖按筛选全选执行安装的入口），不得沿用全选按钮却只提交当前页。

---

## 5. `PlanRun` 设备双视图分离与单 Job 详情契约（解 `#83`，保护 Minimap）

> **核心不变量**：`PlanRunDetailPage` 中的 Minimap 设备矩阵必须始终呈现该 PlanRun 的全量设备状态；任何实施者不得将 Minimap 截断为单页。

### 5.1 独立轻量全集 Minimap 端点

1. 后端实施者必须新增独立只读端点 `GET /api/v1/plan-runs/{run_id}/devices/minimap`，不得新增同义查询参数 `?view=minimap` 制造双权威。
2. 后端实施者必须令 Minimap 轻量投影的每条设备记录仅包含 7 个命名字段：`job_id`、`device_id`、`device_serial`、`host_id`、`job_exec_status`（必返，删除旧回退字段 `ui_status`）、`device_link_status`、`log_signal_count`（驱动悬停与无障碍精确异常条数，不得降为布尔）。
3. 后端实施者必须使 `job_exec_status` 与同响应 `facets.by_status` 的词表包含 `"finishing"`（与分页表 / Job 详情的 `ui_status="finishing"` 共用同一服务端判定谓词；ADR-0065 D1）。
4. 后端实施者不得在 Minimap 设备记录中新增 `finishing_kind`、`finishing_trigger` 或 `finishing_deadline_at`；上述子类型明细必须仅由设备分页表与 Job 详情提供。
5. 后端实施者必须在同一 Minimap 响应中返回全集口径 `facets`（`total`、`by_status`、`by_link_status`、`by_host`）、按 Agent host 去重的显示名映射（包含退役 Agent host 并定义无主机桶）以及全局心跳超时 `stuck_job_ids`。
6. 后端实施者必须在构造 Minimap 响应时直接生成轻量全集与全局计数，不得先构造全量重 `DeviceMatrixItem` 再裁剪，也不得在 Minimap 响应中序列化 `disarm_results`、长错误栈或完整时间线明细。
7. 前端实施者在 Minimap 视图下必须消费 `GET /api/v1/plan-runs/{run_id}/devices/minimap`，在本地仅对服务端已投影字段做即时筛选且清空筛选时恢复全集；心跳超时横幅、Watcher 串号反查与快照设备选择器必须复用该轻量身份索引，不得由表格单页数据推导，也不得回拉旧重列表。

### 5.2 表格真分页视图与聚合 KPI 并集筛选

1. 后端实施者必须保留 `GET /api/v1/plan-runs/{run_id}/devices` 在不传 `skip` / `limit` 时返回全量匹配记录与既有 `total`（PlanRun 全集数）的向后兼容行为。
2. 后端实施者必须为 `GET /api/v1/plan-runs/{run_id}/devices` 扩展可选参数 `skip`、`limit`、`q`（按设备串号过滤）、`sort_by`、`sort_dir`；传显式分页参数时必须先过滤、排序再切片并仅对当前页构造重字段，同时新增 `filtered_total` 字段，不得用 `filtered_total` 覆盖 `total`。
3. 后端实施者必须使 Minimap 与表格分页共用同一套执行态与连接态谓词（保留 `status`、`link_status`、`host_id`），并补齐两张聚合 KPI 的真实并集筛选：失联/退避等价于 `unknown OR backoff`，连接异常等价于 `device_link_status != online`；前端实施者不得再通过仅选中首个非零子类模拟聚合筛选。
4. 前端实施者在表格视图下必须按需请求当前页（默认 `limit=50`）。

### 5.3 单 Job 详情端点与抽屉上/下一台切换

1. 后端实施者必须新增只读端点 `GET /api/v1/plan-runs/{run_id}/jobs/{job_id}`，不得要求前端从全 Job 列表中查找单条记录。
2. 后端实施者必须在 `GET /api/v1/plan-runs/{run_id}/jobs/{job_id}` 中校验 `(run_id, job_id)` 归属并在错配时返回 `404`；响应必须返回单机完整详情（设备身份、Job/执行/连接态、ADB、阶段/当前步骤/脚本版本、心跳与 SLA/Grace、占用原因、巡检周期与重试数据、与后端连接判定同源的操作能力、`disarm_results`、Watcher 计数）。
3. 后端实施者必须将阶段时间线与 Watcher 逐条明细作为按 Job 延迟加载的有上限子查询提供（分别返回阶段/步骤/事件状态与时间，以及异常类别/来源/时间/事件或产物引用），不得将整段日志或全 PlanRun traces 塞入单 Job 详情响应。
4. 前端实施者在 `DeviceDetailDrawer` 中必须保留带 PlanRun 归属校验的报告入口与按串号检索的日志深链，并按以下规则驱动 `< 上一台 / 下一台 >`：
   - Minimap 视图下按当前筛选匹配的全集显示顺序切换；
   - 分页表格视图下按当前页实际显示顺序切换，抵达页边界时禁用按钮并明确标明「本页」；
   - 通过 `?drawer=` 深链打开不在当前浏览集合中的设备时，抽屉必须独立加载单机详情，不得猜测邻居；
   - 选中详情加载成功后，前端实施者最多预取相邻 2 条单机详情，不得预取相邻设备的时间线、异常明细或产物；
   - 前端实施者必须按 `(run_id, job_id)` 隔离查询缓存与操作状态，切换设备时不得残留前一台的操作态。

---

## 6. URL、缓存、守卫与分阶段迁移顺序

### 6.1 URL、缓存与静态守卫不变量

以下各条均为要求（针对前端实施者）：

1. 保留 `useUrlFilterParams`、筛选/分页回退、筛选无结果时保留筛选栏以及详情深链；库存设备详情深链 `?drawer=` 在目标设备不在当前页时必须仍能独立打开，不得失效。
2. 查询缓存键按职责分离：列表分页键包含全部有效筛选条件、页码与排序；Facets 键不包含页码与排序；详情键包含资源身份；不得将分页包络写入共享 `Host[]` 缓存。
3. 沿用既有 Socket 事件合流与终态停止轮询策略。
4. 切换 `DevicesPage` 时，仅替换 `tests/test_frontend_filter_workbench_guard.py` 中对两个本地 `useMemo` 的词法锚点，改为断言消费 `GET /api/v1/devices/facets` 且不拉全量，并补充作用域行为测试；必须保留 `PlanExecutePage` readiness 守卫与其余治理守卫。

### 6.2 分阶段 PR 切分与回滚边界

1. **阶段 B1（只读与后台对账，1 个发布单元，解 `#3325` 库存路径）**：
   - 范围：物理设备与 Agent host 四个 GET 路由、`session_watchdog`、设备陈旧度共享谓词。
   - 迁移与回滚：无数据库 schema 迁移；后端实施者必须以整个接管单元为回滚边界，不得单独回滚后台对账而保留已移除 GET 写入的代码。
2. **阶段 B2（增量后端 API，3 个独立后端切片 PR）**：
   - 切片 `B2-1`：物理设备分页、有界列表 DTO、`GET /api/v1/devices/facets` 与跨页 `filter_query` 预览接口；
   - 切片 `B2-2`：Agent host 增量筛选/排序与 `GET /api/v1/hosts/facets`；
   - 切片 `B2-3`：PlanRun 表格真分页（含 `filtered_total` 与并集筛选）、`GET /api/v1/plan-runs/{run_id}/devices/minimap` 与 `GET /api/v1/plan-runs/{run_id}/jobs/{job_id}`；
   - 每个切片必须按 `add-api-endpoint` SOP 同步更新 `frontend/src/utils/api/types.ts` 与前端 API 客户端，此时旧前端不切换；默认无数据库迁移，新增端点未被前端消费前均可独立撤回。
3. **阶段 B3（按页面切换前端消费，3 个独立前端 PR）**：
   - 顺序：`B3-1 DevicesPage` → `B3-2 HostsPage` → `B3-3 PlanRunDetailPage`；
   - 每个 PR 连同对应表格、抽屉、Query keys 与静态守卫一并切换，保留旧 API 供前端独立回滚，不修改 `PlanExecutePage`。

---

## 7. 规模与回归验收门槛

进入生产放量前，实施者与复核者必须共同核验以下四类门禁（以下各条均为要求）：

1. **只读与后台对账回归（后端实施者）**：在冻结时间测试夹具下证明 4 个库存 GET 端点在超时主机等场景产生 **0 次 SQL DML / 0 次 COMMIT** 且 SQL 查询数不随返回行数增长；同时证明无任何 GET 访问时 `session_watchdog` 仍完成持久化（覆盖 `NULL` 心跳、新鲜与过期 `DEGRADED`、存量 `OFFLINE` 修补、心跳恢复竞态、租约与 ADB/退役事实不被改写、VBUS 关机在槽陈旧例外）。
2. **设备域规模基准与载荷门禁（后端实施者与复核者）**：按 `#3514` 在隔离 PostgreSQL 环境构建 **150 Agent host × 3,750 物理设备** 夹具，执行 `EXPLAIN (ANALYZE, BUFFERS)` 与 1 / 5 / 10 并发查看者基准（覆盖初屏、搜索、翻页、周期刷新、写操作失效），分别报告列表与 Facets 的 p50/p95、错误率、字节数、SQL 次数与浏览器耗时；新版有界设备列表 DTO（`projection=list&limit=50`）原始 UTF-8 JSON 不超过 `100 KiB`、新版设备页 API p95 低于 `200 ms`（不得在共享 CI 上以单次计时替代门禁）。
3. **PlanRun 1,000 Job Minimap 基准（后端实施者与前端实施者）**：在声明的 **1,000 Job** 夹具下证明 `GET /api/v1/plan-runs/{run_id}/devices/minimap` 原始 UTF-8 JSON 不超过 `384 KiB`（gzip 字节另行记录，不得将离线压缩值当作线上传输实测），并验证完整渲染 1,000 个唯一可达格子、有/无过滤切换、悬停精确异常条数、全局 KPI 并集筛选与抽屉网络隔离。
4. **实机浏览器双视口验证（前端实施者与复核者）**：三个切换页面均在 `stp-ui-review` 隔离栈通过双视口（`1440×900` / `1280×800`）× 双主题实机核验（零横向溢出、控件高差 `≤ 2px`、粘性表头、Sheet 焦点与 `Esc`、快捷视图往返、无翻页竞态漏项或重复，对齐 `2026-11-23-frontend-ui-governance-contract.md` §7），且 `tests/test_frontend_filter_workbench_guard.py` 全部通过。
