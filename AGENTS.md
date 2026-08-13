# AGENTS.md

## Dev commands

命令速查已迁出本文，按用途分散在：

| 你要找 | 看这里 |
|---|---|
| 后端 / 前端 / Agent 启动、迁移 | [`docs/development/local-development.md`](docs/development/local-development.md) |
| 测试（含 `./scripts/run_pytest.sh`、DB 前置、`python -m pytest` 的坑） | [`docs/development/testing.md`](docs/development/testing.md) |
| Lint 实际调用参数 | `.github/workflows/ci.yml` §lint（`ruff check backend/ tools/ scripts/`）；规则取向见 `ruff.toml` 抬头注释 |
| 前端 script 名 | `frontend/package.json` |

本文只保留**推导不出来**的部分：依赖三件套分工、lint 现状与取舍、空行注入污染、生产机调试约束、Test quirks。

**依赖清单**（后端三份，各有分工）：

| 文件 | 内容 | 谁用 |
|------|------|------|
| `backend/requirements.txt` | 仅运行时 | Dockerfile.backend（生产镜像） |
| `backend/requirements-dev.txt` | `-r requirements.txt` + pytest/testcontainers/ruff | CI、本地开发 |
| `backend/requirements.lock` | 全量精确版本 + hash | 可复现构建校验 |

本地装开发环境用 `pip install -r backend/requirements-dev.txt`。改了
`requirements.txt` 后**必须重新生成 lock**，命令见该文件抬头（须在 py3.11
下生成，CI 与镜像都是 3.11）。测试/lint 依赖不要加进 `requirements.txt`——
生产镜像带着 pytest 既浪费体积也是无谓的攻击面。

**Lint 现状**：2026-07 首次接入。ruff 已于 2026-08-04 清零（#155/#157），
ESLint 已于 2026-08-05 清零（#159/#161/#162）；CI 的 ruff 与 ESLint
（`--max-warnings 0`）均已改为阻塞，`continue-on-error` 已全部摘除。
ruff 暂未开 `UP`(pyupgrade) 族——全量 2239 处纯风格改写
（`Optional[X]`→`X | None` 882、`Dict`→`dict` 633 等），会淹没 F/B 的真实
信号，与缺陷无关。

**空行注入污染**：编辑器插件会逐行插空行，一次污染后每次 diff 都虚胖一倍。
检测/清理：`python tools/dev/collapse-blank-pollution.py [--check] <file.py>`
（先按文件整体空行率判定是否被污染，只动空行，并以 AST 比对保证语义不变）。
CI 有阻塞式门禁；本地钩子需一次性启用：`git config core.hooksPath .githooks`。

**本地启动**：根目录 Windows/WSL 启动脚本已移除；本地开发统一走
`docs/development/local-development.md`（Compose 或手动命令）。后端手动启动
默认不带 `--reload`（real device safety），显式需要热重载时再加。

**Verification order**: agent tests → tsc → build → (backend tests if PG available).

## 生产机调试约束

部分部署机上 **本机 PostgreSQL 即生产库**（生产 `DATABASE_URL` 在仓库根 `.env.backend`，指向 `stp`），而 **Docker testcontainers 仅用于隔离测试**。在生产机上改代码时务必遵守：

| 场景 | 做法 |
|------|------|
| 日常改码验证 | 优先 `pytest backend/agent/tests/`（不连 PG，~30s） |
| 必须跑 `backend/tests/` | 使用 **Docker testcontainers**（`conftest.py` 自动起临时 `postgres:16` 容器），**不要**把 `TEST_DATABASE_URL` 指到 `stp_dev`（docker-compose 开发栈容器库名，本机 PG 无此库）或任何生产库名 |
| 迁移试验 | 禁止对生产库执行 `alembic upgrade` 试跑；在开发机/CI 或容器内验证 |
| 手工 API 冒烟 | 可连生产控制面，但避免破坏性写操作 |

> **env 源单一化（2026-08-01）**：生产唯一 env 源是仓库根 `.env.backend`。
> `backend/main.py` 与 `backend/alembic/env.py` 都以它为准（ambient 环境变量仍最优先）；
> `backend/.env` 降级为纯本地开发覆盖，已移除其中失效且指向 `stp_dev` 的 `DATABASE_URL`。
> Alembic 与 `core/database.py` **都不再有兜底默认** —— 此前那个默认值是
> `stp:password@localhost:5432/stp`，直接点名生产库，只靠密码错才没连上。
> 现在统一走 `backend/core/env_source.resolve_database_url`：解析不到就
> RuntimeError；alembic 连接前还把目标（已脱敏）打到 stderr。

**禁止示例**（会在生产数据上建表/清库/跑用例）：

```bash
# ❌ 切勿在生产机这样跑后端测试（stp_dev 是 docker-compose 容器库名，本机 PG 无此库）
export TEST_DATABASE_URL=postgresql+psycopg://...@127.0.0.1:5432/stp_dev
pytest backend/tests/
```

**推荐示例**（隔离 PG，与 CI 一致）：

```bash
# 用户须在 docker 组（一次性：sudo usermod -aG docker $USER && newgrp docker）
unset TEST_DATABASE_URL   # 让 conftest 走 testcontainers
JWT_SECRET_KEY=test-secret python -m pytest backend/tests/path/to/test.py -q
```

- 未设置 `TEST_DATABASE_URL` 时，`backend/tests/conftest.py` 通过 Docker 拉起**独立**测试库，测完销毁。
- 若 `docker ps` 报 `permission denied`，将当前用户加入 `docker` 组后**重新登录**（或 `newgrp docker`），不要用生产 `DATABASE_URL` 代替。
- `ALLOW_SQLITE_TESTS=1` 仅适合少量用例；`test_agent_dual_write.py` 等仍需 PostgreSQL partial unique index，不能替代完整 backend 套件。

## Test quirks

- Backend pytest needs `TEST_DATABASE_URL` (PostgreSQL). Set `ALLOW_SQLITE_TESTS=1` for local SQLite (no PG required, but `test_agent_dual_write.py` skips on SQLite — needs PG partial unique index).
- `os.environ["TESTING"] = "1"` is set in `backend/tests/conftest.py` — this disables Redis/SAQ/APScheduler startup in lifespan.
- Backend full-suite can timeout locally due to session-scoped engine fixture. Run single files: `pytest backend/tests/api/test_dedup_scan_endpoints.py -x`.
- Agent tests (`backend/agent/tests/`) are self-contained — no DB/Redis, fast (~30s for 600 tests). Control-plane tests that need DB go in `backend/tests/`, not `backend/agent/tests/`.
- Frontend tests use vitest + jsdom, `@/` path alias maps to `src/`.
- WATCHER_SIGNAL invalidation is debounced 2s in `PlanRunDetailPage.tsx` — tests asserting refetch need `waitFor({ timeout: 4000 })`.

## scan/upload/merge 跨进程契约

以下规则的实现方在**控制面**，不在 `backend/agent/`，所以留在本文（始终加载）：

- **Control-plane merge**（`backend/services/dedup_scan.py:run_merge_sync` / `build_merge_argv`）：跑在 backend、读 NFS `dedup/`。argv **不是固定的**：
  - 先用 `scan_tool_supports_merge_files_list()` 跑一次 `start_log_scan.py -h` 探测能力（结果进程内缓存）。支持则写临时清单文件走 `-merge_files_list {listfile}`；
  - 不支持才回退 `-merge_files {全部 org_files 展开}`，且此路径有 30000 字符的 argv 上限（`_WIN_MERGE_ARGV_CHAR_LIMIT`），超限直接 `RuntimeError` 要求升级扫描工具 —— **host 规模上来后回退路径会先撞这堵墙**。
  - `-side` 由 `STP_DEDUP_SCAN_TAG` 决定：tag 含 `factory`（大小写不敏感）→ `-side factory`，否则 `-side shanghai`（默认）。
- **SAQ 链**（`backend/tasks/saq_tasks.py:scan_task`）：`scan_task` → `merge_task`（`extract_task` 由 merge 链式触发）。事件目录上送仅经 EventUploader + `device_log_event`（DLE），无 `upload_task`。`scan_task` 轮询 NFS 上各 host 的产物**最多 300s**，等齐即提前跳出，等不齐也照样 enqueue 后继 —— 不是「齐了才 enqueue」。这是有意的：为一台慢/坏 host 扣住整轮，等于把「部分报表」换成「零报表」，而零报表正是这条链路要消灭的形态。缺口靠日志与 `PlanRun.run_context.archive` 显性化，不靠拦住后继：
  - 完备性由 `dedup_scan.count_hosts_with_scan_artifacts(run_id, triggered, since=...)` 判定，三个维度都必须收窄：按 **host 去重**而非产物文件数（每台 host 上送 2 个 `*_org*.xls`，拿文件数跟 host 数比会让「一台上送完毕」冒充「全部齐了」）；**限定在本轮 triggered 集合内**（增量扫描复用同一 `plan_run_id`，上轮别的 host 的旧产物会顶替本轮触发 host 的名额）；且**限定在 `since` 水位线之后**（同一台 host 上一轮留下的产物会在本轮首检就计数，合并过期报告）。`since` 取下发 `scan_now` 之前的时刻。三种误判的后果相同：慢的 host 被漏出合并，或合并的是过期报告。
  - 零产物记 `saq_scan_no_artifacts`（ERROR），部分产物记 `saq_scan_partial_artifacts`（WARNING）；两者都写 `run_context.archive`（`hosts_triggered` / `hosts_with_artifacts` / `scan_artifacts_registered`）。否则 Agent 侧扫描失败只有本地一条 WARNING，PlanRun 照报 SUCCESS 却没有任何报表。
- **hot-update 的 env 同步分级**（`backend/services/agent_env_sync.py`）：控制面自己也读的键**不能**原样下发。`STP_DEDUP_SCAN_PYTHON` / `_SCRIPT` 必须经 `STP_AGENT_` 前缀的源键映射；Agent 的 `STP_NFS_ROOT` 由 `STP_AEE_NFS_ROOT` 镜像（旧脚本 env），不下发控制面本机 `STP_NFS_ROOT`。`_FLEET_ENV_KEYS` 只放两边同值的键（含 `STP_DEVICE_LOG_EVENT_ENABLED` / `STP_EVENT_UPLOADER_ENABLED`，#218）。推送后远端会校验 `AGENT_PATH_ENV_KEYS` 的值在 Agent 上确实存在，缺失项经 `env_paths_missing` 回传并记 ERROR。hot-update 远端脚本**先合并 `.env` 再 restart**；勿在 hot-update 未返回前抢 `reload_config`。
- **reload_config**（路由 `backend/api/routes/dedup.py` 的 `POST /api/v1/plan-runs/hosts/{host_id}/reload-config`）：经 `emit_agent_control` 下发 SocketIO `reload_config` 命令，让 Agent 重读安装目录 `.env` 并热刷新运行时配置，无需重启进程。Agent 侧实际刷新的三样见 `backend/agent/CLAUDE.md`。
- **风险评级**（`backend/services/report_service.py:aggregate_risk_summary_from_signals`）：从 `job_log_signal.extra->>'event_subtype'` 聚合（**观测层**；上送/extract 权威是 `device_log_event`，见 ADR-0028 §实体职责），按 `_RISK_RATING_RULES` 定级：

| 级别 | 触发条件 |
|------|---------|
| **S**（致命） | SWT / Fatal NE / Fatal JE / HWT / Kernel (KE) / HW Reboot / HANG — 任 1 次 |
| **A**（高） | ANR ≥ 10 / JE ≥ 3 / NE ≥ 2 / Java ≥ 3 |
| **B**（低） | 其余非零 |

**NFS 路径约定**（控制面与 Agent 共用，统一入口见 `backend/agent/aee/paths.py` / `backend/core/storage_root.py`，#172）：

| 对象族 | 布局 |
|--------|------|
| JobArtifact 文件（watcher puller 默认落点 + LOCAL promote） | `{root}/jobs/{job_id}/` |
| 事件目录（EventUploader / DLE 上送，含 HddSpill enqueue） | `{root}/devices/{plan_run_id}/` 或 `{root}/devices/unassigned/{event_id}/` |
| 扫描报告 / extract 输出 | `{root}/dedup/{run_id}/`、`{root}/jira/{run_id}/` |

中心存储根：**只配置 `STP_AEE_NFS_ROOT`**（`STP_AEE_CIFS_ROOT` / `STP_WATCHER_NFS_BASE_DIR` 为弃用回落，计划删除）。`STP_AEE_LOCAL_ROOT` 为按机 L1 路径，hot-update **不**覆盖（#235）。

`job_id IS NULL` 的 orphan `job_log_signal`：不进 PlanRun watcher-summary；admin 清单 `GET /api/v1/log-signals/orphans`。

## Agent 子系统细则（按需加载）

纯 Agent 侧实现，只在改对应目录时加载：

- **AEE 崩溃检测链**（Reconciler / inotifyd 双路径、ZZ_INTERNAL 解析、监测目录）→ `backend/agent/aee/CLAUDE.md`
  - **#220**：生产只扫 MTK；UNISOC/QCOM 保留 stub 入口、默认跳过；勿扩白名单
- **ScanRunner / UploadManager**（`start_log_scan.py` 的非显然参数、自动发现规则、`reload_config`）→ `backend/agent/CLAUDE.md`

## Key env vars

| Var | Where | Purpose |
|-----|-------|---------|
| `STP_AEE_NFS_ROOT` | Backend + Agent | **中心存储（CIFS）** 挂载点主键（dedup/devices/jira）；过渡 UNC 在控制面同机 15.253 |
| `STP_AEE_LOCAL_ROOT` | Agent only | 本机 L1 AEE 根；**按机配置**，hot-update 不下发（#235） |
| `STP_DEDUP_SCAN_PYTHON` | Backend + Agent | Python interpreter for scan tool — **值按角色不同**，见下 |
| `STP_DEDUP_SCAN_SCRIPT` | Backend + Agent | `start_log_scan.py` path — **值按角色不同**，见下 |
| `STP_AGENT_DEDUP_SCAN_PYTHON` / `_SCRIPT` | Backend only | Agent 侧的 scan 工具路径，hot-update 写进 Agent 的无前缀键 |
| `STP_AEE_LOCAL_ROOT` | Agent | HDD root for AEE events (e.g. `/mnt/hdd/aee_events`) |
| `STP_SCRIPT_ROOT` | Backend | Script catalog scan source（**必须显式设置**；未设 scan 503） |
| `STP_WATCHER_ENABLED` | Agent | Watcher subsystem gate (default `true`) |
| `STP_DEDUP_AUTO_SCAN` | Backend | Terminal auto-dedup trigger (default `1`) |
| `AUTO_ARCHIVE_POLL_INTERVAL_SECONDS` | Backend | auto_archive_sweep interval (default 120) |

See `backend/.env.example` and `backend/agent/.env.example` for full list.  
角色/别称（口头 CIFS/NFS = 中心存储 ≠ 控制面 ≠ 健康页）：[`docs/design/2026-storage-roles-and-aliases.md`](docs/design/2026-storage-roles-and-aliases.md)。

## Key conventions

> 主清单在根 `CLAUDE.md`，分散在三节：
> §架构不变量（唯一 action 类型 `script:<name>`）、
> §关键约定（`default_params` 不可变、表名单数、`types.ts` 权威源、`max_concurrent_jobs` 已删、Pydantic v2）、
> §环境变量 + §开发陷阱（`ANDROID_ADB_SERVER_PORT=5039`）。此处只补它没有的：

- Production Agent needs `AGENT_SECRET` env for SocketIO auth.
- `ORMBaseModel` (`backend/api/schemas/base.py`) auto-serializes datetime to ISO-UTC via `field_serializer(when_used="json")`.
- **PR 合入**：仓库已开启 Auto-merge；`.github/workflows/enable-auto-merge.yml` 自动给同仓库非 draft PR 挂 auto-merge（merge commit，fork PR 不启用），并维护 `code-rabbit-gate` 状态作为 **best-effort 参考门禁**：仅当 CodeRabbit 对**当前 head** 有明确终态决策时生效——APPROVED → 通过；CHANGES_REQUESTED → 阻断；skipped / rate limited / paused / 无当前 head 决策 → 不阻断，由 lint / CodeQL / pr-typecheck / pr-compileall / pr-agent-tests 等稳定 required checks 把关合入。不要手动点 Merge。
- **CodeRabbit 复评（参考意见）**：CodeRabbit 因配额限制实际使用不稳定，定位为参考而非硬门禁。`.coderabbit.yaml` 已关 `auto_incremental_review`，push 修复后不会自动复评；需要它对当前 head 给出新结论时，在 PR 评论 `@coderabbitai review` 显式触发。旧 commit 上的 CHANGES_REQUESTED 不构成当前 head 的阻断决策；其不可用（rate limit / skipped）时不阻塞 auto-merge。
- **CI 分层（2026-08-07）**：PR 只跑轻量 job（lint / pr-typecheck / pr-compileall / pr-agent-tests）；全量 backend-test（PG + pytest）、frontend-check（vitest + build）、docker-build 仅在 push main、workflow_dispatch 或 post-merge 兜底运行。auto-merge 的 merge commit 不触发 on: push / closed / workflow_run（GITHUB_TOKEN 级联限制），由 `main-ci-backstop.yml` 每 15 分钟检查 main 尖端是否已有全量 CI、没有则显式 dispatch；`enable-auto-merge.yml` 的 closed 事件 job 仅覆盖手动合入。PR 合入前不跑 PG/vitest/docker，风险由合入后全量兜底；需要“合并前全量校验”时应引入 Merge Queue。
- **全量 CI 失败通知（2026-08-13）**：`main-ci-backstop.yml` 失败会自动开 `ci/backstop-failed` issue（同 label 去重、只追加评论），恢复通过后自动关闭；Dependabot npm 拆为 `frontend-patch-minor`（自动合入）与 `frontend-major`（人工评审）两组，typescript 的 semver-major 更新被 ignore（typescript-eslint 8.x peer 上限 <6.1）。

## Documentation

- **Entry**: [`docs/DOC-MAP.md`](docs/DOC-MAP.md) — PRD / ADR / design / acceptance layers.
- **Hub**: [`docs/README.md`](docs/README.md) — full documentation center.
- **Design**: [`docs/design/`](docs/design/) — system, backend, frontend, agent (aligned with code).
- **ADR-0025**: [`docs/adr/ADR-0025-phase4-architecture-alignment.md`](docs/adr/ADR-0025-phase4-architecture-alignment.md) — Plan C architecture.
- **Pipeline timing**: [`docs/design/06-realtime-and-background.md`](docs/design/06-realtime-and-background.md) §9 — scan/upload/merge sequence + five-trigger table.
- **Acceptance**: [`docs/acceptance/`](docs/acceptance/) — Sprint 2/3/4 matrices + real-device verification template.

## Production access (for ad-hoc diagnostics)

> 这些是**只读运维凭据源**，用于 SSH/控制面诊断；写操作仍需走 PR 流程。所有路径已 `chmod 0600`/`0700`，泄露风险低。

| 用途 | 凭据源 | 使用 |
|------|--------|------|
| SSH 到 20 台 Agent host | `/home/debian13/hosts.ini` (`[android]` 段 IP + `[android:vars]` 的 `ansible_user` / `ansible_password`) | `ssh android@<ip>`，`sudo -n` 免密可提权到 root。opencode 本机 `~/.ssh/id_ed25519` 已 ssh-copy-id 到 20 台 host，免密 SSH 已通。 |
| Backend DB（生产 `stp` 库）| **仓库根 `.env.backend`** 的 `DATABASE_URL`（systemd `EnvironmentFile`，唯一生产 env 源）。`backend/.env` 是本地开发覆盖，**不含** `DATABASE_URL`，别从那里找 | 用 `/home/debian13/stability-test-platform/venv/bin/python`（含 sqlalchemy 2.0）+ `psycopg` 3 直连。**只读 SELECT 优先**，写须有迁移/PR。 |
| 控制面 admin token | **仓库根 `.env.backend`** 的 `STP_ADMIN_USER` / `STP_ADMIN_PASSWORD`；并需用**同一文件**的 `AGENT_SECRET` 头 `X-Agent-Secret` 绕 CSRF（前端 cookie session 才认 Origin/Referer）。`backend/.env` 里那个 `AGENT_SECRET` 是**陈旧值，控制面与 20 台 Agent 都不认**，照它操作会被拒 | `curl -H "X-Agent-Secret: <AGENT_SECRET>" -F "username=stp-admin&password=<ADMIN_PASS>" http://127.0.0.1:8000/api/v1/auth/token` → `Authorization: Bearer <token>` 调用任意 `/api/v1/...` 路由。 |
| Agent `.env` 错配修复历史 | 20 台 host `STP_AEE_LOCAL_ROOT` 曾错配为 `/home/debian13/...`（android 用户无权写 `/home/debian13`）→ AEE Reconciler 100% 启动崩溃。已于 2026-07-25 改为 `/home/android/aee-local` / `/home/android/aee-nfs`，全部重启生效。详见 #72 + `docs/operations/adr-0026-admission-and-scale-gray-rollout.md`。|

**安全约束**：不要把上面任何一个具体密码 / token 直接填到 commit 文件 / log 输出 / PR diff；AGENTS.md 仅文档化「在哪里能找到」，不复制明文。`backend/.env`、`backend/agent/.env`、`/home/debian13/hosts.ini`、`.env.backend`、`opencode.json` 都已在 `.gitignore`。
