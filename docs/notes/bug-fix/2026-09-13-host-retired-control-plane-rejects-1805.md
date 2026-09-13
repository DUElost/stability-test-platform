# ADR-0038 ④ 切片二：退役主机执行/配置类拒绝面（#1805）

Status: implemented
Class: bug-fix

## Decision

ADR-0038 §2 D5「执行/配置类动作对退役主机拒绝」的本切片落地（与 #1876 的
「派发 fatal 归位」切片互补；claim 切片由并行 Execution 承担）：

1. **统一门禁新增退役判据**：`host_upgrade_gate.begin_host_upgrade` 在获取维护
   窗口**之前**检查 `host.retired_at` → 新增 `HostRetiredError`
   （code=`HOST_RETIRED`）——UI 热更新、Agent 升级门禁、批量脚本 `--direct`
   三条入口共用此收口点（ADR 点名的共享收口点之一），拒绝路径不占窗口。
   入口映射：`hosts.py` 热更新路由与 `agent_api.py` `_raise_upgrade_gate_http`
   均 409 + code；`batch_hot_update.py` 走既有 `HostUpgradeGateError` 分支。
2. **路由级拒绝**：`POST /hosts/{id}/install` 与 `PATCH /hosts/{id}/watcher-admin-state`
   新增退役 409；`POST /plan-runs/hosts/{id}/reload-config` 补 **host 存在性
   404**（矩阵行 4 的「零校验点」）与退役 409，且拒绝时不 emit。
3. **预检 SSH 守卫**：`precheck/sync.py` 的 `sync_host_via_hot_update` 与
   `push_mismatched_scripts` 对退役主机 `return False, "host_retired"`——
   覆盖准入 Phase A「脚本校验先于分类器终检」的 SSH 触碰窗口。
4. **批量脚本默认靶过滤**：`batch_hot_update.py` 的 ONLINE 选靶叠加
   `Host.retired_at IS NULL`（活体退役仍为 ONLINE，status 不构成豁免）。

本切片不含：claim 收口（并行在途）、scan/archive 回收类 `skipped_retired`
语义、AI 助手 `reload_agent_config` 工具面、`emit_agent_control` 通用策略。

## Alternatives

- **在 `emit_agent_control` 做集中式命令策略**：放弃——D5 明确数据回收类
  （scan/archive/日志尾读）应放行，集中式黑名单一旦漏判会误伤回收类；
  按调用点收口（reload-config 两处入口）语义更可控。
- **reload-config 只沿用「判 ONLINE」**：放弃——D4 之下退役活体恰恰是
  `status=ONLINE`，status 不构成豁免；必须叠加 `retired_at IS NULL`。
- **把守卫放在路由而不是门禁服务**：放弃——ADR D5 点名 `begin_host_upgrade`
  为共享收口点，三条入口仅在服务层收口才能保证协议一致（路由级保留仅为
  错误码映射与更早的廉价拒绝）。
- **安装路由先判退役再探依赖**：未采纳（保持 501 依赖探测优先的既有顺序），
  测试用 `shutil.which` 打桩跨过依赖探测；顺序本身不影响退役拒绝语义。

## Verification

- 目标测试：`test_host_upgrade_gate.py` + `test_precheck_sync.py` +
  `test_dedup_jira_endpoints.py` → **64 passed**；`test_hosts.py::TestRetiredHostControlPlaneRejects`
  → **3 passed**（hot-update / install / watcher，均以「ONLINE + retired_at」
  验证 status 不豁免）。
- **反例实证（逐点 mutation，四红一绿）**：分别移除门禁退役前置、install
  路由判据、reload-config 判据、预检 sync 守卫 → 对应用例全部转红
  （3 failed + 1 failed），恢复后全绿（清 `__pycache__` 后复跑）。
- 既有用例适配：`test_dedup_jira_endpoints.py` 两处 reload 用例补建 host 行
  （新存在性校验的前置），未改断言语义。
- `scripts/run_gates.py check:quick`：见 PR（7 门禁）。
- 未覆盖：`batch_hot_update.py` 默认靶过滤无独立单测（脚本无测试基座），
  以门禁兜底与代码审阅覆盖；`socketio_server.py` 按上述 Alternatives 不动。

## Revisit

- 剩余切片（issue #1805 逐条对照）：scan/archive 扇出与回收类
  `skipped_retired`（含 `iter_plan_run_scan_hosts` 调用点）、claim（并行
  Execution `feat-1805-claim-retired-slice`）、AI 助手 `reload_agent_config`
  工具面、182d4e-F7 十场景全量 mutation。
- 若未来新增经 `emit_agent_control` 的执行/配置类命令（非回收类），需同步
  纳入本切片的收口清单，或在 emit 层引入显式命令分类表（届时以 ADR §2.1
  矩阵行 4 为准）。
