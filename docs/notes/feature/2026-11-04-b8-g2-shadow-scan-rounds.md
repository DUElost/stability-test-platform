# B8 G2 影子轮次调度与覆盖

Status: implemented
Class: feature

## Decision

按 [#3800 方案 v1.9](https://github.com/DUElost/stability-test-platform/issues/3800)
与[规划者裁定 6610503613](https://github.com/DUElost/stability-test-platform/issues/3800#issuecomment-6610503613)
实施 G2。借用现有归档检查周期推进影子轮，扫描周期仍独立于 Plan 的归档参数。
第一次影子动作之前写入 CURRENT 模式；影子侧只保存轮次和覆盖，不登记现行产物。

按[起点裁定 6619768806](https://github.com/DUElost/stability-test-platform/issues/3800#issuecomment-6619768806)，
首次观察只接纳 RUNNING 的 run。候选查询通过 G1 服务只返回 RUNNING 的 run，以及已有
MTK 影子轮的终态 run；无影子轮的历史终态 run 不写模式、不建轮、不入队。
取得 PlanRun 行锁后再次检查状态与轮次，避免候选查询后才进入终态的未观察 run 被接纳。
已在 RUNNING 期间建立影子轮的 run，终态后继续推进最后轮和补充轮。

新增配置：`STP_SCAN_ROUND_SHADOW_ENABLED=0`、`STP_SCAN_ROUND_INTERVAL_SECONDS=21600`、
`STP_SCAN_ROUND_REPORT_WAIT_SECONDS=7200`。等待期必须大于 0 且短于扫描周期。
合入后开关默认关闭。开启后，每个周期轮对 run 的各期望 host 通常额外扫描一次，
最后轮和补充轮另按其期望集下发；溢出副本会临时占用 Agent 本地磁盘。
按[下发频率裁定 6619843906](https://github.com/DUElost/stability-test-platform/issues/3800#issuecomment-6619843906)，
同一轮、同一 host 的最近下发时间持久保存在 expected_set 的 last_dispatched_at。
默认检查周期 120 秒不再引起重复扫描；仅入队失败后仍未下发，或超过现行 scan_task
超时（当前 2400 秒）且仍未回报时允许重试。是否开启由 Owner 决定。

现行 scan_now 的键、顺序和值保持 main 原样。仅影子载荷新增 `fact_round_id` 和
`spill_inputs`；影子串号限定为本 run 的 MTK 子集。报表和清单通过共享路径契约
读取 `_rounds/{fact_round_id}/`，不进入现行 upload、merge 或 extract。

按[接口批准 6610775684](https://github.com/DUElost/stability-test-platform/issues/3800#issuecomment-6610775684)
新增四个轮次/覆盖/溢出接口；起点裁定另需候选 ID 只读查询，仍置于 G1 服务内，
不改模型和既有接口签名。轮次推进先锁 PlanRun，再按轮次号
FOR UPDATE 读取 scan_round；覆盖写入已关闭轮次时抛异常（[串行化约束 6610809324](https://github.com/DUElost/stability-test-platform/issues/3800#issuecomment-6610809324)）。

覆盖保存在既有 expected_set JSON 的各 host/platform 条目中，带 coverage、reports、
observed_at 或 late_reports。补充轮真正期望项仍是最后轮和已关闭补充轮覆盖并集中的
缺失项。已覆盖 host 再有迟到材料时，其输入引用附在 `expected=false` 的条目中，
不参与该轮的期望分母；这样期望集为空时也能持久保存新增输入，且不改写旧轮。
报表引用保留源 fact_round_id，按源轮次、host、平台、路径与空回执标记去重转接。

最后轮和补充轮先提交数据库，再入队；每个检查周期使用独立 SAQ 尝试键，避免已完成
任务的 TTL 阻挡恢复，但只给尚未下发或已超时未回报的 host 所属轮次入队。
任务在行锁内提交 host 的下发时间后才发送命令，并行任务不能重复保留下发。
提交后若执行中断，该 host 在扫描超时后恢复。覆盖由后续检查推进，到齐或截止即关闭。
无回执保持缺失，迟到材料只在下一轮或补充轮作为有效输入。材料未被删除。

按[G3 对接裁定 6619941312](https://github.com/DUElost/stability-test-platform/issues/3800#issuecomment-6619941312)，
溢出输入纳入 REMOTE、ARCHIVED、PRUNED：HddSpill 清理本地后中心副本仍在。
仍要求非空 remote_path，且 DLE 与台账属于本分区、本 host；无路径和跨分区记录不纳入。

pr-agent-tests 的失败断言使用 healthy_ops，却仍读取真实 CPU、内存与根盘可用空间。
本地将根盘剩余量设为 19 GiB 时，原断言复现为 FAIL；给该汇总测试固定健康资源后通过，
并补资源确实不足时仍返回 FAIL 的反例。生产 preflight 及其阈值没有改动。
CI 日志没有输出失败时的资源数值，因此具体触发资源不能由日志确定。

## Alternatives

同目录存放影子报表会污染现行上传与 merge 输入，v1.9 已裁定采用独立命名空间。
本实现不修改现行读取方。缺少 fact_round_id 的条目不能靠时间戳猜测归属。
仅依靠 SAQ 状态不足以恢复最后轮与补充轮，因此数据库记录是轮次状态的来源。

## Verification

以下命令均在专属 worktree 使用项目 Python 入口执行。pytest 经 scripts/run_pytest.py
提供 6 GiB / swap=0 的内存保护，再调用同一解释器的 python -m pytest；后端测试使用
Docker testcontainers PostgreSQL，没有读取生产 env 或连接生产库。

- `./scripts/project_python.sh scripts/run_gates.py check:quick`：profile 返回成功（16 项）；
  schema-at-head 因隔离执行未配置 DATABASE_URL 明确跳过，其余门禁通过。
- 完整根测试命令：`./scripts/project_python.sh scripts/run_pytest.py tests/ -q`；
  当前提交的最终结果统一记录在 [PR #3874](https://github.com/DUElost/stability-test-platform/pull/3874)。
- 受影响测试：test_shadow_scan_rounds、test_issue_fact_store、test_saq_scan_pipeline、
  test_plan_run_scan_scope、test_scan_manifest_reader_2188、test_dedup_scan_merge、
  test_cron_scheduler、test_settings_scheduler、test_site_preflight、锁序守卫：208 passed。
  增补 PRUNED 空路径、跨 run 和无台账边界后，G1 服务测试单独重跑：11 passed。
- Ruff、git diff --check 通过；共享路径契约与 G3 worktree 的文件逐字节一致。
- 反例覆盖：等待期非法配置、现行载荷不变、MTK 串号边界、独立清单/legacy glob 隔离、
  upload 与 merge 输入不变、四类覆盖、最后轮及补充轮重试、并发建轮、PostgreSQL 行锁、
  关闭后覆盖写入拒绝、迟到不冒充当前轮、按分区及 host 读取已校验溢出输入。
- 起点修订新增的四条反例在修改前实际得到 4 failed（3 种历史终态、首次加锁观察前结束）；
  修改后与 G1 服务测试一并运行：28 passed。另覆盖已有其他平台轮次不能接纳历史 MTK 分区。
  原最后轮、补充轮与并发反例调整为先在 RUNNING 期间建立影子轮，再进入终态。
- 同轮下发反例在修改前 3 failed；纳入 PRUNED 的边界反例在修改前 1 failed。
  修复后本轮聚焦测试（影子轮、G1、preflight、锁序守卫）：66 passed。
- 临时插件模拟根盘剩余 19 GiB，执行同一 preflight 失败用例：修改前 1 failed、修改后 1 passed。
  同期完整根测试因候选查询移到 G1 后静态守卫看不到定序而失败，补显式 sorted 后守卫通过；
  旧根测试在新裁定到达后中断（1 failed / 1483 passed），不计为验收通过。

## Revisit

复核及 required CI 仍由当前 PR head 的结果决定；本地通过不代表影子运行已激活。
G3 后合入时核对并消除共享路径契约的重复提交。空回执使用分片 artifacts 中的
`empty_receipt=true` 条目，已在 #3800 评论 6611031256 登记对接；规划者在 6619768806
已核对 G3 空回执代码与测试，对接成立。
影子起点以 RUNNING 期间实际建立轮次为准，历史终态 run 的追溯扫描不在本单元范围内。
G4/G5 承接 merge 失败、交付与缺口判定；N 到期后的恢复与保留规则不在本单元新增删除路径。
