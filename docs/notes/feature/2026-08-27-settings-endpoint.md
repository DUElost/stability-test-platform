# 新增 GET /api/v1/settings 只读聚合端点（H1 修复）

- **日期**：2026-08-27
- **关联**：`docs/reviews/FRONTEND_UI_REVIEW_2026-08-27.md` H1（系统设置页硬编码假数据）
- **类型**：feature

## 决定了什么

1. **新增后端只读端点** `GET /api/v1/settings`（`backend/api/routes/settings.py`，
   `require_admin` 鉴权），聚合 8 个运行时真实来源字段：
   - `platform_name` / `timezone`：env `STP_PLATFORM_NAME` / `STP_TIMEZONE`，可覆盖，默认
     `Stability Test Platform` / `Asia/Shanghai`
   - `database_type` / `database_connected`：`engine.dialect.name` + 同步 `SELECT 1` 探测
   - `agent_heartbeat_interval_seconds` / `offline_threshold_seconds`：**导入控制面自身读用的
     同一模块常量**（`heartbeat.HEARTBEAT_INTERVAL_BASE` / `hosts.HOST_HEARTBEAT_TIMEOUT_SECONDS`），
     单一来源，不重复解析 env
   - 两个通知开关：聚合 `alert_rules` 中对应事件类型（`DEVICE_OFFLINE` / `RUN_FAILED`）的
     启用规则是否存在
2. **前端 SettingsPage 接入真实数据**（`frontend/src/pages/settings/SettingsPage.tsx`）：
   react-query `useQuery(['settings'])`，补全 loading（`PageSkeleton.Block`）/ error
   （`InlineError` + 重试）三态；删除全部硬编码值。
3. 响应走 `ApiResponse[T]` envelope + `ok()`，与 Phase 3+ 路由风格一致。

## 放弃的备选

- **方案 B（前端改名"配置概览"+ 只读标注）**：成本最低，但仍是"假数据"；用户选 A，
  做真实聚合端点。
- **设置端点做成可编辑（PUT）**：产品未定哪些配置可写、写到哪里（env / DB / 下发 Agent），
  本轮只做只读聚合，避免过度设计。

## 如何验证

- `backend/tests/api/test_settings_endpoints.py`：6 个用例（401 / 403 / admin 200 字段 /
  无规则开关 False / 启用规则开关 True / 禁用规则不置 True），testcontainers PG 下全过。
- 临时 uvicorn 8001 手工验证：真实返回
  `{"platform_name":"Stability Test Platform","database_connected":true,
  "agent_heartbeat_interval_seconds":20,"offline_threshold_seconds":300, ...}`
  （连接状态真实探测，非硬编码）。
- 前端 `eslint` + `tsc --noEmit` 通过。

## 何时重议

- 若产品需要**可编辑**设置：需要先定配置写入模型（env 下发 vs DB vs 按 host 下发），
  再扩展端点与 Agent 侧（`backend/agent/CLAUDE.md` 的 reload_config 链路）。
- 若需要把"设备离线通知/任务失败通知"开关做成**全局开关**（而非"是否有启用规则"聚合），
  需要新增配置存储。
