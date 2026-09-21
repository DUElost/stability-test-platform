# Agent 技术设计

> **入口**：`python -m backend.agent.main`（开发）或 systemd `stability-test-agent`（生产）  
> **部署实操**：[`backend/agent/DEPLOY.md`](../../backend/agent/DEPLOY.md)

---

## 1. 职责

Linux 主机上的 **执行平面**：

1. 心跳与设备发现  
2. 拉取 PENDING Job → 执行 `pipeline_def.lifecycle`  
3. ADB 操作脚本 `script:<name>`  
4. Watcher：崩溃检测、AEE 拉取、log_signal 上送  
5. 本地存储：运行日志（SSD）、AEE（HDD）、归档 prune / spill  
6. SocketIO `/agent`：实时日志与状态  

---

## 2. 目录结构（开发仓）

```
backend/agent/
├── main.py                 # 主循环、子系统启动
├── config.py               # 路径常量
├── job_runner.py           # 单 Job 执行编排
├── pipeline_engine.py      # lifecycle 状态机
├── pipeline_runner.py
├── job_session.py          # Job 生命周期 + Watcher 绑定
├── api_client.py           # 控制面 REST
├── socketio_client.py      # /agent SocketIO
├── heartbeat_thread.py
├── device_discovery.py
├── adb_wrapper.py
├── lease_renewer.py
├── step_trace_uploader.py
├── patrol_heartbeat_uploader.py
├── patrol_recovery.py
├── artifact_uploader.py
├── log_archiver.py         # SSD prune（方案 C）
├── local_disk_monitor.py   # HddSpillMonitor
├── registry/
│   ├── local_db.py         # SQLite WAL
│   └── script_registry.py
├── watcher/                # ADR-0018
├── aee/                    # 路径 B 拉取、reconciler
├── scripts/                # 可执行脚本树
└── tests/
```

生产安装布局见 `DEPLOY.md`（`/opt/stability-test-agent/`）。

---

## 3. 主循环（`agent_application.py`；`main.py` 只剩入口）

`backend/agent/main.py` 现在只做 dotenv + logging + `run_agent_application()`（#736 已把它拆薄，
其余按主题分文件）。真实生命周期是 `AgentApplication.run()` 的**五个阶段**（顺序即契约）：

1. `initialize` — 读 env（`API_URL` / `HOST_ID` / `POLL_INTERVAL` …）、进程身份与注册、
   `AdbWrapper` + ADB server、SocketIO 连接与**早到 control 缓存**、LocalDB / ScriptRegistry 等
   本地存储；磁盘与 watcher 子系统也在此起步（见下条）  
2. `start_background_tasks` — 心跳线程（主机指标、archive 指标、outbox 积压）+ host 控制面  
3. `register_handlers` — SocketIO control handler 注册 + 早到命令重放  
4. `start_job_plane` — 线程池 / recovery 面：`fetch_pending_jobs` → `JobRunner.run`  
5. `run_loop` — claim 循环（阻塞到退出）；`graceful_shutdown` 在其 `finally` 里跑
   （由 `shutdown_agent_runtime` 承担，不在 `AgentApplication` 内联）  

**子系统门控各不相同**（`bootstrap_subsystems.start_disk_and_watcher_subsystems`，别一锅端）：
`LogArchiver` **无条件**启动；`EventUploader` 与 `LocalDiskMonitor`（HddSpill）只按
**共享存储根** `cifs_root` 门控（#2845：无根时按未配置跳过并记 `event_uploader_skipped cifs_root_empty`）；
只有 `LogWatcherManager` + `OutboxDrainer` + artifact uploader 这一段看 `watcher_subsystem_enabled()`
（§5）。旧文档写的「HddSpill 当前与 `STP_WATCHER_ENABLED` 耦合 — 已知债」**已不成立**，别再照它去修。

---

## 4. Pipeline 执行

与 [`01-execution-pipeline.md`](./01-execution-pipeline.md) 一致。

| 组件 | 说明 |
|------|------|
| `ScriptRegistry` | 解析 `script:<name>` + version → nfs_path |
| `pipeline_engine` | init/patrol/teardown；subprocess + env `STP_*` |
| `step_trace_uploader` | 批量 HTTP 上报步骤 |
| `patrol_heartbeat_uploader` | patrol 周期聚合（ADR-0022） |

**环境变量注入脚本**：`STP_DEVICE_SERIAL`、`STP_STEP_PARAMS`、`STP_JOB_ID`、`STP_LOG_DIR` 等。

---

## 5. Watcher 子系统（ADR-0018）

**开关**（`backend/agent/watcher/enable.py::watcher_subsystem_enabled`）：
`STP_WATCHER_ENABLED` **与** `STP_WATCHER_PLAN_DEFAULT` **默认都是 `true`**，取 `global_on or plan_default`
——**两键任一为真即启用，只有两者都显式 `false` 才整体关闭**（要真关必须两个都关，
按「默认灰度、显式开」理解会把 fleet 的 watcher 状态判反）。
本开关只管**子系统要不要起**；单个 job 要不要挂 watcher 另走
`job_wants_watcher(run, globally_enabled, plan_default)`：`watcher_policy.enabled is False` 可逐 job 显式退出，否则在两键之上再按「是否 Plan 执行」判定。

```
DeviceLogWatcher
  ├── sources (inotify / 轮询)
  ├── LogPuller → HDD 事件目录（方案 C）
  ├── emitter → POST /agent/log-signals
  └── reconciler → 路径 B 批量拉取（STP_WATCHER_AEE_RECONCILE_ENABLED）
```

**JobSession**：Job RUNNING 期间 start/stop watcher；RESUME 重挂（ADR-0025 Sprint 1）。

路径契约：`aee/paths.py` — 默认 `mobilelog/`、`bugreport/`。

---

## 6. 存储与归档（方案 C）

| 子系统 | 职责 |
|--------|------|
| `log_archiver` | grace 后 **prune** SSD `logs/runs/{job_id}/` |
| `HddSpillMonitor` | HDD 超阈 → 最旧事件 copy 到 15.4 `devices/` |
| SocketIO `step_log` + 控制面 `log_writer` | 运行中实时日志（`GET /logs/query`、LiveConsole） |
| 控制面 `POST /agent/logs` | 事后经 SSH 读取 Agent 磁盘日志 |

**已移除**：tar 上送 15.4、`run_log_bundle` 注册、cycle 快照。

详见 [`2026-plan-c-storage-and-access.md`](./2026-plan-c-storage-and-access.md)。

---

## 7. 关键环境变量

| 变量 | 说明 |
|------|------|
| `API_URL` | 控制面地址 |
| `HOST_ID` | 与 DB `host.id` 一致（推荐 IPv4 点转横杠，如 `198-51-100-6`） |
| `AGENT_SECRET` | SocketIO 认证（生产必设） |
| `STP_WATCHER_ENABLED` | Watcher 总开关 |
| `STP_AEE_LOCAL_ROOT` | HDD AEE 根（默认 `/mnt/hdd/aee_events`） |
| `STP_AEE_NFS_ROOT` | 中心存储挂载点（upload / spill） |
| `ANDROID_ADB_SERVER_PORT` | WSL 联调：5039 |

---

## 8. 脚本目录

```
scripts/<name>/v<version>/<entry>.{py,sh}
```

扫描由**控制面** `POST /scripts/scan` 入库；Agent 通过 `nfs_path` 执行。  
开发：`STP_SCRIPT_ROOT` + 可选 `STP_SCRIPT_RUNTIME_ROOT`（WSL）。

详见 [`script-versioning.md`](../development/script-versioning.md)。

---

## 9. 测试

- `backend/agent/tests/`（52 文件）  
- 独立运行：`pytest backend/agent/tests/`

---

## 10. 热更新

- UI：主机管理 → 热更新  
- Ansible：`tools/ansible/playbooks/update_agent.yml`  
- 详见 `DEPLOY.md`
