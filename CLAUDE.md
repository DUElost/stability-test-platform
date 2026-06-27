# stability-test-platform

开发命令、测试运行方式、约定见 @AGENTS.md。设计文档见 @docs/DOC-MAP.md。Cursor IDE 按域规则见 `.cursor/rules/`（薄适配层，权威内容仍以本文与 AGENTS.md 为准）；说明见 @docs/development/cursor-rules.md。

---

## 架构不变量

- **app** = `socketio.ASGIApp(sio_server, fastapi_app)` — 合并 ASGI 挂载（`backend/main.py:122`）
- **Plan 无 lifecycle 列**：由 PlanStep 行 + `patrol_interval_seconds`/`timeout_seconds` 在 dispatcher 阶段组装为 `pipeline_def.lifecycle`（唯一事实源）
- **Redis 仅做 SAQ broker**，不存业务数据
- **Production guard**：`ENV=production` 时强制 `AUTH_COOKIE_SECURE=1` + `AUTH_COOKIE_SAMESITE ∈ {lax,strict}` + `STP_CSRF_ENABLED` 开启，否则 `RuntimeError`（ADR-0024）
- **Pipeline 仅接受 `lifecycle` 顶层键**：`stages`/`phases` 格式被拒绝（`pipeline_engine.py L158-179`）
- **唯一 action 类型** `script:<name>`：`builtin:<name>` / `tool:<id>` / `shell:<command>` 已删除

---

## 关键约定（违反会导致 bug）

- **版本即参数**：已存在版本的 `default_params` 422 不可变，必须 `POST /api/v1/scripts/{name}/versions` 新建版本
- **DB 表名单数**：`device` 非 `devices`，`host` 非 `hosts`
- **Pydantic v2 only**：禁止 `.dict()`/`parse_obj`/`from_orm`/`class Config`；用 `model_dump()`/`model_validate()`/`ConfigDict(from_attributes=True)`
- **前端类型权威源**：`frontend/src/utils/api/types.ts` — 必须与后端 Pydantic schema 同步
- **`host.max_concurrent_jobs` 已删除**（migration `q2r3s4t5u6v7w8`）；容量 = `min(MAX_CONCURRENT_TASKS - active, heartbeat effective_slots)`

---

## 状态机

- **Job**：`PENDING → RUNNING → COMPLETED/FAILED/ABORTED`；`PENDING → FAILED`（recycler 超时）；`UNKNOWN → RUNNING/COMPLETED/FAILED`
- **PlanRun**：`RUNNING → SUCCESS/PARTIAL_SUCCESS/FAILED/DEGRADED`

---

## 方案 C 存储（ADR-0025）

> 详见 @docs/design/2026-plan-c-storage-and-access.md

| 存储 | 用途 | 路径 |
|------|------|------|
| Agent SSD | 运行日志（唯一副本） | `logs/runs/{job_id}/` |
| Agent HDD | AEE + mobilelog + bugreport | `STP_AEE_LOCAL_ROOT`（默认 `/mnt/hdd/aee_events`） |
| 15.4 CIFS | 汇总 xls、按需事件、HDD 溢出 | `STP_AEE_CIFS_ROOT`；**不含**运行日志 |

**已取消（勿依赖）**：运行日志上送 15.4、`run_log_bundle` JobArtifact、patrol cycle `snapshots/`。

---

## 脚本目录契约（ADR-0020）

```
<STP_SCRIPT_ROOT>/<name>/v<version>/<entry>.{py,sh,bat,cmd}
```

- 一级 = 脚本名，二级 = v 开头版本号，入口 = 首个非 `_` 可识别文件
- `_` 开头的辅助模块扫描时跳过
- 扫描结果：created(INSERT) / skipped(sha256一致) / conflicts(sha256不一致,不动DB,须新建版本) / deactivated(磁盘无,标false)
- WiFi 资源池注入是唯一打破「params 完全来自 default_params」的特例（`_inject_wifi_params` 对 `connect_wifi` 注入 `{ssid, password, pool_name, pool_id}`）
- 完整链路：文件 → `POST /scripts/scan` → DB.script → PlanStep → dispatcher `deepcopy(default_params)` → `pipeline_def` → Agent `ScriptRegistry.resolve` → `subprocess.run` → stdout JSON → step_trace → JobStatus → aggregator

---

## 环境变量（开发必设）

> 完整清单见 `backend/.env.example`、`backend/agent/.env.example`

| 变量 | 开发值 | 说明 |
|------|--------|------|
| `STP_SCRIPT_ROOT` | `<repo>/backend/agent/scripts` | **必须覆盖**，默认值指向生产 NFS |
| `STP_SCRIPT_RUNTIME_ROOT` | WSL 联调配 `/opt/stability-test-agent/scripts` | 扫描机≠运行机时须设 |
| `ANDROID_ADB_SERVER_PORT` | WSL Agent 必须 `5039` | 忘配则心跳正常但设备数为 0 |
| `DATABASE_URL` | `postgresql+asyncpg://...` | 同步驱动去掉 `+asyncpg` → `postgresql://...` |

---

## 开发陷阱

- **WSL 安装**：必须 rsync 到本地 FS 再运行；`/mnt/` 下有 CRLF + 权限问题；安装前 `sed -i 's/\r$//'`
- **设备租约紧急释放**：`UPDATE device_leases SET status='RELEASED', released_at=now() WHERE device_id=<id> AND status='ACTIVE'`
- **设备 ADB 端口**：WSL Agent 必须配 `ANDROID_ADB_SERVER_PORT=5039`，否则心跳正常设备数为 0
- **pytest 调用**：必须 `python -m pytest`，裸 `pytest` 落到另一套解释器

---

## 决策记录

| 日期 | ADR | 决策 |
|------|-----|------|
| 2026-06-21 | 0025 | 方案 C 存储：日志留 SSD、AEE 留 HDD、CIFS 仅汇总；取消 run_log_bundle |
| 2026-05-21 | 0024 | HttpOnly Cookie + CSRF + refresh 黑名单 + 生产 guard |
| 2026-05-06 | 0020 | Workflow→Plan + PlanStep；lifecycle 由行+直列字段重组 |
| 2026-04-28 | 0019 | Device Lease + capacity + fencing_token |
| 2026-04-20 | 0018 | Watcher 子系统主线 |
| 2026-04-12 | — | 双轨合并 Wave 7+8：兼容层移除 |

详细见 `docs/adr/`。

---

*2026-06-25*
