# 所有升级入口复用统一门禁与维护窗口（#1249 / R14-F03）

Status: implemented
Class: bug-fix

## Decision

本质问题：ADR-0021 D7/D8 的「先排空再升级」协议此前只完整实现在 UI 热更新
路由（`hosts.py:525-801`）；Ansible `update_agent.yml` 直接 rsync + 重启
（无门禁、无 abort、不持窗口），`batch_hot_update.py --direct` 也没有 abort
能力（`--include-active` 甚至会带活跃 Job 直接重启）。维护窗口
（`host.maintenance_until`）是唯一能同时挡住派发
（`plan_dispatcher_sync.py:162`）与 claim（`agent_api.py:397`）的互斥面（#960）。

修复 = **单一实现 + 三条入口共用**：

1. 新增 `backend/services/host_upgrade_gate.py`：活跃 Job 汇总 / abort 排空
   （`abort_jobs_for_host` + 轮询）/ 维护窗口持有，异常表达拒绝原因；
   `hosts.py` 热更新路由改为调用它（语义不变：409/504/维护冲突）。
2. 新增 agent-auth 端点（Ansible 接入面，`agent_api.py`）：
   - `POST /api/v1/agent/hosts/{id}/upgrade-gate`：默认有活跃 Job 即 409；
     先占维护窗口再检查活跃任务，显式 `abort_running_jobs=true` 时在窗口内 abort 排空；
   - `POST .../upgrade-gate/release`：按 holder 释放（幂等、不误清他人窗口）；
   - 审计 `upgrade_gate_acquire/release`，细节在控制面审计日志。
3. `update_agent.yml`：解析目标机 `.env` 的 `HOST_ID`/`API_URL`（可用 `-e`
   覆盖）→ 申请门禁（`uri` + `X-Agent-Secret`）→ 只有 HTTP 200 才继续；
   正常与 `rescue` 路径都释放窗口，异常退出靠 TTL 过期兜底。
   **控制面不可达 = fail-closed**（拒绝升级，不绕开互斥直接 rsync）。
4. `batch_hot_update.py --direct` 改用共享实现：`--abort-running-jobs` 真正
   可用；`--include-active` 不带 abort 时由门禁拒绝（与 HTTP 模式语义一致），
   不再存在「带活跃 Job 直接重启」的路径。
5. ADR-0021 D8 增一条适用面扩展注记（未改变任何语义）。

**#1824（审计 F03）互斥顺序修正**：先提交维护窗口，再查询活跃 Job 或启动
abort/drain。窗口与 claim 共用 Host 行锁，已先行 claim 的 Job 会被后续检查
看到；稍后的 claim 被已提交窗口阻止。并发维护冲突在检查/abort 之前返回，
避免第二个升级请求干扰已有窗口中的任务。拒绝、超时及异常均先 rollback
失败事务，再释放自己持有的窗口；释放失败保留原异常并记录日志，TTL 兜底。

维护窗口获取与释放的 `FOR UPDATE` 查询强制 `populate_existing`，不能把
锁前缓存的 Host ORM 对象当作锁后事实。释放要求 holder 严格匹配，不会清除
继任者或无 holder 的窗口。仅修复现有互斥实现，不改变 TTL 与升级入口契约。

## Alternatives

- **检查/排空之后才占窗**——#1824 否决：最后一次活跃检查与占窗之间仍可
  派发并 claim；锁必须覆盖检查与排空，而不只是文件传输。

- **B：主机本地只拒绝**（读 agent 本地活跃任务后失败）——否决：不能 abort、
  不持窗口，派发/claim 在升级期间照旧，验收「与 API 热更新互斥语义对齐」不成立；
- **C：控制面本地 CLI（直接连 DB）**——否决：与 API 同源但要求 Ansible
  runner 能连生产 DB；README 明确支持「Linux 运维环境」执行，该场景会退化；
- **D：Ansible 更新整体收敛到 API 热更新（退役自身同步）**——方向级变更，
  且 API 入口需要 admin 凭据，超出本 issue；留作 Revisit；
- **A2：新增独立 ops token 鉴权**——否决：playbook 已持有 `AGENT_SECRET`，
  新增一套 credential 只是把面换成另一个面，收益低；
- **门禁放行开关（控制面不可达时跳过）**——否决：安全协议默认 fail-closed，
  跳过即回到本 issue 要修的状态；若确有需要，另起 issue 走裁决（Revisit）。

## Verification

#1824 本次验证：

- 隔离 testcontainers PostgreSQL 测试覆盖提交窗口早于首次检查、冲突不触发
  abort、检查/abort/drain 异常清理、失败事务 rollback、两会话旧 Host 缓存
  不覆盖窗口、迟到清理不误删新 holder、既有 API 成功/拒绝/超时路径。
- `python -m pytest backend/tests/services/test_host_upgrade_gate.py backend/tests/services/test_host_maintenance.py backend/tests/api/test_upgrade_gate_api.py -q`
  → **38 passed**。
- `python -m pytest backend/tests/api/test_agent_api_watcher.py backend/tests/services/test_plan_dispatcher_device_validation.py -q`
  → **53 passed**（真实 claim 与 dispatcher 维护态拒绝回归）。
- `python scripts/run_gates.py check:quick` → **7 gates 通过**；变更文件 Ruff
  与 diff check 通过。既有 Starlette 弃用警告不影响结果。
- 未读取生产配置、未运行生产 DB 诊断、未对 Agent 主机升级或重启。

此前 #1249 的历史证据（不代表本次重跑）：

- `pytest backend/tests/services/test_host_upgrade_gate.py
  backend/tests/api/test_upgrade_gate_api.py -q` → **16 passed**（隔离
  testcontainers PG）：门禁获取/释放、默认拒绝活跃 Job、abort 排空
  （PENDING 终态化）后占窗、RUNNING 排空超时不留窗、holder 不匹配不误清、
  未知主机 404、错误 secret 401；UI 路由成功/失败路径都释放窗口；
- `pytest tests/test_update_agent_playbook.py tests/test_install_agent_artifacts.py
  tests/test_rsync_host_local_protection.py -q` → **22 passed**（含新增
  playbook 门禁静态断言：fail-closed、200 才放行、abort 开关默认关闭、
  正常与 rollback 双释放）；
- `ruff check` 四个后端改动文件 + `ansible-playbook --syntax-check` → 通过；
- `python scripts/run_gates.py check:quick` → 7 gates 全绿。

未完成（pending）：

- 真实双机验证（控制面 + 隔离 Agent 主机）：有活跃 Job 被 409 拒绝、
  `-e agent_abort_running_jobs=true` 走 abort 排空、窗口期间派发/claim 被跳过、
  升级后窗口释放——本机为生产控制面宿主，不做跨机破坏性验证。

## Revisit

- agent secret 是共享凭据（ADR-0035 过渡态），本单新增了「abort + 占窗」的
  特权操作面——建议纳入 R02 安全联审；若 ADR-0035 落地每主机凭据，端点鉴权
  随之切换；
- 若出现「控制面短暂不可达导致计划内升级窗口被迫取消」的运维痛点，重议是否
  提供显式的受控旁路（须留痕 + 审计），而不是默认放行；
- 维护窗口 TTL（900s）按单机分钟级升级设定；若未来出现单批超长升级，需要
  扩展 TTL 或心跳续期（当前 `serial` 批次内各主机独立持有窗口）；
- `batch_hot_update.py` HTTP 模式未改动（其经 API 天然受协议保护）；direct
  模式的 `--include-active` 语义收紧已在 help 与 Note 明示。
