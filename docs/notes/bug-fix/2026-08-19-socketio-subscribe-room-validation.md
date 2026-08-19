# SocketIO on_subscribe room 校验（ADR-0029 v2.3 D）

Status: implemented
Class: bug-fix

## Decision

`DashboardNamespace.on_subscribe`（`backend/realtime/socketio_server.py`）增加两层校验，room 不合法则不 `enter_room`（记 WARNING）：

1. **格式白名单**：`_ROOM_PATTERN` 限定合法形态 = 后端 emit 端全集——
   `job:{int}` / `run:{int}`（均指 `job_instance.id`，Agent `step_log` 的
   `run_id` 与 `job_id` 同值）/ `plan_run:{int}` / `console:con-{hex}`。
   `agent:` 是 `/agent` namespace 内部房间（namespace 隔离），dashboard
   订阅无意义，不入白名单。
2. **实体存在性**（`_dashboard_room_exists`）：`job:`/`run:` → `job_instance`
   行；`plan_run:` → `plan_run` 行；`console:` → `RunConsole` 进程内 run
   （run 终态后 `_runs` 条目保留，可查）。查询失败 fail-closed——无法证明
   房间有效就不放行，客户端重连会重试。

**修复边界（防止误当安全洞升级）**：本层**不做归属过滤**——REST 面本就
允许任意登录用户读任意 run，实时通道不设更严门槛。G13 定性 = ①P2 前置
一致性（列表过滤与事件不过滤的割裂）②健壮性（room 零格式/存在性校验，
可无上限堆积任意房间条目）。不是越权安全洞。

## Alternatives

- **实体存在性也查归属**（订阅者 → 实体的 owner 关系）：被否决——REST
  面无此限制，加在实时通道会造成 REST/SocketIO 双套权限语义，且 P2 的
  列表过滤本就会在应用层收窄可订阅对象。
- **存在性校验失败 fail-open**：被否决——校验目的正是防无效房间堆积，
  fail-open 等于校验形同虚设；订阅被拒只影响推流（可重试），不阻塞主路径。
- **console: 校验放宽到任意字符串**（只查前缀白名单）：被否决——`con-`
  前缀本身即 RunConsole 生成形态，放宽只给任意 room 堆积留口子。

## Verification

- `backend/tests/realtime/test_dashboard_subscribe.py`：30 用例
  （格式白名单参数化 22 例 + 实体存在性 2 例 + on_subscribe 行为 6 例，
  fake server 记录 enter_room 调用），连同既有 realtime 套件 54 passed。
- 前端核对：`useSocketIO.parseWsUrl` 是唯一 `emit('subscribe')` 构造点，
  四种形态（`job:`/`run:`/`console:`/`plan_run:`）全部落在白名单内；
  console id 唯一生成源为 `RunConsole.start()`（`con-` + hex），REST
  下发后前端原样订阅，无不带前缀的合法 room。

## Revisit

- P2 前端列表过滤落地时，若决定「实时通道也按视图收窄」（当时判定为
  割裂，属一致性项而非安全项），此处再加归属校验，并同步 REST 面语义。
- RunConsole 未来若改变 run_id 生成形态（如去掉 `con-` 前缀），须同步
  `_ROOM_PATTERN` 与前端 URL 匹配；两处都有测试可回归。
- **per-sid room 数量上限**（2026-08-20 决策者复核）：格式 + 存在性只
  消除了「零校验」那半，客户端仍可加入全部 94 个 plan_run:/job: 房间，
  「无上限」那半只是被收窄。当前规模无害；待 P2 前端订阅模式定型后再
  评估是否需要 per-sid 上限，不在 P2 范围内做。
