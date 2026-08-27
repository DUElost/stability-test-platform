# stability-test-platform

开发命令、测试运行方式、约定见：

@AGENTS.md

设计文档索引见：

@docs/DOC-MAP.md

Cursor IDE 按域规则见 `.cursor/rules/`（薄适配层，权威内容仍以本文与 AGENTS.md 为准），说明见：[docs/development/cursor-rules.md](docs/development/cursor-rules.md)（按需查阅）

---

## 架构不变量

- **app** = `socketio.ASGIApp(sio_server, fastapi_app)` — 合并 ASGI 挂载（`backend/main.py:207`）
- **Plan 无 lifecycle 列**：由 PlanStep 行 + `patrol_interval_seconds`/`timeout_seconds` 在 dispatcher 阶段组装为 `pipeline_def.lifecycle`（唯一事实源）
- **Redis 仅做 SAQ broker**，不存业务数据
- **Production guard**：`ENV=production` 时强制 `AUTH_COOKIE_SECURE=1` + `AUTH_COOKIE_SAMESITE ∈ {lax,strict}` + `STP_CSRF_ENABLED` 开启，否则 `RuntimeError`（ADR-0024）
- **Pipeline 仅接受 `lifecycle` 顶层键**：`stages`/`phases` 格式被拒绝（`backend/agent/pipeline_engine.py:325-332`）
- **步骤两层钟**（#115 阶段 1，`pipeline_engine.py`）：总时长钟 `timeout_seconds`（缺省 300，安全网）+ 停滞钟 `stall_seconds`（**缺省 0=关闭**——全部脚本 `capture_output=True` 全程零输出，「任意输出=活」等于「全体判死」）。停滞钟按**逐个 PlanStep 显式打开**；`STP_STEP_STALL_SECONDS` 环境变量会**全机启用**（灰度后期开关，须等全部相关脚本接入打戳后才能设置），两者都要求脚本先接入 `PROGRESS` 打戳（阶段 2）。`timeout_seconds=0`（不限）已按步骤开门（2026-08-04，schema step 级 minimum 1→0），但**只对已接打戳 + 显式配了 `stall_seconds` 的步骤安全**——没打戳的步骤配 0 仍是"卡死永远占槽位"。完整协议见 `docs/design/2026-08-step-stall-detection.md`
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

- **Job**（集中校验见 `backend/services/state_machine.py` 的 `VALID_TRANSITIONS`）：`PENDING → RUNNING → COMPLETED/FAILED/ABORTED`；`PENDING → FAILED`（recycler 派发超时）；`PENDING → ABORTED`（PlanRun abort）；`RUNNING → UNKNOWN`（recycler/watchdog/reconciler 心跳超时或 patrol stall——**不是**直接到 FAILED）；`UNKNOWN → RUNNING`（grace 内 recovery/sync 恢复）或 `UNKNOWN → FAILED`（grace 到期）
- **PlanRun**（无集中状态机，靠 `_TERMINAL_PLAN_RUN_STATUSES` 终态守卫防覆盖）：新执行仅 `RUNNING → SUCCESS/PARTIAL_SUCCESS/FAILED`；`DEGRADED` 仅历史可读，不再生产。存在有意的 `FAILED → RUNNING`（precheck 失败后人工重试派发，`precheck/runner.py:retry_plan_run_dispatch`）
- **Agent 终态协议**：`/jobs/{id}/status` 与 `/heartbeat` 仅接受 RUNNING；COMPLETED/FAILED/ABORTED 只能通过 `/jobs/{id}/complete`，相同 payload 幂等、冲突 payload 返回 409。

---

## 方案 C 存储（ADR-0025）

详见：[docs/design/2026-plan-c-storage-and-access.md](docs/design/2026-plan-c-storage-and-access.md)（按需查阅）

| 存储 | 用途 | 路径 |
|------|------|------|
| Agent SSD | 运行日志（唯一副本） | `logs/runs/{job_id}/` |
| Agent HDD | AEE + mobilelog + bugreport | `STP_AEE_LOCAL_ROOT`（默认 `/mnt/hdd/aee_events`） |
| 中心存储（CIFS / NFS） | 汇总 xls、按需事件、HDD 溢出；**不含**运行日志 | 挂载点 `STP_AEE_NFS_ROOT`。过渡 UNC 在 8.202；「15.4」是角色外号/目标。口头 CIFS/NFS 都指此角色 |

角色/别称：[docs/design/2026-storage-roles-and-aliases.md](docs/design/2026-storage-roles-and-aliases.md)。**已取消（勿依赖）**：运行日志上送 CIFS、`run_log_bundle` JobArtifact、patrol cycle `snapshots/`。

---

## 脚本目录契约（ADR-0020）

```
<STP_SCRIPT_ROOT>/<name>/v<version>/<entry>.{py,sh,bat,cmd}
```

- 一级 = 脚本名，二级 = v 开头版本号，入口 = 首个非 `_` 可识别文件
- `_` 开头的辅助模块扫描时跳过
- 扫描结果：created(INSERT) / skipped(sha256一致) / conflicts(sha256不一致,不动DB,须新建版本) / deactivated(磁盘无,标false)
- **已发布版本目录的内容不可变**：`script.content_sha256` 是**扫描那一刻**的快照，也是 precheck 的期望值。原地改写 → conflicts 只记录不落库 → DB 期望值永久冻结 → 引用该脚本的 Plan 在准入阶段 `script_verify_failed`，**self-heal 推送也修不好**（推的是磁盘内容，对不上的是 DB）。CI 门禁 `tools/dev/check-script-version-immutability.py` 拦截（含 `_` 辅助模块——它们不计入 entry sha，改了连 conflicts 都不报）；`ruff.toml` 已把该目录加进 `extend-exclude`
- 逃生阀 `POST /scripts/scan?force_rebaseline=true`：把 conflicts 的 sha 重锚到磁盘，返回 `rebaselined[]`。仅 admin，且有在途 PlanRun（RUNNING/QUEUED/PRECHECK）时返回 409。**只用于契约已被上游破坏的既成事实**，正常改脚本一律新建版本
- WiFi 资源池注入是唯一打破「params 完全来自 default_params」的特例（`_inject_wifi_params` 对 `connect_wifi` 注入 `{ssid, password, pool_name, pool_id}`）
- 完整链路：文件 → `POST /scripts/scan` → DB.script → PlanStep → dispatcher `deepcopy(default_params)` → `pipeline_def` → Agent `ScriptRegistry.resolve` → `subprocess.run` → stdout JSON → step_trace → JobStatus → aggregator

---

## 环境变量（开发必设）

> 完整清单见 `backend/.env.example`、`backend/agent/.env.example`

| 变量 | 开发值 | 说明 |
|------|--------|------|
| `STP_SCRIPT_ROOT` | `<repo>/backend/agent/scripts` | **必须显式设置**；未设不再回落到 `STP_NFS_ROOT/scripts` |
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
| 2026-08-19 | 0030 | 多用例平台化管理(**Accepted** v1.8,P0+P1 双轨/M7;**P0 真机验收✅**2026-08-20 PlanRun #218,**P1 全部✅合入**(#404:P1a 实体+管理面 14 端点/双漂移检测器/原子写,P1b `plan.suite_id` 绑定+prepare 冻结 dispatch_suite+mtbf 步骤参数自动注入+precheck 五步门禁 suite_verify_failed+#402 守卫精确化+expected env 双层退役,P1c CLI tools/dev/mtbf-cases.py+文档收口),**D6 真机冒烟✅**2026-08-25 Run #224(suite_sha256==门禁 sha 逐字节),**mtbf 绑定翻转硬拒**(v1.8:未绑定 mtbf 计划派发即 SUITE_BINDING_REQUIRED,非 mtbf 不受影响);**未做**:P2 前端;**v1.4:D2 绑定=`plan.suite_id` 可空外键**,NULL=P0 文件真源模式(仅限非 mtbf 脚本)/非空=托管五步门禁):test_suite/test_case **配置层实体**(粒度=testpoint,含 1..N exec_descs,不进调度模型,唯一 action 不变量保持)+ 文件↔库双向(import/export/validate,runtask.xml 变生成物,消费面不变)+ 项目分化(project_id 可空=通用套件 / 必填=项目套件+apk_binding,相机 MTBF 频繁分化,APK↔项目严格对应)+ 外部管理面(REST 8000 + OpenAPI 真源 + CLI,不新增端口,全量审计)+ 快照留痕(suite_id/exported_sha256/apk_binding);**与 ADR-0029 显式和解**:test_suite 是 0029 非目标 ExecutionProfile 实体族**例外子集**,不复活挂起 D1/D4/D5/D7/D8/D9(套件项目匹配门禁 D3b 为本 ADR 自有),演化成通用 Profile 须另开 ADR;P0 脚本三件套(mtbf_setup/check/finish,可独立先行)→ P1 实体+管理面 → P2 前端+用例结果;结果主路径 report_json(P0 不扩 artifact 白名单);背景:[reviews/MTBF_MULTI_CASE_RESEARCH_2026-08-19.md](docs/reviews/MTBF_MULTI_CASE_RESEARCH_2026-08-19.md) |
| 2026-08-19 | 0029 | 项目分类域(**Accepted**,v2.4):TestProject **登记簿**定位(知识层:客户/关系/形态/jira 映射,adb 指纹读不出)+ 单层身份 + 正交 facet(产品线/客户/平台/形态,**不建层级树**);`/projects` **只列 USER 人工项目**(P1 六 key 为 SEED 回填,非产品面);型号经 `match_models` 精确映射(禁前缀推断);APK 差异由**脚本端设备路由**吸收(`backend=auto` 先例,路由表住工具目录,step_trace 记路由表 sha256),原参数分层/派发门禁/存储命名空间/**全局上下文 D1/D4/D5/D7/D8/D9 挂起**(复议触发条件见 ADR 修订记录,防兜圈子);新增 `jira_project_key`(唯一硬需求);P1 建表回填**已生产执行**(2026-08-19,完成标准=NULL 归零达成)→ P2 登记簿页 → P3 jira 自动带关键字 |
| 2026-07-16 | 0026 | 大规模化执行架构(目标态,分阶段落地中):PlanRun 准入队列 + Host OperationScheduler + 批量续租/O(1) 聚合;当前状态机不变,QUEUED/PRECHECK 待 feature flag 路径落地后生效 |
| 2026-06-21 | 0025 | 方案 C 存储：日志留 SSD、AEE 留 HDD、CIFS 仅汇总；取消 run_log_bundle |
| 2026-05-21 | 0024 | HttpOnly Cookie + CSRF + refresh 黑名单 + 生产 guard |
| 2026-05-06 | 0020 | Workflow→Plan + PlanStep；lifecycle 由行+直列字段重组 |
| 2026-04-28 | 0019 | Device Lease + capacity + fencing_token |
| 2026-04-20 | 0018 | Watcher 子系统主线 |
| 2026-04-12 | — | 双轨合并 Wave 7+8：兼容层移除 |

详细见 `docs/adr/`。

---

*2026-07-17*
