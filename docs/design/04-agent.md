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

## 3. 主循环（main.py）

1. 读 env：`API_URL`、`HOST_ID`、`POLL_INTERVAL`  
2. 初始化 LocalDB、ScriptRegistry  
3. 可选启动：Watcher、`LogArchiver`、`HddSpillMonitor`（当前与 `STP_WATCHER_ENABLED` 耦合 — 已知债）  
4. 线程池：`fetch_pending_jobs` → `JobRunner.run`  
5. 心跳线程：主机指标、archive 指标、outbox 积压  

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

**开关**：`STP_WATCHER_ENABLED`（默认 `false` 灰度）

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
| `HOST_ID` | 与 DB `host.id` 一致 |
| `AGENT_SECRET` | SocketIO 认证（生产必设） |
| `STP_WATCHER_ENABLED` | Watcher 总开关 |
| `STP_AEE_LOCAL_ROOT` | HDD AEE 根（默认 `/mnt/hdd/aee_events`） |
| `STP_AEE_CIFS_ROOT` | 上送 15.4 挂载点 |
| `ANDROID_ADB_SERVER_PORT` | WSL 联调：5039 |

---

## 8. 脚本目录

```
scripts/<name>/v<version>/<entry>.py|sh|bat
```

扫描由**控制面** `POST /scripts/scan` 入库；Agent 通过 `nfs_path` 执行。  
开发：`STP_SCRIPT_ROOT` + 可选 `STP_SCRIPT_RUNTIME_ROOT`（WSL）。

详见 [`CLAUDE.md`](../../CLAUDE.md) §脚本目录与扫描机制。

---

## 9. 测试

- `backend/agent/tests/`（52 文件）  
- 独立运行：`pytest backend/agent/tests/`

---

## 10. 热更新

- UI：主机管理 → 热更新  
- Ansible：`tools/ansible/playbooks/update_agent.yml`  
- 详见 `DEPLOY.md`
