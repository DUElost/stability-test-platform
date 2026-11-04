# B8 G2 影子轮次调度与覆盖

Status: implemented
Class: feature

## Decision

按 [#3800 方案 v1.9](https://github.com/DUElost/stability-test-platform/issues/3800)
与[规划者裁定 6610503613](https://github.com/DUElost/stability-test-platform/issues/3800#issuecomment-6610503613)
实施 G2。借用现有归档检查周期推进影子轮，扫描周期仍独立于 Plan 的归档参数。
第一次影子动作之前写入 CURRENT 模式；影子侧只保存轮次和覆盖，不登记现行产物。

新增配置：`STP_SCAN_ROUND_SHADOW_ENABLED=0`、`STP_SCAN_ROUND_INTERVAL_SECONDS=21600`、
`STP_SCAN_ROUND_REPORT_WAIT_SECONDS=7200`。等待期必须大于 0 且短于扫描周期。
合入后开关默认关闭。开启后，Agent 每周期对每个运行中的 run 额外扫描一次；
溢出副本还会临时占用 Agent 本地磁盘。是否开启由 Owner 决定。

现行 scan_now 的键、顺序和值保持 main 原样。仅影子载荷新增 `fact_round_id` 和
`spill_inputs`；影子串号限定为本 run 的 MTK 子集。报表和清单通过共享路径契约
读取 `_rounds/{fact_round_id}/`，不进入现行 upload、merge 或 extract。

按[接口批准 6610775684](https://github.com/DUElost/stability-test-platform/issues/3800#issuecomment-6610775684)
只新增四个 G1 服务接口，不改模型和既有接口签名。轮次推进先锁 PlanRun，再按轮次号
FOR UPDATE 读取 scan_round；覆盖写入已关闭轮次时抛异常（[串行化约束 6610809324](https://github.com/DUElost/stability-test-platform/issues/3800#issuecomment-6610809324)）。

覆盖保存在既有 expected_set JSON 的各 host/platform 条目中，带 coverage、reports、
observed_at 或 late_reports。补充轮真正期望项仍是最后轮和已关闭补充轮覆盖并集中的
缺失项。已覆盖 host 再有迟到材料时，其输入引用附在 `expected=false` 的条目中，
不参与该轮的期望分母；这样期望集为空时也能持久保存新增输入，且不改写旧轮。
报表引用保留源 fact_round_id，按源轮次、host、平台、路径与空回执标记去重转接。

最后轮和补充轮先提交数据库，再入队；每个检查周期使用独立 SAQ 尝试键，避免已完成
任务的 TTL 阻挡恢复。任务只短暂派发扫描；覆盖由后续检查推进，到齐或截止即关闭。
无回执保持缺失，迟到材料只在下一轮或补充轮作为有效输入。材料未被删除。

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
- `./scripts/project_python.sh scripts/run_pytest.py tests/ -q`：2435 passed、18 skipped。
  首轮 1 failed、2434 passed，原因是新增 Agent 契约文件尚未 git add；加入索引后整套重跑通过。
- 受影响测试：test_shadow_scan_rounds、test_issue_fact_store、test_saq_scan_pipeline、
  test_plan_run_scan_scope、test_scan_manifest_reader_2188、test_dedup_scan_merge、
  test_cron_scheduler、test_settings_scheduler：165 passed。
- Ruff、git diff --check 通过；共享路径契约与 G3 worktree 的文件逐字节一致。
- 反例覆盖：等待期非法配置、现行载荷不变、MTK 串号边界、独立清单/legacy glob 隔离、
  upload 与 merge 输入不变、四类覆盖、最后轮及补充轮重试、并发建轮、PostgreSQL 行锁、
  关闭后覆盖写入拒绝、迟到不冒充当前轮、按分区及 host 读取已校验溢出输入。

## Revisit

复核及 required CI 仍由当前 PR head 的结果决定；本地通过不代表影子运行已激活。
G3 后合入时核对并消除共享路径契约的重复提交。空回执使用分片 artifacts 中的
`empty_receipt=true` 条目，已在 #3800 评论 6611031256 登记对接；阶段一激活前需核对
G3 的实际生成能力。首次打开全局影子开关会覆盖可发现的 MTK 终态 run，需评估存量扫描量。
G4/G5 承接 merge 失败、交付与缺口判定；N 到期后的恢复与保留规则不在本单元新增删除路径。
