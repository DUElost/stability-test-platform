-- ============================================================================
-- ADR-0023 C1 上线前审计 SQL — 脚本溯源 / phantom params 指纹 / 失活反向引用
-- ============================================================================
--
-- 用途
--   在合入 ADR-0023 C1 (dispatcher fail-fast on missing/inactive scripts) 之前,
--   在生产 PG 上跑这套只读 SQL,确认:
--     1. 没有 Plan 引用了失效脚本 (任何返回行 = C1 上线会立刻阻塞该 Plan)
--     2. 历史 PlanRun snapshot 中是否有 phantom params 痕迹 (历史 BUG 暴露面)
--     3. 失活脚本是否还被 enabled PlanStep 引用 (反向解绑视角)
--
-- 执行方式
--   psql -h <prod-host> -U <user> -d <db> -f docs/ops/adr-0023-c1-audit.sql
--   或在 psql 内: \i docs/ops/adr-0023-c1-audit.sql
--
-- 风险面
--   全是 SELECT,无 INSERT/UPDATE/DELETE/DDL。文件末尾有一段"注入自测",
--   该段被 BEGIN; ... ROLLBACK; 严格包裹,事务结束后无残留(可在生产侧执行)。
--   只读 SQL 的唯一风险是大表扫,plan_run 体量大时面 2/3 可能慢一些,加了
--   LIMIT 500 兜底。需要更小窗口时,在面 2/3 加 WHERE pr.started_at > now() - interval '90 days'。
--
-- 输出解读
--   面 1 返回 0 行 -> 安全合 C1
--   面 1 返回 N 行 -> 联系 Plan 负责人 (改 PlanStep / 恢复 Script),不要直接合
--   面 3 返回 N 行 -> 历史损耗证据,挂 release note
--   面 4 返回 N 行 -> 失活脚本的反向引用清单,辅助沟通
--
-- ============================================================================


\echo
\echo '=== FACE 1: plan_step 引用失效脚本 (C1 上线硬阻塞) ==='
\echo

SELECT
  ps.plan_id,
  ps.id            AS plan_step_id,
  ps.step_key,
  ps.script_name,
  ps.script_version,
  ps.stage,
  CASE
    WHEN s.id IS NULL        THEN 'script_not_found'
    WHEN s.is_active = false THEN 'script_deactivated'
  END AS reason
FROM plan_step ps
LEFT JOIN script s
  ON s.name = ps.script_name AND s.version = ps.script_version
WHERE ps.enabled = true
  AND (s.id IS NULL OR s.is_active = false)
ORDER BY ps.plan_id, ps.sort_order;


\echo
\echo '=== FACE 2: 历史 plan_run.plan_snapshot 引用现表外脚本 (信息面,非阻塞) ==='
\echo

WITH snap AS (
  SELECT pr.id        AS plan_run_id,
         pr.plan_id,
         pr.status,
         pr.started_at,
         step ->> 'script_name'    AS script_name,
         step ->> 'script_version' AS script_version,
         step ->> 'step_key'       AS step_key,
         step ->> 'stage'          AS stage
  FROM plan_run pr,
       jsonb_array_elements(COALESCE(pr.plan_snapshot -> 'steps', '[]'::jsonb)) step
  WHERE pr.plan_snapshot IS NOT NULL
)
SELECT snap.*,
       CASE WHEN s.id IS NULL THEN 'script_not_found'
            WHEN s.is_active = false THEN 'script_deactivated'
       END AS reason
FROM snap
LEFT JOIN script s
  ON s.name = snap.script_name AND s.version = snap.script_version
WHERE s.id IS NULL OR s.is_active = false
ORDER BY snap.plan_run_id DESC
LIMIT 500;


\echo
\echo '=== FACE 3: phantom params 指纹 (snapshot.default_params={} 而 当前 script.default_params 非空) ==='
\echo

WITH snap AS (
  SELECT pr.id        AS plan_run_id,
         pr.plan_id,
         pr.started_at,
         step ->> 'step_key'       AS step_key,
         step ->> 'stage'          AS stage,
         step ->> 'script_name'    AS script_name,
         step ->> 'script_version' AS script_version,
         step -> 'default_params'  AS snapshot_default_params
  FROM plan_run pr,
       jsonb_array_elements(COALESCE(pr.plan_snapshot -> 'steps', '[]'::jsonb)) step
  WHERE pr.plan_snapshot IS NOT NULL
)
SELECT snap.plan_run_id,
       snap.plan_id,
       snap.started_at,
       snap.step_key,
       snap.stage,
       snap.script_name,
       snap.script_version,
       s.default_params       AS current_script_default_params
FROM snap
JOIN script s
  ON s.name = snap.script_name AND s.version = snap.script_version
WHERE snap.snapshot_default_params = '{}'::jsonb
  AND s.default_params <> '{}'::jsonb
ORDER BY snap.plan_run_id DESC
LIMIT 500;


\echo
\echo '=== FACE 4: 失活 Script 当前被多少 enabled PlanStep 引用 (反向解绑) ==='
\echo

SELECT s.id, s.name, s.version, s.is_active,
       COUNT(ps.id) FILTER (WHERE ps.enabled = true) AS active_step_refs,
       array_agg(DISTINCT ps.plan_id ORDER BY ps.plan_id)
         FILTER (WHERE ps.enabled = true)             AS plan_ids
FROM script s
LEFT JOIN plan_step ps
  ON ps.script_name = s.name AND ps.script_version = s.version
WHERE s.is_active = false
GROUP BY s.id, s.name, s.version
HAVING COUNT(ps.id) FILTER (WHERE ps.enabled = true) > 0
ORDER BY active_step_refs DESC;


\echo
\echo '=== SANITY: 分母确认 (审计前置量纲) ==='
\echo

SELECT 'plan_run_snapshot_null'                  AS metric, COUNT(*) FROM plan_run WHERE plan_snapshot IS NULL
UNION ALL SELECT 'plan_run_snapshot_notnull',              COUNT(*) FROM plan_run WHERE plan_snapshot IS NOT NULL
UNION ALL SELECT 'plan_run_snapshot_empty_steps',          COUNT(*) FROM plan_run WHERE plan_snapshot IS NOT NULL AND jsonb_array_length(COALESCE(plan_snapshot -> 'steps','[]'::jsonb)) = 0
UNION ALL SELECT 'script_inactive',                        COUNT(*) FROM script WHERE is_active = false
UNION ALL SELECT 'script_active',                          COUNT(*) FROM script WHERE is_active = true
UNION ALL SELECT 'script_default_params_empty',            COUNT(*) FROM script WHERE default_params = '{}'::jsonb
UNION ALL SELECT 'script_default_params_nonempty',         COUNT(*) FROM script WHERE default_params <> '{}'::jsonb
UNION ALL SELECT 'plan_step_disabled',                     COUNT(*) FROM plan_step WHERE enabled = false
UNION ALL SELECT 'plan_step_enabled',                      COUNT(*) FROM plan_step WHERE enabled = true
UNION ALL SELECT 'snapshot_step_total',                    COUNT(*) FROM plan_run pr, jsonb_array_elements(COALESCE(pr.plan_snapshot -> 'steps','[]'::jsonb)) step WHERE pr.plan_snapshot IS NOT NULL
UNION ALL SELECT 'snapshot_step_default_params_empty',     COUNT(*) FROM plan_run pr, jsonb_array_elements(COALESCE(pr.plan_snapshot -> 'steps','[]'::jsonb)) step WHERE pr.plan_snapshot IS NOT NULL AND step->'default_params' = '{}'::jsonb
UNION ALL SELECT 'snapshot_step_default_params_nonempty',  COUNT(*) FROM plan_run pr, jsonb_array_elements(COALESCE(pr.plan_snapshot -> 'steps','[]'::jsonb)) step WHERE pr.plan_snapshot IS NOT NULL AND step->'default_params' <> '{}'::jsonb;


-- ============================================================================
-- SELF-TEST: 注入 + 立即 ROLLBACK
--
-- 目的:在不污染生产数据的前提下,证明上述 4 段 SQL 真的有辨识能力——
--      如果连这一段都查不到结果,说明库的数据形态让 SQL 无法被触发,
--      不能据此推断"生产无问题"。
--
-- 严格约束:整个块被 BEGIN ... ROLLBACK 包裹,事务结束后无任何残留。
--           最后的 LEAK CHECK 必须返回 leak_count = 0;若 > 0 立即排查。
-- ============================================================================

\echo
\echo '=== SELF-TEST: BEGIN tx, inject probes, run face 1/3, ROLLBACK ==='
\echo

BEGIN;

-- 注入 1:plan_step 引用一个不存在的 (ghost_x, 9.9.9)
INSERT INTO plan_step (plan_id, step_key, script_name, script_version, stage, sort_order, retry, enabled, created_at)
SELECT id, '__audit_probe_ghost__', 'ghost_x', '9.9.9', 'init', 99, 0, true, now()
FROM plan ORDER BY id LIMIT 1;

-- 注入 2:挑一个真实被引用的 script,标 inactive (面 1 触发用)
UPDATE script SET is_active = false
WHERE (name, version) = (
  SELECT script_name, script_version FROM plan_step WHERE enabled = true LIMIT 1
);

-- 注入 3:写一个 plan_run.plan_snapshot 引用 phantom 步骤 (面 3 触发用)
INSERT INTO plan_run (plan_id, status, failure_threshold, plan_snapshot, run_type, started_at)
SELECT id, 'FAILED', 0.05,
       jsonb_build_object(
         'plan', jsonb_build_object('id', id, 'name', name),
         'steps', jsonb_build_array(jsonb_build_object(
            'stage', 'init',
            'step_key', '__audit_probe_phantom__',
            'script_name', (SELECT name    FROM script ORDER BY id LIMIT 1),
            'script_version', (SELECT version FROM script ORDER BY id LIMIT 1),
            'default_params', '{}'::jsonb,
            'sort_order', 0,
            'enabled', true
         ))
       ),
       'MANUAL', now()
FROM plan ORDER BY id LIMIT 1;

-- 注入 4:把 ”被引用”的 script 的 current default_params 改成非空 (面 3 触发用)
UPDATE script
   SET default_params = '{"__audit_probe_marker__": true}'::jsonb
 WHERE id = (SELECT id FROM script ORDER BY id LIMIT 1);

\echo '--- SELF-TEST FACE 1 (应命中至少 2 行) ---'

SELECT ps.plan_id, ps.step_key, ps.script_name, ps.script_version,
       CASE WHEN s.id IS NULL THEN 'script_not_found'
            WHEN s.is_active = false THEN 'script_deactivated' END AS reason
FROM plan_step ps
LEFT JOIN script s ON s.name = ps.script_name AND s.version = ps.script_version
WHERE ps.enabled = true
  AND (s.id IS NULL OR s.is_active = false)
ORDER BY ps.plan_id
LIMIT 20;

\echo '--- SELF-TEST FACE 3 (应命中至少 1 行 = __audit_probe_phantom__) ---'

WITH snap AS (
  SELECT pr.id AS plan_run_id, pr.plan_id,
         step ->> 'step_key'       AS step_key,
         step ->> 'script_name'    AS script_name,
         step ->> 'script_version' AS script_version,
         step -> 'default_params'  AS snap_dp
  FROM plan_run pr,
       jsonb_array_elements(COALESCE(pr.plan_snapshot -> 'steps','[]'::jsonb)) step
  WHERE pr.plan_snapshot IS NOT NULL
)
SELECT snap.plan_run_id, snap.step_key, snap.script_name, snap.script_version,
       s.default_params AS current_dp
FROM snap
JOIN script s ON s.name = snap.script_name AND s.version = snap.script_version
WHERE snap.snap_dp = '{}'::jsonb AND s.default_params <> '{}'::jsonb
ORDER BY snap.plan_run_id DESC
LIMIT 10;

ROLLBACK;

\echo
\echo '=== LEAK CHECK (必须为 0,否则 ROLLBACK 未生效) ==='
\echo

SELECT 'plan_step_leak'  AS metric, COUNT(*) FROM plan_step WHERE step_key = '__audit_probe_ghost__'
UNION ALL
SELECT 'plan_run_leak',            COUNT(*) FROM plan_run
WHERE plan_snapshot::text LIKE '%__audit_probe_phantom__%'
UNION ALL
SELECT 'script_marker_leak',       COUNT(*) FROM script
WHERE default_params @> '{"__audit_probe_marker__": true}'::jsonb;

\echo
\echo '=== AUDIT DONE ==='
\echo
