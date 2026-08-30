# 脚本使用统计 versions_used 下钻（#506 follow-up）

Status: implemented
Class: feature

在 P2-10 基础统计上补 `versions_used`：按项目展示 plan_snapshot
中**实际执行**的脚本版本，与 `plan_count`（plan_step 当前配置引用）
刻意分离，供退役判据交叉校验。

## Decision

- `plan_count`：`plan_step` 按 `script_name + script_version`（当前脚本行）
- `run_count` / `success_rate`：同版本在 `plan_snapshot.steps` 中的执行次数
- `versions_used[]`：同 `script_name` 下各 `script_version` 的执行分布
- 项目行 = 配置引用 ∪ 执行事实（并集），覆盖「有引用无执行」「无引用有执行」

## Verification

- `test_script_usage_aggregates_by_project`（快照口径）
- `test_script_usage_config_ref_without_recent_runs`
- `test_script_usage_runs_without_config_ref`
- `test_script_usage_versions_used_when_config_diverges`

## Revisit

2026-09-28 前 `run_count` 因历史 `project_id` 归属未回填，不宜单独作退役依据（#506 评论）。
