# #3296 C2 基线 14 文件清零（plan_run_* / project_* / ai_assistant 等）：36 → 5

Status: implemented
Class: architecture

关联：[#3296](https://github.com/DUElost/stability-test-platform/issues/3296)（本单）、
[#3295](https://github.com/DUElost/stability-test-platform/pull/3336)（同路径先行批，本单复用其产出）、
[#3297](https://github.com/DUElost/stability-test-platform/pull/3354)（C4 清零，本单基于其后的 main）、
[#3336 owner 评注](https://github.com/DUElost/stability-test-platform/issues/3296#issuecomment-5841935084)
（ai_assistant 4 处 catch 必须同步改的实施提醒）。

## Decision

单内 14 个 services 文件不再 import fastapi（含 4 个文件的 `Request` 类型注解），
全部改抛 `backend/services/errors.py` 领域异常，由 #3295 的统一 handler 翻译：

- 状态码 → 异常类映射沿用 #3295（400→BadRequest / 404→NotFound / 409→Conflict），
  本单新增 **`UnprocessableEntity`(422)**——project_mapping/project_registry 的
  「保留名 / SEED backfill / LEGACY 不可 promote」语义（422＝格式合法但业务规则不可受理，
  与 400 的入参缺陷不同类）。
- `plan_run_archive` / `project_mapping` / `project_registry` / `host_retirement` 的
  `request: Optional[Request]` 注解改 `Optional[Any]`：这些参数**只**透传给
  `record_audit(request=...)`（#3297 的 audit_writer 做结构化取用，零 web import），
  本仓 grep 确认无其它 `request.` 用途。
- **ai_assistant 4 处 `except HTTPException` 同 PR 改 `except ServiceError`**
  （owner 评注点名的实施提醒）：`dispatch.execute_dispatch_plan_run` 的 wifi 分支、
  `plan_run_ops.describe_manual_job_preview` / `run_manual_retry_job` /
  `run_manual_exit_job`；`_http_exception_to_runtime` 顺带改名
  `_service_error_to_runtime`（函数体不变——它只读 `.detail`，`ServiceError` 同样携带）。
  漏改的后果是异常穿过 except 直接上抛（112 行丢「未找到」预览文案、235/309 行丢
  RuntimeError 包装、109 行丢 detail 提示），而路由层全局 handler 会把领域异常正确
  渲染成 4xx——现有 API 测试**测不出**这一侧失效。
- 按评注建议补 `backend/tests/services/test_ai_assistant_domain_error_catches_3296.py`：
  4 条服务层直测，patch `load_job_in_run` / `require_active_wifi_pool` 抛领域异常，
  断言预览文案 / RuntimeError（含 detail message、且**不是** ServiceError 实例）。
- `.importlinter` C2 基线删 14 行（36 → **5**）：剩余 5 行全是返回
  FileResponse/RedirectResponse 的下载服务（artifact_zip、device_log_event_download、
  job_artifact_download、plan_run_artifact_download），单内明确另开一单
  （出口：services 返回文件描述、路由构造响应）。
- 38 处 raise 站点（单行 19 + 多行 15 + ……）逐字保留 detail 文本与 `from None`
  链控；3 个模块 docstring 的「异常沿用 HTTPException」措辞同步。

## Alternatives

- **422 复用 BadRequest(400)**：语义不同类（业务规则不可受理 vs 入参缺陷），且
  project_registry 的守卫函数名（`require_user_project`）与测试断言都按 422 表达；
  合并会把「保留名拒绝」与「字段缺失」压成同一状态码，前端提示面丢失区分。新增类成本
  三行，沿用。
- **`Request` 注解保留、只删 HTTPException**：C2 是对 fastapi **任何**直接 import 的
  禁令，`from fastapi import HTTPException, Request` 一行不删完基线就消不掉；而
  `Optional[Any]` 的类型损失由 audit_writer 的结构化契约（docstring 已注明 starlette
  Request 天然满足）兜住。
- **ai_assistant 侧改成捕获基类 `ServiceError` 还是逐类型**：选基类——原
  `except HTTPException` 捕的就是「服务层抛出的全部 HTTP 异常」，语义等价物就是
  基类；逐类型捕获会在服务侧新增 422/409 时静默漏接（正是 owner 评注警告的失效形态）。

## Verification

- `lint-imports`：**C2 KEPT (5 ignored imports)**（36→19→5，`unmatched_ignore_imports_alerting=error`
  强制 14 行删除真实发生）；C1/C3/C4/C5 不动。服务层 grep 确认 fastapi 残留仅剩
  4 个 out-of-scope 下载服务。
- `backend/tests` 全量（testcontainers PG，6G cgroup 顶）+ 根 `tests/`：见 PR
  （含 `test_project_registry` 11 处直测、`test_project_mapping`、`test_plans_api`、
  `test_host_retirement_api_1801`、`test_ai_assistant_endpoints` 等响应逐字守卫）。
- 新增 catch 护栏 4/4 绿；`ruff check backend/` 全绿。
- CI required checks：**pending**，合入前以 FIFO auto-merge 结果为准。

## Revisit

- 剩余 5 行（下载服务族）按单内出口另单：services 返回文件描述 + 路由构造
  FileResponse；涉及流式响应语义，不与异常翻译混做。
- `describe_manual_job_preview` 对「服务层异常→文案」的归并在 AI 助手推广后若出现
  更多 catch 点，可考虑统一 `_service_error_to_runtime` 供各 runner 复用（本单只改名
  不扩面）。
