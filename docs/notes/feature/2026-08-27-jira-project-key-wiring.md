# 2026-08-27 — G17：jira_project_key 登记簿映射接线

对应缺口：`docs/reviews/TOOLKIT_INTEGRATION_FEASIBILITY_2026-08-26.md` §2.6
方向 6 · G17（`test_project.jira_project_key` 此前「仅透传」——API 可写但全链路零消费者）。
看板条目：DUElost/stability-test-platform#460。

## 决定了什么

- **接线形态**：`source=plan_run` 的提单 run 在起子进程前，经
  `PlanRun → Plan.project_id → test_project.jira_project_key` 解析出目标键，
  注入 stage1（upload_list）argv 的 `--set-project-key`；解析值随 run 落库
  （新列 `jira_run.jira_project_key`，可空）并透出到响应，供审计与排障。
- **软失败**：键缺失不阻断提单——厂商工具自身 config/机型映射仍生效，后端只记
  WARNING（`jira_project_key_unresolved`）。硬门禁（缺键即拒绝建正式单）留给
  G18 自动草稿策略一并裁决。
- **注入安全性前置确认**：对照 Transsion/Tinno generate 脚本源码（2026-08-27，
  gh REST 原文），工具内部按机型的 `affect_project_mapping.json` 逐行优先，
  CLI 值只替换默认槽位——登记簿的粗粒度键不会破坏细粒度映射。
- **create 阶段不注入**：其输入是 stage1 已带 Project 列的上传模板。
- **Moto 不在范围**：`_VENDORS={transsion,tinno}`；且 Moto 模板脚本无该参数
  （属 G16 接入时的适配项）。

## 放弃的备选

| 备选 | 为什么放弃 |
|------|-----------|
| 缺键直接 422 拒绝 run | 现网既有用法立即被卡；映射回填是数据工程非一日之功，先观测缺口面 |
| 只做数据回填不动代码 | 「仅透传」的根因正是零消费者；回填后依然无人读 |
| 把键写进 draft/前端展示就完事 | argv 才是唯一真正影响产物正确性的位置 |

## 如何验证

- 单测 `backend/tests/api/test_dedup_jira_endpoints.py::TestJiraProjectKeyWiring`：
  有键注入 argv+响应+落库、无键软回落不带 flag、create 阶段恒不注入、
  DB 行记录实际值（真 PG 下验证独立 SessionLocal 写入可见性）。
- 迁移 `b8c9d0e1f2a3`：加列可空、downgrade 回删；CI pr-backend-test 会跑
  alembic upgrade head。
- 生产侧收尾（代码合入后另行执行）：盘点 `test_project` 现状 → 用户确认映射表
  → 经 admin API PUT 回填 → 用一个近期 PlanRun 产物跑真实 dry-run 对照上传
  模板的 Project 列。

## 何时重议

- G18 dry-run 自动草稿立项时：把「S/A 级 PlanRun 完结缺 jira_project_key」
  从 soft-WARNING 升级为阻断或入待办清单；
- Moto vendor（G16）接入时：模板脚本需补等价参数或走其他注入通道；
- 若出现同一 test_project 需要按机型细分 Jira 项目键的需求：登记簿字段粒度
  要重新审视（当前是项目级单值）。
