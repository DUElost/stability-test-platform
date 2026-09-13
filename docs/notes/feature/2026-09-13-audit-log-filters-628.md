# 审计日志新增用户名 / IP / 资源 ID 筛选（#628）

Status: implemented
Class: feature

## Decision

审计日志页此前只有「资源类型 / 操作 / 起止时间」三类筛选，排查「谁何时对这台设备/
这个 plan 做了什么」只能翻页目扫。按 issue 增补三个维度：

**后端**（`backend/api/routes/audit.py`）：`GET /audit-logs` 增加
`username` / `ip_address` / `resource_id` 三个 Query 参数，直接过滤审计行**自带的
快照列**——不 join `users`（审计行已存 username，且用户可能已改名/删除，快照才是审计
事实），`resource_id` 按 varchar 精确匹配（#832：该列与业务主键类型无关）。

**前端**（`AuditLogPage.tsx` + `management.ts`）：
- 三个文本输入与既有 select 同排；用户名带 `datalist` 候选（来自 `api.users.list`，
  失败静默降级自由输入）；
- **文本类不逐键发请求**：本地草稿在 `blur` / `Enter` 时提交（与 issue 对 IP 的要求
  一致，用户名/资源 ID 同语义）；select 仍即时生效；
- 提交后 `setPage(0)` 回第一页，与既有筛选行为一致；清空并提交即撤下该参数。

## Alternatives

- **用户名改走 `user_id`（issue 提到的「前端先查 username→id」路径）**：弃——
  审计行的 user_id 可能为 NULL（未登录/系统动作）或指向已删除用户，而 username 快照
  始终可读；多一次映射还把「查不到用户就筛不出」引入排查路径；
- **后端提供「distinct usernames」专用端点供下拉**：弃——`users.list` 已存在且页面
  本就 admin-only，新增端点收益仅覆盖「已删用户的历史名字」这一边缘；datalist 只是
  建议不是白名单，自由输入仍可命中快照值；
- **逐键防抖（debounce）而非 blur 提交**：弃——需要额外计时器状态与测试时钟；
  blur/Enter 语义更直白，且 issue 明确 IP 用 blur；
- **资源 ID 支持模糊/前缀匹配**：弃——`resource_id` 是跨资源类型的 varchar，
  前缀匹配会在 plan#1 与 host#10 之间产生噪声；精确匹配配合已选 resource_type 才可用。

## Verification

- **后端红绿（真实 PG testcontainer）**：新增
  `test_audit_log_filters_username_ip_resource_id`：三行不同快照 → 各维单独过滤计数、
  组合收窄（alice ∧ ip）、以及「部分匹配不成立」（`resource_id="6284"` 不等于 `62842`）：
  - 还原旧路由 → **1 failed**；本 PR → **9 passed**（该文件全量）；
- **前端红绿**：新增 `AuditLogFilters.test.tsx`（3 例）——下拉候选来自 users、
  **未提交不发请求**、blur 提交 IP、Enter 提交资源 ID、清空后参数撤下：
  - 还原旧页面 → **3 failed**（找不到筛选输入）；本 PR → **3 passed**；
- 前端全量 `npx vitest run` → **759 passed**；`npm run type-check`、
  `npx eslint src --max-warnings 0`、`python scripts/run_gates.py check:quick`
  （7 gates）均通过。

未做：生产库上的真实筛选目视（筛选语义由接口测试覆盖）。

## Revisit

- **测试文件名**：本单新建 `AuditLogFilters.test.tsx` 而非扩展 `AuditLogPage.test.tsx`
  ——后者正由 #1755（#750 窄屏修复）新建，同文件会与在窗 PR 冲突；两者合入后建议合并
  为单一页面测试文件；
- **无索引**：`audit_logs` 现有索引为 `(user_id, timestamp)` 与
  `(resource_type, resource_id)`；`username` / `ip_address` 过滤走顺序扫描。当前日志量
  下无碍，若审计表增长到百万级，应为 `ip_address`（或 `username`）补索引——属 DDL，
  独立 Requirement；
- **用户名快照与当前用户名漂移**：筛选按历史快照精确匹配；若用户改名，同一人的历史
  操作会分属两个名字。是否提供「按 user_id 聚合」视图留待需要时单独立项（依赖行内
  user_id 的完整率）。
