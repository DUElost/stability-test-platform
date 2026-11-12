# 设备改绑端点（#3892 / ADR-0063 D7 配套）

Status: implemented
Class: feature

## Decision

`device.host_id` 的唯一自动写路径是心跳（`backend/api/routes/heartbeat.py`，设备须经 ADB
上报 online）。vbus 断电刷机的设备在刷机前是 powered_off（不上报 ADB），无法靠心跳改绑；
而派发准入 `device_host_drift` 与 prepare 分组都按当前 `host_id` 判定——「先改绑再刷」
因此卡死（15.94 的 7 台 LX3 误绑在 15.58，须改绑到 15.94 才能刷）。本单元补齐这条人工路径。

### 返修 R1（#3944 合入后复核退回；Owner 2026-11-12 裁决「乙」）

PR #3944 已由 FIFO 合入（`a87355034`，所审 HEAD `e73044ee0`）。独立复核在
`e73044ee0` 上复现：admin 改绑 A→B 返回 200 后，重放改绑前采集的旧主机 ADB 报文，
归属回 A、审计仍只有 A→B；另列目标主机退役竞态、同归属前置短路、strict 审计测试缺口。
载体 #3892 退回评论（`6814357678`）请规划者裁决人工改绑与迟到 ADB 观测的先后语义；
Owner 2026-11-12 选「乙：收窄端点 + 静默期」，本单元按规划者修订判据实现 B1–B4。

- **B1 目标主机退役竞态**：目标主机前置分两段——先用列级快速检查（存在/在役）给出
  404/409；在 `_assert_idle` 之后、写入之前再用 `host_retirement._locked_host` 取
  `FOR UPDATE` 行锁并锁内复检 `retired_at`。锁与 `host_id` 写入、审计同事务；锁序
  device→host，单锁非循环。退役事务要么先提交（本处锁内读到 retired_at → 409），要么
  等本事务提交后才拿到锁。锁内复检前 `db.refresh(host)`，防同一 Session 身份映射返回
  预检时的旧值。
- **B2 同归属前置短路**：幂等早返回 `if device.host_id == target_host_id: return` 移到
  设备在役、静默期、目标主机存在且在役、`_assert_idle` 全部之后。同归属带阻断项返回
  对应 409；同归属干净仍原样返回、零审计。
- **B3 strict 审计回归保护**：产品实现原本就是 `strict=True`，补一条故障注入用例：隔离库
  临时重命名 `audit_logs`，经生产同款 `get_db` 会话边界调 API，断言 500 + `host_id`
  回滚原值 + 零审计（等价复核探针 `test_missing_audit_table_rolls_back`）。
- **B4 迟到旧主机心跳回改（乙）**：新增改绑前置——设备须 ADB 缺席且静默：
  `adb_connected is not True` 且（`last_seen is None` 或
  `last_seen < now - QUIET_PERIOD`）。`QUIET_PERIOD` 复用
  `HOST_HEARTBEAT_TIMEOUT_SECONDS`（生产 300s），不新增旋钮；不满足 → 409
  `DEVICE_NOT_QUIET`，结构化 message 说明须 ADB 缺席且静默 ≥N s（避免迟到心跳回改）。
  `last_seen` 只在 serial 进入心跳 ADB 枚举时更新（`heartbeat.py:570`），USB 槽位在位
  心跳不更新；`adb_connected` 是最近一次枚举的连接标志（`host.py:114`）。边界：
  `last_seen` 恰等于 `now - QUIET_PERIOD` 不算静默（严格小于才放行）；naive
  `last_seen` 按 UTC 解释。
- **闭合边界**：改绑只在无当前/近期 ADB 观测时放行。此后只有设备真的在旧主机启动（新鲜
  如实观测）才会回改，属既有「归属随最新心跳」契约的正确行为；静默期内重放旧 ADB 报文
  与新鲜观测在服务端不可区分，仍走既有契约。复核 B4 的「在线设备改绑后重放」反例现在
  在改绑步即 409，竞态不成立。15.94 七台 LX3 按用例持续关机、无含 serial 报文在途，
  走 c) 分支（持续关机可改绑；仅槽位在位心跳不改回归属）。
- **不改 Agent 输入集**：heartbeat 改绑契约与 Agent 代码输入集未改；基线与 HEAD 的
  `compute_deploy_digest.py` 均为
  `sha256:cee436e2f50068ee02c5d25e08752a7361b03a5559b899a3bad4031aee746bc8`。

### 端点语义（原实现保留）

- 新增 `backend/services/device_rebind.py`：`rebind_device(db, *, device_id,
  target_host_id, reason, actor_id, actor_username, request)`。行锁 device
  （`FOR UPDATE`）+ 锁内复检；目标主机 `FOR UPDATE` 行锁 + 锁内复检。前置：
  - 设备在役（`retired_at IS NULL`），否则 409 `DEVICE_RETIRED`；
  - 设备 ADB 缺席且静默满 `QUIET_PERIOD`，否则 409 `DEVICE_NOT_QUIET`；
  - 目标主机存在（否则 404 `HOST_NOT_FOUND`）且在役（否则 409 `HOST_RETIRED`）；
  - 设备无活跃 Job、无 ACTIVE 租约（否则 409 `DEVICE_HAS_ACTIVE_WORK`）——不顺带中止
    在跑测试，也不把租约从一台主机抽走。
  只改 `host_id`，**不改写 `status`**（心跳所有）、不删历史行。幂等：目标即当前归属时
  原样返回、不重复审计。审计 fail-closed（`record_audit(strict=True)`，action=
  `rebind_device`，details 含 `serial`/`from_host_id`/`to_host_id`/`reason`）；
  action 登记在 `BUSINESS_ACTIONS_ALLOWLIST`（90d，与 `retire_device` 同族，
  ADR-0049 D2）。
- 路由 `POST /api/v1/devices/{device_id}/rebind`（`require_admin`，
  `response_model=ApiResponse[DeviceOut]`，返回 `ok(_device_out(device, db))`）。
- Schema `DeviceRebindIn{host_id, reason}`（均 `min_length=1` + 去空白校验）。
- 前端：`types.ts` 增 `DeviceRebindRequest`；`devices.ts` 增
  `devices.rebind(id, body)`（`unwrapApiResponse<Device>`）。无页面入口（与
  `releaseLease` 同惯例，运维经 API 调用）；本轮未新增前端错误码分支。
- 错误用结构化 `detail={"code", "message"}`（前端按 code 判定），经 `ServiceError`
  统一 handler 翻译为 404/409。

## Alternatives

- **B4 甲（心跳侧区分观测新旧）vs 乙（收窄端点 + 静默期）**：甲需要给心跳/Agent 输入
  增加观测时间戳或 epoch，改变 Agent 代码输入集与 digest，且要把「旧报文」语义扩散到
  所有心跳消费方；乙只改 admin 改绑前置，复用既有的 `last_seen`/`adb_connected`，
  不动心跳契约与 Agent 输入。Owner 2026-11-12 选乙，本单元按乙实施。
- 在改绑端点里校验「serial 在目标主机槽位映射里」：会把通用改绑端点耦合到 vbus 槽位表；
  槽位在位是刷机前的运维前置（已单独核验），不进通用端点。
- 复用退役服务的 `_assert_no_active_work`：该函数带「仅欠账租约可退役」的 D5.8 例外并在
  退役事务内释放欠账租约——改绑不应释放租约，语义不同，故改绑自带独立的
  `_assert_idle`（任何 ACTIVE 租约都拒绝），不复用。
- **B1 在 `_assert_idle` 之前持目标主机锁**：会让「退役事务在检查后、写入前提交」的
  时序无法出现（退役被锁阻塞），验收用例只能测到「退役先提交」一支；最终采用
  「快速存在性检查 → 空闲检查 → 行锁 + 锁内复检」，既保留可复现的竞态窗口，又让
  权威检查与写入同事务。锁只覆盖最后一段，缩短持锁时间。
- **B2 保留早返回并文档化**：复核明确要求同归属也走前置（带阻断项返回 409），
  不接受「同归属跳过检查」的语义。
- 让心跳支持 powered_off 改绑：心跳只认 ADB 上报的设备，powered_off 设备不在上报清单里，
  改心跳判据会污染「归属按最新心跳」的语义；人工 admin 入口是更小的闭合。

## Verification

本机生产控制面同机；测试经 `scripts/run_pytest.py`（cgroup 6 GiB 内存硬顶）跑隔离测试库。
以下均为本轮实跑命令与结果。

- `backend/tests/api/test_device_rebind.py`：**19 passed**。覆盖 admin 门禁 403；改绑成功
  （写 host_id、status 不变、审计单条含 from/to/reason）；设备/目标主机不存在 404；
  设备退役 / 目标主机退役 / 活跃 Job / ACTIVE 租约 409（各自 code）；幂等同归属无审计；
  **同归属带 B 退役 / PENDING Job / ACTIVE 租约重放三次均 409（各自 code）**；
  **B1 两 Session 在线交错**：退役线程执行真实 `retire_host`，取到 host 行锁、写入
  `retired_at` 后在 audit 处暂停未提交；改绑线程先 `session.get(host)` 预热身份映射，
  再执行真实 `rebind_device`；改绑到达 host `FOR UPDATE`（真实现）或
  `device.host_id` `UPDATE`（M1 无锁路径）时释放退役提交。真实现 **409 HOST_RETIRED、
  归属仍 A、零审计**；M1（`db.get` + 去 `db.refresh` + 去锁内复检）→ **1 failed /
  exit 1**（改绑成功写 B）。编排全用 `threading.Event` + SQLAlchemy
  `before_cursor_execute`，无裸 sleep；真实现连跑 5 次全绿，M1a 连跑 3 次全红。
  **audit_logs 临时缺失 → 500、host_id 回滚、零审计**；静默期 a/b/c/d
  （在线 409、刚关机 409、持续关机 200 + 旧主机仅槽位心跳不改回、边界/naive 单测）。
- 相邻回归：`backend/tests/api/{test_device_rebind,test_devices,
  test_device_retirement_api_2962,test_devices_bulk_swipe_trail}.py`
  + `backend/tests/test_audit_action_retention_guard_3017.py`
  + `backend/tests/api/test_heartbeat.py`：**97 passed**。
- 锁序与静态守卫：`tests/test_lock_order_collection_total_order.py`、
  `tests/test_lock_order_pr_path_contract.py`、
  `tests/test_static_guard_pr_path_contract.py`：**23 passed**。
- `tools/dev/check_pr_migrate.py`：空库迁移 + schema 比对 + seed 身份对拍
  **通过**（postgres:16 一次性容器；compare_metadata 基线 5 项、新增 0）。
- `scripts/run_gates.py check:quick`：**16 gates 通过**（含 lint/compileall）。
- `npm --prefix frontend run type-check`：**通过**（`tsc --noEmit` 双项目）。
- 全量 `backend/tests/api/`：**1646 passed**。历史上 `test_ai_assistant_endpoints.py::
  TestConfigEndpoints::test_connection_test_refuses_fast` 的 `InvalidURL` 本轮未复现；
  该节点在干净基线 `origin/main` 上单跑 **1 passed**，与本改动无关。
- R2 复跑变异（每次恢复后全文 `git diff --exit-code HEAD` 通过）：M1a（主机锁改
  `db.get` + 去 `db.refresh` + 去锁内复检）→ B1 用例 **1 failed / exit 1**；
  M2 → 3 failed；M3 → 1 failed；M4a → 2 failed；M4b → 1 failed。锁序静态守卫 +
  #3017 留存守卫：**31 passed**。
- **Agent digest 相等**：`tools/ansible/compute_deploy_digest.py --source-dir
  backend/agent --schema-file backend/schemas/pipeline_schema.json` 在 HEAD 与
  `origin/main` 基线（`git archive` 展开）各算一次，均为
  `sha256:cee436e2f50068ee02c5d25e08752a7361b03a5559b899a3bad4031aee746bc8`。

## Revisit

- 静默期固定复用 `HOST_HEARTBEAT_TIMEOUT_SECONDS`（300s）。若运维出现「设备刚关机就要
  立即改绑」的真实诉求，先评估把等待时间明确告知调用方（如 409 message 带剩余秒数），
  再考虑独立旋钮；本轮刻意不新增配置。
- 静默期内的旧 ADB 报文仍可能在改绑后回改（与新鲜观测不可区分），这是「归属随最新心跳」
  既有契约的边界；若要彻底消除，需要在心跳/Agent 输入里携带观测时间戳或设备侧 epoch，
  属新的 ADR 级设计，不在本单元。
- 15.94 的 7 台 LX3 部署后按「持续关机 + 无在途含 serial 报文」路径改绑；若现场发现
  设备仍在线或刚关机，先等待静默期，不要绕过端点直接改库。
- 批量改绑（多设备一次性）暂无诉求；如需要，按退役批量同款「逐台独立事务」形态扩展，
  不在本单元预置。
