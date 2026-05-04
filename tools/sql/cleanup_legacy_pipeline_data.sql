-- DB 清理：lifecycle + script 收敛之后的孤儿/残留数据
-- (1) UPDATE task_template id=2681 (system anchor) stages -> lifecycle placeholder
-- (2) DELETE 5 scene-orphan workflow_definitions (CASCADE deletes their task_templates)
-- (3) DELETE FROM scene_template (orphan table, no code reference)
-- Used together with tools/sql/scan_legacy_action_prefix.sql for verification.
\pset format aligned
\pset null '∅'

\echo
\echo === BEGIN cleanup transaction ===
\echo

BEGIN;

-- (1) Fix system anchor pipeline_def: stages -> lifecycle (placeholder)
-- Mirrors backend/services/script_execution.py:82-98 synthesize_script_pipeline()
UPDATE task_template
SET pipeline_def = jsonb_build_object(
  'lifecycle', jsonb_build_object(
    'init', jsonb_build_array(
      jsonb_build_object(
        'step_id', 'script_0_placeholder',
        'action', 'script:placeholder',
        'version', '0.0.0',
        'params', '{}'::jsonb,
        'timeout_seconds', 1,
        'retry', 0,
        'enabled', true
      )
    ),
    'teardown', '[]'::jsonb
  )
)
WHERE id = 2681 AND name = '__script_sequence__';

\echo Step 1 done: system anchor updated

-- (2) Delete 5 scene-orphan workflows (CASCADE deletes 2682-2685, 2746 task_templates)
DELETE FROM workflow_definition
WHERE id IN (2809, 2810, 2811, 2812, 2873)
  AND created_by LIKE 'scene:monkey_aee_%@1.0.0';

\echo Step 2 done: 5 scene-orphan workflows deleted (CASCADE removes their task_templates)

-- (3) Empty scene_template table (orphan, no code reference)
DELETE FROM scene_template;

\echo Step 3 done: scene_template emptied

\echo
\echo === Pre-commit verification ===

SELECT 'task_template stages count' AS metric, count(*) AS value
FROM task_template WHERE pipeline_def ? 'stages';

SELECT 'task_template builtin: count' AS metric, count(*) AS value
FROM task_template WHERE pipeline_def::text ~ '"action"\s*:\s*"builtin:';

SELECT 'scene_template rows' AS metric, count(*) AS value FROM scene_template;

SELECT 'task_template id=2681 lifecycle?' AS metric, count(*) AS value
FROM task_template WHERE id=2681 AND pipeline_def ? 'lifecycle';

SELECT 'task_template id=2681 stages?' AS metric, count(*) AS value
FROM task_template WHERE id=2681 AND pipeline_def ? 'stages';

SELECT 'remaining stages task_template (should be only id=2745 pytest fixture)' AS metric,
       string_agg(id::text || '/' || name, ', ') AS rows
FROM task_template WHERE pipeline_def ? 'stages';

COMMIT;

\echo
\echo === COMMITTED ===
