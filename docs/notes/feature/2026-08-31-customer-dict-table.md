# customer 字典表（ADR-0029 D12 收尾）——数据源形态与 JIRA 探测放弃

日期：2026-08-31 · 类型：feature · 关联：ADR-0029 D12 · PR：#643

## 决定了什么

1. **`customer` 字典表以「数据源、列不动」形态落地**：新增 `customer` 表
   （specialty 范式：key/display_name/sort_order），`test_project.customer`
   列保持自由文本字符串不变。编辑弹窗（新建/编辑项目）改为原生 datalist
   下拉建议，可继续自由输入（历史/手写值不受约束）。
2. **JIRA 提单前存在性探测正式放弃**（D12 原文要求），理由：控制面**没有**
   JIRA API 访问通道——提单由厂商脚本在 Agent 侧执行（
   `create_transsion`/`tinno_jira_batch_from_excel.py`），控制面只解析脚本
   stdout 提取 issue key（`jira_issue_parser.py`）；无服务器地址/凭据，
   探测无从实现。详情页格式校验（JIRA_KEY_RE + 徽标）即当前形态的
   「最强输入校验」。

## 放弃的备选

- **字典表 + FK 迁移**（customer → customer_id）：5 个值不值得表结构迁移
  成本；「列不动」保持与 v2.5「删副本列」方向的张力最小
- **严格下拉（Select 组件）**：前端组件库无 Select/Combobox；且会阻塞字典
  外的历史值编辑——datalist 兼顾建议与自由输入，零新增依赖
- **customer 字典表整体跳过**：用户决策保留（「字典表做数据源」），
  JIRA 探测则选择跳过

## 如何验证

- 后端：`GET /api/v1/projects/customers` 排序/认证/路由捕获回归
  （`/customers` 静态段须先于 `/{project_key}` 注册，否则被捕获为 key 404）
  ——`test_project_routes.py::TestCustomerDict` 3 例
- 前端：两个 Dialog 的 datalist 渲染断言（`list` 属性 + option 出现）
- migration：单 head（d6e7f8a9b0c1），seed 从 `test_project.customer`
  去重回填（生产 4 值：荣耀 3 / 中兴 2 / 传音 1 / ODM 1），
  `ON CONFLICT DO NOTHING` 幂等；`pr-migrate-empty-db` 会跑空库升级
- 部署形态：control-plane 常规部署（backend restart + 前端换包），
  无 Agent 热更新、无脚本版本变化

## 何时重议

- 出现 customer 聚合分析诉求（按客户统计脚本使用/成功率）→ 考虑 FK 化
- JIRA 侧提供控制面可用的 API 凭据 → 探测可复活（D12 原文条件）
