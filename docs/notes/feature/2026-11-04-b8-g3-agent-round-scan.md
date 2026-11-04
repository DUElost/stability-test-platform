# B8 G3 Agent 轮次扫描、溢出回接与伴随映射

Status: implemented
Class: feature

## Decision

依据 [#3800 方案 v1.9](https://github.com/DUElost/stability-test-platform/issues/3800)、
[#3866](https://github.com/DUElost/stability-test-platform/issues/3866) 及规划者裁定
[6610503613](https://github.com/DUElost/stability-test-platform/issues/3800#issuecomment-6610503613)、
[6610775684](https://github.com/DUElost/stability-test-platform/issues/3800#issuecomment-6610775684)、
[6610795662](https://github.com/DUElost/stability-test-platform/issues/3800#issuecomment-6610795662)、
[6610809324](https://github.com/DUElost/stability-test-platform/issues/3800#issuecomment-6610809324)、
[6619941312](https://github.com/DUElost/stability-test-platform/issues/3800#issuecomment-6619941312) 实施。

Agent 入口校验并透传 `fact_round_id`、`spill_inputs`，排队合并键改为
`(plan_run_id, fact_round_id)`。载荷不带新键时保持原调用参数及日志；报表仍写入原目录，
清单条目不增加轮次字段。影子报表和映射写入 `dedup/{run}/_rounds/{id}/{platform}/`，
清单写入 `_meta/{run}/_rounds/{id}/{host}.json`。两端共用 `round_report_layout.py`。

`scan_scope` 从 DLE 的明确路径建立观察映射，将载荷中的溢出副本回接到 staging，
通过既有 `_link_or_copy_file` 跨文件系统复制回落。本地与溢出输入分开放置，身份沿用
DLE 的 `occurrence_key`。映射不从 xls、目录名或 DLE id 推导发生身份。
HddSpill 的 REMOTE 补丁增加 `hdd_spill=true`；控制面经 G1 的
`record_spill_ledger_entry` 在同一事务中登记实测 `size_bytes`。普通上传不登记溢出台账。
溢出意图通过既有 Agent 状态库保留，重启恢复与重试仍带溢出标记；REMOTE 回执成功后清除标记。
Agent 专用 DLE 列表同时返回已持久化的 `occurrence_key`，供本轮映射读取。
列表接口增加可选 `plan_run_id`，扫描端按 host 与 run 在 SQL 中过滤后读取；未传参数的调用
保留原行为。扫描查询明确区分失败与成功空列表，失败记录 `scan_mapping_events_unavailable`。

伴随文件名为 `{host}_{org文件名}.mapping.json`。JSON 包含 `schema_version=1`、
`plan_run_id`、`host_id`、`platform`、可选的 `fact_round_id` 及 `rows`。
每项包含 `sheet`、`row`（从 1 起，表头为第 1 行）、`path`（Path 原文）、
`dle_id`、`occurrence_key`、`source`（local / spill）和 `mapped`。
不能唯一对应到 DLE 的行，其身份与来源为 null、`mapped=false`。
事件查询失败时，顶层额外记录 `events_unavailable=true`，现行、影子与展锐的有效报表继续上送；
成功的空列表不设置该标志，避免把没有对应观察与查询故障混为一类。
报表条目用 `companion_file_key` 指向映射；映射在同一清单登记，`registerable=false`，
类型为 `scan_occurrence_mapping`。xls 字节不改动。
有效零行影子报表的条目额外带 `empty_receipt=true`，与 G2 的显式空回执判据对接；
没有报表时不推断为空回执。

上传前逐行验证 serial 属于本 run 的设备集合。纯 MTK 的 run 跳过展锐 runner；
展锐超时报告没有本 run 的 serial 证据时拒绝上送。这些校验同时作用于现行和影子路径。
watcher 的 PULL_FAILED 与当前 MTK LOCAL 兜底路径保持空 key，记录
`occurrence_key_unprovable path_category=pull_failed|local`；没有增加 db_history 读取。

## Alternatives

未采用共享影子与现行清单的方案，避免影子报表进入现行上传与 merge 输入。
未通过改扫描工具参数或 xls 结构携带身份，避免改变工具契约。
未使用发现时间或 inotify 路径替代设备历史原文。collector 将来能提供原文时，
watcher 使用 G1 的唯一身份契约；当前缺少原文时沿用 fail-open。

## Verification

新增反例覆盖首次扫描前溢出、
本地与溢出同身份且来源不同、全部报表行与未映射行、异串拒绝、影子与现行文件隔离、
轮次排队互不顶替、纯 MTK 不启动展锐、超时无串号证据拒绝，以及入口类型错误不降级。
复制回落 fixture 的证据文件为 26 字节，复制后的 inode 与中心源不同、链接数为 1，
占用独立磁盘块；staging 回收后中心源保留。`temporary_bytes` 日志报告逻辑字节数，
不包含文件系统块对齐开销。生产临时占用随本轮溢出输入总量增长。

实际命令均经 `./scripts/project_python.sh`，pytest 经 `scripts/run_pytest.py` 的
6 GiB / swap=0 内存保护。结果如下：

- `scripts/run_gates.py check:quick`：16 gates 通过；未配置数据库，schema-at-head 跳过。
- `scripts/run_pytest.py backend/agent/tests/ -q`：临时空 udev 目录隔离后 3044 passed。
  使用 `env -i PATH="$PATH" PYTHONPATH=.` 和 `STP_STEP_PARAMS` 的 `udev_rules_dir`。
  未隔离首轮为 3040 passed / 1 failed，失败来自宿主旧 udev 规则影响刷机预检测试。
- `scripts/run_pytest.py backend/tests/api/test_agent_device_log_events.py backend/tests/services/test_agent_device_log_events.py -q`：
  testcontainers PostgreSQL，25 passed。
- `scripts/run_pytest.py tests/ -q`：首轮 2426 passed / 18 skipped / 4 failed。
  import 棘轮、未跟踪载荷检查与 Excel 台账登记修复后，相关三文件复跑 17 passed；
  最终完整根测试结果随 PR 记录。
- 最新受影响 Agent 子集 102 passed；映射函数调整后单文件再跑 18 passed。

规划者核对 6619941312 后新增服务端按 run 过滤、limit 前过滤、参数缺省兼容、HTTP 错误、
超时及无效响应反例；同时验证故障日志、映射标志和报表继续上传。该轮实际命令与最终结果随 PR 更新。

经 Owner 在本会话同意最小三文件 scope 后，`backend/agent/requirements.txt` 登记 `xlrd==2.0.2`，
`tests/test_excel_dependency_inventory.py` 登记 `scan_scope.py` 的读取点，
`docs/notes/process/2026-09-20-dependency-convergence-739.md` 补充既定 xls 格式的使用理由。
三处登记与本次读取点同步交付。当前开发环境已有 xlrd 2.0.2，
干净 Agent 需要同步此依赖；缺库时拒绝未经校验的报表，不降级上传，也不阻止 Agent 导入。

## Revisit

影子阶段仍需按来源统计空 key 比例，并按真实溢出输入量测量临时磁盘占用。
扫描工具保持原参数；影子开启会增加一次扫描及溢出副本的临时本地占用。
部署、开影子开关、改变上传集合由后续批次闸门控制，本 PR 以 draft 交独立复核。

G2 的溢出查询需要包含仍有中心副本的 PRUNED 事件：现有 HddSpill 在删除本地后
会写入该状态。G3 读取 DLE 列表时没有排除该状态；G2 的载荷查询归 G2，不在本单元修改。
未归属事件的溢出回执记录 `spill_ledger_unassigned`，没有分区时不伪造台账归属。
