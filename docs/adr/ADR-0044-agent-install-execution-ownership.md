# ADR-0044：Agent 安装的执行归属——RunConsole 自持，SAQ 不再持有安装

- 状态：**Accepted** v1.0（2026-09-15，owner 裁决：按「本质问题」把安装从作业窗口里摘出来；由 [#2220](https://github.com/DUElost/stability-test-platform/issues/2220) 触发，现场证据见该单；D3/D4 在实施中两次修正：先是发现「活动登记在结束时清空」会使状态读不到结果，改为 DB 来源；现场复跑又发现 `host.extra` 会被心跳重建覆盖，故最终定为「审计 = 持久证据」）
- 优先级：P1
- 目标里程碑：M7
- 日期：2026-09-15
- 决策者：平台研发组（owner 裁决，2026-09-15）
- 标签：Agent 安装, RunConsole, SAQ, 作业超时, 状态语义, #2220
- 关联：[ADR-0025](./ADR-0025-run-console-and-command-execution.md)（RunConsole 基础能力 §8/§9，安装复用它）、[ADR-0027](./ADR-0027-control-plane-horizontal-scaling.md)（多实例；console 仍为实例本地）、[ADR-0021](./ADR-0021-script-content-alignment-gate.md)（维护窗口/升级门禁）、[#2225](https://github.com/DUElost/stability-test-platform/pull/2225)（最小修复：CANCELED ≠ FAILED，本 ADR 保留其判据）

## 1. 背景

城市 B 站点（238）首次接两台真机时，一台安装被 **SAQ 作业窗口**掐掉，而报告写成「安装失败」：

```text
FAIL install.s5.install role=agent $.agents [agent_install_failed]
backend_error.log:
  File ".../saq/worker.py", line 368, in process
      result = await asyncio.wait_for(asyncio.shield(task), job.timeout if job.timeout else None)
  TimeoutError
```

事实清单（读现行代码得到，不是推测）：

1. `POST /hosts/{id}/install` **已经先**起 RunConsole（`start_install_agent_runconsole`），它自带：
   实例本地进程组、行级实时日志（socket.io room `console:{id}`）、落盘 replay、取消、
   `run_key` 串行（`install:{host_id}`，冲突即 409 + 现有 id）、终态保留 1h
   （`STP_RUN_CONSOLE_TERMINAL_RETENTION_SECONDS`）。
2. 随后才入队 SAQ 作业 `install_agent_task`（`timeout=900, retries=0`），而该作业的工作是
   `run_install_agent_sync` → `wait_install_agent_runconsole`：**纯粹等待**，唯一的实质副作用是
   结束时写 `host.extra.agent_installed[_at]` 与 `install_agent` 审计。
3. `/install/status` 从「SAQ 状态 + console 快照」合成；`log_path` 取自 SAQ result
   （而 console 侧有稳定可推导的 `log_file_path(run_id)`）。
4. 前端不使用这个状态接口（只用 POST 返回的 `room` 订阅实时日志）。
5. 于是「谁拥有安装」在两处分裂：执行归 console，**生命周期与终态判定归作业窗口**。
   慢目标机（首次 `apt update` + 装 `nfs-common`）超过 900s → 作业被取消 → 报告失真；
   同时一个 worker 槽被整段占用 15 分钟。

窗口调大/可配只把问题推迟（更慢的目标机仍在窗口外，且更久占住 worker）；#2225 把
CANCELED 与 FAILED 分开报，解决了**读数**，没解决**归属**。

## 2. 决策

- **D1（执行归属）**：Agent 安装以 **RunConsole 为唯一 owner**——起进程、串行、日志、取消、
  终态都在它这一处；安装没有「作业窗口」这个概念。
- **D2（触发路径）**：`POST /hosts/{id}/install` = 校验 → 起 console → 写
  `install_agent_request` 审计 → 返回 `{console_run_id, room, log_path, status}`。
  **不再入队 SAQ 作业**：删除 `install_agent_task` 与 `wait_install_agent_runconsole`
  的等待职责（连同 `saq_key`）。同主机并发仍由 console 的 `run_key` 拒绝（409 + 现有 id）。
- **D3（状态落库 = 审计，不是 host.extra）**：
  - **开始**：路由写 `install_agent_request` 审计（`details.console_run_id`）——内存里的
    「活动运行」注册表在安装结束/进程重启后就没；审计是 append-only，才是持久证据；
  - **结束**：console 的 `on_complete(run)` 回调写 `install_agent` 审计（`ok` / `rc` /
    `console_status` / `log_path` / `console_run_id`），并维护 `host.extra.agent_installed[_at]`
    （该键在心跳的 keep-list 里，能穿过 `extra` 重建；`status == "SUCCESS"` 才置位）。
    回调在 console 的终态快照之后执行（`run_console._finalize`），失败只记日志、不影响清理。
  - **为什么不用 host.extra 存运行态**：心跳会按 allowlist **重建** `extra`，控制面侧写进去的
    裸键会在 ~20 秒内被静默抹掉（238 现场实测：`install_console_run_id` / `last_install` 落库后
    随即消失）；这也与 ADR-0040 D2「禁 Host.extra 裸键、信号走显式列」一致。
- **D4（状态接口）**：`/install/status` 取三级来源（权威顺序）：
  1. 有活动运行 → console 实时快照（`console_found=true`）；
  2. 无活动运行 → 读该 Host 的最近一次 `install_agent_request` 与 `install_agent` 审计：
     结果审计不早于请求审计 → 回放该终态（`console_status` / `exit_code` / `log_path`）；
  3. 请求存在但没有更新的结果 → `lost`：控制面重启或结果未及落库，调用方按取消处理；
  4. 两者都无 → `idle`（这台主机没跑过安装）。
  返回 `console_status` / `console_found` / `log_path` / `exit_code`；删除 `saq_key` 与
  SAQ 状态字段（`status` 改为 console 派生摘要：`idle|running|succeeded|failed|canceled|lost`）。
- **D5（判据保留与补充）**：#2225 的「CANCELED ≠ FAILED」保留；调用方（S5 `await_install`）
  再加一条——**`lost` 按「取消」报**：那是控制面重启/运行记录到期这类进程生命周期边界，
  不是脚本失败。
- **D6（明确不做）**：不给安装引入「可配窗口」（没有窗口可配）；不改 RunConsole 的
  in-process 生命周期（跨重启存活见 §5）。

## 3. 备选方案与权衡

- **只把窗口调大 / 变成可配**：治症状。慢目标机只会在更大的窗口外再失败，且窗口越长，
  卡住的作业占用 worker 越久（SAQ 并发 10，安装占一个槽 15 分钟已经是浪费）。
- **作业只做「starter」**（起 console 后立即返回，保留队列记录与 `saq_key`）：保留了
  「触发作业」的持久记录，但引入**双启动路径**与 busy 竞态（route 与 job 都可能起 console），
  且 `status=complete` 会立刻表示「已启动」，语义上更像误导。队列**记录**不是本需求，
  真需要「安装历史」应当由审计（D3 已有）承载。
- **把安装放到 API 进程之外**（`systemd-run --unit` 或独立 supervisor 进程）：能让安装
  跨控制面重启存活（今天重启即取消），并天然有独立日志归属；代价是引入第二套进程/日志/
  状态机制与清理语义，且要重新回答「谁归属哪个实例」（ADR-0027）。属于**另一个决策**，
  本 ADR 只把它列为终态候选（§5）。
- **保持现状 + 只改文案**（= #2225）：读数变准，但执行仍归作业窗口——再遇到更慢的目标机，
  仍然是「健康的安装被判失败」。作为过渡可接受，作为终态不可接受。

## 4. 影响

正面：
- 慢而健康的安装不再被任何窗口取消；安装的成败只由目标侧结果决定；
- 释放一个 SAQ worker 槽（安装全程不再占 worker）；
- 状态来源单一（console），`log_path` 不再依赖作业结果的内存留下物；
- 与现有其它能力一致：RunConsole 已是 dedup→Jira/备份/运维命令的通用执行面（ADR-0025 §9），
  安装回到同一模式。

代价与不变式：
- 删除 `install_agent_task`（3 处引用：路由、注册表、runbook），`/install/status` 与
  前端 `hosts.ts` 类型同步收紧（`saq_key` 移除、`console_found` 新增）；
- **同主机安装串行**不变（`run_key`）；**取消语义**不变（显式 cancel 与 backend 重启仍取消）；
- **审计**不变（请求侧 + 结果侧两条都在），`agent_installed` 标记的产生时机不变（终态回调）；
- 安装 live log / replay / room 订阅全不变。

## 5. 遗留与终态出口

- **控制面重启会取消在跑的安装**（in-process console，ADR-0025 §9 的既有边界）：本 ADR 让
  它在报告里如实呈现为「取消」（D5），但不续跑。要跨重启存活需独立 supervisor（§3 第三项），
  未立单，触发条件是「现场出现重启打断安装且需要续跑」。
- **多实例（ADR-0027）**：console 仍是实例本地语义；若将来多实例部署安装，需要按 console
  registry 做 owner 路由（本 ADR 不引入）。
- **#2220 里的双 ansible 进程观察**：去掉「等待者」这一层后若再现，说明另有第二条执行路径，
  应在实施后的现场复跑里复查（本 ADR 不预设结论）。

## 6. 实施与验证（本 ADR 的钉子）

- 路由不再入队（单测：触发安装只起 console + 写请求审计；busy → 409 + 现有 id 不重复起）；
- `on_complete` 落 `install_agent` 审计与 `agent_installed`（单测：SUCCESS 置位、FAILED/CANCELED
  不置位且审计如实）；
- `/install/status`：返回 `console_found` 与可推导的 `log_path`（单测：记录在/不在两条路径）；
- S5 `await_install`：CANCELED → `agent_install_canceled`；`lost` → 同样按取消报
  （单测，含「不得因记录消失而报失败」）；
- **现场复跑（238）抓到的自伤**：首版把运行态写进 `host.extra`，心跳按 allowlist 重建
  `extra` 会在 ~20 秒内抹掉它——状态接口改读 `audit_logs`；
- runbook 与相关文档同步（`docs/linux-agent-ansible-runbook.md` 的调用链描述）；
- 现场复跑：238 上重跑一次 Agent 接入，确认安装不再受窗口影响、报告与实时日志一致。
- **取消入口**（2026-09-16 补，[#2255](https://github.com/DUElost/stability-test-platform/issues/2255)）：
  把 D1 里 console 自带的 cancel 暴露到安装链上——`POST /hosts/{id}/install/cancel`（管理员）
  无在跑安装时 409 `NO_INSTALL_IN_PROGRESS`（仍落 `install_agent_cancel` 审计），有则调
  `RunConsole.cancel` 并如实返回 `canceling`/`not_canceled`；终态仍由 `on_complete` 落库、
  端点不推断结果。现场缺口是「目标机侧 sshd 楔住时只能重启控制面收尾」，入口落地后不必再
  重启（单测：受理 / 无在跑 / 不可发起；前端面板与 hook 同步，热更新行不适用）。
  取消在 UI 里是独立终态（「已取消」+ info 提示 + 汇总单独计数），不落“失败”——
  238 现场端到端验证抓到过反例，已在同一 PR 修正。
