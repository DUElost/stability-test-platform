# 删抽屉 + 详情页重排（ADR-0029 P2-11）

Status: implemented
Class: simplification

P2 收尾：项目一共 2 个，「不离开列表的渐进披露」换不来抽屉的成本——
227 行组件 + 独立 query key + 双编辑入口 + 三条手写 invalidate，维护的
都是同一份数据。删抽屉，卡片直接跳详情页；详情页归属规则提为主块。

## Decision

**1. 删 `ProjectDetailSheet.tsx`（227 行）**

- 列表卡片点击 `setSheetKey(...)` → `navigate('/projects/:key')` 直接跳详情页
- 删 `sheetKey` state、`['project-sheet', ...]` query key（前端无其它引用）、
  抽屉内编辑入口；`ProjectsPage` 净减 ~30 行

**2. 详情页重排**

- **归属规则提为主块**（`detail-rules` Card，KPI 后第一位）：型号 badge +
  台数 + 覆盖率 + 「在项目登记簿维护归属规则」引导按钮（map 交互在列表页
  勾选映射，详情页不重复实现）。原头部「当前归属此项目的设备型号」小字
  升级为此块
- **删 JIRA 占位卡片**（「JIRA 集成」Card）：头部 badge 已有
  `JIRA: xxx / JIRA: 未配置` 同信息；P2-10 的「提单记录」块（jira_run
  有数据后）将替代它

## Alternatives

- **保留 JIRA 卡片**：与头部 badge 重复；方案要求删占位、P2-10 用提单
  记录替代。头部 badge 已承载配置状态。
- **详情页实现规则增删（添加型号/移除/重放）**：map 交互（preview/apply
  两段式）在列表页已完整；详情页重复实现交互是第二套写入面。P2-11
  只做展示 + 引导，规则增删留列表页（规则表 P1 后仍是单一写入面）。

## Verification

- `ProjectsPage.test.tsx`：抽屉 2 测试替换为「卡片点击 navigate + 无
  sheet」1 测试
- `ProjectDetailPage.test.tsx`：四块断言改（detail-rules 存在、JIRA 集成
  不存在）、hanging-models 文案「共 M1 (1)」
- 26 passed（projects 域）+ tsc + 全量 vitest + eslint 全绿

## Revisit

P2-10 剩余：项目详情页 JIRA 提单记录块（jira_run 有数据后）——替代被删
占位卡片的正式内容。P2 至此完成。
