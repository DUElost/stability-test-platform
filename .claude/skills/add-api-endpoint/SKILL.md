---
name: add-api-endpoint
description: 新增或变更后端 API 端点的全链路 SOP（Schema → 路由 → 服务 → 前端 types.ts → 前端客户端 → 测试）。触发时机：新增 /api/v1 端点、修改请求/响应模型、调整路由契约、后端改字段后需要同步前端类型。
---

# 新增 API 端点全链路 SOP

## 执行前置检查

- [ ] 先读 `docs/DOC-MAP.md` 定位领域权威文档；能扩展现有端点就不新增
- [ ] Pydantic 一律 v2（禁止 `.dict()` / `parse_obj`，用 `model_dump()` / `model_validate`）
- [ ] 数据库业务表名单数；新表走 alembic 迁移
- [ ] 触碰脚本 `default_params` 时：已发布版本的 `default_params` 不可原地修改（见
      `script-version-lifecycle`）

## 标准作业流程（SOP）

1. **Schema**：`backend/api/schemas/<domain>.py` —— 请求/响应模型继承 `ORMBaseModel`
   （`backend/api/schemas/base.py`）；响应统一包 `ApiResponse[T]`（`backend/api/response.py`）
2. **路由**：`backend/api/routes/<domain>.py` —— `@router.get/post(...)` +
   `response_model=ApiResponse[T]`；鉴权用 `get_current_active_user`（运维配置类一律
   `require_admin`）；错误抛 `HTTPException` + 结构化
   `detail={"code": "...", "message": "<人话>"}`（前端按 `code` 判定，不匹配文案）
3. **注册**：确认 router 已在 `backend/main.py` 的 `include_router` 列表（新文件必须加）
4. **服务层**：查询/写入落 `backend/services/*`，路由层只做参数与响应编排
5. **前端类型**：同步 `frontend/src/utils/api/types.ts`（**前端类型权威源**）——
   新增/变更的 interface 与字段必须在此对齐
6. **前端客户端**：在 `frontend/src/utils/api/<domain>.ts` 加方法
   （`apiClient.get/post` + `unwrapApiResponse<T>`）；被 react-query 查询的写入
   `frontend/src/utils/api/queryKeys.ts`
7. **测试**：后端 `backend/tests/api/test_<domain>_endpoints.py` 覆盖成功/权限/校验失败
   三条路径；前端在消费页面或组件的测试内覆盖

## 后置验证

```bash
python -m pytest backend/tests/api/ -q
npm --prefix frontend run type-check
python scripts/run_gates.py check:quick
```

三条命令全绿才算完成；类型检查失败回到步骤 5/6 修正，不得跳过。

## 踩坑守卫（负向约束）

- **漏同步 `types.ts` 是静默失败**：后端字段变了而前端类型不动，编译期不报错、运行时
  才炸——步骤 5 不可省；
- 枚举值必须双端对齐（后端 Enum ↔ 前端 union/常量），改一处必查另一处；
- 连接串、token、主机清单不得进入代码、文档、日志与 PR diff；测试连库一律隔离库
  （`TEST_DATABASE_URL` 或 testcontainers）；
- 已发布脚本版本的 `default_params` 不可原地修改（参数变化用新版本表达）。
