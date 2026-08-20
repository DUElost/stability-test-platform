# 环境变量参考

> **最后更新**：2026-08-09  
> 模板权威源：`backend/.env.example`、`backend/agent/.env.example`、根目录 `.env.server.example`。  
> 本文只整理**常用/易踩坑**变量；完整清单以 example 文件为准。

---

## 1. 控制平面（后端）

| 变量 | 说明 |
|------|------|
| `DATABASE_URL` | PostgreSQL（async 驱动用 `postgresql+asyncpg://`；同步去掉 `+asyncpg`） |
| `REDIS_URL` | SAQ broker；开启 `STP_SOCKETIO_REDIS_ADAPTER` 时兼作 SocketIO pub/sub（**不**存业务数据） |
| `STP_SOCKETIO_REDIS_ADAPTER` | `1`=挂载 `AsyncRedisManager`（多实例 room fan-out）；默认 `0`（ADR-0027 P3-2） |
| `STP_SOCKETIO_REDIS_CHANNEL` | Redis pub/sub channel 前缀（默认 `stp-socketio`） |
| `STP_AGENT_SID_REGISTRY` | Agent `host_id` owner 登记；默认跟随 Redis adapter；`0`/`1` 可显式覆盖（ADR-0027 P3-3） |
| `STP_AGENT_SID_REGISTRY_TTL_SECONDS` | owner key TTL（默认 120） |
| `JWT_SECRET_KEY` | JWT 签名；生产必改 |
| `AGENT_SECRET` | Agent HTTP/SocketIO 共用密钥；与 Agent 侧一致 |
| `ENV` | `development` / `internal` / `production`。内网 HTTP 正式环境用 `internal`；HTTPS 才用 `production` |
| `AUTH_COOKIE_SECURE` / `AUTH_COOKIE_SAMESITE` | Cookie 策略；`production` 强制 secure + lax/strict（ADR-0024） |
| `STP_CSRF_ENABLED` | 浏览器 CSRF；生产/正式须开启 |
| `CORS_ORIGINS` | 前端 Origin 白名单（须与浏览器访问地址完全一致） |
| `STP_ALLOW_REGISTER` | 公开注册；生产默认关闭 |
| `STP_METRICS_AUTH_REQUIRED` | `/metrics` 鉴权（建议生产 `1`） |
| `STP_ENABLE_INPROCESS_SAQ` | `1`=进程内 SAQ Worker；`0`=仅 producer（enqueue），需外部 worker 同队列消费（ADR-0026 P0） |
| `DEVICE_SNAPSHOT_INTERVAL` | 心跳硬件字段降采样间隔秒（默认 30） |
| `STP_HEARTBEAT_INTERVAL_BASE` / `_MIN` / `_MAX` | 控制面建议 Agent 心跳周期（随在线设备数缓增） |
| `STP_LOG_RATE_LIMIT_BASE` / `_MIN` | 控制面建议每 host `step_log` 行速率（随设备数收紧；ADR-0026 P2-2） |
| `STP_COUNTER_RECONCILE_INTERVAL_SECONDS` | O(1) 计数器对账 sweep 周期（默认 300） |
| `STP_PLAN_ADMISSION_QUEUE_ENABLED` | `1`=V2 准入队列；默认 `1`。设为 `0` 会停用新派发（不会恢复已移除的 legacy inline dispatch）；存量 QUEUED 仍 drain。灰度见 [`../operations/adr-0026-admission-and-scale-gray-rollout.md`](../operations/adr-0026-admission-and-scale-gray-rollout.md)；`/health` 暴露 `admission_queue_*` |
| `STP_SCRIPT_ROOT` | 脚本扫描根；**必须显式设置**（未设 scan 返回 503，不再回落到 `STP_NFS_ROOT/scripts`）。开发：`<repo>/backend/agent/scripts` |
| `STP_SCRIPT_RUNTIME_ROOT` | 扫描机 ≠ 运行机时 Agent 侧脚本根 |
| `STP_NFS_ROOT` | Agent 上由 hot-update **镜像** `STP_AEE_NFS_ROOT`（旧脚本仍读此名）。控制面本机值**不下发**，也不再当脚本默认根 |
| `STP_AEE_NFS_ROOT` | **中心存储** 本机挂载点（**唯一文档化主键**）。控制面 + Agent 指向同一分享；路径字符串可不同 |
| `STP_AEE_CIFS_ROOT` / `STP_WATCHER_NFS_BASE_DIR` | **弃用别名**（计划删除，#213 E1）：仅当主键未设时回落并打 WARNING；新部署勿再配置 |
| `STP_FILE_SERVER_ADDRESS` | 共享存储健康页**左栏控制面**展示 IP（现 8.202）。**不是** CIFS 根 / UNC |
| `STP_AEE_SHARE_ADDRESS` | 健康页**右栏中心存储机**展示 IP。未设 = 与控制面同源（过渡期同机）；迁离 8.202 后必设（#205） |
| `STP_FILE_SERVER_NODE_JOB` / `STP_CONTROL_PLANE_NODE_JOB` | 左栏控制面 node exporter 的 Prometheus job（前者为旧名回落，默认 `file-server`） |
| `STP_STORAGE_NODE_JOB` | 右栏中心存储机 node exporter 的 Prometheus job（默认未设→同源；分源后必设，如 `storage-server`） |
| `STP_AEE_LOCAL_ROOT` | Agent **本机** L1 AEE 根（按机配置；hot-update **不下发**，见 #235） |
| `STP_EVENT_UPLOADER_PRUNE_LOCAL` | Agent only。上送成功后删本机事件目录并标 `PRUNED`（**默认 0**）。**禁止** fleet 同步（#217）：hot-update 仅下发 `_FLEET_ENV_KEYS` allowlist（`hot_update_env_overrides()`），本键不在列表中，控制面误设也不会进 payload。风险：CIFS 事后不可读时本地已无副本。灰度：单机改 Agent `.env` + `reload_config` |
| `STP_BACKEND_DEDUP_SCAN_PYTHON` / `_SCRIPT` | **仅控制面**：后端 merge/scan 工具路径（控制面本机；旧无前缀键 `STP_DEDUP_SCAN_*` 兼容回落 + WARNING，#295） |
| `STP_DEDUP_SCAN_PYTHON` / `_SCRIPT` | **仅 Agent**：Agent 侧 scan 工具路径（hot-update 经 `STP_AGENT_*` 源键写入；控制面不再读，#295） |
| `STP_AGENT_DEDUP_SCAN_PYTHON` / `_SCRIPT` | **仅控制面**：Agent 侧 scan 工具路径的源键，hot-update 写成 Agent 的无前缀键 |
| `STP_ADMIN_USER` / `STP_ADMIN_PASSWORD` | Compose 开发初始化管理员；**禁止**用于生产默认值 |

### Agent 协议门禁

| 变量 | 说明 |
|------|------|
| `STP_AGENT_MIN_VERSION` | claim 最低协议版本。**未设置时关闭门控**（旧 Agent 可继续 claim）。舰队升级后再显式设置（如 `2.0.0`），低于门槛返回 **426** `AGENT_UPGRADE_REQUIRED`。 |

滚动建议：先热更新 Agent → 再设置 `STP_AGENT_MIN_VERSION`。见 [`../operations/agent-version-and-hot-update.md`](../operations/agent-version-and-hot-update.md)。

---

## 2. Job / 租约超时（`backend/core/job_timeout_config.py`）

| 变量 | 生产默认 | 说明 |
|------|---------|------|
| `DISPATCHED_TIMEOUT_SECONDS` | 120 | PENDING 超时 → FAILED；**同 host 仍有 RUNNING 时不杀排队 PENDING** |
| `RUNNING_HEARTBEAT_TIMEOUT_SECONDS` | 900 | RUNNING 心跳丢失 → UNKNOWN |
| `PATROL_RUNNING_HEARTBEAT_TIMEOUT_SECONDS` | 300（dev 180） | patrol 阶段心跳窗口 |
| `PATROL_STALL_MULTIPLIER` | 3 | patrol stall 倍数 |
| `UNKNOWN_GRACE_SECONDS` | 300 | UNKNOWN grace 后释放租约并 FAILED |
| `ABORT_REAPER_GRACE_SECONDS` | 60 | abort ACK 超时 → UNKNOWN（租约仍保留） |
| `PRECHECK_QUEUE_STALE_SECONDS` | 90 | precheck SAQ 丢失后补 enqueue 窗口 |
| `PRECHECK_ACTIVE_STALE_SECONDS` | 180 | precheck worker 失联判定 |

兼容旧名：`RUN_DISPATCHED_TIMEOUT_SECONDS` / `RUN_HEARTBEAT_TIMEOUT_SECONDS`。

---

## 3. Agent

| 变量 | 说明 |
|------|------|
| `API_URL` | 控制平面地址 |
| `HOST_ID` | 须与 DB `host.id` 对齐（不可为 `0`；推荐 `198.51.100.6` → `198-51-100-6`） |
| `AGENT_SECRET` | 与控制平面一致 |
| `POLL_INTERVAL` | claim 轮询间隔（秒） |
| `ADB_PATH` / `ANDROID_ADB_SERVER_PORT` | WSL 联调端口须 `5039` |
| `STP_AEE_LOCAL_ROOT` | HDD AEE（默认 `/mnt/hdd/aee_events`） |
| `STP_WATCHER_ENABLED` | Watcher 子系统开关（默认 `true`） |
| `STP_DEVICE_LOG_EVENT_ENABLED` / `STP_EVENT_UPLOADER_ENABLED` | ADR-0028 DLE + EventUploader（默认过滤模型，见下）；控制面非空值经 hot-update fleet 同步（#218） |
| `STP_EVENT_UPLOADER_CONTINUOUS` | 方案 A：`0`=仅上传 `UPLOAD_PENDING`（过滤模型，默认）；`1`=上传全部 `LOCAL`（逃生阀） |
| `STP_EVENT_UPLOADER_PRUNE_LOCAL` | 上送成功后删本机目录→`PRUNED`（**默认 0**）。**勿**进 fleet（#217）；单机 `.env` + `reload_config` |
| `STP_LOCAL_DISK_SPILL_THRESHOLD` / `_TARGET` | HddSpill 触发/回落水位（%）；改阈值须**重启** Agent（configure 后不可热改） |
| `STP_STEP_LOG_STREAM` | `1`=pipeline 日志经 SocketIO 批推送；`0`=保持 no-op（ADR-0026 P2-2） |
| `STP_LOG_BATCH_MAX_LINES` / `STP_LOG_BATCH_FLUSH_MS` | step_log 批大小与定时 flush（默认 50 / 200） |
| `STP_AEE_NFS_ROOT` | **中心存储** 挂载点主键（upload / spill / merge 同一把）。过渡 UNC 在 8.202，目标 15.4/9.4 |

热更新会附带控制面 `pipeline_schema.json` 与 Agent `VERSION`（code revision）。  
热更新还会**行级合并**舰队级 `.env` 键（安装布局路径 + 控制面 `backend/.env` 中非空的 `STP_*` 等）；`HOST_ID`、`API_URL`、`ANDROID_ADB_SERVER_PORT` 等 per-host 键不同步。见 [`../operations/agent-version-and-hot-update.md`](../operations/agent-version-and-hot-update.md)。

---

## 4. 测试

见 [`.env.test.example`](../../.env.test.example) 与 [`testing.md`](./testing.md)。

| 变量 | 说明 |
|------|------|
| `TESTING=1` | conftest 自动设置；跳过 Redis/SAQ/Scheduler lifespan |
| `TEST_DATABASE_URL` | **仅**隔离测试库；生产机禁止指向业务库 |
| `ALLOW_SQLITE_TESTS=1` | 本地无 PG 时子集用例 |
| `JWT_SECRET_KEY` | 测试必备（例见 `.env.test.example`） |

---

## 5. 相关文档

- 方案 C 存储：[../design/2026-plan-c-storage-and-access.md](../design/2026-plan-c-storage-and-access.md)
- 存储角色与别称：[../design/2026-storage-roles-and-aliases.md](../design/2026-storage-roles-and-aliases.md)
- 执行协议：[../design/07-execution-protocol.md](../design/07-execution-protocol.md)
- 生产清单：[../production-minimum-deployment-checklist.md](../production-minimum-deployment-checklist.md)
