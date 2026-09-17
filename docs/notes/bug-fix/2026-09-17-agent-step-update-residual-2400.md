# Agent 侧停发 `step_update` + 接线守卫补 agent→server 判据（#2400 残余）

Status: implemented
Class: bug-fix

## Decision

#2421 把 `step_update` / `step_log` / `run_update` / `report_ready` 的**服务端与前端**
两侧死半删掉，但 agent 侧生产端没跟上：`backend/agent/socketio_client.py` 仍在
`_emit("step_update", …)`（一个专用方法 + `send()` 里的路由分支），而服务端
`AgentNamespace` 已无 `on_step_update` —— python-socketio 对无 handler 的事件
**静默丢弃**（无错误、无日志），通道看起来是通的。

三条收口：

1. **agent 停发**（issue 验收 ① 的「二者择一」中选删除）：删掉 `send_step_update()`
   （全仓**零调用**的死方法）与 `send()` 的 `step_update` 路由分支，模块 docstring
   与 `requirements.txt` 注释同步。**不**选「服务端恢复 handler」：消费端已在
   #2421 删除，而 agent 的真实步骤状态现在走 HTTP（`/agent/jobs/.../steps`），
   恢复一个无人消费的通道正是 #2400 要消除的形态（两端一起接，或一起删）。
2. **夹具与断言同步**（验收 ②）：`tools/dev/fake_agent.py` 的 `AGENT_EMIT_EVENTS`
   去掉 `step_update`；`tests/test_dev_fake_agent.py` 的钉住断言同步。该元组自述
   「= `socketio_server.AgentNamespace` 的 `on_*` 集合」，此前与实现不符。
3. **守卫扩 agent→server**（验收 ③）：`tests/test_realtime_wiring_contract.py` 增
   **判据 5**——agent 侧 `_emit("<name>", …)` 字面量集必须落在 `AgentNamespace`
   的 `on_<name>` 集内，或进带理由的豁免表（当前为空；豁免表也判过期）。

选「生产代码的字面量」而不是夹具元组作为扫描面：夹具是 dev 辅助，且它的
`job_status` 项在生产 agent 里根本没有发射点（服务端有 handler）——两者是不同的
契约，用夹具当全集会让判据跟着辅助代码漂移。

## Alternatives

- **服务端补 `on_step_update`**：否决。消费端（前端 case、服务端转发）已删；
  补齐只会复活一条无人消费的通道，且 agent 的真实状态已走 HTTP。
- **保留 `send_step_update()` 但标记 deprecated**：否决。零调用 + 服务端无 handler
  = 双死代码；留着它正是「事件目录看起来是通的」这一现象的来源。
- **判据 5 只扫「agent 声明的事件常量」**：否决。agent 侧没有集中常量，实际发射点
  就是 `_emit` 字面量；扫常量会让「新加一个 `_emit("foo")`」漏网。
- **顺带清理 `send()` 这个 legacy 路由**：本轮不做。它现在没有调用方，但它是公开
  方法且不属于本单残余面；已登记 Revisit。

## Verification

- 判据 5 现状（实测扫描面）：agent 发射集 `{step_log, heartbeat}`、handler 集
  `{connect, disconnect, job_status, step_log, heartbeat}` → 绿；两条非空断言防
  扫描面塌陷（各 ≥ 2）。
- **反例构造（先证伪再采信）**：
  - A：把 `_emit("step_update", …)` 加回 agent 侧（**即修复前的状态**）→
    `test_every_agent_emit_has_a_server_handler` **FAILED**；
  - B：把服务端 `on_heartbeat` 改名 → 同一条 **FAILED**（证明 handler 侧真的在比对）。
  恢复后守卫文件 **10 passed**。
- 实测命令与结果：
  - `env -i PATH="$PATH" PYTHONPATH=. python -m pytest backend/agent/tests/ -q` →
    **2126 passed**（agent 套件自足，无需 env）；
  - `python -m pytest tests/test_dev_fake_agent.py tests/test_realtime_wiring_contract.py -q`
    → **26 passed**；
  - `python -m pytest tests/ -q` → 1351 passed, 1 failed：失败项仍是
    `test_script_seed_governance.py::test_new_seed_migrations_deactivating_versions_check_references`
    （**主线既有红灯**，与本单无关）；
  - `python -m ruff check`（改动文件）→ All checks passed；
  - `python scripts/run_gates.py check:quick` → **[OK] check:quick (10 gates)**。

## Revisit

- **动态转发路径对判据 5 不可见**：`_emit(msg_type or "message", …)` 走变量，扫描
  只看字面量。要新增通道仍须写出字面量（判据才看得见）；若将来出现「全部事件名
  都由变量拼出」的写法，本判据需升级到 AST 常量传播，否则会静默失效。
- **`send()` legacy 路由已无调用方**：删掉 `step_update` 分支后它只剩 `log` /
  `heartbeat` 两条分支，而这两条也都有专用方法。是否整块退役（连同
  `AgentSocketIOClient` 的「Drop-in replacement」兼容承诺）应另单裁决。
- **反方向的同族缺口**：`job_status` 在服务端有 handler、生产 agent 侧没有发射点
  （只在 dev 夹具里）——与判据 5 对称的「服务端 handler 无生产者」当前无门禁；
  若将来还有同类漂移，按判据 5 的同一形态加判据 6。
- `tests/` 目录不在 `check:quick` 的 ruff 扫描集内（gate 只扫 `backend/ tools/
  scripts/`），现存 `tests/test_backstop_attribution.py` 等 3 处 lint 债因此长期不可见；
  是否把 `tests/` 纳入扫描属门禁范围裁决，不在本单。
