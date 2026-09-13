# 脚本使用统计增加版本级下钻（#706）

Status: implemented
Class: feature

## Decision

`GET /api/v1/scripts/{id}/usage` 原本只给「项目 → 该项目用过的版本」视角（#506 交付，
`projects[].versions_used`），而退役判据（`AGENTS.md`「脚本版本退役」：配置零引用 +
近期无执行事实）需要的是**版本 → 谁在跑**。本单在响应中新增顶层 `versions` 数组：

```
versions: [{
  script_version, run_count, success_count, success_rate, project_count,
  projects: [{ project_key, run_count, success_count, success_rate }]
}]
```

- 与 `projects` **同源**（同一批 `exec_rows`，即窗口内 `plan_snapshot` 执行事实），
  只是换轴聚合，因此两侧数字天然自洽；
- 版本间按总 `run_count` 降序、同数按版本号升序；版本内项目按 run 数降序、同数按
  key 升序（结果稳定，便于 UI/脚本直接消费）；
- **只含窗口内有执行的版本**：配置引用（`plan_step`）锚定的是被查询的版本行自身，
  「零执行版本不出现在列表」正是退役判据要的信号，故不为无执行版本补零行；
- 前端 `types.ts` 同步新增 `ScriptUsageVersion` / `ScriptUsageVersionProject` 与
  `versions` 字段（硬不变量：类型入口与后端 schema 同步）。本单**不改 UI 展示**——
  issue 的需求是接口层，且版本优先视图的交互形态宜与实际退役操作流一起设计
  （见 Revisit）。

## Alternatives

- **复用 `projects[].versions_used` 在前端做转置**：弃——数据虽全，但每个消费方
  （脚本退役诊断脚本、将来的 UI、运维 ad-hoc 查询）都要重复实现同一转置与排序；
  退役判据是平台级口径，应在 API 层固定；
- **把 `plan_count`（配置引用）也拆到版本维度**：弃——配置引用只对被查询的版本行
  有意义（`plan_step` 存 name+version，查询本身已锁定该版本），把它摊进 versions
  数组会造出「其它版本的配置引用」这一无意义字段；
- **返回所有历史版本（含零执行）**：弃——需要额外查 `script` 表按 name 取全部版本
  行，收益仅是「零执行版本显式出现」；而退役判据的「近期无执行」用「不在
  versions 列表」已可判定，额外的表扫描与语义歧义不值；
- **同时改 ScriptManagementPage 展示版本优先视图**：暂缓——页面当前展示项目优先，
  改为/新增版本视图涉及交互设计（展开态、与 version 退役按钮的关系）与页面级测试
  基建（该页目前无独立测试文件），宜另行立项。

## Verification

- **后端红绿（真实 PG testcontainer）**：新增
  `test_script_usage_versions_drilldown`——两个版本 × 两个项目（v1.0.0 共 3 run/2 成功、
  v1.0.1 共 1 run/0 成功），断言：版本集合、版本内项目排序与逐项 run/success/rate、
  版本间排序；
  - 还原旧端点 → **1 failed**（响应无 `versions`）；本 PR →
    `pytest backend/tests/api/test_scripts.py -q` → **30 passed**；
- `npm run type-check`（types.ts 同步后）→ 通过；
- `python scripts/run_gates.py check:quick` → `[OK] check:quick (7 gates)`。

未做：前端 UI 呈现与生产数据目视（接口语义由测试覆盖）。

## Revisit

- **UI 版本优先视图**：`versions` 目前无前端消费者；建议与脚本退役操作（`PUT
  /scripts/{id}` 置 `is_active=false` 的 409 流程）合并设计，避免先做一版只读表格；
- **窗口参数固定 30 天的展示**：API 支持 `days=1..365`，但页面固定 30；退役判断
  若需要更长窗口（如 90 天），应把窗口选择做成显式控件而非再开接口；
- **与 `refs == 0` 的合并判据**：本接口给运行侧事实，配置侧仍在
  `check_unreferenced_script_versions`（#735 工具）；若将来做「退役建议」一体化视图，
  需要把两侧口径合并到同一响应或前端组合，届时再评估是否在 `/usage` 内联配置侧。
