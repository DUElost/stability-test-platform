# dev 假 Agent 夹具固化：三件套 → `tools/dev/fake_agent.py`（#2402）

Status: implemented
Class: process

## Decision

把上一轮 B5 临时件（`hb.py` / `fake_agent.py` / `driver.py`，写在
`/home/debian13/stp-dev/.tmp_b5/`，未入 Git）固化成**一个** CLI：
`tools/dev/fake_agent.py`，子命令 `heartbeat | claim | step | complete | inject | serve`。

固化的不是便利，是**测试能力**：`#2369 / #2324 / #2133` 这类推送拓扑改动，改一次就要
重踩一轮夹具；不固化，「job 级 WS 渲染」就永远没有可回归入口（上一轮的结论正是靠
一次性临时件跑出来的，下一次执行无法复用）。

### 三条红线都做成了可判红的东西，而不是注释

| 红线 | 落地 | 守它的用例 |
|---|---|---|
| 只注册与回报，绝不执行脚本 | 模块不 import 执行类库；`execute_job`/`run_job` 等推送事件只回 ack | AST 级守卫（不得 import `subprocess`/`pty`/`shutil`/`asyncio`，不得出现 `os.system` 类调用） |
| 只打 dev 栈 | 默认 `127.0.0.1:18000`；`:8000`（本机生产控制面）**默认拒绝**，需 `--allow-non-dev-target` | 断言「被拒时一个 HTTP 都不发」（先判目标、后连网）——顺序写反就红 |
| 凭据/token 不外泄 | `AGENT_SECRET` 只从环境读；`fencing_token` 由唯一一处 `fencing_token_for()` 取用 | 断言 `step`/`claim` 的输出里没有 token 值，只有 `bool` |

反向红线同样写进 `--help` 与文档：**不得把真机 Agent 指向 dev `:18000`**（会把真机
设备/Job 事实写进 dev 库，两边事实源一起坏掉）。

### 上一轮 4 个坑，处理方式都进了代码或文档

1. dev 镜像无 `websocket-client`（生产 Agent 是 #1121 的 websocket-only）→
   `--transport auto` 先试 websocket、失败退 polling 并**打一条差异说明**；
   polling 会话约 5 分钟掉一次 → `serve` 带自愈重连（`--reconnect-limit`）。
   **不为此加依赖**：加 `websocket-client` 要动 lock 与镜像层，收益只是让夹具与生产
   拓扑一致，而 dev 单实例下 polling 已够用；差异写进文档比消掉差异便宜（备选做法见
   Alternatives）。
2. 容器内 `/app` 只读 → 日志默认 `/tmp/stp-fake-agent.log`。
3. `docker compose exec -d` 的进程随会话回收 → 文档给 `setsid nohup … </dev/null &`。
4. `fencing_token` 不在 API 响应里 → 收进一个函数，SQL 与「不打印」由同一条用例守。

另外两处是**本轮新踩的**，也一并固化：

5. `inject` / 注入文件的事件名走白名单（只允许 agent→server 的
   `step_log/step_update/job_status/heartbeat`），且 `inject` 在**建连之前**就拒未知事件
   —— 否则夹具会变成「任意事件转发器」，红线 1 名存实亡。
6. argparse 的 parent parser 会让 **subparser 用自己的 default 覆盖顶层已解析值**：
   `--base http://x:8000 heartbeat` 会静默退回默认端口，等于绕过红线 2。改成全局参数
   `SUPPRESS` + `parse_cli()` 统一补默认（`_global_defaults()` 一处），于是参数在子命令
   前后都合法，且显式值一定被尊重（用例覆盖两种位置）。

### 文档

`docs/development/local-development.md` 新增「开发期假 Agent 夹具」小节，含命令样例、
三条红线表、两个「不要当 bug 查」的差异，以及本单的验收点：**dev 冒烟要含 job 级 WS
渲染**（起假 Agent → 跑 1 设备 1 step 的 Plan → 不刷新页面即断言反映终态）。

## Alternatives

- **给 dev 镜像加 `websocket-client`**（issue 里 #2 的另一支）：本轮不做。它会改变
  夹具与生产的一致性方向（让 dev 更像生产）但没有对应门禁需求，代价是依赖/lock/镜像层
  三处联动；`--transport websocket` 已经留着，谁装了依赖就能直接用。
- **把三件套原样搬进仓库**：否决——一次性件的形状（`B5_*` 环境变量、写死路径、
  `globals()["_LAST_CMD"]`）没法复用，也正是「下一个人从头再踩」的原因。
- **写成 pytest fixture 而非 CLI**：否决。夹具要跨容器/跨窗口常驻并接收反向注入，
  pytest fixture 承载不了 `serve` 的生命周期；单测部分另有归属（红线用例）。
- **让 `verify_scripts` 直接返回「全对」**（最省事的过门禁方式）：否决。那会让夹具与
  派发门禁脱钩，测出来的「实时面」与真 Agent 不等价——复用生产的
  `verify_scripts_payload`（只读 + sha256）才既过门禁又不引入执行面。

## Verification

- `tests/test_dev_fake_agent.py` **16 passed**（`python -m pytest tests/test_dev_fake_agent.py -q`）：
  三条红线各正反两面 + 4 个坑的处理 + 参数两种位置都能解析。
  **红绿自证**：前两轮里这些用例确实以「实现有问题」的方式红过——子串式红线守卫把
  docstring 自身判成违规（改 AST）、argparse 覆盖导致 `:8000` 被静默忽略（改 SUPPRESS +
  `parse_cli`）、平台交替断言写反（改断言）；记录在此以免被当成「一次写对」。
- `ruff` 干净；`scripts/run_gates.py check:quick` 绿——**但 CI `lint` job 仍然红了第一次**，
  原因是 `tools/dev/check-internal-ip-leak.py`（#538/#550/#557）只在 CI 的 lint job 里跑、
  不在 `check:quick` 的 10 个 gate 内：我在夹具里把假主机写成了上一轮临时件里的真实内网 IP（此处按该工具
  自己的建议泛化成 `10.77.x.x`）。已改为 TEST-NET-1 文档保留段 `192.0.2.11`（假主机占真实内网
  地址既触发门禁、也可能与真机撞 IP），改后本地跑该工具 `3100 个文件无真实内网主机地址`。
  第二次红是**我自己写的这条 Note** 里留下了那个真实地址——门禁连 `docs/` 一起扫，而
  "记录踩了什么坑"天然会把坑里的值抄进来。泛化成 `10.77.x.x`（该工具给出的处置方式 ①）。
  教训两条，都记在这里：**① 「本地 quick 全绿」不等于「CI lint 会绿」**（gate 集合不同）；
  **② 本地复跑必须用 CI 的调用姿势**（`check-internal-ip-leak.py --check`，扫全仓含 `docs/`），
  我第一次只跑了不带 `--check` 的形态，于是"本地通过"是假信号。
- **未做真机/真 dev 栈冒烟**（诚实标注）：`heartbeat`/`serve` 会往共享 dev 库写入
  假主机与 3 台假设备，而此刻 dev 上有在窗验证（#2368 的 Run#409、#2404 handover）。
  在无冲突窗口里跑本条冒烟的命令已写进文档；本单不为了「跑过一次」去污染别人的现场。
  因此「工具能连上真实 /agent」这件事目前只由单元级证据支持（连接参数、auth 形状、
  事件名全部对齐 `backend/agent/socketio_client.py` 与
  `backend/realtime/socketio_server.py` 的 `AgentNamespace`），标 **pending**。

## Revisit

- 若 dev 栈加了 `websocket-client`：把 `--transport` 默认从 `auto` 改成 `websocket`，
  并删掉文档里「polling 与生产不等价」那条差异（差异消失，说明也该消失）。
- `inject` 的白名单目前等于服务端 `on_*` 集合；服务端新增 agent→server 事件时这里要跟着
  加，否则夹具造不出新面的数据。判据落点：`AGENT_EMIT_EVENTS` 与
  `socketio_server.AgentNamespace.on_step_log/on_step_update/on_job_status/on_heartbeat`
  的同步，适合在 #2400（实时通道两侧各半死）收口时一起做成一条契约断言。
- 上一轮的 `.tmp_b5/` 临时件仍在 `/home/debian13/stp-dev/`（未入 Git）。本工具覆盖其能力后
  应当删除，避免「两套假 Agent」并存——需要 dev 目录的操作者动手，我不越界删别人现场。
