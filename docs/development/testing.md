# 测试指南

---

## 1. 测试目录

| 目录 | 范围 | 运行 |
|------|------|------|
| `backend/tests/` | 控制面 API、服务、集成 | `pytest backend/tests/` |
| `backend/agent/tests/` | Agent、pipeline、watcher | `pytest backend/agent/tests/` |
| `frontend/src/**/*.test.tsx` | 组件、页面 | `cd frontend && npx vitest run` |
| `tests/`（根） | 脚本、Ansible、迁移契约 | `pytest tests/` |

**不要混跑**：agent 与控制面测试依赖不同 fixture。

---

## 2. 后端测试环境

| 变量 | 说明 |
|------|------|
| `TEST_DATABASE_URL` | PostgreSQL（CI 默认） |
| `ALLOW_SQLITE_TESTS=1` | 本地无 PG 时部分用例 |
| `TESTING=1` | `conftest.py` 自动设；禁用 Redis/SAQ/Scheduler lifespan |

```bash
# 本地 SQLite 快速验证
ALLOW_SQLITE_TESTS=1 pytest backend/tests/api/test_plan_run_aggregation_endpoints.py -q
```

---

## 3. 前端测试

```bash
cd frontend
npx vitest run
npx tsc --noEmit
```

- 环境：jsdom（`vitest.config.ts`）  
- 路径别名：`@/` → `src/`

---

## 4. CI 流程

`.github/workflows/ci.yml`：

1. `compileall backend/`  
2. `pytest backend/tests/`（PG service）  
3. `tsc --noEmit` + `npm run build`  
4. Docker build（可选）

---

## 5. 验收文档映射

| 文档 | 用途 |
|------|------|
| [`acceptance/00-platform-smoke.md`](../acceptance/00-platform-smoke.md) | 平台级 AC ID |
| [`acceptance/2026-plan-c-sprint2-3.md`](../acceptance/2026-plan-c-sprint2-3.md) | 方案 C |
| [`preprod-drill-runbook.md`](../preprod-drill-runbook.md) | 手工发版 |

---

## 6. 编写约定

- 新 API：在 `backend/tests/api/` 补用例；优先 PG 兼容或标 `@pytest.mark.skipif`  
- 新 Agent 行为：`backend/agent/tests/`  
- 新 UI 交互：Vitest + 语义查询；关键流 PlanRunDetailPage 已有集成测试参考  
- 主链变更：更新 `integration/test_main_chain_happy_path.py`

---

## 7. 已知限制

- 真机 ADB/NFS 不在默认 CI  
- 部分 backend 全量需 PG testcontainer，本地可能慢/超时  
- E2E dedup extract 需 NFS 环境（#30）
