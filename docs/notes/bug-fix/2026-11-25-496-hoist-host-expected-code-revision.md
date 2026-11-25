# Agent Note: Hoist `expected_code_revision` Out of Per-Host Inventory Loop (#496)

Status: implemented
Class: bug-fix
Related: #496

## Decision

1. **三态入参复用（`backend/services/agent_version_info.py`）**：为 `build_host_version_view()` 新增可选参数 `expected_code_revision: str | None = None`，与 `desired_artifact_digest` 保持一致的三态契约：
   - `None`（未传）：保持既有回退逻辑，调用 `get_agent_code_version() or None`；
   - `""`（显式空串）：不触发回退调用，直接返回 `None`；
   - 非空串：直接复用调用方预计算值。
2. **单请求外层提升与按需跳过（`backend/services/host_inventory_query.py`）**：
   - `build_host_out_readonly()` 透传 `expected_code_revision`；
   - `_load_base_host_outs()` 新增 `include_expected_revision: bool = True`：在 `query_hosts_page()` 中于循环外只调用一次 `get_agent_code_version()` 并复用至全部 `N` 台主机；
   - `build_host_facets()` 与 `build_host_selection_preview()` 不消费也不输出 `expected_code_revision`，传 `include_expected_revision=False` 实现零调用。
3. **bundle 布局免 fork 失败子进程（`backend/services/host_updater.py`）**：在 `get_agent_code_version()` 中先判断 `_AGENT_SOURCE_DIR` 及其父目录链是否存在 `.git`，无 git 上下文时跳过注定失败的 `git rev-parse --short HEAD` 子进程调用，直接读取 `<agent 源树>/VERSION`。

## Alternatives

- **方案 A：仅在进程内无限期缓存 `get_agent_code_version()`**：在开发/CI git 检出切换分支或 commit 时容易引入进程内陈旧值风险，且测试中 monkeypatch `_AGENT_SOURCE_DIR` 需要额外维护清缓存逻辑；相比之下，在请求外层提升 + bundle 跳过 `.git` 失败 fork 零缓存失效风险，且对生产与开发路径均无副作用。
- **方案 B：改写 `build_host_facets` 为纯 SQL 聚合**：当前 `host` 表仅有 78–150 行，PostgreSQL 单次 `SELECT` 耗时仅 `0.32 ms`，真正耗时在每台主机重复 fork `git`（150 台规模下占 `~320 ms`、生产 78 台 bundle 布局下占 `~115 ms`）。外层提升与免 fork 是最小且收益最高的本质修复，无需在 SQL 层复制 USB/健康/挂载的 Python 判定规则。

## Verification

- 隔离 PostgreSQL 17 基准暴露根因（150 Host × 3,750 设备）：`hosts_list_priority` 与 `hosts_facets` 的 PG `EXPLAIN (ANALYZE, BUFFERS)` 仅 `0.32 ms`，而 HTTP 端到端在 git 检出耗时 `314–330 ms`、线上 78 台 bundle 布局耗时 `114–119 ms`。
- 150 台主机构造微观对比实测：
  - 修复前（逐台触发 `get_agent_code_version`）：`330.92 ms`；
  - 修复后列表路径（单请求外层提升 1 次）：`4.81 ms`（约 69× 加速）；
  - 修复后 Facets 路径（跳过调用，0 次）：`2.85 ms`（约 116× 加速）。
- 单元与 API 回归（cgroup 内存顶保护，`run_pytest.py`）：
  - `backend/tests/services/test_host_updater.py`、`backend/tests/services/test_agent_version_info.py`、`backend/tests/api/test_hosts_b2_2_pagination_facets_496.py`、`backend/tests/api/test_hosts.py` 共 **117 passed**；
  - 移除冗余 `GIT_DIR` 读取后重跑 `test_host_updater.py` + `test_agent_version_info.py` **53 passed**。
  - 覆盖：bundle 布局无 `.git` 时读取 `VERSION` 且断言不调用 `subprocess.run`；`build_host_version_view` 显式传非空串/空串均不调用 `get_agent_code_version`；5 台主机场景下 `GET /api/v1/hosts?skip=0&limit=50` 仅调用 1 次，`GET /api/v1/hosts/facets` 与 `POST /api/v1/hosts/selection-preview` 调用 0 次。
- 静态与治理门禁：`scripts/run_gates.py check:quick` **16/16 门禁全绿**。

## Revisit

- 若未来主机规模扩展至千台级导致 `HostOut` 构造或 JSON 解析成为新瓶颈，可再评估是否将 `build_host_facets` 下沉为轻量投影或缓存层；当前 150 台规模下无需增加缓存或索引迁移。
