-- 扫描 pipeline_def JSONB 中残留的 builtin:/tool: action 前缀
-- 只读查询，不修改数据
-- 用法：psql "postgresql://stability:stability@localhost:5432/stability" -f tools/sql/scan_legacy_action_prefix.sql

\pset format aligned
\pset null '∅'

\echo
\echo ===== 1) 4 张表的命中行数汇总 =====
\echo

SELECT 'task_template' AS source, count(*) AS rows_with_legacy
FROM task_template
WHERE pipeline_def::text ~ '"action"\s*:\s*"(builtin|tool):'
UNION ALL
SELECT 'job_instance', count(*)
FROM job_instance
WHERE pipeline_def::text ~ '"action"\s*:\s*"(builtin|tool):'
UNION ALL
SELECT 'workflow_definition.setup_pipeline', count(*)
FROM workflow_definition
WHERE setup_pipeline IS NOT NULL
  AND setup_pipeline::text ~ '"action"\s*:\s*"(builtin|tool):'
UNION ALL
SELECT 'workflow_definition.teardown_pipeline', count(*)
FROM workflow_definition
WHERE teardown_pipeline IS NOT NULL
  AND teardown_pipeline::text ~ '"action"\s*:\s*"(builtin|tool):';

\echo
\echo ===== 2) task_template 详细命中（最多 50 行） =====
\echo

SELECT id, name,
       (pipeline_def::text ~ '"action"\s*:\s*"builtin:') AS has_builtin,
       (pipeline_def::text ~ '"action"\s*:\s*"tool:')    AS has_tool
FROM task_template
WHERE pipeline_def::text ~ '"action"\s*:\s*"(builtin|tool):'
ORDER BY id
LIMIT 50;

\echo
\echo ===== 3) job_instance 详细命中（按 id desc 最近 50 行） =====
\echo

SELECT id, workflow_run_id, status, started_at,
       (pipeline_def::text ~ '"action"\s*:\s*"builtin:') AS has_builtin,
       (pipeline_def::text ~ '"action"\s*:\s*"tool:')    AS has_tool
FROM job_instance
WHERE pipeline_def::text ~ '"action"\s*:\s*"(builtin|tool):'
ORDER BY id DESC
LIMIT 50;

\echo
\echo ===== 4) workflow_definition.setup_pipeline 详细命中 =====
\echo

SELECT id, name,
       (setup_pipeline::text ~ '"action"\s*:\s*"builtin:') AS has_builtin,
       (setup_pipeline::text ~ '"action"\s*:\s*"tool:')    AS has_tool
FROM workflow_definition
WHERE setup_pipeline IS NOT NULL
  AND setup_pipeline::text ~ '"action"\s*:\s*"(builtin|tool):'
ORDER BY id;

\echo
\echo ===== 5) workflow_definition.teardown_pipeline 详细命中 =====
\echo

SELECT id, name,
       (teardown_pipeline::text ~ '"action"\s*:\s*"builtin:') AS has_builtin,
       (teardown_pipeline::text ~ '"action"\s*:\s*"tool:')    AS has_tool
FROM workflow_definition
WHERE teardown_pipeline IS NOT NULL
  AND teardown_pipeline::text ~ '"action"\s*:\s*"(builtin|tool):'
ORDER BY id;

\echo
\echo ===== 6) Action 名称分布（task_template） =====
\echo

SELECT m[1] AS action, count(*) AS occurrences
FROM task_template,
     LATERAL regexp_matches(pipeline_def::text, '"action"\s*:\s*"(builtin:[^"]+|tool:[^"]+)"', 'g') AS m
GROUP BY m[1]
ORDER BY occurrences DESC, m[1];

\echo
\echo ===== 7) Action 名称分布（job_instance） =====
\echo

SELECT m[1] AS action, count(*) AS occurrences
FROM job_instance,
     LATERAL regexp_matches(pipeline_def::text, '"action"\s*:\s*"(builtin:[^"]+|tool:[^"]+)"', 'g') AS m
GROUP BY m[1]
ORDER BY occurrences DESC, m[1];

\echo
\echo ===== 8) Action 名称分布（workflow_definition.setup + teardown） =====
\echo

SELECT 'setup'    AS slot, m[1] AS action, count(*) AS occurrences
FROM workflow_definition,
     LATERAL regexp_matches(coalesce(setup_pipeline::text, ''), '"action"\s*:\s*"(builtin:[^"]+|tool:[^"]+)"', 'g') AS m
GROUP BY m[1]
UNION ALL
SELECT 'teardown' AS slot, m[1] AS action, count(*) AS occurrences
FROM workflow_definition,
     LATERAL regexp_matches(coalesce(teardown_pipeline::text, ''), '"action"\s*:\s*"(builtin:[^"]+|tool:[^"]+)"', 'g') AS m
GROUP BY m[1]
ORDER BY slot, occurrences DESC, action;
