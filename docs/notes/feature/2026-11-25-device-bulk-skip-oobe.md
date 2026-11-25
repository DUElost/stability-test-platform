# 设备页批量快捷跳过 OOBE

Status: implemented
Class: feature

## Decision

在 `/devices` 底部批量操作栏「更多操作」菜单中，于「关闭滑动留痕」下方为 admin 新增「快捷跳过OOBE」按钮，直接复用脚本库中的 `oobe_skip` 脚本包。

1. **脚本复用（ADR-0051）**：控制面服务 `backend/services/device_skip_oobe.py` 从 `script` 表选取最新且已启用、带 `package_sha256` 的 `oobe_skip` 版本，合并其 `default_params` 并将快捷操作场景下的 `wait_for_device_seconds` 收紧为 `5`、`verify_retries` 收紧为 `1`（避免离线设备单台阻塞 120s+）。
2. **安全与通路**：`POST /api/v1/devices/bulk-skip-oobe`（`require_admin` + 批量上限校验 + `bulk_skip_oobe` 审计）→ 按 `host_id` 并发 `call_agent_rpc` → Agent control `skip_device_oobe`。Agent 侧**严格白名单**仅接受 `script.name == "oobe_skip"`，经 `resolve_script_path` 解析并校验站点包，以独立子进程执行包内 `oobe_skip.py`（主机内最多 8 并发），按脚本 stdout JSON 协议的 `success` 字段判定单台成败。
3. **结果口径与超时**：无 host / host 非 ONLINE / 设备状态为 `BUSY`（避免 `adb root` + `HOME` 键序打断在跑任务）计入 `skipped`；Agent 未连 / RPC 或脚本执行失败计入 `failed`；前端调用显式豁免默认 30s 超时（`NO_TIMEOUT`），toast 汇总「快捷跳过OOBE（vX.Y.Z）：成功 N · 失败 M · 跳过 K」。

## Alternatives

- **在 Agent 硬编码一套 OOBE ADB 命令**：与 `backend/agent/scripts/oobe_skip/oobe_skip.py` 双轨分叉，后续 OOBE 配方演进会漂移；否决。
- **通过创建临时 Plan / Job 派发 `oobe_skip`**：会制造大量一次性执行记录、依赖完整调度/准入链且占用租约队列，违背「设备页快捷操作」诉求；否决。
- **允许 `BUSY` 设备直接跳过 OOBE**：`oobe_skip` 会执行 `adb root` 重启 adbd 并触发唤醒/解锁/HOME 键序，会破坏在跑稳定性任务；因此对 `BUSY` 设备显式 `skipped`。

## Verification

- `./scripts/project_python.sh scripts/run_pytest.py backend/agent/tests/test_control_handler_736.py tests/test_api_response_shape_contract.py tests/test_frontend_api_types_sync.py tests/test_api_4xx_advice_has_real_exit.py tests/test_agent_import_boundary.py tests/test_inner_import_ratchet.py tests/test_god_files_ceiling.py -q` → `70 passed`
- `unset TEST_DATABASE_URL && TESTING=1 JWT_SECRET_KEY=test-secret ./scripts/project_python.sh scripts/run_pytest.py backend/tests/api/test_devices_bulk_skip_oobe.py backend/tests/api/test_devices_bulk_swipe_trail.py backend/tests/scheduler/test_audit_log_cleanup.py -q` → `22 passed`
- `unset TEST_DATABASE_URL && TESTING=1 JWT_SECRET_KEY=test-secret ./scripts/project_python.sh scripts/run_pytest.py backend/tests/api/test_devices_b2_1_pagination_facets_496.py -q` → `17 passed`
- `npm --prefix frontend run type-check && npm --prefix frontend run test -- --run DeviceBulkActionBar DevicesPage timeouts.test` → `tsc` 通过，3 test files / `44 passed`
- `./scripts/project_python.sh scripts/run_gates.py check:quick` → `16 gates` 全绿（`schema-at-head` 因测试工作树未设 `DATABASE_URL` 按预期 WARN 跳过）
- Required CI checks：随 PR 创建后由 GitHub Actions 运行（pending）

## Revisit

- 合入后须同时部署控制面并 **Agent 热更新**，Agent 侧才有 `skip_device_oobe` control 分支；且站点需已扫描并启用带 `package_sha256` 的 `oobe_skip` 版本。
- 若后续有大批量（数十台以上）一键跳 OOBE 的进度可视化需求，可升级为异步任务或增加结果明细抽屉；当前 toast 汇总与审计日志满足小批量运维场景。
