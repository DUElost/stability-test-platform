# 脚本使用统计「被哪些项目的 Plan 用过」（ADR-0029 P2-10）

Status: implemented
Class: feature

P2-10 第一段（方案「脚本库 ← 项目：单向、派生、只读」的落地）：脚本详情
展示「近 30 天被哪些项目的 Plan 使用过 / 各自成功率」——回答「这脚本在我
这个项目上靠谱吗」，零新增 schema。**不建 applicable 列**（方案明确拒绝：
执行差异由脚本端设备路由吸收，applicable 是项目 facet 的镜像副本会制造
三方不一致）。

## Decision

**1. 后端 `GET /scripts/{script_id}/usage?days=`**

`plan_step → plan → plan_run.project_id` 一次 join 派生（plan_step 按
script_name + script_version 匹配当前脚本；plan_run 快照口径，P0-1 起新
Run 有真实归属）：

```sql
SELECT tp.project_key,
       COUNT(DISTINCT p.id)  AS plan_count,
       COUNT(DISTINCT pr.id) AS run_count,
       COUNT(DISTINCT pr.id) FILTER (WHERE pr.status='SUCCESS') AS success_count
FROM plan_step ps JOIN plan p ... JOIN plan_run pr ... JOIN test_project tp ...
GROUP BY tp.project_key
```

- 只统计 `started_at >= now-days` 的 run；成功率 = SUCCESS run / 总 run
  （plan_run 枚举是 SUCCESS/PARTIAL_SUCCESS/FAILED）
- 隐藏的 legacy AEE 脚本复用 `_raise_if_hidden_legacy_aee_script_row` 404

**2. 前端脚本库展开区**

「使用统计」小节（懒加载：展开时才 query）：项目 key + run 数 + 成功数 +
成功率（≥80% 绿 / ≥50% 黄 / 其余红，状态语义色）+ 多 Plan 标注；空态
「近 30 天无 Plan 使用记录」。

## Alternatives

- **建 Script.applicable（方案 §R2）**：拒绝——applicable 是 facet 镜像，
  脚本声明 platform / 项目 facet / 设备事实三方可不一致（A57 前科）；
  派生使用统计零 schema 且回答真实问题。
- **按 plan_snapshot 反查历史 step**：plan_step 表是当前 Plan 的 step，
  历史 run 的 step 快照在 plan_run.plan_snapshot（JSONB）。当前实现按
  plan_step 关联（脚本改名/版本变化时轻微偏差）——快照反查 JSONB 成本
  高且脚本身份漂移难精确匹配，偏差可接受。

## Verification

- `test_scripts.py::test_script_usage_aggregates_by_project`：双项目 3 run
  （2+1），断言 run_count / success_count / success_rate / plan_count；
  20 passed + ruff 全过
- 前端 tsc + 全量 vitest + eslint 全绿

## Revisit

P2-10 剩余：项目详情页 JIRA 块改提单记录（jira_run 有数据后）；P2-11
删抽屉、详情页重排。
