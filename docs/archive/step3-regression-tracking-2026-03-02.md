# 第三步回归执行记录（2026-03-02）

## 已执行项

1. 前端类型检查
- 命令：`npm --prefix frontend run type-check`
- 结果：通过（`tsc --noEmit` 退出码 0）

2. 新 STP 核心后端测试
- 命令：`$env:TEST_DATABASE_URL=$env:DATABASE_URL; pytest backend/tests/api/test_agent_routes.py -q`
- 结果：通过（7 passed）
- 备注：存在 Windows + asyncpg 的 `ProactorEventLoop` 退出阶段 warning，不影响断言结果

3. pipeline_def 新模型约束测试
- 命令：`pytest backend/core/test_pipeline_validator.py -q`
- 结果：通过（3 passed）

4. 模板 stages 格式验证
- 命令：`pytest backend/api/routes/test_pipeline_templates_stages.py -q`
- 结果：通过（1 passed）

5. 编排接口 pipeline 校验
- 命令：`pytest backend/api/routes/test_orchestration_pipeline_validation.py -q`
- 结果：通过（2 passed）

6. Phase C 数据库状态只读校验
- 命令：`python backend/tests/e2e/validate_phase_c_state.py`
- 结果：`[SUMMARY] PASS`

7. `hosts/devices/heartbeat` 残留引用扫描 + 最小修补
- 执行前状态：`19 failed, 29 passed, 1 skipped`
- 主要问题：
  - `audit_logs` 缺表导致 `record_audit()` 直接中断主业务
  - 旧测试仍从 `backend.models.schemas` 导入 `Device/HostStatus` 等旧引用
  - 多处断言依赖“空库假设”和旧类型约束（如 `host_id`、422/400）
  - `hosts/devices` 状态判断存在 naive/aware 时间比较异常风险
- 修补后回归命令：`$env:TEST_DATABASE_URL=$env:DATABASE_URL; pytest backend/tests/api/test_hosts.py backend/tests/api/test_devices.py backend/tests/api/test_heartbeat.py -q`
- 结果：`48 passed, 1 skipped`

8. 修补后关键路径复验
- 命令：`$env:TEST_DATABASE_URL=$env:DATABASE_URL; pytest backend/tests/api/test_agent_routes.py -q`
- 结果：`7 passed`
- 命令：`$env:TEST_DATABASE_URL=$env:DATABASE_URL; pytest backend/core/test_pipeline_validator.py -q`
- 结果：`3 passed`
- 命令：`$env:TEST_DATABASE_URL=$env:DATABASE_URL; pytest backend/api/routes/test_pipeline_templates_stages.py -q`
- 结果：`1 passed`
- 命令：`$env:TEST_DATABASE_URL=$env:DATABASE_URL; pytest backend/api/routes/test_orchestration_pipeline_validation.py -q`
- 结果：`2 passed`
- 命令：`python backend/tests/e2e/validate_phase_c_state.py`
- 结果：`[SUMMARY] PASS`

9. `backend/tests/api` 全量回归（第三步本轮）
- 命令：`$env:TEST_DATABASE_URL=$env:DATABASE_URL; pytest backend/tests/api -q`
- 结果：`99 passed, 9 skipped`
- 说明：
  - 已完成 `tasks/templates/tools/workflows` 测试向新 STP 接口语义的定向迁移
  - `audit/results` 已补缺表降级路径，避免在新模型库上因旧表缺失导致 500
  - `users` 用例已通过；`conftest` 中固定用户夹具已改为“存在即复用”
  - 跳过项为 Windows 本地 `asyncpg + TestClient` 不稳定相关写路径用例（见未执行项 1）

## 未执行项清单

1. Windows 下被条件跳过的异步写路径用例
- 当前状态：`backend/tests/api/test_workflows.py` 与 `backend/tests/api/test_tools.py` 在 Windows 本地已条件 `skip`
- 原因：`asyncpg + TestClient + ProactorEventLoop` 在本地环境出现连接写阶段不稳定（`Event loop is closed`）
- 目标：在 Linux/CI 环境补齐等价验证并取消或收敛 skip

2. Linux 主机 F-3 实机切换验收
- 当前状态：未执行（需目标环境手动）
- 执行依据：`docs/phase-f3-agent-cutover-checklist.md`

3. 全量回归范围（非本轮）
- 未执行：`pytest backend/tests -q` 全量、前端单元测试全量、跨模块 e2e 流程
- 原因：本轮优先验证迁移后关键路径与可上线风险点

## 后续建议（按优先级）

1. 在 Linux CI 环境执行 `backend/tests/api` 全量并验证当前 9 个 skip 的可恢复性
2. 建立独立测试库并隔离数据（避免与本地开发库共享）
3. 在 CI 中固定 asyncio loop 策略，降低 Windows 本地 warning 噪音
