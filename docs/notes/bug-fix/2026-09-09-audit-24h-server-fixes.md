# 24h 审计服务端三项修复（#1137 窗口 follow-up）

Status: implemented
Class: bug-fix

## Decision

09-09 只读审计（窗口 `7b9d5f1c..db0d7e22`）确认 4 项服务端明确缺陷，本次逐一修复，
均在 registry 在窗 Execution 的 `backend/agent`/`scripts/ci` 域之外（agent 侧与
ADR/产品决策类延后，见 Revisit）。

1. **DLE create 幂等重放状态守卫**（`backend/api/routes/agent_api.py`）
   `ingest_device_log_events` id 分支对命中已有行无条件覆写 `state/remote_path/
   checksum/plan_run_id`。预分配 id 的 create 意图恒为 `state=LOCAL` 且不带
   `remote_path/checksum`；「服务端已提交、响应丢失」的模糊失败后该意图经 outbox
   重放，可能把已推进到 UPLOAD_PENDING/REMOTE 的行回退成 LOCAL 并清空
   remote_path——extract 只认 REMOTE/ARCHIVED/PRUNED 且有 remote_path 的行，
   且此时本地副本可能已被 #1083 prune，事件引用永久丢失。修复：命中已存在行的
   create 意图（LOCAL + 无 remote_path + 无 checksum）一律按幂等成功处理，不改行。
   另：no-id 去重分支补 `host_id` 等值谓词，与 id 分支的 403 host 校验对齐
   （`JobInstance.host_id` NOT NULL，实际仅理论边界可达，属防御性加固）。

2. **post_completion 两段提交**（`backend/services/post_completion.py`）
   #1076 把摄入失败改为 rollback+defer 后，detail JSON 永久损坏/合法空 testpoints
   的 job 永远走 pending：每次 recycler 重试都重算报告再整体回滚，`report_json`/
   `jira_draft_json` 永不可见且空转。`compose_run_report` 不读 `test_case_result`
   （数据源为 job 终态快照与 artifacts），故改为：① compose + report/draft 先
   commit（不写 `post_processed_at`）；② `_complete_case_ingest` 补摄入，pending
   返回 False 留 recycler 重试。已落库报告的重试走短路径（跳过重算只补摄入）；
   risk_high 通知移到终态化成功后，摄入抛错不再连带回滚已生成报告。

3. **readiness Redis ping 超时**（`backend/main.py`）
   `/health` 对 `redis_client.ping()` 无超时包裹（#885/#883 引入），而
   `verify_redis_connectivity` 用 `REDIS_PING_TIMEOUT`（3s）。黑洞式分区下探针
   悬挂到 OS TCP 超时、超出编排探针时限且任务在事件循环累积。复用同一常量
   `asyncio.wait_for` 包裹，超时按既有 `REDIS_UNREACHABLE` 503 语义返回。

## Alternatives

- **服务端按状态机拒绝降级**（REMOTE 等推进态拒收 LOCAL 更新）：语义更显式，
  但需维护状态偏序表；本次的「create 意图命中已有行即 no-op」以最小面覆盖
  重放场景，且不触碰 uploader 的合法 PATCH 路径（带 remote_path/checksum）。
- **post_completion 拆分 post_processed_at/case_ingested_at 两列**：更精确但需
  迁移，前轮 #1076 已否决，本次沿用其单列 + NULL 延迟语义。
- **/health 探针级 try/timeout 计独立常量**：与既有 `REDIS_PING_TIMEOUT` 重复，
  取单源常量对齐。

## Verification

```bash
cd /tmp/stp-auditfix   # branch fix/audit-24h-0909-server @ f9704c07
.venv/bin/python -m pytest \
  backend/tests/api/test_agent_device_log_events.py \   # 6 passed（+1 新回归）
  backend/tests/api/test_health_saq.py \                # 13 passed（+1 新超时）
  backend/tests/services/test_post_completion.py \      # 4 passed（+1 新短路径）
  backend/tests/services/test_test_case_result_ingest.py \
  backend/tests/tasks/test_saq_tasks.py \
  backend/tests/api/test_agent_dual_write.py            # 合计 95 passed
```

新增回归测试：
- `test_device_log_events_create_replay_does_not_regress_advanced_row`：
  LOCAL create → REMOTE promote → LOCAL create 重放 → 行保持 REMOTE，
  remote_path/checksum/plan_run_id 不被清空。
- `test_post_completion_retry_skips_recompose_after_report_persisted`：
  报告先落库后重试不再调用 compose（短路径补摄入），损坏 detail 恢复后可终态化。
- `test_redis_ping_hang_times_out_returns_503`：挂起 ping 在 0.2s 超时内 503。

ruff check 对改动文件通过（repo 基线不满足 `ruff format`，非门禁，未格式化）。

## Revisit

- **审计项暂缓（未修）**：agent 侧三项——#1009 `execute_actions` 吞 drain 异常仍
  返回 settled、#1006 `release` 补偿清继任占位、DLE 本地 outbox 无 dead-letter/
  队首饿死——因 `fix-r07-recovery-lifecycle-1008-1013`（opencode）在窗 CODING
  且声明 scope 覆盖整个 `backend/agent`，按并行契约让窗，其合入后另行修复；
  #1101 PAT 回退告警在 `scripts/ci/pr-automerge-queue.sh`，同因
  `docs-adr0035-merge`（dsh）在窗占用而延后。
- **设计/产品决策类未改**：#890 leadership fail-closed 默认形态（ADR-0027 v1.1
  已裁决）、#955 available 端点暴露面（有意的普通用户 wifi 选择功能）、#1110
  终态归档无界补偿与 #1111 轮次并发（需最终轮优先序设计）、#1073 远端目录增长
  （磁盘清理属运维）、sleep_finish v1.0.2 死代码副本（已发布版本不可原地改）。
