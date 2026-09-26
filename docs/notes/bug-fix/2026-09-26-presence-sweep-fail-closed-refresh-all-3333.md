# presence 全量 sweep 收口：CLI 误跑拒之、合法入口补齐（#3333）

Status: implemented
Class: bug-fix

## Decision

**① 全量 sweep 必须 fail-closed；② 补一个合法的「立即全量重采」入口。** 触发背景（#3315
部署实跑）：CLI 独立进程跑 `run_sweep` 时，verify RPC（`call_agent_rpc`）走 backend 进程内
socketio 长连接，48 台全 `AgentNotConnectedError`——但 CLI 返回**形似成功**
（`hosts_verified=48/rows=2496`），副作用是 `host.script_packages_mode` 全被 None 打脏、
账本按 `agent_offline`（state=unknown）落库并推进 `checked_at`；又因
`stability_script_presence_sweep_timestamp` 是 `/metrics` 拉取期从库里 `min(checked_at)`
现算的，垃圾轮还会把 `StabilityScriptPresenceSweepStale` **喂成新鲜**（真 sweep 已死两天
时 CLI 一跑即静音告警）。

实现：

1. **就绪判据**（`backend/realtime/socketio_server.py` 新增 `agent_rpc_ready()`）：
   本进程 `_sio` 与 `_agent_ns` 都已初始化才为真。判据放在 socketio 侧而不是
   「试着调一次再兜底」——CLI 进程的失败形态是**沉默**的（RPC 抛的是
   `AgentNotConnectedError`，业务层读起来就是「机器离线」）。
2. **`run_sweep` 前置守卫**（`services/script_presence.py`）：未就绪即抛
   `SweepNotReadyError`，位置在**任何写动作与任何 RPC 之前**（`sweep_id` 生成之前），
   报错文案带上合法替代入口。CLI 从「静默打脏」变成「明确拒绝」。
3. **`POST /api/v1/script-presence/refresh-all`**（`require_admin` + 60s 进程内节流）：
   在 backend 进程内跑一轮完整 `run_sweep`，返回汇总（hosts/hosts_verified/rows/
   orphans_removed/六态计数 + `uncovered_active_versions`）。响应模型
   `ScriptPresenceFullSweepOut` 无 SPA 消费方 → 形状契约里具名认领
   （`_MODEL_UNREGISTERED`），同 PR 把 `routes/script_presence.py` 纳入 opt-in 台账
   （三个端点已是具名模型，盲区为空集）。
4. **文档**：`control-plane-deploy` SOP §6 坑行由「CLI 全量 = 静默打脏」改写为
   「= 被拒（fail-closed）」+ 新入口指引，§7 校准记录追加一行；单机
   `POST /refresh?host_id=` 语义不变。

## Alternatives

- **靠「CLI 里 RPC 全失败」的返回值判脏并回滚**：已经太晚——`_persist_modes` 与
  `_persist` 先于汇总执行，账本行与 host 列都已落库；且「全失败」与「真离线机队」
  不可区分（判据本身就不成立）。
- **在 CLI 侧（`__main__`）加守卫**：只挡住当前已知的 CLI；服务层任何未来调用方
  （脚本、notebook、另一个 worker）都会重犯。守卫放 `run_sweep` 入口才是本质修法。
- **让 CLI 也能跑（起本地 socketio / 走 Redis 适配器）**：CLI 没有 ASGI 事件循环与
  agent 连接面，起一个只为自己用的 server 属于为错误入口补基础设施；合法入口是
  backend 进程内的 HTTP 端点。
- **`refresh-all` 异步化（SAQ 任务 + 轮询）**：全量实测是秒级到几十秒级（并发按
  `VERIFY_CONCURRENCY` 加界），当前量级下同步返回更简单；真超时可以拆任务，
  触发器见 Revisit。
- **节流做成跨实例（Redis）**：Redis 只承载队列与瞬时跨进程通信（AGENTS.md 硬不变量），
  且此处语义是「防连点」而非「全局单飞」——全局单飞由 cron 的 `SINGLETON_SCHEDULE_IDS`
  承担。进程内单调时钟足够。

## Verification

| 命令 | 结果 |
|---|---|
| `pytest backend/tests/services/test_script_presence.py backend/tests/api/test_script_presence_api.py -q` | **31 passed**（含新增 5 例） |
| `pytest backend/tests/services/ -q -k "precheck or verify"` | **81 passed**（1284 deselected） |
| `pytest backend/tests/realtime -q` | **133 passed** |
| `pytest tests/test_api_response_shape_contract.py -q` | **15 passed**（新增 opt-in + 具名豁免） |
| `scripts/run_gates.py check:quick` | **[OK] check:quick（16 gates）** |

新增用例形状：

- **拒跑且不写任何行**（服务级）：`agent_rpc_ready→False` ⇒ `SweepNotReadyError`；
  同时断言 ① verify RPC 一次都没发起（拒跑在**任何动作之前**）、② 账本零行
  （否则 `checked_at` 仍会推进、新鲜度告警仍会被静音）、③ `host.script_packages_mode`
  保持原值（原缺陷是打成全 None）。
- **`refresh-all` 需要 admin**：非管理员 403。
- **`refresh-all` 是全量语义**：stub `run_sweep`，断言调用不带 `host_ids`、返回汇总字段。
- **节流**：第二次立即调用 429；时间戳回拨出窗后放行。
- **拒跑不得被吞**：`SweepNotReadyError` 穿透到调用方（防以后包 try/except 做成静默 200）。

## Revisit

- **`refresh-all` 的墙钟**：现为同步返回（48 台 × verify RPC，并发加界）。若实测 P95
  超过网关超时，改为 SAQ 任务 + 轮询/推送（届时 `ScriptPresenceFullSweepOut` 补任务 id）。
- **节流窗口（60s）**：按真实使用回看；若运维需要「重采→等待→再采」的连跑节奏，
  评估改为参数化或按运行时长退避。
- **就绪判据的覆盖面**：`agent_rpc_ready()` 只看本进程 socketio/namespace。若将来
  出现「本进程就绪但注册表为空」的第三种形态（例如 ADR-0027 多实例把 sid registry
  关掉），判据需要加**注册表可达性**维度——当前 `socketio_redis_adapter=false` 的
  单实例部署下两者等价。
