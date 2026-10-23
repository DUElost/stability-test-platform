# B6-U6：解除结果、欠账与人工放行界面（#3731 / #3723 v1.7.9 §3.7）

Status: implemented
Class: feature

- 日期：2026-10-20
- 关联：Refs #3731、Refs #3723；ADR-0061 D1、D4.4、D5.7、D5.9
- 基线：origin/main 2d437bc31（已含 U2 #3734、U3 #3748）

## Decision

1. 新增只读端点 `GET /api/v1/devices/{device_id}/return-debts`。
   - `DeviceReturnDebtOut` 只暴露条目 id、专项、来源类型、来源 job id、原因、建立时间；
     `evidence` 刻意不进模型，避免把运维原文带到界面。
   - 鉴权与设备详情一致（`get_current_active_user`）；设备不存在返回 404
     `DEVICE_NOT_FOUND`；只返回 `state=open`，按 `created_at` 升序并以 id 作为同事务并列
     的稳定次序。
   - 查询落在 `backend/services/device_return.py:open_debts_for_device_sync`，路由只做
     存在性校验与响应编排（add-api-endpoint SOP）。
   - 响应形状登记进 `tests/test_api_response_shape_contract.py` 的
     `DeviceReturnDebtOut ↔ DeviceReturnDebtItem` 配对，并加入受保护读端点清单。

2. 前端展示口径集中到 `frontend/src/components/device/deviceReturn.ts`：
   - 解除结果六值映射为已确认、已执行未验证、失败、回读失败、未执行、未完成；
   - 专项归还状态 returned/debt/unverified → 已确认/欠账/未验证；
   - 来源 job/init_check/discharge/override/stock → job/init 检查/清偿/人工放行/存量初始化；
   - 设备级聚合按欠账 > 未验证 > 已归还；`return_state` 为空返回「未初始化」，
     任一已登记专项缺记录按未验证（与后端 `device_aggregate_return_state` 同口径）；
   - 所有未知机器值原样透出，不做 `throw` 或兜底成已确认。

3. 设备页展开行新增 `DeviceReturnPanel`：
   - 展示设备归还状态、逐专项状态/来源/更新时间，以及按需拉取的未关闭条目；
   - 查询键 `deviceKeys.returnDebts(deviceId)`，仅在展开时挂载；
   - 人工放行入口只在 `isAdmin` 时渲染。

4. 人工放行对话框（`DeviceReturnOverrideDialog`）：
   - 专项可多选或勾选「全部专项」；原因类别固定四值下拉；证据引用必填；
   - 全部专项提交 `families: null`，多选提交专项数组；证据引用 trim 后提交；
   - 成功后展示关闭条目数、是否释放欠账租约、剩余条目数；父组件同时失效
     `['devices']` 列表与 `['devices', id, 'return-debts']`，刷新状态与条目。

5. 运行详情设备抽屉：`DeviceMatrixItem` 增加 `disarm_results`（后端 `plan_run_devices`
   投影直接取 job 列），抽屉按专项展示结果与原因；空/缺省显示「无解除记录」，
   未知机器值原样显示。

6. 派发预览：`PlanRunPreview.warnings` 补类型并在 `DispatchCockpit` 渲染；
   `device_return_debt` 行显式展示序列号与「需清偿或人工放行后才能派发」。

7. 激活前空态：`return_state` 为空、条目列表为空、`disarm_results` 为空均为正常展示；
   组件测试与真实浏览器均覆盖，不出现异常边界。

未改动 Agent、`backend/agent/contracts/device_return.py` 或 `device_return.py` 的写路径；
与 U5（PR #3747）的文件交叉仅为 `backend/tests/api/` 与 `docs/notes/feature/` 目录，
新增文件名互不重叠（已只读核对 PR 文件清单）。

## Alternatives

1. 抽屉数据改调 `GET /plan-runs/{id}/jobs` 自行按 `job_id` 找 `disarm_results`。
   放弃：为打开一个抽屉拉整轮 jobs，且把已有投影字段藏到第二个请求里；在矩阵投影
   增加一个 nullable 字段更小、更一致（`JobInstanceOut` 早已有该字段）。
2. 设备归还状态由后端新增聚合字段返回。
   放弃：ADR-0061 D1.4 的优先级是纯函数，前端已有 `return_state` 原始事实；再加一个
   派生字段会形成第二处口径，且本单元不触碰设备 schema。
3. 把未关闭条目并入 `GET /devices/{id}`。
   放弃：设备详情是高频轮询面（设备页 10s 刷新），条目只在展开时看；独立只读端点
   可按需加载，也符合 v1.7.9 对接口形状与鉴权的明确要求。

## Verification

前端（工作树 `frontend/`）：

```sh
npx tsc --noEmit
# exit 0

npx vitest run
# Test Files 152 passed (152); Tests 1333 passed (1333); exit 0
```

后端（隔离 PG；未加载生产 env、未连接生产库）：

```sh
env -i PATH="$PATH" PYTHONPATH=. TESTING=1 JWT_SECRET_KEY=b6-isolated-test-only \
  ./scripts/project_python.sh scripts/run_pytest.py \
  backend/tests/api/test_device_return_debts_u6_3731.py \
  tests/test_api_response_shape_contract.py backend/tests/api/test_read_api_auth.py -q
# 94 passed; exit 0

env -i PATH="$PATH" PYTHONPATH=. TESTING=1 JWT_SECRET_KEY=b6-isolated-test-only \
  ./scripts/project_python.sh scripts/run_pytest.py \
  backend/tests/api/ backend/tests/services/test_device_return_u3_3728.py -q
# 1463 passed; exit 0

./scripts/project_python.sh scripts/run_gates.py check:quick
# [OK] check:quick (16 gates); exit 0
```

真实浏览器（testing.md §4；隔离环境）：

- 隔离 PG：`docker run postgres:16`，库名 `stp_u6_test`，地址 `172.17.x.x:5432`；
  用 `backend/scripts/init_dev_db.py` 迁移建库并建 admin，seed 脚本写入 1 台设备、
  1 个 Plan、1 个 PlanRun/Job 与 2 条未关闭欠账。
- 后端 `127.0.0.1:18080`（`ENV=dev`，显式 DATABASE_URL；未读 `.env.backend`），
  前端 `vite dev` `127.0.0.1:5174`（代理到 18080）。取证前用
  `curl http://127.0.0.1:5174/src/components/device/DeviceReturnPanel.tsx` 确认返回
  本分支源码（含 `device-return-panel`）。
- headless Chromium（`~/.cache/ms-playwright/chromium-1237`）逐项断言通过：
  1) 设备展开行：`欠账`、`来源：job`、`来源：人工放行`、两条条目、`来源：job #1`，
     且页面文本不含 seed 的 evidence 原文；
  2) 放行对话框：空提交显示三条必填错误；选择开关机 + flashed + flash-job-1 后
     显示「已关闭 1 条欠账 / 未释放欠账租约 / 剩余 1 条」，关闭后条目列表刷新为 1 条，
     设备仍为欠账；
  3) 运行详情抽屉：显示未完成、已执行未验证、回读失败与原因；
  4) 派发预览：`device_return_debt` 告警显示 `U6-SERIAL-0001` 与
     「需清偿或人工放行后才能派发」。
- 截图：[设备展开](2026-10-20-b6-u6-browser-evidence/01-devices-expanded.jpg)、
  [放行校验](2026-10-20-b6-u6-browser-evidence/02-override-validation.jpg)、
  [放行结果](2026-10-20-b6-u6-browser-evidence/03-override-result.jpg)、
  [抽屉解除结果](2026-10-20-b6-u6-browser-evidence/04-run-drawer-disarm.jpg)、
  [预览告警](2026-10-20-b6-u6-browser-evidence/05-dispatch-preview-warning.jpg)。

## Revisit

- 专项名称映射目前是前端的五个已登记 family；新增专项时若不补 `FAMILY_LABELS`，
  界面会原样显示机器值（不报错），但会失去中文标签。
- 设备级聚合按当前五个 family 判断缺记录；若后端登记表扩面，需同步
  `REGISTERED_FAMILIES`，否则旧设备可能显示为已归还。
- 浏览器脚本为一次性取证脚本（隔离库 ID/账号固定），未入库；复跑按本 Note 的
  环境准备步骤重建隔离库并重放断言。
- 本单元可先于激活合入；激活后应观察真实 `return_state` 与新端点返回是否符合空态约定。
