# 幽灵 `/complete` 404 速率可告警：请求级指标接线（#743 期望 2/3）

Status: implemented
Class: bug-fix

## Decision

- **接线，而不是新建指标**：`backend/core/metrics.py` 早已注册
  `stability_api_requests_total` / `stability_api_request_duration_seconds`
  与 `record_api_request(...)`，但**全仓零调用点**（grep 只有定义）——指标注册了、
  数据永远是 0。#729 的幽灵 `POST .../jobs/*/complete` 404 风暴因此只能靠人翻日志
  发现（持续约一个月）。本次只做**接线**：新增
  `backend/core/request_metrics.py`，**不改** `metrics.py`
  （该文件另有在办会话 `fix-77-counter-drift-metrics`），也不改任何请求语义。
- **中间件放请求链最外层**（`main.py` 中最后 `add_middleware`）：CORS 预检、
  CSRF 403、限流 429、路由 404 全部被统计。`call_next` 抛异常时记 500 后
  **照常抛出**（不吞异常、不改变错误处理链，也不会与
  `global_exception_handler` 重复计数）。
- **基数纪律**（决定 `endpoint` 标签怎么取）：
  - 命中路由 → **路由模板**（`/api/v1/jobs/{job_id}/complete`），天然有界；
  - 未命中路由（**正是幽灵端点形态**）→ **归一化路径**：UUID / 纯数字 / 长十六进制段
    折叠为 `{id}`，段数超限或非法形态归 `other`。
- **告警**：新增 `StabilityGhostJobCompleteEndpoint`
  （`rate(stability_api_requests_total{status_code="404", endpoint=~".*/complete"}[5m]) > 0.02`，
  `for: 15m`），归入既有分组 `stability-platform-silent-skips`——该组自述即
  「稳态不该出现的桶（#518 教训：**静默跳过只记日志无人盯 → 指标化**）」，与 #729 同构。
- **期望 3（Alertmanager 路由）**：`deploy/prometheus/alertmanager.yml` 按 `severity`
  路由（critical / warning），本规则用 `severity: warning` 即**自动接入既有路由**，
  **无需修改路由配置**。

## Alternatives

- **改从 access log 取信号**：`deploy/` 下无日志管道（只有 control-plane / nginx /
  postgres / prometheus），Prometheus 规则读不到 uvicorn access log → 不可行。
- **只删掉这段死代码**：会把 #743 期望 2 的**使能件**删掉，之后仍要重写一遍；
  且死代码本身不是问题，"注册了却永远没数据"才是。
- **`endpoint` 直接用 `request.url.path`**：每个 job id 都会成为新时间序列 →
  基数随业务实体数线性膨胀（仓库既有 `rate_limiter_evicted_total` 的注释即同类警告）。
  已用反例证明这样做会让对应用例转红。
- **用 `sum by (...)` 聚合**：仓库的告警契约解析器不认聚合语法（会把 `sum` 当成指标名
  → 报「未知指标 sum」），因此采用 `rate(...)` 裸选择器形态。

## Verification

| 项 | 命令 | 结果 |
|---|---|---|
| 新增行为用例 | `pytest backend/tests/test_request_metrics_middleware.py -q` | **9 passed** |
| 告警契约（结构层） | `pytest tests/test_prometheus_alerts_contract.py -q` | 通过（指标名与标签均在注册表内） |
| 告警场景（promtool 层） | 同上（内含 `promtool test rules`） | **通过**：新规则在真实标签形状上触发，注解逐字匹配 |
| 仪表板契约 | `pytest tests/test_grafana_dashboard_contract.py -q` | 通过 |
| app 导入链（含中间件注册） | `pytest backend/tests/api/test_dispatch_warnings.py -q` | **5 passed** |
| **反例实证** | 临时把 `endpoint_label` 改为直返 `request.url.path` | **2 条转红**（模板断言 + 归一化断言各一），恢复后 9 passed |
| 门禁 | `scripts/run_gates.py check:quick` | `ruff` **All checks passed**；`eslint` **未跑**（worktree 无 `node_modules`，`eslint: not found`——本次未改前端，交由 CI） |

归一化行为（**直接执行**验证，非目测）：

```
/api/v1/jobs/12345/complete                -> /api/v1/jobs/{id}/complete
/api/v1/jobs/550e8400-e29b-41d4-…/complete -> /api/v1/jobs/{id}/complete
/api/v1/jobs/abc123def456/complete         -> /api/v1/jobs/{id}/complete
/api/v1/jobs/12345/devices/67890/complete  -> /api/v1/jobs/{id}/devices/{id}/complete
/a/a/…×12   |   garbage                    -> other
```

## Revisit

- **阈值与 `for`**：`0.02 req/s`（≈1.2 次/分）与 `for: 15m` 是按「稳态为 0」定的。
  若上线后发现正常客户端也有零星 404（健康探测、探针等），需按实测基线调整，
  或改为按 `endpoint` 分档。
- **`_MAX_SEGMENTS = 8`**：超深路径统一归 `other`。若将来出现**合法**的深路径 API
  且需要单独观测，需提高上限或改为按前缀归并。
- **本接线只解决"看得见"**：它不改变任何"漏跑/阻塞"判定，也不修 #729 的根因
  （为何有客户端在打幽灵端点）——那需要在 Agent 侧继续跟进。
- **`metrics.py` 的接线面**：本次未动该文件（避免与在办会话冲突）。若后续
  `fix-77-counter-drift-metrics` 调整了 `stability_api_requests_total` 的形态，
  本 note 的标签约定（`method` / `endpoint` / `status_code`）需同步复核。
