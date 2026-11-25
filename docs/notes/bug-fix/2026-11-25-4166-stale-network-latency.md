# 设备断网后清空陈旧 network_latency（#4166）

Status: implemented
Class: bug-fix

## Decision

区分「本拍未采样网络（降频跳过 / 离线 / 旧版 Agent）」与「本拍已实际执行 ping 且失败（无网）」：

1. **Agent 侧（`backend/agent/heartbeat_thread.py`）**：
   - 慢指标 `_absorb_slow_metrics()` 对电量/温度/版本仍保留「单键瞬态 `None` 不覆盖旧缓存」的保守合并语义，但将 `network_latency` 归入 `_AUTHORITATIVE_NONE_SLOW_KEYS`：在设备 `adb_connected=True` 且本轮至少有一项慢指标有效时，将实测 `network_latency`（有网为浮点 ms，断网为 `None`）权威写入缓存，并在心跳 `devices[]` 中附带 `network_latency_probed: true`。
   - 若本轮全部 4 项慢指标均为 `None`（如重启后系统服务未就绪）或设备未连通，直接短路返回，不改缓存也不置 `network_latency_probed`。
   - 非采样（降频）拍透传缓存的 `network_latency` 与 `network_latency_probed` 标记，防止控制面 30s 硬件快照降采样窗口跳过清空拍。
2. **控制面侧（`backend/api/routes/heartbeat.py`）**：
   - `network_latency` 非 `None` 时照常写入；为 `None` 且 `network_latency_probed is True` 时显式将 `device.network_latency` 清空为 `None`（若原值已为 `None` 则不触发多余写库）。
   - 未携带 `network_latency_probed` 的旧版 Agent 或未探测拍保持原 `_update_if_not_none` 行为，向后兼容。

## Alternatives

- **控制面直接对所有 `network_latency=None` 无条件置 `NULL`**：会与未采样拍/离线拍/未传该键的旧 Agent 冲突，导致离线瞬间或降频拍误清数据；否决。
- **把 `network_latency` 改为魔数（如 `-1`）表达无网**：污染数值类型语义且需同步改前端展示、统计与历史查询；否决。

## Verification

- `./scripts/project_python.sh scripts/run_pytest.py backend/agent/tests/test_device_slow_metrics_sampling.py backend/agent/tests/test_device_discovery.py tests/test_internal_ip_leak_allowlist.py -q` → `68 passed`
- `unset TEST_DATABASE_URL && TESTING=1 JWT_SECRET_KEY=test-secret ./scripts/project_python.sh scripts/run_pytest.py backend/tests/api/test_heartbeat.py backend/tests/api/test_heartbeat_device_log_noise_2960.py -q` → `36 passed`
- `./scripts/project_python.sh scripts/run_gates.py check:quick` → `16 gates` 全绿（`schema-at-head` 因测试工作树未设 `DATABASE_URL` 按预期 WARN 跳过）
- Required CI checks：随 PR 创建后由 GitHub Actions 运行（pending）

## Revisit

- 本修复需同时部署控制面并完成 Agent 热更新后，断网设备才会在下一轮慢采样周期（默认 `STP_DEVICE_INFO_SAMPLE_INTERVAL_SECONDS=1800s` 或回线首拍）清空历史残留的 `network_latency`。
- 若业务希望断网状态在分钟级更快反映，可另行调整 `STP_DEVICE_INFO_SAMPLE_INTERVAL_SECONDS` 或独立拆分网络探测频率。
