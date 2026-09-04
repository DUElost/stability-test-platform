# 新建专项 Runbook（适配新项目）

> 面向操作者的一次性操作手册：从零把一个新项目/新专项接入平台并完成首次
> 试运行验证。只写现状步骤与入口位置；设计取舍与背景见文末参考表。
> 缺口编号 G21，见 `docs/reviews/TOOLKIT_INTEGRATION_FEASIBILITY_2026-08-26.md` §2.7。

## 0. 前置条件

| 需要什么 | 说明 |
|------|------|
| 控制面账号 | `admin` 角色可走完全部步骤；普通用户仅能操作自己 created_by 的 Plan |
| 网络可达控制面 `:8000` | 本手册统一写 `<base>` = `http://<control-plane>:8000` |
| 目标设备已入池 | Heartbeat 正常上报、platform 已判定（MTK/UNISOC/QCOM） |
| （脚本主轨）NFS 脚本树访问权 | 控制面 env `STP_SCRIPT_ROOT` 指向的目录（未配置时 scan 返回 503） |

**拿调用凭据**（bearer token，供 curl / Swagger / 外部 agent 用）：

```bash
curl -s -X POST '<base>/api/v1/auth/token' \
  -d 'username=<user>&password=<pass>' | jq -r .access_token
# 之后每个请求带 -H "Authorization: Bearer $TOKEN"
```

浏览器走 cookie session 时受 CSRF Origin 校验约束；token 流不受影响。
若部署侧关闭了 API 文档（`STP_API_DOCS_ENABLED=0`），交互式 `/docs` 不可用，
一切以本手册的端点清单为准。

## 1. 概念速查（先分清四个词）

| 概念 | 是什么 | 权威定义 |
|------|--------|----------|
| **project**（项目） | 商务/组织维度的机型族，登记簿实体，提单与报表聚合键 | ADR-0029 |
| **specialty**（专项） | 测试类型维度标签（如 MTBF/GPU/Sleep），Plan 的下拉字典 | ADR-0029 D6 |
| **script**（脚本） | 版本化可执行单元，唯一 action 类型 `script:<name>` 的载体 | ADR-0020 |
| **suite**（套件） | 设备端用例清单元数据层（多用例专项才需要） | ADR-0030 |

Plan 归属两个维度：`project_key`（**可选**，空 = 显式「不限」）+ `specialty_key`（**必填**）。
前者决定派发目标与报表聚合，后者决定用例/套件语义（ADR-0029 D2/D6 + v2.5 D11）；
纯冒烟可以只填 specialty，但**不能**只有 project 没有 specialty。

## 2. Step 1 —— 项目登记

```bash
curl -s -X POST '<base>/api/v1/projects' -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{
    "project_key": "Z2581",          // [A-Za-z0-9][A-Za-z0-9-]{0,62}
    "display_name": "Z2581 项目",
    "customer": "<客户名>",           // 可选，登记用
    "jira_project_key": null          // Jira 提单键，可后补
  }'
```

> 注：项目不再手动登记 `platform` / `form_factor` / `product_line` facet（列已随
> ADR-0029 v2.5 D12 删除）；platform 由设备心跳派生，型号映射在登记簿工作台维护
> （活跃 `project_model` 成员行，ADR-0029 v2.5）。

- 登记簿查询：`GET /api/v1/projects`；详情含聚合视图：`GET /api/v1/projects/{project_key}`。
- 后补 Jira 键：`PUT /api/v1/projects/{project_key}` 传 `{"jira_project_key": "XXX"}`。
  不填则该项目的 PlanRun 无法进入自动提单流程（风险评级照常产出）。
- 改 project_key：用 `PUT /api/v1/projects/{key}/rename`（admin；ADR-0029 v2.5 D2 复核后 key 可改）。
  不要用「新建 key + 复制项目」绕过 rename——旧 key 下的归档与报表不会自动迁移。

## 3. Step 2 —— 专项（specialty）确认

```bash
curl -s '<base>/api/v1/plans/specialties' -H "Authorization: Bearer $TOKEN"
```

返回既有专项字典（key + 展示名）。**已知缺口**：specialty 目前没有 REST 管理
路由，新专项 key 需 DB 登记（`specialty` 表，key 唯一 + sort_order）；联系平台
维护者插入或等管理面立项。已有标签够用就跳过本步。

## 4. Step 3 —— 脚本入库

### 4a. 主轨：NFS 目录 + 目录扫描（生产推荐）

目录规约（扫描器只认这个形状）：

```
{STP_SCRIPT_ROOT}/<script_name>/v<version>/<entry>.py   # 入口文件
                                   _helper.py            # 同版本伴随模块（_ 开头不作入口）
                                   capabilities.json     # 能力声明，见下
```

- `capabilities.json`：`{"capabilities": ["progress_stamps"]}` 等。给 PlanStep 配
  `stall_seconds` 停滞钟的前置条件就是脚本声明 `progress_stamps`（#136/#171 门禁）；
  未声明的脚本配 stall 会被校验拒绝。
- 内容放置后由 scan 对账 sha256：内容漂移报 `conflicts` 且行不被改动（ADR-0020
  不可变契约）；修复只能新建版本目录，原地改文件需 admin `force_rebaseline`
  （有 PlanRun 在飞时被 409 拒绝）。

```bash
curl -s -X POST '<base>/api/v1/scripts/scan' -H "Authorization: Bearer $TOKEN"
# 返回 {created, skipped, deactivated, conflicts:[...]} —— conflicts 必须为空再继续
```

### 4b. 兜底轨：REST 手动登记

scan 不便时逐条登记（fields 与 4a 同一套真源，`content_sha256` 自己算：
`sha256sum <entry>.py`）：

```bash
curl -s -X POST '<base>/api/v1/scripts' ... -d '{
  "name": "my_smoke", "script_type": "python", "version": "1.0.0",
  "nfs_path": "/mnt/nfs/scripts/my_smoke/v1.0.0/my_smoke.py",
  "content_sha256": "<64hex>",
  "param_schema": {"type": "object"},
  "default_params": {"package": "com.example"}   # 一经创建不可改（ADR-0020）
}'
```

- 改默认参数 = 建 `POST /scripts/{name}/versions`（新 version + 新 nfs_path +
  新 sha）；对旧版本的 PUT 只放行非 lifecycle 字段。
- 停用 `DELETE /scripts/{id}`：仍被 PlanStep 引用时 409（先改 Plan）。
- 两条轨都会被下一次 scan 以 NFS 树为准覆盖对账——别把 REST 当真源。

### 参数怎么进脚本（约定）

控制面注入 env，Agent 子进程读取；stdout 可输出结构化进度戳配合停滞钟。
协议细节以 `docs/design/` 执行协议文档与现有脚本样例为准。

## 5. Step 4 —— 建 Plan（编排）

三段式 lifecycle：`init`（一次性前置）→ `patrol`（循环体，需
`patrol_interval_seconds` ≥ 1）→ `teardown`（收尾）。纯一次性任务只写 init+
teardown、patrol 留空即可。

```bash
curl -s -X POST '<base>/api/v1/plans' ... -d '{
  "name": "Z2581 稳定性冒烟 v1",
  "failure_threshold": 0.05,             // 失败率熔断线
  "patrol_interval_seconds": 1800,
  "barrier_timeout_seconds": 1800,       // init 长前置必须抬高预算(见字段注释)
  "project_key": "Z2581",
  "specialty_key": "mtbf",
  "steps": [
    {"step_key": "prepare",   "script_name": "device_prep", "script_version": "1.2.0",
     "stage": "init",  "sort_order": 10, "timeout_seconds": 600},
    {"step_key": "monkey",    "script_name": "monkey_run",  "script_version": "2.0.1",
     "stage": "patrol","sort_order": 10, "timeout_seconds": 3600,
     "stall_seconds": 900,             // 要求脚本具备 progress_stamps 能力
     "retry": 1},
    {"step_key": "collect",   "script_name": "log_collect", "script_version": "1.0.3",
     "stage": "teardown", "sort_order": 10, "timeout_seconds": 1200}
  ]
}'
```

易踩点（schema 都是 `extra=forbid`，字段名打错直接 422）：

- `steps[].stage` 只接受 `init|patrol|teardown`；`retry` 上限 5。
- `stall_seconds=None/0` 表示关闭停滞钟；配了但脚本无能力会在预览/派发期报错。
- 引用脚本用 `script_name + script_version` 精确到版本；plan 不跟「latest」。
- 多用例专项（suite 绑定）：先建套件 `POST /api/v1/test-suites`（或走
  `.../{id}/import` 导入设备端 XML），Plan 里传 `suite_name`；MTBF 类执行包可先用
  `POST /api/v1/mtbf/runtask/validate` 校验 runtask.xml。
- 计划链：`next_plan_id` 或 `POST /plans/{id}/append-chain-tail` 追加链尾。

## 6. Step 5 —— 试运行验证

```bash
# 1) 干跑预览（不占设备，检查编排/参数/能力门禁）
curl -s -X POST '<base>/api/v1/plans/<plan_id>/run/preview' ...

# 2) 选定设备正式触发（注意 device_ids 必填且去重）
curl -s -X POST '<base>/api/v1/plans/<plan_id>/run' ... -d '{
  "device_ids": [12, 13],
  "note": "first smoke for Z2581",
  "wifi_pool_id": null
}'

# 3) 观察
GET /api/v1/plan-runs/<run_id>            # 状态机与汇总
GET /api/v1/plan-runs/<run_id>/timeline   # 步骤时间轴
GET /api/v1/plan-runs/<run_id>/jobs       # job 明细
GET /api/v1/plan-runs/<run_id>/summary    # 完结后的风险评级摘要
```

排查速查：

| 症状 | 先看哪 |
|------|--------|
| dispatch 报 stall/capability 相关错误 | 脚本 capabilities.json 是否声明 progress_stamps |
| init 卡住超时 | `barrier_timeout_seconds` 预算是否盖得住同 host 的 init 落差 |
| 步骤反复被判卡死杀掉 | `stall_seconds` 是否短于脚本最长静默段（PROGRESS 戳间隔） |
| run 报 SUCCESS 但没报表 | dedup 链日志按 AGENTS.md「scan/upload/merge」节排查 |

试运行通过标准（建议）：一条完整 init→patrol→teardown 周期无 FAILED；
watcher 信号正常上送（`/plan-runs/{id}/events` 有增量）；终态报告与 summary 可打开。

## 7. Step 6 —— 专项上线检查单

- [ ] `POST /scripts/scan` 返回 `conflicts: []`
- [ ] 所有引用脚本的能力声明齐备（配了 stall 的都有 progress_stamps）
- [ ] Plan 归属正确：`project_key` 已填、`specialty_key` 属于既有字典
- [ ] （要提单的项目）`jira_project_key` 已补
- [ ] run preview 无 error、真实 run 至少一个完整周期全绿
- [ ] 生产部署核对已过 `docs/operations/production-minimum-deployment-checklist.md`
- [ ] 回滚路径明确：停脚本先摘引用（409 提示会列出 plan_ids）

## 8. 参考

| 主题 | 出处 |
|------|------|
| Plan/Step 模型与不可变契约 | `docs/adr/ADR-0020-plan-step-one-shot-migration.md` |
| 项目登记簿 D6/facet 决策 | `docs/adr/`ADR-0029 系列 |
| 套件/用例与导入导出 | `docs/adr/ADR-0030-multi-case-suite-management.md` |
| 七方向缺口编号来源 | `docs/reviews/TOOLKIT_INTEGRATION_FEASIBILITY_2026-08-26.md` |
| 生产部署前置 | `docs/operations/production-minimum-deployment-checklist.md` |
| MTBF 工具 API | `docs/operations/mtbf-api.md` |
