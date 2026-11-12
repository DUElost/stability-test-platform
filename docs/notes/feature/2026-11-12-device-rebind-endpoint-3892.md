# 设备改绑端点（#3892 / ADR-0063 D7 配套）

Status: implemented
Class: feature

## Decision

`device.host_id` 的唯一自动写路径是心跳（`backend/api/routes/heartbeat.py`，设备须经 ADB
上报 online）。vbus 断电刷机的设备在刷机前是 powered_off（不上报 ADB），无法靠心跳改绑；
而派发准入 `device_host_drift` 与 prepare 分组都按当前 `host_id` 判定——「先改绑再刷」
因此卡死（15.94 的 7 台 LX3 误绑在 15.58，须改绑到 15.94 才能刷）。本单元补齐这条人工路径。

- 新增 `backend/services/device_rebind.py`：`rebind_device(db, *, device_id, target_host_id,
  reason, actor_id, actor_username, request)`。行锁 device（`FOR UPDATE`）+ 锁内复检，
  闭合「复检→写入」窗口（与退役家族同锁序）。前置：
  - 设备在役（`retired_at IS NULL`），否则 409 `DEVICE_RETIRED`；
  - 目标主机存在（否则 404 `HOST_NOT_FOUND`）且在役（否则 409 `HOST_RETIRED`）；
  - 设备无活跃 Job、无 ACTIVE 租约（否则 409 `DEVICE_HAS_ACTIVE_WORK`）——不顺带中止在跑
    测试，也不把租约从一台主机抽走。
  只改 `host_id`，**不改写 `status`**（心跳所有）、不删历史行。幂等：目标即当前归属时
  原样返回、不重复审计。审计 fail-closed（`record_audit(strict=True)`，action=`rebind_device`，
  details 含 `serial`/`from_host_id`/`to_host_id`/`reason`）。
- 路由 `POST /api/v1/devices/{device_id}/rebind`（`require_admin`，
  `response_model=ApiResponse[DeviceOut]`，返回 `ok(_device_out(device, db))`）。
- Schema `DeviceRebindIn{host_id, reason}`（均 `min_length=1` + 去空白校验）。
- 前端：`types.ts` 增 `DeviceRebindRequest`；`devices.ts` 增 `devices.rebind(id, body)`
  （`unwrapApiResponse<Device>`）。无页面入口（与 `releaseLease` 同惯例，运维经 API 调用）。
- 错误用结构化 `detail={"code", "message"}`（前端按 code 判定），经 `ServiceError` 统一
  handler 翻译为 404/409。

本单元只改控制面与前端类型，**不改 Agent 代码输入集**，期望 digest 不变，无需 Agent 发布。

## Alternatives

- 直接改库（`UPDATE device SET host_id`）：绕过审计与前置校验，不留事件真源；Owner 选定
  新增审计端点（PR）而非临时改库。
- 复用退役服务的 `_assert_no_active_work`：该函数带「仅欠账租约可退役」的 D5.8 例外并在
  退役事务内释放欠账租约——改绑不应释放租约，语义不同，故改绑自带独立的 `_assert_idle`
  （任何 ACTIVE 租约都拒绝），不复用。
- 让心跳支持 powered_off 改绑：心跳只认 ADB 上报的设备，powered_off 设备不在上报清单里，
  改心跳判据会污染「归属按最新心跳」的语义；人工 admin 入口是更小的闭合。
- 端点内校验「serial 在目标主机槽位映射里」：会把通用改绑端点耦合到 vbus 槽位表；槽位
  在位是刷机前的运维前置（已单独核验），不进通用端点。

## Verification

本机生产控制面同机；测试经 `scripts/run_pytest.py`（cgroup 内存硬顶）跑隔离测试库。

- `backend/tests/api/test_device_rebind.py`（10 条）：admin 门禁 403；改绑成功（写 host_id、
  status 不变、审计单条 `rebind_device` 含 from/to/reason）；设备/目标主机不存在 404；
  设备退役 / 目标主机退役 / 活跃 Job / ACTIVE 租约 409（各自 code）；幂等同归属无审计；
  host_id/reason 空白 422。10 passed。
- 相邻回归：`test_devices.py` + `test_device_retirement_api_2962.py` + `test_device_rebind.py`
  + `test_devices_bulk_swipe_trail.py` 合计 49 passed（路由新增与 schema 导入未破坏既有端点）。
- `npm --prefix frontend run type-check`（`tsc --noEmit` 双项目）通过。
- `python scripts/run_gates.py check:quick`（16 gates）通过。
- `python scripts/run_pytest.py backend/tests/api/ -q`：1636 passed, 1 failed。唯一失败
  `test_ai_assistant_endpoints.py::TestConfigEndpoints::test_connection_test_refuses_fast`
  与本改动无关——在干净基线 worktree（HEAD `5e12ddd13`，不含本单元）上单跑同样失败
  （`httpx.InvalidURL: Invalid port: ':1]'`，本机 httpx 版本对硬编码 URL 的解析差异），
  属环境敏感的存量失败。

## Revisit

- 若后续出现「带欠账租约的设备也需改绑」的真实诉求，再评估是否引入类似 D5.8 的例外；
  当前一律拒绝 ACTIVE 租约。
- 批量改绑（多设备一次性）暂无诉求；如需要，按退役批量同款「逐台独立事务」形态扩展，
  不在本单元预置。