# 测试指南

> **最后更新**：2026-07-15  
> 命令速查：根目录 [`AGENTS.md`](../../AGENTS.md)

---

## 1. 目录与边界

| 目录 | 范围 | 依赖 |
|------|------|------|
| `backend/agent/tests/` | Agent、pipeline、watcher | **无 PG**，日常优先 |
| `backend/tests/` | 控制面 API / 服务 / 集成 | PostgreSQL（testcontainers 或显式 URL） |
| `frontend/**/*.test.tsx` | 组件 / 页面 | vitest + jsdom |
| `tests/`（根） | 脚本、Ansible、迁移契约 | 按文件而定 |

**不要混跑** agent 与控制面 fixture。一律 `python -m pytest`（裸 `pytest` 可能落到错误解释器）。

包装脚本（可选加载 `.env.test`）：

```bash
cp .env.test.example .env.test   # 首次
./scripts/run_pytest.sh backend/agent/tests/ -q
```

根目录 `pytest.ini`：`pythonpath=.`、`asyncio_mode=auto`。

---

## 2. 生产机 / 本机业务库约束

部分部署机上 **本机 PostgreSQL 即生产库**。在此类主机改码时：

| 场景 | 做法 |
|------|------|
| 日常验证 | 优先 `pytest backend/agent/tests/` |
| 必须跑 `backend/tests/` | **Docker testcontainers**（`conftest` 在未设 `TEST_DATABASE_URL` 时拉起临时 `postgres:16`） |
| 迁移试验 | 禁止对业务库试跑 `alembic upgrade`；在 CI / 容器 / 开发机验证 |
| ❌ 禁止 | `TEST_DATABASE_URL=...@localhost:5432/<业务库>` |

`ALLOW_SQLITE_TESTS=1` 仅覆盖子集；`test_agent_dual_write.py` 等仍需 PG partial unique index。

用户须在 `docker` 组（`permission denied` 时 `usermod -aG docker` 后重新登录），不要用生产 `DATABASE_URL` 代替测试库。

---

## 3. 后端测试环境

| 变量 | 说明 |
|------|------|
| `TESTING=1` | conftest 设置；禁用 Redis/SAQ/Scheduler lifespan |
| `TEST_DATABASE_URL` | 仅隔离库；生产机请 **unset** 走 testcontainers |
| `ALLOW_SQLITE_TESTS=1` | 本地无 Docker/PG 时的退路 |
| `JWT_SECRET_KEY` | 必设（见 `.env.test.example`） |

```bash
unset TEST_DATABASE_URL
JWT_SECRET_KEY=test-secret python -m pytest backend/tests/path/to/test.py -q
```

协议 / abort / 链相关用例映射见 [`../design/07-execution-protocol.md`](../design/07-execution-protocol.md) §8。

执行协议 migration 前：

```bash
python -m backend.scripts.migration.preflight_execution_protocol
```

---

## 4. 前端测试

```bash
cd frontend
npx vitest run
npx tsc --noEmit
npx vitest run src/pages/execution/PlanRunDetailPage.test.tsx
```

- `@/` → `src/`  
- PlanRun capabilities（如 `final_archive`）由后端权威控制；测试须显式 mock，勿依赖「缺省为 true」。  
- Watcher 信号防抖 2s：断言 refetch 用 `waitFor({ timeout: 4000 })`。

---

## 5. CI（`.github/workflows/ci.yml`）

1. `compileall backend/`  
2. `pytest backend/tests/`（PostgreSQL service）  
3. `npx vitest run` · `tsc --noEmit` · `npm run build`  
4. Docker build（依赖前序 success）

---

## 6. 验收文档

| 文档 | 用途 |
|------|------|
| [`../acceptance/00-platform-smoke.md`](../acceptance/00-platform-smoke.md) | 平台级 AC |
| [`../acceptance/2026-plan-c-sprint2-3.md`](../acceptance/2026-plan-c-sprint2-3.md) | 方案 C |
| [`../preprod-drill-runbook.md`](../preprod-drill-runbook.md) | 手工发版 |

---

## 7. 编写约定

- 新 API → `backend/tests/api/`  
- 新 Agent 行为 → `backend/agent/tests/`  
- 新 UI → Vitest；主机表见 `ExpandableHostTable.test.tsx`  
- 主链 → `integration/` + 更新 `07-execution-protocol` / `01-execution-pipeline` 若契约变化  

## 8. 已知限制

- 真机 ADB/NFS 不在默认 CI  
- 控制面全量可能较慢；可按文件跑 `-x`  
- E2E dedup extract 需共享存储环境  
