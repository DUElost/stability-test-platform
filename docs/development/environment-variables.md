# 环境变量参考

> **最后更新**：2026-09-14  
> **运维模板权威源**：`backend/.env.example`、`backend/agent/.env.example`、根目录 `.env.server.example`。  
> **代码侧完整读取清单**：见文末[附录](#附录运行时读取清单自动生成勿手改)（自动生成 + 漂移门禁，#737）。  
> 本文其余章节只整理**常用/易踩坑**变量；模板未列的内部/开发变量以附录为准。

---

## 1. 控制平面（后端）

| 变量 | 说明 |
|------|------|
| `DATABASE_URL` | PostgreSQL（async 驱动用 `postgresql+asyncpg://`；同步去掉 `+asyncpg`） |
| `STP_DB_POOL_SIZE` / `STP_DB_MAX_OVERFLOW` / `STP_DB_POOL_RECYCLE` | 连接池容量（默认 `30` / `60` / `1800`）；**同步与异步引擎同源驱动**，改一处两侧同步（#1516） |
| `REDIS_URL` | SAQ broker；开启 `STP_SOCKETIO_REDIS_ADAPTER` 时兼作 SocketIO pub/sub（**不**存业务数据） |
| `STP_SOCKETIO_REDIS_ADAPTER` | `1`=挂载 `AsyncRedisManager`（多实例 room fan-out）；默认 `0`（ADR-0027 P3-2）。**启用前读 ADR-0027 清单第 6 条**：RunConsole 依赖功能的跨实例边界按 `STP_CONSOLE_REGISTRY` 状态区分（未启用时仍为单实例语义，#1114；启用后 `run_key` 互斥与 owner 登记跨实例生效，`read_log` 仍为 owner 本地限制，ADR-0027 v1.4） |
| `STP_SOCKETIO_REDIS_CHANNEL` | Redis pub/sub channel 前缀（默认 `stp-socketio`） |
| `STP_AGENT_SID_REGISTRY` | Agent `host_id` owner 登记；默认跟随 Redis adapter；`0`/`1` 可显式覆盖（ADR-0027 P3-3） |
| `STP_AGENT_SID_REGISTRY_TTL_SECONDS` | owner key TTL（默认 120） |
| `STP_CONSOLE_REGISTRY` | RunConsole 归属注册表：跨实例 `run_key` 互斥（fail-closed）+ owner 登记；默认跟随 Redis adapter；`0`/`1` 可显式覆盖（ADR-0027 P3-4，P1+P2+P3；`TESTING=1` 恒关） |
| `STP_CONSOLE_REGISTRY_TTL_SECONDS` | console 互斥/owner key TTL（默认 120，下限 30；续期间隔 = TTL/3） |
| `STP_CONSOLE_CONTROL_TICK_SECONDS` | console 控制 tick（消费取消请求；默认 1s，须小于取消等待窗） |
| `STP_CONSOLE_CANCEL_TTL_SECONDS` | 取消请求位/ack 的 TTL（默认 60，下限 10） |
| `JWT_SECRET_KEY` | JWT 签名；生产必改 |
| `AGENT_SECRET` | Agent HTTP/SocketIO 共用密钥；与 Agent 侧一致 |
| `ENV` | `development` / `internal` / `production`。内网 HTTP 正式环境用 `internal`；HTTPS 才用 `production` |
| `AUTH_COOKIE_SECURE` / `AUTH_COOKIE_SAMESITE` | Cookie 策略；`production` 强制 secure + lax/strict（ADR-0024） |
| `STP_CSRF_ENABLED` | 浏览器 CSRF；生产/正式须开启 |
| `CORS_ORIGINS` | 前端 Origin 白名单（须与浏览器访问地址完全一致） |
| `STP_ALLOW_REGISTER` | 公开注册；生产默认关闭 |
| `STP_METRICS_AUTH_REQUIRED` | `/metrics` 鉴权（建议生产 `1`） |
| `STP_API_DOCS_ENABLED` | `/docs` `/redoc` `/openapi.json` 开关，缺省开；白名单 `1/true/yes/on` 为开、其余值一律关。控制面对公网开放前置 `0`（G22）；脚本客户端走 `/auth/token` bearer 不受影响 |
| `STP_ENABLE_INPROCESS_SAQ` | `1`=进程内 SAQ Worker；`0`=仅 producer（enqueue），需外部 worker 同队列消费（ADR-0026 P0） |
| `DEVICE_SNAPSHOT_INTERVAL` | 心跳硬件字段降采样间隔秒（默认 30） |
| `STP_HEARTBEAT_INTERVAL_BASE` / `_MIN` / `_MAX` | 控制面建议 Agent 心跳周期（随在线设备数缓增） |
| `STP_LOG_RATE_LIMIT_BASE` / `_MIN` | 控制面建议每 host `step_log` 行速率（随设备数收紧；ADR-0026 P2-2） |
| `STP_COUNTER_RECONCILE_INTERVAL_SECONDS` | O(1) 计数器对账 sweep 周期（默认 300） |
| `STP_LOG_LEVEL` | `backend.**` 应用日志级别（默认 `INFO`）；uvicorn 访问/错误日志不受此键影响 |
| `STP_SIGNAL_LINK_RECONCILE_INTERVAL_SECONDS` | `job_log_signal` ↔ `device_log_event` 补链 sweep 周期（默认 300）；只读路由不补链 |
| `STP_SIGNAL_LINK_RECONCILE_BATCH` | 每个 tick 最多处理的 job 数（默认 200）；积压按 tick 逐步排干 |
| `STP_PLAN_ADMISSION_QUEUE_ENABLED` | `1`=V2 准入队列；默认 `1`。设为 `0` 会停用新派发（不会恢复已移除的 legacy inline dispatch）；存量 QUEUED 仍 drain。灰度见 [`../operations/adr-0026-admission-and-scale-gray-rollout.md`](../operations/adr-0026-admission-and-scale-gray-rollout.md)；`/health` 暴露 `admission_queue_*` |
| `STP_SCRIPT_ROOT` | 脚本扫描根；**必须显式设置**（未设 scan 返回 503，不再回落到 `STP_NFS_ROOT/scripts`）。开发：`<repo>/backend/agent/scripts` |
| `STP_SCRIPT_RUNTIME_ROOT` | 扫描机 ≠ 运行机时 Agent 侧脚本根 |
| `STP_NFS_ROOT` | **脚本专用别名**（#289 钉死）：仅 `backend/agent/scripts/` 下已发布脚本读此名，hot-update 将其镜像为 `STP_AEE_NFS_ROOT` 的值；运行时代码一律解析 `STP_AEE_NFS_ROOT`。控制面本机值**不下发** |
| `STP_AEE_NFS_ROOT` | **中心存储** 本机挂载点（**唯一文档化主键**）。控制面 + Agent 指向同一分享；路径字符串可不同 |
| ~~`STP_AEE_CIFS_ROOT` / `STP_WATCHER_NFS_BASE_DIR`~~ | 已删除（#289）：唯一主键是 `STP_AEE_NFS_ROOT`，未设即视为未配置（调用方按契约报错/503） |
| `STP_FILE_SERVER_ADDRESS` | 共享存储健康页**左栏控制面**展示 IP（现 8.202）。**不是** CIFS 根 / UNC |
| `STP_AEE_SHARE_ADDRESS` | 健康页**右栏中心存储机**展示 IP。未设 = 与控制面同源（过渡期同机）；迁离 8.202 后必设（#205） |
| `STP_CONTROL_PLANE_NODE_JOB` | 左栏控制面 node exporter 的 Prometheus job（默认 `file-server`；旧名 `STP_FILE_SERVER_NODE_JOB` 已删除，#289） |
| `STP_STORAGE_NODE_JOB` | 右栏中心存储机 node exporter 的 Prometheus job（默认未设→同源；分源后必设，如 `storage-server`） |
| `STP_AEE_LOCAL_ROOT` | Agent **本机** L1 AEE 根（按机配置；hot-update **不下发**，见 #235） |
| `STP_AEE_MAX_CONCURRENT_PULLS` | Agent only。主机级 AEE/`adb pull`+mobilelog/bugreport 并发槽（**默认 2**，#740）。机械盘宿主机保持小值；SSD 可酌情上调。hot-update **不下发**（按机 I/O 能力） |
| `STP_EVENT_UPLOADER_PRUNE_LOCAL` | Agent only。上送成功后删本机事件目录并标 `PRUNED`（**默认 0**）。**禁止** fleet 同步（#217）：hot-update 仅下发 `_FLEET_ENV_KEYS` allowlist（`hot_update_env_overrides()`），本键不在列表中，控制面误设也不会进 payload。风险：CIFS 事后不可读时本地已无副本。灰度：单机改 Agent `.env` + `reload_config` |
| `STP_BACKEND_DEDUP_SCAN_PYTHON` / `_SCRIPT` | **仅控制面**：后端 merge/scan 工具路径（#518 起不再回落旧无前缀键） |
| `STP_DEDUP_SCAN_PYTHON` / `_SCRIPT` | **仅 Agent**：Agent 侧 scan 工具路径（hot-update 经 `STP_AGENT_*` 源键写入） |
| `STP_AGENT_DEDUP_SCAN_PYTHON` / `_SCRIPT` | **仅控制面**：Agent 侧 scan 工具路径的源键，hot-update 写成 Agent 的无前缀键 |
| `STP_JIRA_BASE_URL` / `STP_JIRA_TOKEN` | **可选**：JIRA REST 基址与 Bearer token（#710）。配置后 dedup 提单前对 `jira_project_key` 做一次存在性探测（`GET /rest/api/2/project/{key}`），404 记 WARNING 不阻断；未配置则跳过探测（保持 best-effort） |
| `STP_AGENT_UNISOC_LOG_SCAN_PYTHON` / `_SCRIPT` | **仅控制面**：展锐采集工具（`Monkey-Log-Scan-GT-SPRD`）路径的源键，hot-update 写成 `STP_UNISOC_LOG_SCAN_*`（ADR-0032） |
| `STP_AGENT_UNISOC_SCAN_RESULT_PYTHON` / `_SCRIPT` | **仅控制面**：展锐汇总去重工具（`Scan-Result-GT`）路径的源键，hot-update 写成 `STP_UNISOC_SCAN_RESULT_*`（ADR-0032） |
| `STP_UNISOC_LOG_SCAN_PYTHON` / `_SCRIPT` | **仅 Agent**：展锐采集工具路径；属 `AGENT_PATH_ENV_KEYS`（推送后校验路径存在）。与下两行四键齐备才启用，缺任一 = 静默 no-op |
| `STP_UNISOC_SCAN_RESULT_PYTHON` / `_SCRIPT` | **仅 Agent**：展锐汇总去重工具路径；同上属路径校验集 |
| `STP_UNISOC_LOG_SCAN_POLL_SECONDS` | **仅 Agent**：展锐采集轮询间隔秒（默认 `60`）；host 级手工键，**不进** fleet 同步列表 |
| `STP_SCAN_POLL_MAX_WAIT` | 控制面 `scan_task` 主轮询预算秒数（默认 `300`；#732） |
| `STP_SCAN_POLL_PER_HOST_SECONDS` | 叠加预算：`MAX_WAIT + n_triggered * PER_HOST`（默认 `0`） |
| `STP_SCAN_POLL_INTERVAL` | 轮询间隔秒（默认 `10`） |
| `STP_SCAN_POLL_GRACE_SECONDS` | 高进度宽限秒数（默认 `120`；就绪率≥ratio 且缺口≤max_missing 时一次） |
| `STP_SCAN_POLL_GRACE_RATIO` | 触发宽限的最低就绪率（默认 `0.9`） |
| `STP_SCAN_POLL_GRACE_MAX_MISSING` | 触发宽限的最大缺口 host 数（默认 `3`） |
| `STP_HOST_MAINTENANCE_TTL_SECONDS` | 升级维护窗口兜底 TTL 秒（默认 `900`，上限 `3600`）；持有进程崩溃后窗口按此过期（#960） |
| `STP_SMTP_TIMEOUT_SECONDS` | 通知 SMTP 网络 deadline 秒（默认 `15`；#1122） |
| `STP_NOTIFY_WEBHOOK_TIMEOUT_S` | 通知 Webhook 单通道网络 deadline 秒（默认 `10`；#1167 P4） |
| `STP_NOTIFY_DINGTALK_TIMEOUT_S` | 通知钉钉单通道网络 deadline 秒（默认 `10`；#1167 P4） |
| `STP_NOTIFY_SAQ_RETRIES` | 通知 SAQ 入队重试次数；默认派生自 D5 策略上限（`DEFAULT_RETRY_POLICY.max_attempts`，当前 `3`），非法值告警回落、下限 `1`（#1167 P5） |
| `STP_NOTIFY_SAQ_TIMEOUT_S` | 通知 SAQ 单次 job 上限秒（默认 `120`）；须覆盖「一次投递串行经过全部通道」的最坏耗时（#1167 P5） |
| `BACKGROUND_POOL_SIZE` | 后台线程池 worker 数（默认 `8`；#1122） |
| `BACKGROUND_POOL_MAX_QUEUE` | 后台线程池待提交队列上限（默认 `200`）；满即拒绝，不再无界堆积（#1122） |
| `STP_RUN_CONSOLE_LOG_ROOT` | RunConsole 日志根目录（默认 `logs/console`）。**多实例部署须对全部实例可见**（同机多进程天然共享；多机需挂同一存储），否则跨实例日志 replay 返回 `replay_unavailable`（#1737 P4） |
| `STP_RUN_CONSOLE_REPLAY_MAX_LINES` | RunConsole replay 单次回放行数上限（默认 `2000`；#1124） |
| `STP_RUN_CONSOLE_TERMINAL_RETENTION_SECONDS` | RunConsole 终态运行记录保留秒数（默认 `3600`；#1124） |
| `STP_RUN_CONSOLE_CANCEL_WAIT_SECONDS` | 跨实例 cancel 等待 owner ack 的上界秒数（默认 `3`；超时 fail-closed；#1737 P3） |
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
| `STP_DEVICE_LOG_EVENT_ENABLED` | ADR-0028 DLE + EventUploader **单一开关**（#287 合并双 flag），未设时代码默认开，`=0` 显式关闭；控制面非空值经 hot-update fleet 同步（#218）。过滤模型是唯一路径（`STP_EVENT_UPLOADER_*` 双键已删除） |
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

---

## 附录：运行时读取清单（自动生成，勿手改）

> 生成：`python tools/dev/env_inventory.py --write`；
> 校验：`python tools/dev/env_inventory.py --check`（已接入 `run_gates.py` 的
> `check:quick` / `check:pr`，代码新增读取名而本表未刷新即红）。
> 覆盖范围：`backend/**/*.py`（不含 `backend/agent/scripts/**`——版本化脚本目录
> 自管环境契约，见 ADR-0020）；`示例` 列 ✅ = 该名出现在任一 `.env*.example`
> （含注释态条目），`—` = 仅内部/开发使用、未进运维模板。

<!-- env-inventory:begin（generated：python tools/dev/env_inventory.py --write） -->

共 **210** 个读取名（`backend/**`，不含 `backend/agent/scripts/**`）：**191** 个已在 `.env*.example` 登记，**19** 个声明为内部（理由见下节）。
示例文件是**运维模板**（承载需要运维/机型调整的子集）；本表是**代码侧完整清单**。
门禁：每个读取名必须「登记进示例」或「内部声明」二选一，二者之外即红。

| 变量 | 默认 | 示例 | 类别 | 首个读取点 |
|---|---|---|---|---|
| `ADB_PATH` | `adb` | ✅ | 运行时 | `backend/agent/main.py:792` |
| `ADMISSION_REQUEUE_BACKOFF_SECONDS` | `60` | ✅ | 运行时 | `backend/scheduler/precheck_reaper.py:288` |
| `AGENT_INSTALL_DIR` | `-` | ✅ | 运行时 | `backend/agent/config.py:21` |
| `AGENT_LEASE_EXTEND_BATCH_CHUNK` | `100` | ✅ | 运行时 | `backend/agent/lease_renewer.py:54` |
| `AGENT_LEASE_EXTEND_BATCH_MAX` | `200` | ✅ | 运行时 | `backend/api/routes/agent_api.py:1360` |
| `AGENT_LEASE_TTL` | `-` | ✅ | 运行时 | `backend/agent/lease_renewer.py:57` |
| `AGENT_LOCK_RENEWAL_INTERVAL` | `60` | ✅ | 运行时 | `backend/agent/lease_renewer.py:44` |
| `AGENT_POST_RETRIES` | `3` | ✅ | 运行时 | `backend/agent/api_client.py:39` |
| `AGENT_POST_RETRY_BASE_DELAY` | `1` | ✅ | 运行时 | `backend/agent/api_client.py:43` |
| `AGENT_SECRET` | `` | ✅ | 运行时 | `backend/agent/api_client.py:35` |
| `AGENT_SECRET_B64` | `-` | — | 运行时 | `backend/services/host_updater.py:272` |
| `AIMONKEY_RESOURCE_DIR` | `` | ✅ | 运行时 | `backend/agent/aimonkey_paths.py:23` |
| `API_URL` | `http://127.0.0.1:8000` | ✅ | 运行时 | `backend/agent/main.py:721` |
| `ARTIFACT_RETENTION_DAYS` | `30` | ✅ | 运行时 | `backend/scheduler/recycler.py:54` |
| `AUTH_ACCESS_COOKIE_NAME` | `stp_access_token` | ✅ | 运行时 | `backend/core/security.py:28` |
| `AUTH_COOKIE_PATH` | `/` | ✅ | 运行时 | `backend/core/security.py:30` |
| `AUTH_COOKIE_SAMESITE` | `lax` | ✅ | 运行时 | `backend/core/security.py:51` |
| `AUTH_COOKIE_SECURE` | `0` | ✅ | 运行时 | `backend/core/security.py:47` |
| `AUTH_REFRESH_COOKIE_NAME` | `stp_refresh_token` | ✅ | 运行时 | `backend/core/security.py:29` |
| `AUTO_ARCHIVE_POLL_INTERVAL_SECONDS` | `120` | ✅ | 运行时 | `backend/scheduler/app_scheduler.py:48` |
| `AUTO_REGISTER_HOST` | `false` | ✅ | 运行时 | `backend/agent/main.py:756` |
| `AUTO_REGISTER_MAX_RETRIES` | `0` | ✅ | 运行时 | `backend/agent/main.py:773` |
| `AUTO_REGISTER_RETRY_DELAY` | `10` | ✅ | 运行时 | `backend/agent/main.py:774` |
| `BACKGROUND_POOL_MAX_QUEUE` | `200` | ✅ | 运行时 | `backend/core/thread_pool.py:21` |
| `BACKGROUND_POOL_SIZE` | `8` | ✅ | 运行时 | `backend/core/thread_pool.py:19` |
| `CHAIN_RECONCILER_INTERVAL_SECONDS` | `60` | ✅ | 运行时 | `backend/scheduler/app_scheduler.py:39` |
| `CHAIN_RECONCILE_BATCH_SIZE` | `100` | ✅ | 运行时 | `backend/scheduler/plan_chain_reconciler.py:21` |
| `COORDINATOR_HEARTBEAT_INTERVAL` | `30` | ✅ | 运行时 | `backend/agent/coordinator.py:171` |
| `COORDINATOR_HEARTBEAT_TIMEOUT_SECONDS` | `300` | ✅ | 运行时 | `backend/api/routes/plan_runs.py:1753` |
| `COORDINATOR_MAX_PLAN_RUN_HOSTS` | `200` | ✅ | 运行时 | `backend/agent/coordinator.py:175` |
| `CORS_ALLOW_HEADERS` | `-` | ✅ | 运行时 | `backend/core/cors.py:17` |
| `CORS_ALLOW_METHODS` | `-` | ✅ | 运行时 | `backend/core/cors.py:16` |
| `CORS_ORIGINS` | `-` | ✅ | 运行时 | `backend/core/cors.py:15` |
| `CRON_POLL_INTERVAL` | `30` | ✅ | 运行时 | `backend/scheduler/app_scheduler.py:34` |
| `DATABASE_URL` | `` | ✅ | 运行时 | `backend/agent/tests/test_env_isolation.py:34` |
| `DEVICE_LOCK_LEASE_SECONDS` | `600` | ✅ | 运行时 | `backend/api/routes/agent_api.py:76` |
| `DEVICE_OFFLINE_TIMEOUT` | `60` | ✅ | 运行时 | `backend/api/routes/heartbeat.py:123` |
| `DEVICE_SNAPSHOT_INTERVAL` | `30` | ✅ | 运行时 | `backend/api/routes/heartbeat.py:29` |
| `DISPATCH_SYNC_MAX_ATTEMPTS` | `1` | ✅ | 运行时 | `backend/services/precheck/__init__.py:12` |
| `ENV` | `` | ✅ | 运行时 | `backend/core/job_timeout_config.py:22` |
| `ENV_OVERRIDES_B64` | `-` | — | 运行时 | `backend/services/host_updater.py:303` |
| `ENV_PATH_KEYS_B64` | `-` | — | 运行时 | `backend/services/host_updater.py:304` |
| `FAKE_TAR_SLEEP` | `0.15` | — | 测试 | `backend/agent/tests/test_script_progress_stamps.py:81` |
| `HOST_ID` | `` | ✅ | 运行时 | `backend/agent/host_registry.py:28` |
| `HOST_IP` | `-` | — | 测试 | `backend/agent/tests/test_agent.py:84` |
| `HOT_UPDATE_ABORT_POLL_INTERVAL_SECONDS` | `1.0` | ✅ | 运行时 | `backend/services/host_upgrade_gate.py:55` |
| `HOT_UPDATE_ABORT_POLL_TIMEOUT_SECONDS` | `45` | ✅ | 运行时 | `backend/services/host_upgrade_gate.py:52` |
| `INSTALL_DIR` | `-` | — | 运行时 | `backend/services/host_updater.py:267` |
| `JWT_SECRET_KEY` | `` | ✅ | 运行时 | `backend/core/security.py:16` |
| `LOG_BASE_DIR` | `data/logs` | ✅ | 运行时 | `backend/realtime/log_writer.py:19` |
| `LOG_LEVEL` | `INFO` | ✅ | 运行时 | `backend/agent/main.py:84` |
| `MAX_ADMISSION_REQUEUE_ATTEMPTS` | `3` | ✅ | 运行时 | `backend/scheduler/precheck_reaper.py:287` |
| `MAX_PRECHECK_REENQUEUE_ATTEMPTS` | `1` | ✅ | 运行时 | `backend/scheduler/precheck_reaper.py:52` |
| `MOUNT_POINTS` | `` | ✅ | 运行时 | `backend/agent/main.py:791` |
| `PATROL_STALL_BATCH_LIMIT` | `100` | ✅ | 运行时 | `backend/scheduler/recycler.py:57` |
| `PLAN_RUN_RETENTION_DAYS` | `3` | ✅ | 运行时 | `backend/scheduler/cron_scheduler.py:29` |
| `POLL_INTERVAL` | `5` | ✅ | 运行时 | `backend/agent/main.py:790` |
| `POST_COMPLETION_GRACE_SECONDS` | `120` | ✅ | 运行时 | `backend/scheduler/recycler.py:718` |
| `PRECHECK_NOTIFY_DEBOUNCE_SECONDS` | `0.5` | — | 运行时 | `backend/services/precheck/notify.py:14` |
| `PRECHECK_REAPER_INTERVAL_SECONDS` | `45` | ✅ | 运行时 | `backend/scheduler/app_scheduler.py:37` |
| `QUEUE_DEPTH_POLL_INTERVAL_SECONDS` | `15` | ✅ | 运行时 | `backend/scheduler/app_scheduler.py:36` |
| `RECONCILER_INTERVAL_SECONDS` | `15` | ✅ | 运行时 | `backend/scheduler/app_scheduler.py:33` |
| `RECYCLER_BATCH_SIZE` | `200` | ✅ | 运行时 | `backend/scheduler/recycler.py:53` |
| `REDIS_PING_TIMEOUT` | `3.0` | ✅ | 运行时 | `backend/main.py:122` |
| `REDIS_URL` | `redis://localhost:6379/0` | ✅ | 运行时 | `backend/main.py:157` |
| `RETENTION_CLEANUP_INTERVAL_SECONDS` | `3600` | ✅ | 运行时 | `backend/scheduler/app_scheduler.py:35` |
| `REVOKED_TOKEN_CLEANUP_INTERVAL_SECONDS` | `-` | ✅ | 运行时 | `backend/scheduler/app_scheduler.py:44` |
| `RUN_RECYCLE_INTERVAL_SECONDS` | `30` | ✅ | 运行时 | `backend/scheduler/app_scheduler.py:31` |
| `RUN_REPORT_ALERT_ANR_THRESHOLD` | `1` | ✅ | 运行时 | `backend/services/report_service.py:53` |
| `RUN_REPORT_ALERT_CRASH_THRESHOLD` | `1` | ✅ | 运行时 | `backend/services/report_service.py:54` |
| `RUN_REPORT_ALERT_RESTART_THRESHOLD` | `2` | ✅ | 运行时 | `backend/services/report_service.py:55` |
| `RUN_REPORT_JIRA_PROJECT_KEY` | `STABILITY` | ✅ | 运行时 | `backend/services/report_service.py:56` |
| `RUN_REPORT_JIRA_TEMPLATE_JSON` | `` | ✅ | 运行时 | `backend/services/report_service.py:57` |
| `SAQ_CONCURRENCY` | `10` | ✅ | 运行时 | `backend/tasks/saq_worker.py:52` |
| `SAQ_ENQUEUE_WAIT_TIMEOUT` | `5.0` | ✅ | 运行时 | `backend/tasks/saq_worker.py:54` |
| `SAQ_QUEUE_NAME` | `stp` | ✅ | 运行时 | `backend/main.py:219` |
| `SCHEDULE_DEDUP_WINDOW_SECONDS` | `60` | ✅ | 运行时 | `backend/scheduler/cron_scheduler.py:31` |
| `SESSION_WATCHDOG_INTERVAL_SECONDS` | `15` | ✅ | 运行时 | `backend/scheduler/app_scheduler.py:32` |
| `SMTP_FROM` | `` | ✅ | 运行时 | `backend/services/notification_service.py:48` |
| `SMTP_HOST` | `` | ✅ | 运行时 | `backend/services/notification_service.py:44` |
| `SMTP_PASSWORD` | `` | ✅ | 运行时 | `backend/services/notification_service.py:47` |
| `SMTP_PORT` | `587` | ✅ | 运行时 | `backend/services/notification_service.py:45` |
| `SMTP_USER` | `` | ✅ | 运行时 | `backend/services/notification_service.py:46` |
| `SSH_CREDENTIALS_FERNET_KEY` | `` | ✅ | 运行时 | `backend/core/ssh_security.py:345` |
| `STP_ADB_AUTO_REPAIR` | `0` | ✅ | 运行时 | `backend/agent/heartbeat_thread.py:295` |
| `STP_ADB_REPAIR_COOLDOWN_SECONDS` | `300` | ✅ | 运行时 | `backend/agent/heartbeat_thread.py:94` |
| `STP_ADMIN_PASSWORD` | `-` | ✅ | 测试 | `backend/tests/test_seed_and_smoke.py:61` |
| `STP_ADMIN_USER` | `-` | ✅ | 测试 | `backend/tests/test_seed_and_smoke.py:62` |
| `STP_ADMISSION_AGING_MAX_BOOST` | `5` | ✅ | 运行时 | `backend/services/admission_pump.py:69` |
| `STP_ADMISSION_AGING_STEP_SECONDS` | `1800` | ✅ | 运行时 | `backend/services/admission_pump.py:68` |
| `STP_ADMISSION_PUMP_BATCH` | `5` | ✅ | 运行时 | `backend/services/admission_pump.py:64` |
| `STP_ADMISSION_PUMP_INTERVAL_SECONDS` | `5` | ✅ | 运行时 | `backend/scheduler/app_scheduler.py:51` |
| `STP_ADMISSION_RETRY_BACKOFF_SECONDS` | `30` | ✅ | 运行时 | `backend/services/admission_pump.py:65` |
| `STP_AEE_LOCAL_ROOT` | `-` | ✅ | 运行时 | `backend/agent/aee/paths.py:101` |
| `STP_AEE_MAX_CONCURRENT_PULLS` | `-` | ✅ | 运行时 | `backend/agent/aee/extraction_slot.py:27` |
| `STP_AEE_NFS_ROOT` | `` | ✅ | 运行时 | `backend/agent/aee/paths.py:27` |
| `STP_AEE_SHARE_ADDRESS` | `` | ✅ | 运行时 | `backend/services/file_server_monitor.py:323` |
| `STP_AEE_SSD_FALLBACK_ROOT` | `-` | ✅ | 运行时 | `backend/agent/aee/paths.py:108` |
| `STP_AGENT_MIN_VERSION` | `-` | ✅ | 运行时 | `backend/services/agent_version_gate.py:16` |
| `STP_AGENT_PIP_INDEX_URL` | `` | ✅ | 运行时 | `backend/services/host_updater.py:644` |
| `STP_AGENT_PRIV_CONF` | `-` | ✅ | 运行时 | `backend/agent/stp_agent_priv.py:256` |
| `STP_AGENT_SID_REGISTRY` | `` | ✅ | 运行时 | `backend/realtime/agent_sid_registry.py:42` |
| `STP_AGENT_SID_REGISTRY_TTL_SECONDS` | `-` | ✅ | 运行时 | `backend/realtime/agent_sid_registry.py:94` |
| `STP_AGENT_STATE_DB` | `` | ✅ | 运行时 | `backend/agent/aee/state_store.py:15` |
| `STP_AGENT_VERSION` | `unknown` | — | 运行时 | `backend/agent/script_verifier.py:115` |
| `STP_ALLOW_REGISTER` | `` | ✅ | 运行时 | `backend/core/security.py:64` |
| `STP_ALLOW_UNSAFE_TEST_DATABASE_URL` | `` | — | 运行时 | `backend/core/db_url_guard.py:30` |
| `STP_API_DOCS_ENABLED` | `-` | ✅ | 运行时 | `backend/main.py:320` |
| `STP_ARTIFACT_DIGEST_CACHE` | `` | — | 运行时 | `backend/services/artifact_digest.py:40` |
| `STP_BACKEND_DEDUP_SCAN_PYTHON` | `` | ✅ | 运行时 | `backend/services/dedup_scan.py:42` |
| `STP_BACKEND_DEDUP_SCAN_SCRIPT` | `` | ✅ | 运行时 | `backend/services/dedup_scan.py:43` |
| `STP_BARRIER_MAX_WAIT_SECONDS` | `1800` | ✅ | 运行时 | `backend/agent/pipeline_engine.py:226` |
| `STP_BARRIER_PROGRESS_STALE_SECONDS` | `-` | ✅ | 运行时 | `backend/agent/pipeline_engine.py:190` |
| `STP_BARRIER_TIMEOUT_SECONDS` | `600` | ✅ | 运行时 | `backend/agent/pipeline_engine.py:1308` |
| `STP_CONSOLE_CANCEL_TTL_SECONDS` | `-` | ✅ | 运行时 | `backend/realtime/console_registry.py:433` |
| `STP_CONSOLE_CONTROL_TICK_SECONDS` | `-` | ✅ | 运行时 | `backend/services/run_console.py:891` |
| `STP_CONSOLE_REGISTRY` | `` | ✅ | 运行时 | `backend/realtime/console_registry.py:72` |
| `STP_CONSOLE_REGISTRY_TTL_SECONDS` | `-` | ✅ | 运行时 | `backend/realtime/console_registry.py:81` |
| `STP_CONTROL_PLANE_NODE_JOB` | `` | ✅ | 运行时 | `backend/services/file_server_monitor.py:321` |
| `STP_COUNTER_RECONCILE_BATCH` | `200` | ✅ | 运行时 | `backend/scheduler/counter_reconciler.py:29` |
| `STP_COUNTER_RECONCILE_INTERVAL_SECONDS` | `300` | ✅ | 运行时 | `backend/scheduler/app_scheduler.py:55` |
| `STP_COUNTER_RECONCILE_LOOKBACK_HOURS` | `48` | ✅ | 运行时 | `backend/scheduler/counter_reconciler.py:27` |
| `STP_CSRF_ENABLED` | `1` | ✅ | 运行时 | `backend/core/csrf.py:37` |
| `STP_DEDUP_LOG_ENCODING` | `utf-8` | — | 运行时 | `backend/main.py:182` |
| `STP_DEDUP_PLACE` | `SH` | — | 运行时 | `backend/services/dedup_scan.py:60` |
| `STP_DEDUP_SCAN_PYTHON` | `` | ✅ | 运行时 | `backend/agent/scan_runner.py:345` |
| `STP_DEDUP_SCAN_SCRIPT` | `` | ✅ | 运行时 | `backend/agent/scan_runner.py:346` |
| `STP_DEDUP_SCAN_TAG` | `` | ✅ | 运行时 | `backend/agent/scan_runner.py:351` |
| `STP_DEDUP_WORK_DIR` | `logs/dedup_uploads` | ✅ | 运行时 | `backend/api/routes/dedup.py:125` |
| `STP_DEVICE_LOG_EVENT_ENABLED` | `-` | ✅ | 运行时 | `backend/agent/event_uploader.py:109` |
| `STP_DEVICE_SERIAL` | `-` | — | 测试 | `backend/agent/tests/test_pipeline_engine_script_action.py:51` |
| `STP_ENABLE_INPROCESS_SAQ` | `1` | ✅ | 运行时 | `backend/main.py:188` |
| `STP_EVENT_UPLOADER_PRUNE_LOCAL` | `0` | ✅ | 运行时 | `backend/agent/event_uploader.py:548` |
| `STP_FILE_SERVER_ADDRESS` | `` | ✅ | 运行时 | `backend/services/file_server_monitor.py:253` |
| `STP_FILE_SERVER_AGENT_FRESH_SECONDS` | `180` | ✅ | 运行时 | `backend/api/routes/stats.py:245` |
| `STP_FLASH_FIRMWARE_ROOT` | `-` | ✅ | 测试 | `backend/agent/tests/test_flash_firmware_v131.py:62` |
| `STP_HDD_SPILL_CATCHUP_INTERVAL` | `-` | ✅ | 运行时 | `backend/agent/local_disk_monitor.py:27` |
| `STP_HDD_SPILL_CRITICAL_PCT` | `-` | ✅ | 运行时 | `backend/agent/local_disk_monitor.py:93` |
| `STP_HEARTBEAT_INTERVAL_BASE` | `20` | ✅ | 运行时 | `backend/api/routes/heartbeat.py:33` |
| `STP_HEARTBEAT_INTERVAL_MAX` | `120` | ✅ | 运行时 | `backend/agent/heartbeat_thread.py:71` |
| `STP_HEARTBEAT_INTERVAL_MIN` | `10` | ✅ | 运行时 | `backend/agent/heartbeat_thread.py:70` |
| `STP_HOST_MAINTENANCE_TTL_SECONDS` | `` | ✅ | 运行时 | `backend/services/host_maintenance.py:41` |
| `STP_JIRA_BASE_URL` | `-` | ✅ | 运行时 | `backend/services/jira_project_key.py:36` |
| `STP_JIRA_TOKEN` | `-` | ✅ | 运行时 | `backend/services/jira_project_key.py:39` |
| `STP_JOB_WORKER_POOL_SIZE` | `50` | ✅ | 运行时 | `backend/agent/main.py:1291` |
| `STP_LOCAL_DISK_MONITOR_INTERVAL_SECONDS` | `300` | ✅ | 运行时 | `backend/agent/main.py:857` |
| `STP_LOCAL_DISK_SPILL_TARGET` | `70` | ✅ | 运行时 | `backend/agent/main.py:859` |
| `STP_LOCAL_DISK_SPILL_THRESHOLD` | `80` | ✅ | 运行时 | `backend/agent/main.py:858` |
| `STP_LOG_ARCHIVE_GRACE_SECONDS` | `1800` | ✅ | 运行时 | `backend/agent/main.py:841` |
| `STP_LOG_ARCHIVE_INTERVAL_SECONDS` | `3600` | ✅ | 运行时 | `backend/agent/main.py:840` |
| `STP_LOG_LEVEL` | `-` | ✅ | 运行时 | `backend/core/logging_setup.py:31` |
| `STP_LOG_RATE_LIMIT_BASE` | `200` | ✅ | 运行时 | `backend/api/routes/heartbeat.py:35` |
| `STP_LOG_RATE_LIMIT_MIN` | `20` | ✅ | 运行时 | `backend/api/routes/heartbeat.py:36` |
| `STP_MAX_CLAIM_SLOTS` | `-` | ✅ | 运行时 | `backend/agent/capacity_reporter.py:93` |
| `STP_MAX_CONCURRENT_OPERATIONS` | `-` | ✅ | 运行时 | `backend/agent/operation_scheduler.py:38` |
| `STP_METRICS_AUTH_REQUIRED` | `1` | ✅ | 运行时 | `backend/api/routes/metrics.py:65` |
| `STP_NOTIFY_SAQ_RETRIES` | `-` | — | 测试 | `backend/tests/services/test_notification_service.py:497` |
| `STP_NOTIFY_SAQ_TIMEOUT_S` | `-` | ✅ | 运行时 | `backend/services/notification_service.py:99` |
| `STP_PHASE_BARRIER_ENABLED` | `1` | ✅ | 运行时 | `backend/agent/job_runner.py:220` |
| `STP_PLATFORM_NAME` | `Stability Test Platform` | ✅ | 运行时 | `backend/api/routes/settings.py:19` |
| `STP_PROMETHEUS_URL` | `-` | ✅ | 运行时 | `backend/services/file_server_monitor.py:120` |
| `STP_RECOVERY_SYNC_INTERVAL_SECONDS` | `60` | ✅ | 运行时 | `backend/agent/main.py:1355` |
| `STP_RUN_CONSOLE_CANCEL_WAIT_SECONDS` | `-` | ✅ | 运行时 | `backend/services/run_console.py:292` |
| `STP_RUN_CONSOLE_LOG_ROOT` | `logs/console` | ✅ | 运行时 | `backend/main.py:181` |
| `STP_RUN_CONSOLE_REPLAY_MAX_LINES` | `-` | ✅ | 运行时 | `backend/services/run_console.py:270` |
| `STP_RUN_CONSOLE_TERMINAL_RETENTION_SECONDS` | `-` | ✅ | 运行时 | `backend/services/run_console.py:274` |
| `STP_SCHEDULER_LEADER_ELECTION` | `1` | ✅ | 运行时 | `backend/core/leader_election.py:38` |
| `STP_SCRIPT_CATALOG_VERSION_CACHE_TTL` | `-` | ✅ | 运行时 | `backend/services/script_catalog_version.py:51` |
| `STP_SCRIPT_ROOT` | `` | ✅ | 运行时 | `backend/api/routes/scripts.py:150` |
| `STP_SCRIPT_RUNTIME_ROOT` | `-` | ✅ | 运行时 | `backend/api/routes/scripts.py:165` |
| `STP_SIGNAL_LINK_RECONCILE_BATCH` | `200` | ✅ | 运行时 | `backend/scheduler/signal_link_reconciler.py:27` |
| `STP_SIGNAL_LINK_RECONCILE_INTERVAL_SECONDS` | `300` | ✅ | 运行时 | `backend/scheduler/app_scheduler.py:60` |
| `STP_SKIP_INFRA_CHECK` | `0` | ✅ | 运行时 | `backend/main.py:190` |
| `STP_SMOKE_ORIGIN` | `-` | — | 测试 | `backend/tests/test_seed_and_smoke.py:63` |
| `STP_SMTP_TIMEOUT_SECONDS` | `15` | ✅ | 运行时 | `backend/services/notification_service.py:53` |
| `STP_SOCKETIO_REDIS_ADAPTER` | `0` | ✅ | 运行时 | `backend/realtime/socketio_redis.py:35` |
| `STP_SOCKETIO_REDIS_CHANNEL` | `-` | ✅ | 运行时 | `backend/realtime/socketio_redis.py:39` |
| `STP_SSH_KNOWN_HOSTS` | `` | ✅ | 运行时 | `backend/core/ssh_security.py:93` |
| `STP_SSH_LOG_ROOTS` | `-` | ✅ | 运行时 | `backend/core/ssh_security.py:105` |
| `STP_STEP_LOG_STREAM` | `1` | ✅ | 运行时 | `backend/agent/mq/producer.py:22` |
| `STP_STEP_PARAMS` | `-` | — | 测试 | `backend/agent/tests/test_pipeline_engine_script_action.py:50` |
| `STP_STEP_STALL_SECONDS` | `-` | ✅ | 运行时 | `backend/agent/pipeline_engine.py:281` |
| `STP_STEP_WALL_CLOCK_SECONDS` | `-` | ✅ | 运行时 | `backend/agent/pipeline_engine.py:114` |
| `STP_STORAGE_NODE_JOB` | `` | ✅ | 运行时 | `backend/services/file_server_monitor.py:325` |
| `STP_TIMEZONE` | `Asia/Shanghai` | ✅ | 运行时 | `backend/api/routes/settings.py:20` |
| `STP_TRUSTED_PROXIES` | `-` | ✅ | 运行时 | `backend/core/limiter.py:76` |
| `STP_UNISOC_LOG_SCAN_POLL_SECONDS` | `-` | ✅ | 运行时 | `backend/agent/unisoc_scan_runner.py:140` |
| `STP_UNISOC_LOG_SCAN_PYTHON` | `` | ✅ | 运行时 | `backend/agent/unisoc_scan_runner.py:59` |
| `STP_UNISOC_LOG_SCAN_SCRIPT` | `` | ✅ | 运行时 | `backend/agent/unisoc_scan_runner.py:60` |
| `STP_UNISOC_SCAN_RESULT_PYTHON` | `` | ✅ | 运行时 | `backend/agent/unisoc_scan_runner.py:61` |
| `STP_UNISOC_SCAN_RESULT_SCRIPT` | `` | ✅ | 运行时 | `backend/agent/unisoc_scan_runner.py:62` |
| `STP_WATCHER_AEE_RECONCILE_BURST_INTERVAL_SECONDS` | `60` | ✅ | 运行时 | `backend/agent/main.py:918` |
| `STP_WATCHER_AEE_RECONCILE_BURST_ROUNDS` | `5` | ✅ | 运行时 | `backend/agent/main.py:919` |
| `STP_WATCHER_AEE_RECONCILE_ENABLED` | `true` | ✅ | 运行时 | `backend/agent/main.py:916` |
| `STP_WATCHER_AEE_RECONCILE_HOSTS` | `` | — | 运行时 | `backend/agent/aee/reconciler.py:185` |
| `STP_WATCHER_AEE_RECONCILE_INTERVAL_SECONDS` | `180` | ✅ | 运行时 | `backend/agent/main.py:917` |
| `STP_WATCHER_AEE_SUBDIR_LAYOUT` | `stp` | ✅ | 运行时 | `backend/agent/aee/paths.py:194` |
| `STP_WATCHER_ENABLED` | `true` | ✅ | 运行时 | `backend/agent/main.py:92` |
| `STP_WATCHER_PLAN_DEFAULT` | `true` | ✅ | 运行时 | `backend/agent/main.py:93` |
| `SUDO_GID` | `` | — | 运行时 | `backend/agent/stp_agent_priv.py:116` |
| `SUDO_UID` | `` | — | 运行时 | `backend/agent/stp_agent_priv.py:109` |
| `TESTING` | `-` | ✅ | 运行时 | `backend/core/agent_secret.py:15` |
| `TEST_DATABASE_URL` | `` | ✅ | 测试 | `backend/tests/conftest.py:73` |
| `WATCHER_BATCH_INTERVAL_SECONDS` | `-` | ✅ | 运行时 | `backend/agent/watcher/policy.py:144` |
| `WATCHER_EXIT_DRAIN_TIMEOUT_SECONDS` | `-` | ✅ | 运行时 | `backend/agent/watcher/policy.py:165` |
| `WATCHER_LOG_LEVEL` | `-` | ✅ | 运行时 | `backend/agent/watcher/policy.py:162` |
| `WATCHER_NFS_QUOTA_MB` | `-` | ✅ | 运行时 | `backend/agent/watcher/policy.py:150` |
| `WATCHER_ON_UNAVAILABLE` | `-` | ✅ | 运行时 | `backend/agent/watcher/policy.py:138` |
| `WATCHER_PULL_TIMEOUT_SECONDS` | `-` | ✅ | 运行时 | `backend/agent/watcher/policy.py:156` |
| `WS_TOKEN` | `` | ✅ | 运行时 | `backend/realtime/socketio_server.py:59` |

### 内部声明（未进运维模板，含理由）

| 变量 | 理由 |
|---|---|
| `AGENT_SECRET_B64` | 控制面 hot-update 经环境变量下发的 base64 密钥载荷（传输通道，非运维配置） |
| `ENV_OVERRIDES_B64` | 同上：控制面下发的 .env 覆盖载荷（base64 JSON） |
| `ENV_PATH_KEYS_B64` | 同上：路径类键清单载荷（base64 JSON） |
| `FAKE_TAR_SLEEP` | 测试夹具（模拟 tar 耗时），无常驻配置语义 |
| `HOST_IP` | 测试注入的 host 身份；生产由 Agent 自行解析 |
| `INSTALL_DIR` | hot-update 在目标机执行时由部署环境注入的安装目录 |
| `PRECHECK_NOTIFY_DEBOUNCE_SECONDS` | precheck 通知去抖：实现细节（防重复推送），不属运维旋钮 |
| `STP_AGENT_VERSION` | hot-update 写入的版本标记（派生值，不自设） |
| `STP_ALLOW_UNSAFE_TEST_DATABASE_URL` | 测试守卫逃生门：仅本地测试库用，生产禁止设置 |
| `STP_ARTIFACT_DIGEST_CACHE` | 制品摘要缓存的紧急关闭开关（内部实现细节） |
| `STP_DEDUP_LOG_ENCODING` | 去重日志文件编码（locale 细节，跟随机型） |
| `STP_DEDUP_PLACE` | 去重扫描写入的站点标签（元数据；由采集侧脚本语境决定） |
| `STP_DEVICE_SERIAL` | 脚本运行时注入：Agent 为脚本进程注入设备序列号 |
| `STP_NOTIFY_SAQ_RETRIES` | 读取点仅存在于测试（断言 _int_env 行为） |
| `STP_SMOKE_ORIGIN` | 测试用：smoke 夹具断言 origin |
| `STP_STEP_PARAMS` | 脚本运行时注入：步骤参数 JSON（Agent→脚本协议） |
| `STP_WATCHER_AEE_RECONCILE_HOSTS` | 目标机本地选择性对账清单（现场排障临时用，默认空=全量） |
| `SUDO_GID` | sudo 调用时由系统注入（stp_agent_priv） |
| `SUDO_UID` | sudo 调用时由系统注入（stp_agent_priv） |

<!-- env-inventory:end -->








