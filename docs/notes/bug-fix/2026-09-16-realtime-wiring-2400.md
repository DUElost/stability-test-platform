# 实时通道两端接线收口：删两侧死半 + 接线守卫（#2400）

Status: implemented
Class: bug-fix

## Decision

**方向：删，不接。** `job:` / `run:` 房间级实时推送与 `run_update` / `report_ready`
广播在 origin/main 上都是**两侧半死**——一端有另一端没有，运行时两端都不报错：

| 形态 | 证据（main `86e47dbd` 实核） |
|---|---|
| 有 producer 无 consumer | `broadcast_run_update` / `broadcast_report_ready` / `broadcast_job_log` / `broadcast_precheck_update` 零生产调用点，只有 `backend/tests/api/test_websocket.py` 直调自证「函数可用」 |
| 有 consumer 无 producer | Agent `step_log` 每行向 `job:{id}` / `run:{id}` 双投，而这两个房间**无任何订阅方**（`jobLogsSubscription` / `runLogsSubscription` 零生产调用点） |

选「删」而不是「接上」，因为**这两条需求都已经有别的东西在承担**：

- 行级 stdout 的实时面 = `log_writer` 落盘 → `GET /api/v1/logs/query`（REST 轮询），
  命令式输出 = `console:{runId}` 通道（ADR-0025 §9，`LiveConsole` 订阅
  `consoleSubscription`，是唯一被生产消费的实时日志通道，`run_console.py` 投递）；
- dashboard 的进展面 = 轮询（实测 dashboard 60s 一轮、dedup/log-events/test-case-results
  30s 一轮，`plan_run:{id}` 房间的 JOB_STATUS/PLAN_RUN_STATUS 是**真通**的，不在本单范围）。

「接上」等于新做功能（要给 progress/report 补调用点、把抽屉接到 job 房间、还要回答
扇出预算），不是本单能自证的；#2400 的证据只支持「现状不可维持」这一条。

**删除清单（上下游成对删，避免再次半边死）**：

- 服务端 `socketio_server.py`
  - `broadcast_run_update` / `broadcast_report_ready` / `broadcast_job_log`（后者连测试
    都没人调）；
  - `broadcast_precheck_update`：与 `backend/services/precheck/notify.py::_do_emit`
    的 `schedule_emit` **同形重复**，而 notify 那条才是被生产调用的（`precheck/state.py`
    / `runner.py`）。**事件本身不删**——前端 `PRECHECK_UPDATE` 消费位保留；
  - `on_step_update`：两个 emit 去掉后是空处理器（Agent 仍会发该事件，服务端无处理器
    = 静默丢弃，与今天的效果相同）→ 整个处理器删除；
  - `on_step_log`：去掉两处 emit，**保留** `log_writer.append_log_lines`（唯一去向）；
  - `on_job_status`：去掉 `job:{id}` 副本，保留 `plan_run:{id}`（唯一真通订阅）。
- 房间白名单：`_ROOM_PATTERN` 去掉 `job:` / `run:`，`_dashboard_room_exists` 的
  job/run 分支随之删除。该注释自称「合法形态 = 后端 emit 端全集」——删了 emit 端
  就必须同步，否则白名单允许订阅一个永远收不到消息的房间。
- 前端：`socketEvents.ts` 四个事件名 + 四个消息类型；`useSocketIO.ts` 的 EVENTS 白名单
  四项与 `job:` / `run:` 解析分支；`config/index.ts` 两个订阅工厂；
  `useRealtimeDashboard.ts` 的 RUN_UPDATE / REPORT_READY 分支。

**新增守卫 `tests/test_realtime_wiring_contract.py`**（落在 PR 路径：`pr-agent-tests`
跑 `tests/`，夜间全量不是唯一防线），三条判据一一对应三种漂移：

1. `broadcast_*` 每个定义必须有**生产**调用方（测试直调不算）；
2. `config/index.ts` 每个订阅描述符（camelCase 工厂 + SCREAMING_SNAKE 常量）必须有
   **生产**消费方；
3. `_ROOM_PATTERN` 里每个房间族必须在生产面有 emit 位（`f"<kind>:` 或
   `FLEET_DEVICES_ROOM`），且探针表与白名单**互为全集**。

每条都带非空钉子（`broadcast_*` ≥5、订阅描述符 ≥3、房间族 ≥2），防扫描面改名后塌成
恒真。

## Alternatives

- **A. 接上（给 dashboard 补 `broadcast_run_update`/`report_ready` 调用点、把设备抽屉
  接到 `job:{id}`）**：搁置。理由见 Decision——行级实时已有 console 通道，进展已由
  轮询兜住，「接上」是功能设计（扇出预算、UI 语义、report_ready 的语义归属都未定），
  不该由一条「契约漂移」的 bug 单顺手决定。要做得单独开单。
- **B. 只删一侧（前端删、服务端留 或 反之）**：否决。这正是半边死的成因本身。
- **C. 保留 `job:` / `run:` 白名单项，只删两端行为**：否决。会让「白名单=emit 端全集」
  的注释变成谎话，且客户端仍可订阅永远收不到消息的房间（该函数自己的注释就写着
  「订阅它只会堆积无意义 room 条目」）。
- **D. 保留 `broadcast_precheck_update`（与 notify 同形但它「看着像 API」）**：否决。
  同一事件两份 emit 实现正是漂移温床；被调用的那条（notify）才是事实。
- **E. 把 `step_update` 落库（顺手做成步骤状态追踪）**：不做。现状机以 job 级为准，
  步骤级状态没有消费方；要做先设计，别在删死通道的单里夹带新状态面。

## Verification

- **红绿差分**：守卫在未改动的 base（`git checkout` 回全部生产文件）上 **4 failed /
  2 passed**——红的正是三条判据 + 白名单钉子；改动后 **6 passed**。失败信息可读
  （「以下 broadcast_* 没有生产调用方…」）。
- **后端**：`python -m pytest tests/test_realtime_wiring_contract.py
  backend/tests/api/test_websocket.py backend/tests/realtime/ -q` → **115 passed**；
  `backend/agent/tests/test_step_log_batching.py` 带 `AGENT_TEST_ENV`
  （TESTING/JWT_SECRET_KEY/DATABASE_URL/TEST_DATABASE_URL，与 `run_gates.py` 同值）
  → **7 passed**（该用例原本断言「2 行 × 2 房间 = 4 次 emit」，现断言落盘唯一 + 零 emit）。
- **前端**：`npx vitest run`（全量）→ **901 例：899 passed / 2 failed**。两例失败为
  **本机存量环境差**（Node v24 vs CI Node 22 的 `PlanRunDetailPage` socket/抽屉两例），
  已在未改动基线上复现，与本单无关。改动面 `npx tsc --noEmit` / `eslint
  --max-warnings 0`（6 文件）通过。
- **仓库门禁**：`python scripts/run_gates.py check:quick` → `[OK] check:quick (10 gates)`。
- **未做**：未在真实浏览器/生产上验证推送面（本机无隔离前端环境）。删除的通道本就
  无生产消费者，风险面是「删多了」——由守卫的三条判据兜住其余在用的通道
  （device_update / dashboard_summary / job_status / plan_run_status / watcher_signal
  / precheck_update / console_* 全部保持有生产者且有消费者）。

## Revisit

- **将来要做步骤级实时日志**：三处一起接（emit 位 → 订阅工厂 → 房间白名单），守卫
  自然放行；设计上优先复用 `console:{runId}` 通道，而不是重新开 `job:{id}` 房间
  （后者要额外回答「谁订阅、按什么权限」）。
- **`step_update` 现在无人处理**：Agent 仍在发（`backend/agent/socketio_client.py`），
  服务端删掉处理器后即为静默丢弃。若将来要落库步骤级状态，先设计再接线——不要
  只把处理器加回来（那会重新变成「有 producer 无 consumer」）。
- **两条实时面未合并**：`console_log`（RunConsole 通道）与 plan-run 的 `logs/query`
  轮询是两套机制；本单只删死通道，不动它们。若要统一，属于方向级决策。
- **守卫的边界**：判据 3 只覆盖 `_ROOM_PATTERN` 的房间族与 `f"<kind>:` 探针；
  动态拼接的房间名（如 `room=some_var`）不在扫描面内——新增这类形状时需同步扩守卫。
