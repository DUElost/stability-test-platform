# 2026-08-27 — G17 收尾：项目登记簿编辑入口

对应：方向 6 · G17 的「人工修正通道」；看板 #460。G17 代码侧接线已在
PR #467 落地（plan_run 源提单自动注入 `--set-project-key`），但存量键值
纠错（如 V552AA-VFFB 与权威映射不符）此前只能走 Swagger/curl——本篇把
编辑能力补进界面，让回填回归操作者自助。

## 决定了什么

- **形态**：详情页头部卡片加 admin 可见的「编辑」按钮 →
  `EditProjectDialog`（新组件）编辑 `_UPDATABLE_FIELDS` 全部六个字段
  （display_name + 四 facet + jira_project_key），不是只做 JIRA 单字段——
  后端可改面就这六项，一次对齐避免日后第二个对话框。
- **门控**：前端按 `useAuthSession().role === 'admin'` 隐藏入口（与后端
  `require_admin` 双保险）；ARCHIVED 项目禁用按钮（后端 409 兜底）。
- **空串语义**：提交时空白统一转显式 `null`，命中 PUT `fields_set`
  「显式传 null = 清空」契约（实测后端逐字段审计会记录清空动作）。
- **文案随现状更新**：JIRA 集成卡的「P3 落地后生效/由管理员在此维护」
  改为已接线的实况表述（自动带出 + 编辑入口指引）。
- SEED 回填项目同样开放编辑——它们正是需要被纠正的对象。

## 放弃的备选

| 备选 | 为什么放弃 |
|------|-----------|
| 只做 jira_project_key 单字段对话框 | 半年后大概率要为 facet 再开一个，两套表单更乱 |
| 列表页行内编辑 | 详情页已有完整上下文与测试基建；列表保持只读概览 |
| 提交仅发「有变化的字段」 | 后端对未变字段本来就短路；全量六字段简单且与审计无害 |

## 如何验证

- `ProjectDetailPage.test.tsx` 新增三例 + mock 扩展：
  admin 预填→改键→断言 PUT payload 含 `jira_project_key:'VFFCA'` 且成功后
  缓存失效重取、关窗；空白键提交显式 `null`；非 admin 无编辑入口。
  另 beforeEach 复位 authRole='admin' 防用例间泄漏。
- 本地：vitest（projects 目录）、`tsc --noEmit`、eslint 全绿。

## 何时重议

- 若要求普通用户（非 admin）也能维护自己项目的 JIRA 键：后端权限模型先动，
  前端放开一行；
- facet 数量增长或需要字典化时：考虑把对话框字段改成 schema 驱动。
