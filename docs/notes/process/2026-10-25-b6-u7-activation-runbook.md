# B6-U7：激活清单（第二 PR，未执行）

Status: implemented
Class: process

## Decision

按 [#3732](https://github.com/DUElost/stability-test-platform/issues/3732) 交付第二个文档 PR，仅新增
[激活清单](../../operations/2026-10-25-b6-device-return-activation-runbook.md)、登记运维索引与本 Note。
基线为最新 `origin/main@ccf54b3b77e442f332389c53f1f5d092e1527611`，包含第一 PR
[#3788](https://github.com/DUElost/stability-test-platform/pull/3788)；第一 PR 的记录见
[前一 Note](./2026-10-24-b6-u7-return-protocol-docs.md)，本 Note 不替代其协议与排障结论。

依据是 #3723 **当前正文 v1.7.21** §3.6.3、§3.7、§4 U7、§5，Owner 已批准的
[草案 v0.1](https://github.com/DUElost/stability-test-platform/issues/3723#issuecomment-6385265226)，以及
[规划者裁定](https://github.com/DUElost/stability-test-platform/issues/3723#issuecomment-6387873600)。
原派单引用 v1.7.20；当前正文与裁定修正第 10、11 步，其余 18 步顺序和批准范围不变。

- 前次 [退回：U7](https://github.com/DUElost/stability-test-platform/issues/3723#issuecomment-6387796547)
  指出 presence 目标集在第 12 步前没有 1.0.8；本轮按裁定恢复原 Execution，保留完整三文件 scope。
- 第 10 步分别核对 active 目录、站点包、canary 目录版本、主机侧包 SHA；presence 只证明已引用版本的包源可用。
  第 11 步逐 ONLINE 主机核对目录版本及包 SHA；预热是重指后的优化，首批 init trace 进入 L1。
- 确认点固定在第 4、12、14、17 步，分别为开始窗口、重指清单、apply/放行口径、L2 清单。
  第 1 步先绑定本 PR 合入后的 main 并完成完整测试，不把本 PR CI 当作激活 SHA 的全量验证。
- Owner 已于 2026-10-25 中止全部在跑 PlanRun；第 6 步仍以当前 PENDING/RUNNING/UNKNOWN job 零行为闸门。
- 通用构建、切控制面、换前端、热更新与 Agent 回退引用部署 SOP 步骤；B6 专有命令写全。
  八个旧版本的树对象齐全，按正文逐个恢复发布；不新建设计、接口、脚本或测试。
- 审计按实际代码区分：toggle、初始化和成功清偿没有专门审计行；scan、计划更新、热更新、中止、人工放行等按现有分类回查。
  激活后的文档收尾另开 PR；单元关闭后再关批次，#3761 保持开放。

本轮只编写与验证文档。没有读取生产凭据、连接生产库、调用生产接口、部署、发包、scan、热更新、重指、初始化或操作设备。
清单状态为「未执行」；PR 保持 Draft，规划者核对后由 Owner 转 Ready。

## Alternatives

- 在第 10 步提前重指或先建临时 Plan 来预热：不采用；违反批准顺序。裁定已提供不依赖重指的四项核验。
- 用 Agent 的 2.0.0 版本号代替能力核验：不采用；B6 前后相同，改查 matched、心跳能力声明、目录版本与包 SHA。
- 把 CLI dry-run 日志当作专项/设备类别统计：不采用；CLI 只有逐设备状态与总计。
  内联汇总复用 main 的分页与逐设备 dry-run 入口，按专项、状态、平台、型号聚合，不输出 summary 中的 serial。
- 把初始化/清偿成功编成新审计事件，或用清偿成功日志判定：不采用；现有实现没有这些事件/日志，改查 stock/discharge 状态、条目与租约事实。
- 控制面指回旧 rev 或把释放全部欠账作为恢复流程：不采用；前者不能识别新 migration，后者需要另行 Owner 授权。

## Verification

### 命令、接口、参数与返回码核对清单

以下入口均直读上述 main 基线；运行手册中的生产命令未在本轮执行。

1. 第 1、18 步的 `git fetch/rev-parse/merge-base`、`gh pr/run/issue`：部署 SOP §0.5 A1–A6、
   `.github/workflows/ci.yml` 的 workflow_dispatch/backend-test 与 `.github/workflows/main-ci-backstop.yml`。
   全量作业明确包含 Run backend tests、Install pinned promtool、Run agent tests、Run repo-level tests；后者设置 PROMTOOL_REQUIRED=1。
   `gh run watch --exit-status` 和 JSON conclusion/headSha/jobs 用于只读验收；关单命令只在未来第 18 步由规划者执行。
2. 通用部署引用：`.claude/skills/control-plane-deploy/SKILL.md` §0、§0.5 A1–A14、B1/B3/B8、C1–C7、§5。
   入口是 release-manifest、unit ExecStartPre/health、前端入口、host hot-update 与 direct 批量工具；没有复制通用部署命令。
   解释器路径以 `scripts/project_python.sh`、`tools/release/build_bundle.py`、`backend/agent/stability-test-agent.service` 为依据。
3. 第 3、4、16 步 schedule：`backend/api/routes/schedules.py::list_schedules/get_schedule/toggle_schedule`，
   `backend/api/schemas/schedule.py::TaskScheduleOut`。
   GET `/api/v1/schedules` 参数 skip≥0、1≤limit≤200，返回 items/total；客户端筛 enabled，没有 enabled 请求参数。
   单项 GET 与 POST `/{id}/toggle` 返回裸对象；200 后核对 enabled/next_run_at，缺失或隐藏项 404。
   toggle 翻转状态、重算/清空 next_run_at，没有 record_audit；请求不明先 GET，不盲重试。
4. 第 3、6 步运行状态：`backend/models/plan_run.py::PlanRun`、`backend/models/job.py::JobInstance`、
   `backend/models/enums.py::PlanRunStatus/JobStatus`。QUEUED/PRECHECK/RUNNING 与 PENDING/RUNNING/UNKNOWN 分别查询。
   第 5 步 POST `/api/v1/plan-runs/{run_id}/abort`：`backend/api/routes/plan_runs.py::abort_plan_run_endpoint/_raise_abort_http`，
   `backend/services/plan_run_abort.py::abort_plan_run/_bulk_abort_pending_jobs`。
   reason 为可选请求体字段；200 回执不证明排空，404/409 停止；审计 abort_plan_run，批量终态路径另有 job_batch_terminalized。
5. 第 7 步迁移：`backend/alembic/versions/c3d4e5f6a7b8_add_job_disarm_results_3727.py`、
   `u3b6c7d8e9f0_add_device_return_debt_3728.py`、`u8b6d3752a01_add_disarm_guard.py::upgrade`；
   `deploy/control-plane/systemd/stability-backend.service` 的 ExecStartPre 与 schema-at-head 守卫。
   version_num 与 information_schema 查询只读核对，不运行迁移命令。
6. 第 8 步发包：`tools/dev/check_script_packages.py::main/publish_latest/publish_restored/rebuild_package_from_git_tree`。
   参数逐项为 --publish、--packages-root、--publish-restored NAME@VERSION、--source-tree TREE；0 成功、非零停止。
   main 从 git 树重建并验证 SHA，拒绝异字节覆盖；restored 只写单个包，不改 manifest 副本。
   `tool_manifest.json` 的两个 active 版本/SHA、八个 retired 条目与正文 §3.6.3 的八个树对象逐项对应。
7. 第 8、10、12 步脚本目录：`backend/api/routes/scripts.py::scan_scripts/list_scripts/_script_out`、
   `backend/services/script_catalog.py::sync_scripts_from_manifest/default_packages_root`。
   POST `/api/v1/scripts/scan` 返回 200/data 下 created、skipped、conflicts、package_missing、package_conflicts；包源未配置返回 503。
   扫描写 action=scan/resource_type=script；未传 force_rebaseline。
   GET `/api/v1/scripts?is_active=true|false` 返回 data 数组，含 name/version/package_sha256/default_params；无 name 请求参数。
8. 第 9 步告警：`deploy/prometheus/alerts-stability-platform.yml` 的五条规则；
   `tools/dev/check-monitoring-assets.py::main/inspect/summarize` 的 --json、assets/source_used/state/exit_code。
   promtool check rules、规则人工副本路径、127.0.0.1:9091 的 POST /-/reload 与 GET /api/v1/rules 沿用
   `docs/operations/2026-09-24-capacity-p0-rollout-runbook.md` 的既有入口；reload 200，平台副本 match。
   其它资产的 drift 只记录，不把本清单变成资产修复批次。
9. 第 10、11、17 步 host：`backend/api/routes/hosts.py::get_host/list_hosts/_ensure_host_status_up_to_date/host_hot_update`，
   `backend/services/agent_version_info.py::build_host_version_view/finalize_hot_update_outcome`。
   单项 GET 是裸 HostOut，显式 limit=200 的列表是 items/total；matched 为投影字段，不当作数据库列。
   GET 可能刷新过期心跳为 OFFLINE，无专门审计；404 停止。热更新总有 hot_update_result（含 no-op），请求路径可能另写 hot_update。
10. 第 10、11 步目录版本：`backend/services/script_catalog_version.py::compute_script_catalog_version`，
    `backend/api/routes/heartbeat.py::_process_heartbeat_with_db`，`backend/agent/heartbeat_bindings.py::build_heartbeat_thread`，
    `backend/agent/registry/script_registry.py::ScriptRegistry._load_from_server`。
    心跳存 host.script_catalog_version，返回 script_catalog_outdated 与 return_init_check_available；Agent 重载全部 active 行。
    本地声明 key 来自 `backend/agent/device_return.py::ReturnDebtConsumer.receive`，值 1/0。
11. 第 10、11 步主机只读程序：`backend/agent/tool_cache.py::_packages_root/_sha256_of`，
    `backend/agent/config.py::_resolve_base_dir`，`backend/agent/local_runtime.py::initialize_local_stores`，
    `backend/agent/registry/local_db.py` 的 agent_state(key,value) 表与 get_state。
    包源优先 STP_PACKAGES_ROOT，缺省 STP_AEE_NFS_ROOT/packages；状态文件为 BASE_DIR/agent_state.db，SQLite URI mode=ro。
    程序不调用 initialize_local_stores/LocalDB.initialize；只读包 SHA 和已收到的能力声明。
    日志位置沿用 Agent unit/main 日志设置；grep 无匹配为 1、读失败为 2，SSH 失败通常 255，均显式区分。
12. 第 10、12 步 presence：`backend/api/routes/script_presence.py::refresh_script_presence/host_script_presence`，
    `backend/services/script_presence.py::build_full_target_set/run_sweep`。
    POST `/api/v1/script-presence/refresh?host_id=...` 是 admin 写端点，200/data 的 rows/counts；不存在或退役 host 返回 404。
    GET `/hosts/{id}` 返回 data.items/counts；refresh 更新账本与 host.script_packages_mode，无专门审计。
    目标是启用步骤引用版本 ∩ active，空目标不证明包源可用；不把它写成第 12 步前的 1.0.8 预热。
13. 第 12 步计划：`backend/api/routes/plans.py::PlanStepIn/PlanUpdate/get_plan/update_plan/_effective_step_params/_validate_onboarded_wall_clock`，
    `backend/agent/contracts/device_return.py::_pc_disarm_min_wall/_pc_finish_min_wall`。
    GET/PUT `/api/v1/plans/{id}` 返回 data；完整 steps 整体替换，保留 init/patrol 与未提交的其它计划字段。
    expected_updated_at 缺省 422、并发修改 409、计划缺失 404，鉴权沿用 admin/owner；200 后读回。
    timeout_seconds 至少 660/420 且满足合并后的参数预算；proposed 从 `backend/schemas/pipeline_templates/powercycle.json` 复制缺少的 disarm，排在 teardown 前，冲突/禁用时停止。
    proposed 只作候选，必须人工核算有效参数后交 Owner 确认。
    成功写 plan_updated（changed 字段名与 step_count），并 emit_plan_changed(plan.id,"updated")。
14. 第 13、14 步初始化：`backend/scripts/init_device_return.py::main/_fetch_device_id_page/_process_one_device`，
    `backend/services/device_return.py::init_device_return_sync`。
    CLI --dry-run/--apply，PAGE_SIZE=100，最多三次 claim 尝试；failed/conflict_exhausted 导致 exit 1，否则 0。
    dry-run 会取得设备行锁后 rollback，不等于 PostgreSQL READ ONLY；本轮未执行该生产 CLI。
    汇总只复用 dry_run=True 并按 Device.platform/model 聚合；不输出带 serial 的原始 summary。
    apply 没有专门初始化审计；stock 状态、条目、租约与日志是回查事实。
15. 第 14、15、17 步归还：`backend/services/device_return.py::is_debt_lease/apply_discharge_sync/apply_override_sync/_maybe_release_debt_lease_sync`，
    `backend/api/routes/devices.py::return_override_endpoint/list_device_return_debts_endpoint`，
    `backend/api/routes/runs.py::list_run_steps`。
    已有人工放行入口与参数详见开关机手册 §5.3；清偿 409 丢旧 token，成功没有专门日志/审计。
    GET `/api/v1/runs/{job_id}/steps` 是最新步骤裸数组，name/phase/status/exit_code；404 无 job，COMPLETED/FAILED 分别表示 init 通过/失败。
    GET `/api/v1/devices/{device_id}/return-debts` 返回 data 未关闭条目，404 无设备，不暴露 evidence 原文。
16. 全部 SQL 与附录 A：`backend/models/host.py::Host/Device`、`job.py::JobInstance/StepTrace`、`plan.py::PlanStep`、
    `plan_run.py::PlanRun`、`schedule.py::TaskSchedule`、`device_lease.py::DeviceLease`、
    `device_return_debt.py::DeviceReturnDebt`、`audit.py::AuditLog`。
    查询用 audit_logs.timestamp；只投影必要 details，不输出完整 JSON、设备 serial 或 host.ip。
    关闭原因 discharged/overridden、来源 stock/discharge/override 与 ACTIVE/MAINTENANCE 沿用 main 字符串。
    第 17 步按恢复派发时刻筛 job，并取每 job 最新 init trace，不核对 trace 条数。
    审计 action 的生产入口另核对 `device_return.py::audit_unverified_probe`、`backend/services/device_retirement.py` 与
    `init_device_return.py::_run_release_all_debt_leases`；最后一项只记录另行授权边界，不提供日常释放命令。

### 本地验证记录

- `git fetch origin main`：exit 0，main 仍为上述完整基线。
- `./scripts/project_python.sh tools/dev/ai_work.py status --risk`：exit 0；本 Execution 为 CODING/NO_PR，完整三文件 scope；开放 PR 前检无竞争项。
  原 Execution `b6-u7-3732-activation-docs` 经 resume 恢复（exit 0），issue=3732、test-impact=none，分支 `docs/b6-u7-activation-runbook`，
  独立 worktree 为 `.wt/b6-u7-activation-runbook`，不在 /tmp；scope 为清单、运维 README 与本 Note。
- 一次性内联验证：`env -i PATH="$PATH" HOME="$HOME" TESTING=1 ./scripts/project_python.sh -`。
  首轮 exit 0：19 段 bash -n、4 段 Python AST；新建 testcontainers PostgreSQL 16（512 MiB 上限），
  用 main 的 Base.metadata 创建隔离 schema，8 段 SQL/16 条 SELECT 及 1 条 Agent 状态 SELECT 均在 transaction_read_only=on 的事务中通过并 rollback，容器已停止。
  Agent 状态 SELECT 使用 main 的 key/value 表结构在该隔离库验证，适配占位符；SQLite URI/Python 语法只作静态核对，没有打开任何运行中 Agent 数据库。
- 补齐 L0 断言与恢复派发时刻筛选后，同一项目入口内联复验 exit 0：20 段 bash -n、4 段 Python AST、
  3 文件的 39 个相对链接/锚点、新增内容只有回环地址、8 段 SQL/16 条 SELECT 与 Agent 状态 SELECT 均通过。
  事务 read_only=on，全部 rollback，隔离容器已停止。第一次地址扫描误扫 README 未改的旧事故索引而 exit 1，收窄到本次新增行后通过；没有改旧索引。
- 最终候选请求与入口静态验证：同一干净环境的项目 Python 内联命令 exit 0；20 段 bash -n、24 个 jq 过滤器编译、
  40 个 main 路径引用/66 个函数或类符号通过。用 main 的 PlanUpdate/PlanStepIn、接入形态与墙钟函数验证旧合并版计划、已有 disarm、step_key 冲突、禁用 disarm 四种合成输入；
  前两种保留 init/patrol 并通过，后两种按预期拒绝。首次 jq 编译发现 sort_order 算术缺括号，修正文档表达式后通过；没有改运行时代码或测试。
- `env -i PATH="$PATH" HOME="$HOME" ./scripts/project_python.sh scripts/run_gates.py check:quick`：exit 0，16 gates 通过。
  schema-at-head 因没有 DATABASE_URL 明确 WARN/跳过，不代表迁移已验收；日志为本机会话产物 `activation-check-quick.log`。
- `git diff --check`（staged 与 unstaged）：exit 0。实际 diff 只含上述三份 Markdown；没有代码、冻结包、测试或生产配置变更。
- 最终 HEAD 的 required CI：pending；激活 SHA 的完整测试、窗口操作与 L0–L3：pending，留待本清单合入后另行授权执行。

### ADR-0059 写作约定自查

以下只列本次新写的规则句与步骤；编号均指 ADR-0059。README 新行是「未执行」索引描述，没有新增规则或术语表。

1. 「执行者必须按第 1–18 步顺序执行」：D3.1、D3.3、D3.5；承载下列步骤祈使句的要求级强度。
2. 「执行者遇到停止条件时必须停在当步并报告 Owner」：D3.1；各步停止段独立，D4.2。
3. 「执行者不得自行调整顺序或用后续确认点的结果补前置闸门」：D3.1；四确认点先于受保护动作，D4.2–3。
4. 「迁移后执行者必须只前滚修复」：D2.2、D3.1；限定控制面迁移后的路径。
5. 「执行者不得指回不认识新 revision 的旧控制面」：D2.2、D3.1。
6. 「执行者不得用 downgrade 删列」：D3.1；同处说明旧控制面不认识 revision 的后果，D4.3。
7. 「执行者不得把变量的凭据值写进文档或评论」：D3.1；变量只列名称/占位符。
8. 「初始化执行者不得把只读诊断凭据传给 apply」：D2.2、D3.1；诊断连接与 CLI 事务性质写明。
9. 「执行者必须逐段执行命令」「执行者遇到任一非预期的非零状态时必须停在当步」：分别一条动作，D3.1、D4.1–2。
10. 「执行者不得在前一段失败后继续粘贴下一段」：D3.1、D4.2。
11. 「执行者不得把 token、连接串、完整设备序列号或 SSH 凭据写进证据摘要」：D3.1。
12. 「实施者不得使用本 PR head 或本文编写基线作为激活 SHA」：D2.2、D3.1；第 1 步列 SHA/CI 查法，D4.6。
13. 「核验者必须同时核对该运行的 backend-test 日志中 promtool 版本与场景用例实际执行」：D3.1、D4.6；skip 不是成功。
14. 「执行者必须保留该确认记录」「执行者不得以“issue 应关闭”代替批准」：两条独立规则，D3.1；条件在前，D4.2。
15. 「核验者不得把隐藏/不可访问的 schedule 算作已停用」：D2.2、D3.1；API/SQL 对照查法，D4.6。
16. 「窗口内执行者必须暂停手工触发 PlanRun」：D2.1–2、D3.1；明确不是操作系统定时器或其它任务。
17. 「执行者必须先读取当前 enabled」「请求结果不明时，执行者不得盲重试」：D3.1、D4.2–3；toggle 警告前置，说明重复调用会重新启用 schedule 的后果。
18. 「如果出现新增运行，Owner 必须逐个选择等待自然结束或中止，并记录选择」：D3.1、D4.2；逐个批准在 abort 前。
19. 「核验者不得只凭接口成功判定已排空」：D3.1；PENDING/RUNNING/UNKNOWN 零行查询，D4.6。
20. 「执行者不得自动重试中止」「核验者不得跳过 UNKNOWN」：D2.4、D3.1。
21. 「执行者不得让新消费者接管旧在跑 job」：D2.2、D3.1；回退节重复同一边界。
22. 「执行者不得继续换前端或升级 Agent」：D3.1；迁移失败停止条件在前，D4.2。
23. 「执行者必须核对它与控制面实际包源一致」：D2.2、D3.1、D4.6；同处列出环境来源与 SHA 命令。
24. 「执行者必须记录实际新落位清单」：D3.1、D4.5；说明 --publish 按族最新而非按版本过滤。
25. 「执行者不得用 force_rebaseline 消除冲突」「执行者不得改写旧包或 manifest」：D2.4、D3.1；两句分别禁止一类篡改，停止条件单列。
26. 「执行者不得把其它资产的既有 drift 记为本窗已修复」：D2.2、D3.1；副本核对只断言本次目标。
27. 「执行者不得为 B6 设置 STP_AGENT_MIN_VERSION」：D3.1；同处交代版本号相同的原因。
28. 「核验者必须按时间区分历史旧行」「核验者不得把历史旧警告记为本次失败」：D2.2、D3.1；grep/SSH 退出状态写明。
29. 「核验者不得用空目标集的全零结果作成功证据」：D3.1；presence 目标语义与查法分开，D4.6。
30. 「执行者不得加入 abort_running_jobs」：D2.4、D3.1；热更新动作仍引用 SOP。
31. 「核验者必须逐项核对 TSV 覆盖与结果文件」「核验者不得以循环退出或已有旧文件代替逐台成功」：D3.1、D4.6；有 diff 查法。
32. 「核验者不得宣称旧 Agent 已具备 B6 能力」：D2.2、D3.1；离线补更另列。
33. 「核验者不得把未核验机算通过」：D3.1；Owner 暂不使用在恢复前确认，D4.2。
34. 「执行者必须保留其它计划字段和 init/patrol 步骤」：D3.1；整体替换警告与 GET/PUT 查法，D4.3、D4.6。
35. 「核验者必须合并目标版本的 default_params 与步骤 params 核算预算」：D2.2、D3.1；明确有效参数与静态下限不同。
36. 「如果参数超过默认预算，执行者必须提高墙钟并重新交 Owner 确认」：D3.1、D4.2。
37. 「Owner 未确认清单与完整请求体时，执行者不得调用 PUT」：D3.1、D4.2–3；说明丢步骤/预算的后果。
38. 「409 后执行者必须重新取现态并重新确认」「执行者不得自动覆盖并发修改」：D3.1、D4.2；返回码沿用机器值，D2.4。
39. 「执行者不得整批盲重试」：D3.1；部分成功/失败分列，D4.5。
40. 「初始化执行者不得进入 apply」：D3.1；dry-run 异常条件在前，D4.2。
41. 「执行者必须先取得本确认」「核验者不得把 executed_unverified 当成已清偿」「执行者不得按历史数量批量放行」：各一条规则，D3.1；apply 警告前置且写后果，D4.3。
42. 「核验者不得把正常释放误判为少租约」：D3.1；SQL 只查仍 open 条目的设备，D4.6。
43. 「执行者不得创建额外清理 PlanRun」：D2.2、D3.1；本步骤读取统计而非派发新运行。
44. 「核验者不得把这些类别合并记为“清偿成功”」：D2.2、D3.1；保留长期不可达/未接入/不可信类别。
45. 「执行者不得为恢复 schedule 直接释放欠账租约」：D3.1；清偿异常停止条件在前，D4.2。
46. 「核验者不得额外派发来伪造只读证据」：D3.1；L1 读取真实首批 job，D4.6。
47. 「核验者不得从 job 绿态推断 returned」：D2.4、D3.1；init 通过/失败/pending 明确列出。
48. 「执行者此前不得执行 L2」「执行者不得扩大到未批准的设备」：D3.1、D4.2；Owner 清单在动作前。
49. 「核验者不得用旧 SHA 结论代签新的回归或运行证据」：D2.2、D3.1。
50. 「核验者不得宣布整批验收完成」：D3.1；任一层 pending 列入停止段，D4.2。
51. 「实施者不得在 PR 中预先自动关闭 issue」「规划者不得提前关 #3732/#3723」：D3.1；收尾合入、证据齐备在动作前，D4.2。
52. 「Owner 决定回退后，执行者必须先停止新归还工作」：D3.1、D4.2；复用暂停排空，不新增回退设计。
53. 「执行者不得指回旧 rev」「执行者不得运行 alembic downgrade」「执行者不得删除 guard 列/原始证据」：三句分别一条规则，D3.1。
54. 「只有 Owner 另行明确授权后执行者才可以使用」「核验者不得把释放租约记为 confirmed」：D3.1；只指 release-all-debt-leases，D2.2。
55. 「执行者必须保留已完成/未完成清单、原始错误、状态快照与下一步待 Owner 决定事项」「执行者不得把部分完成记为激活完成」：D3.1。
56. 「执行者不得为没有专门审计行的步骤补写假审计」「核验者必须按事实来源回查」：D3.1、D4.6；逐步审计预期与附录查询对应。
57. 所有 18 步的操作导语与命令：D3.5；每个命令为单个动作，关联的检查/发布子动作留在草案指定的大步骤内，D4.1。
    每步均写执行者、预期输出、审计预期/回查、独立停止段，D4.2、D4.4–6。
58. 新增正文使用控制面、Agent host、PlanRun、job 与主机离线/设备退役等限定术语：D2.1–2；机器值保持原字符串，D2.4。
    没有新增对照表、修改旧历史 Note 或借本次迁移未触碰句子，D1、D2.3、D2.5；头部更新日期与本文同日。
59. 「执行者不得扩到 #3758 的其余待裁事项」「核验者不得在文档中填主机地址或凭据」：分别写出执行者与不得，D2.2、D3.1。
60. 「核验者不得把历史 146 台当作本次预期」：D2.2、D3.1；dry-run 最新聚合是查法，D4.6。

## Revisit

规划者核对清单后，由 Owner 决定 Ready 与维护窗口。激活 SHA 的完整测试、四次确认、生产结果与 L0–L3 都是后续执行证据，本 PR 不代签。
如果 main 的命令、响应、八版本表或批准步骤变化，重新绑定代码与权威正文；存在方案/代码冲突时先在 #3723「退回：U7」，不扩到代码或测试。
激活生效并贴证据后另开文档收尾 PR；本清单的「未执行」状态只由真实执行记录更新。
