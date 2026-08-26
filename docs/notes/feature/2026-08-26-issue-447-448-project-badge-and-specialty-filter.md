# 2026-08-26 #447/#448：PlanRun 详情项目徽章 + Plan 列表专项筛选

- Class: feature
- Status: implemented
- 关联：#447（Fixes，随 PR 关闭）、#448（半段——specialty 筛选落地，二维分组维持 deferred 挂产品决策钩子）
- 关联 ADR：ADR-0029（D5 快照语义保留段 / D6 专项维度 / D8 最小形态「页面级筛选」）

## 决定了什么

1. **#447 徽章位置 = PlanRunHero 标题行，数据源 = `run.project_key`（快照）**。
   `PlanRunDetailOut.project_key`（`routes/plan_runs.py`）读的是 `plan_run.project_id`
   快照关联——详情页展示的必须是**运行时归属**，Plan 事后改归属不影响历史 Run 归因。
   直接在 Hero 内读 `run.project_key`（`PlanRun` 类型已有该字段），不加新 prop、
   PlanRunDetailPage 零改动；`ProjectKeyBadge` 扩展可选 `className`（首个非列表行内用法）。
   NULL（未归属）渲染 null——`ProjectKeyBadge` 既有语义，不加占位文案。
2. **#448 半段 = `GET /plans` 加 `specialty_key` 查询参数 + PlanListPage 原生 select**。
   未知 key 404，与 `project_key` 同语义（`routes/plans.py:list_plans`）；
   下拉为 PlanListPage 内局部组件 `SpecialtyFilterSelect`（字典源 `GET /specialties`，
   queryKey `['specialties']` 与 Plan 编辑器共用缓存；B4 决议原生 `<select>`）。
   不做跨页跟随（D8 挂起口径），刷新回「全部专项」。

## 放弃的备选

- **`SpecialtyFilterSelect` 提为共享组件**：当前仅 PlanListPage 一个消费方，提共享是
  过早抽象；#448 的分组视图落地时再抽（此时它才可能有第二个消费者）。
- **specialty 客户端过滤**（列表全拉、前端 filter）：与 project 走后端过滤的既有口径
  不一致，且 limit 截断后过滤结果会缺页。
- **#447 在详情页头部（AppShell 槽位）放徽章**：hero 是 run 身份信息的聚集地
  （状态 pill、时长、Plan 名），归属放那里语义最顺，也免去跨组件传参。

## 如何验证

- 后端：`pytest backend/tests/api/test_plans_api.py`（testcontainers PG16）**54 passed**，
  含新增 `TestPlanListFilters`（project/specialty 单独与组合过滤、无归属排除、
  未知 key 404；project 过滤此前零覆盖，本类一并补齐）；ruff 通过。
- 前端：`tsc --noEmit` 通过；vitest 两页 **24 passed**（新增：specialty select 触发
  `api.plans.list(0,100,undefined,'mtbf')`、hero `getByTitle('归属项目 MLD')`）；
  `npm run build` 通过。
- 未跑全量 `backend/tests/`（改动面仅 list_plans 参数 + 该文件全量已过）；
  pr-backend-test 信息性 job 会在 PR 上再跑一遍控制面回归。

## 何时重议

- #448 的「项目×专项二维分组」维持 deferred：Plan 数量增长到平铺难扫描、或跨项目
  对比成为日常动作时升级（触发条件已写在 issue）；届时 `SpecialtyFilterSelect`
  随分组视图一起提共享组件。
