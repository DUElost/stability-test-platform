# 健康页审计遗留四项收口

Status: implemented
Class: bug-fix

## Decision

08-15 健康页审计（#269/#273/#279 一带）遗留 4 项，本次收口 3 修 1 证伪：

1. **趋势图数据源跟随 storage 面板**（`file_server_monitor.py` history 查询）：
   分源已配 `STP_STORAGE_NODE_JOB` 时，CPU/内存/NFS 历史改刮存储机 job，
   不再把控制面负载冒充存储机；分源未配 job 时历史留空（fail-closed，与
   右栏 `STORAGE_METRICS_UNAVAILABLE` 同一口径，见 [[2026-08-14-file-server-health-dual-panel]]）。
   **容量历史例外**：仍按控制面客户端挂载查询——NFS 客户端看到的
   avail/size 就是服务端磁盘本身，与控制面/存储机归属无关；控制面持续
   挂载共享盘（dedup 入口依赖），因此分源后容量趋势依然成立。
2. **同源判定 IP/hostname 归一化**：`_share_is_co_located()` 先把
   `STP_AEE_SHARE_ADDRESS` 与 `STP_FILE_SERVER_ADDRESS` 各自
   `gethostbyname` 再比较，解析失败回落原串。此前字面比较在「一处写 IP
   一处写主机名」时误判分源 → 右栏永远不可用告警（#205 页面语义）。
3. **趋势标题跟随本地选择**：`FileServerPage` 标题改取本地 `hours` state
   （168 → 「7 天」），不再读 `data.history.hours`——`keepPreviousData`
   占位期间旧实现会显示上一个时间范围的标签；占位数据出现时追加
   「（更新中…）」提示。
4. **usage_percent 阈值/显示一致性：证伪，不改**。Agent 侧
   `system_monitor.get_disk_usage` 已把 `usage_percent` 预舍入到 2 位小数，
   控制面告警阈值、前端进度条/颜色全部消费同一个舍入后的值，理论上的
   「94.996 显示 95.0 红色但只 warning」无法端到端到达；前端 `toFixed(1)`
   的向上取整属常规显示舍入，色带与告警级别始终一致。

## Alternatives

- 分源时容量历史也改刮 storage job：被否。存储机本地挂载路径字符串与控制面
  `STP_AEE_NFS_ROOT` 不保证相同，`mountpoint=` 选择器会整段落空，需新增
  `STP_STORAGE_MOUNT_PATH` 配置；而控制面客户端挂载已能给出正确的共享盘
  容量，属「改有风险、不改不亏」，留待分源实际落地时按现场决定。
- 分源未配 job 时容量历史仍按控制面挂载出数据：被否。与右栏
  fail-closed 口径不一致，页面会出现「容量有图、机器无指标」的半可用态，
  违背 #205「来源错了比没有更糟」。
- 前端标题改取本地 hours 但不加占位提示：被否。占位期间图还是旧范围
  数据，无提示会变成「标题对、图错」的新错标。

## Verification

- `backend/tests/services/test_file_server_monitor.py`：15 passed（新增
  hostname→同 IP 判同机、share 地址解析失败不炸两例；分源用例断言 range
  查询含 `job="storage-server"`、容量仍 `job="file-server"`；分源未配 job
  用例断言四段历史全空；`_FakePrometheus.range` 增加调用记录）。
- `backend/tests/api/test_stats.py`：23 passed（testcontainers PG）。
- `backend/agent/tests/test_system_monitor.py`：18 passed。
- frontend：全量 vitest 601 passed（含「7D 后标题显示『最近 7 天』」断言，
  mock 数据 `history.hours` 仍为 6，钉死「标题跟随本地选择」）；tsc、eslint、
  ruff 全绿。

## Revisit

- 分源迁移（#205）实际落地时：核对容量历史是否仍走控制面挂载（若控制面
  停止挂载共享盘，容量趋势会空，需按现场决定是否引入存储机挂载路径配置）。
- 本机 venv 的 `bin/python3` 符号链接在 08-22 起失效（Python 3.11+ 按
  realpath 找 pyvenv.cfg，Debian 基解释器本身是符号链接时找不到）——
  测试用 `PYTHONPATH=venv/lib/python3.13/site-packages` 直指站包绕过；
  根治可重建 venv 或固定解释器，属环境卫生问题另立。
