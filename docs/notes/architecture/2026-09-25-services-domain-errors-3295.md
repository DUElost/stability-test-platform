# #3295 services 不感知 HTTP：agent_* 域清零（C2 基线 36 → 19）

Status: implemented
Class: architecture

关联：[#3295](https://github.com/DUElost/stability-test-platform/issues/3295)（本单）、
[#3291](https://github.com/DUElost/stability-test-platform/pull/3291)（`.importlinter` 基线来源）、
[#1520](https://github.com/DUElost/stability-test-platform/issues/1520)（上帝文件拆分——HTTPException 随代码搬进 services 的源头）、
[#2638](https://github.com/DUElost/stability-test-platform/issues/2638)（升级门禁 except 元组可达性契约，本次迁移必须原样保住）。

## Decision

13 个 `backend/services/agent_*.py` 不再 import `fastapi` / `backend.api.error_helpers`：

- 新增 `backend/services/errors.py`（零框架依赖）：`ServiceError` 基类 +
  `BadRequest(400) / Forbidden(403) / NotFound(404) / Conflict(409) /
  BatchTooLarge(413) / UpgradeRequired(426) / Timeout(504)`；
  `detail` 原样承载重构前 `HTTPException.detail` 的载荷（字符串或端点既定的结构化 dict），
  `status` 是异常类的语义归属（纯 int）。
- 新增 `backend/api/error_handlers.py`：
  - `register_domain_exception_handlers(app)`（main.py 接线）为 `ServiceError` 基类注册
    统一 handler，渲染 `JSONResponse(status_code=exc.status, content={"detail": exc.detail})`
    ——与 FastAPI 默认 HTTPException 渲染逐字一致；
  - 升级门禁的 `Host*` 领域异常映射（`raise_upgrade_gate_http` + `UPGRADE_GATE_DOMAIN_ERRORS`
    元组）从 services 整体搬进本文件，`agent_api.py` 端点只留 3 行 try/except 瘦包装。
- `agent_log_signals` 内部 `except HTTPException` → `except ServiceError`（拒绝原因串
  `lease_check_failed(<status>): <code>` 逐字不变）。
- `.importlinter` C2 基线删除 17 行（13×fastapi + 4×error_helpers，36 → 19）；
  `unmatched_ignore_imports_alerting = error` 同 PR 强制。
- 测试同步：17 个测试文件中对**转换过的服务/路由函数**的直接断言
  `pytest.raises(HTTPException)`/`.status_code` → `pytest.raises(ServiceError)`/`.status`；
  TestClient 响应类断言（status code + body）**零改动**——它们是「对外响应逐字不变」的守卫。

`agent_upgrade_gate` 的 `Host*` 族**不进**统一 handler：该族异常今天还会从
`release` 路径与 `hosts.py` 未捕获分支冒到 `global_exception_handler`（表现为 500）；
若按类型全局注册 handler 会把那些路径顺带改写 404/409/504——「对外响应逐字不变」
禁止这种超出本单射程的顺带变化。映射与元组同处一文件反而比原来（映射在 service、
元组在 service 函数体内）更难漂移。

## Alternatives

- **异常类自带 `status_code` 属性、handler 只读属性**（最省事的 HTTPException 换皮）：
  被否——services 继续以 HTTP 状态码为设计词汇，#3295 的语义目标落空，C2 只消了 import
  没消 HTTP 心智。现设计里 `status` 是**类级语义归属**，站点只写 `NotFound("job not found")`。
- **`Host*` 族也注册全局 handler**：见 Decision——会顺带改写其他调用路径的响应形态。
- **可选步骤「13 文件收进 `backend/services/agent_api/` 子包」**（issue 标为可选）：本单不做——
  目录变更会放大与 #3297/#3299 后续切片的 diff 冲突面，且 C2 验收不依赖它；留作 Revisit。

## Verification

- `lint-imports`（PYTHONPATH=.）：5 合约全 KEPT，C2 `KEPT (19 ignored imports)`，
  基线只删不增（`unmatched_ignore_imports_alerting=error` 通过，即 13 文件确已无直接边）。
- `backend/tests/services` 1318 passed；`backend/tests/api` 1311 passed；
  `backend/tests/scheduler` 179 passed；根 `tests/` 1841 passed / 18 skipped；
  定向集含 `test_agent_api*`、`test_patrol_heartbeat_api`、`test_agent_routes`、
  `test_agent_dual_write`（并发 gather 失败收集改判 `ServiceError`）、
  `test_upgrade_gate_api` + 重写的 `test_agent_upgrade_gate`（#2638 可达性契约改钉
  `UPGRADE_GATE_DOMAIN_ERRORS` 元组 + 端点函数，工厂表双向相等断言保留）。
- `ruff check` 全绿；`scripts/run_gates.py check:quick` 通过（god-files 门：
  `agent_api.py` 408/411——翻译器未堆进路由文件，改住 `error_handlers.py`）。
- CI 复跑（PR 路径 required checks）：**pending**，合入前以 FIFO auto-merge 结果为准。

## Revisit

- 剩余 19 行 C2 基线（plan_run_*/project_*/artifact_zip 等）沿同一出口分批清；
  `artifact_zip`/`job_artifact_download` 的 `FileResponse/Request` 形态需要先定义
  服务层返回值类型（不属于「异常翻译」射程）。
- 子包收编（issue 可选步骤）若后续要做，单独开单，不与基线清零混 diff。
- 若某端点想把 `Host*` 族并入统一 handler（减少一处显式 try/except），前提是先枚举
  该族全部传播路径并逐条证明响应不变。
