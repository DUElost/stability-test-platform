-- DB 清理 Part 2：删除 pytest dual-write fixture (stages + builtin: 残留)
-- 链路（自下而上）：
--   device_leases id=1877 (RELEASED, FK -> job_instance 2995)
--   job_instance id=2995 (FAILED, FK -> task_template 2745)
--   workflow_run id=2774 (FK -> workflow_definition 2872)
--   workflow_definition id=2872 (CASCADE deletes task_template id=2745)
\pset format aligned
\pset null '∅'

\echo
\echo === Pre-cleanup baseline ===
SELECT 'device_leases id=1877' AS row_id, count(*) AS exists FROM device_leases WHERE id=1877
UNION ALL SELECT 'job_instance id=2995', count(*) FROM job_instance WHERE id=2995
UNION ALL SELECT 'workflow_run id=2774', count(*) FROM workflow_run WHERE id=2774
UNION ALL SELECT 'task_template id=2745', count(*) FROM task_template WHERE id=2745
UNION ALL SELECT 'workflow_definition id=2872', count(*) FROM workflow_definition WHERE id=2872;

\echo
\echo === BEGIN cleanup transaction ===
\echo

BEGIN;

-- (1) Release FK from device_leases (RELEASED terminal state, safe)
DELETE FROM device_leases WHERE id = 1877 AND job_id = 2995 AND status = 'RELEASED';
\echo Step 1 done: device_leases 1877 deleted

-- (2) Delete the FAILED job_instance (CASCADE removes job_log_signal if any)
DELETE FROM job_instance WHERE id = 2995 AND status = 'FAILED' AND task_template_id = 2745;
\echo Step 2 done: job_instance 2995 deleted

-- (3) Delete the workflow_run that hosted the failed job
DELETE FROM workflow_run WHERE id = 2774 AND workflow_definition_id = 2872;
\echo Step 3 done: workflow_run 2774 deleted

-- (4) Delete the workflow_definition (CASCADE removes task_template 2745)
DELETE FROM workflow_definition
WHERE id = 2872
  AND name = 'wf-fb10bb90'
  AND created_by = 'pytest';
\echo Step 4 done: workflow_definition 2872 + task_template 2745 (CASCADE) deleted

\echo
\echo === Pre-commit verification (all should be 0) ===

SELECT 'task_template stages count' AS metric, count(*) AS value
FROM task_template WHERE pipeline_def ? 'stages';

SELECT 'task_template builtin: count' AS metric, count(*) AS value
FROM task_template WHERE pipeline_def::text ~ '"action"\s*:\s*"builtin:';

SELECT 'job_instance with stages pipeline_def' AS metric, count(*) AS value
FROM job_instance WHERE pipeline_def ? 'stages';

SELECT 'job_instance with builtin: action' AS metric, count(*) AS value
FROM job_instance WHERE pipeline_def::text ~ '"action"\s*:\s*"builtin:';

SELECT 'remaining task_template (only lifecycle expected)' AS metric, count(*) AS value
FROM task_template;

SELECT id, name, (SELECT array_agg(k) FROM jsonb_object_keys(pipeline_def) k) AS top_keys
FROM task_template
ORDER BY id;

COMMIT;

\echo
\echo === COMMITTED ===
