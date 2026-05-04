-- DB 清理 Part 3: 删除 __script_sequence__ 历史 job_instance (stages 顶层格式)
-- 链路（自下而上）：
--   step_trace.job_id IN (2934, 2935) - 14 rows
--   device_leases id IN (1842, 1843)  - 2 rows (RELEASED)
--   job_instance id IN (2934, 2935)   - 2 rows (FAILED, ABORTED)
--   workflow_run id IN (2713, 2714)   - 2 rows
-- 保留: workflow_definition 2808 (system anchor) + task_template 2681 (lifecycle)
\pset format aligned
\pset null '∅'

\echo
\echo === Pre-cleanup baseline ===
SELECT 'step_trace (jobs 2934/2935)' AS row_id, count(*) AS rows FROM step_trace WHERE job_id IN (2934, 2935)
UNION ALL SELECT 'device_leases (1842/1843)', count(*) FROM device_leases WHERE id IN (1842, 1843)
UNION ALL SELECT 'job_instance (2934/2935)', count(*) FROM job_instance WHERE id IN (2934, 2935)
UNION ALL SELECT 'workflow_run (2713/2714)', count(*) FROM workflow_run WHERE id IN (2713, 2714)
UNION ALL SELECT 'workflow_definition 2808 (KEEP)', count(*) FROM workflow_definition WHERE id = 2808
UNION ALL SELECT 'task_template 2681 (KEEP)', count(*) FROM task_template WHERE id = 2681;

\echo
\echo === BEGIN cleanup transaction ===
\echo

BEGIN;

DELETE FROM step_trace WHERE job_id IN (2934, 2935);
\echo Step 1 done: step_trace deleted

DELETE FROM device_leases WHERE id IN (1842, 1843) AND status = 'RELEASED';
\echo Step 2 done: device_leases deleted

DELETE FROM job_instance WHERE id IN (2934, 2935) AND task_template_id = 2681 AND status IN ('FAILED', 'ABORTED');
\echo Step 3 done: job_instance deleted

DELETE FROM workflow_run WHERE id IN (2713, 2714) AND workflow_definition_id = 2808;
\echo Step 4 done: workflow_run deleted

\echo
\echo === Pre-commit verification ===

SELECT 'task_template stages count' AS metric, count(*) AS value
FROM task_template WHERE pipeline_def ? 'stages';

SELECT 'task_template builtin: count' AS metric, count(*) AS value
FROM task_template WHERE pipeline_def::text ~ '"action"\s*:\s*"builtin:';

SELECT 'job_instance stages count' AS metric, count(*) AS value
FROM job_instance WHERE pipeline_def ? 'stages';

SELECT 'job_instance builtin: count' AS metric, count(*) AS value
FROM job_instance WHERE pipeline_def::text ~ '"action"\s*:\s*"builtin:';

SELECT 'task_template total' AS metric, count(*) AS value FROM task_template;
SELECT 'job_instance total' AS metric, count(*) AS value FROM job_instance;
SELECT 'workflow_run total' AS metric, count(*) AS value FROM workflow_run;
SELECT 'workflow_definition total' AS metric, count(*) AS value FROM workflow_definition;

SELECT id, name, (SELECT array_agg(k) FROM jsonb_object_keys(pipeline_def) k) AS top_keys
FROM task_template ORDER BY id;

COMMIT;

\echo
\echo === COMMITTED ===
