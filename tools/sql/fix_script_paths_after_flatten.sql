-- DB 清理：扁平化 scripts 路径迁移
-- 背景：commit 310c24d 把旧分层脚本目录
--      移到 backend/agent/scripts/<name>/<version>/（去掉中间分类层）
-- 链路：
--   Step 1: UPDATE 10 条 active 记录的 nfs_path 到新 Linux 扁平路径
--           (id=17,18,19,20,21,22,23,24,28 旧分层路径记录)
--           (id=67 monkey_teardown 同时刷新 sha — task #53 marker schema 升级)
--   Step 2: deactivate 7 条 dangling 记录（文件已删除或不存在）
--           (id=30,31 e2e fixture; id=59 monkey_guard; id=62 wait;
--            id=64 aimonkey_launch; id=65 monkey_launch v3.0.0; id=66 monkey_patrol_loop)
--   Step 3: INSERT monkey_launch v1.0.0 + v2.0.0 active 记录（文件存在但 DB 缺失）
\pset format aligned
\pset null '∅'

\echo
\echo === Pre-cleanup baseline ===
SELECT id, name, version, nfs_path, is_active FROM script ORDER BY id;

\echo
\echo === BEGIN cleanup transaction ===
\echo

BEGIN;

-- ==================== Step 1: UPDATE 10 条 active 记录路径 ====================

UPDATE script
SET nfs_path = '/opt/stability-test-agent/agent/scripts/check_device/v1.0.0/check_device.py',
    updated_at = now()
WHERE id = 17 AND name = 'check_device' AND version = '1.0.0';

UPDATE script
SET nfs_path = '/opt/stability-test-agent/agent/scripts/clean_env/v1.0.0/clean_env.py',
    updated_at = now()
WHERE id = 18 AND name = 'clean_env' AND version = '1.0.0';

UPDATE script
SET nfs_path = '/opt/stability-test-agent/agent/scripts/connect_wifi/v1.0.0/connect_wifi.py',
    updated_at = now()
WHERE id = 19 AND name = 'connect_wifi' AND version = '1.0.0';

UPDATE script
SET nfs_path = '/opt/stability-test-agent/agent/scripts/ensure_root/v1.0.0/ensure_root.py',
    updated_at = now()
WHERE id = 20 AND name = 'ensure_root' AND version = '1.0.0';

UPDATE script
SET nfs_path = '/opt/stability-test-agent/agent/scripts/fill_storage/v1.0.0/fill_storage.py',
    updated_at = now()
WHERE id = 21 AND name = 'fill_storage' AND version = '1.0.0';

UPDATE script
SET nfs_path = '/opt/stability-test-agent/agent/scripts/install_apk/v1.0.0/install_apk.py',
    updated_at = now()
WHERE id = 22 AND name = 'install_apk' AND version = '1.0.0';

UPDATE script
SET nfs_path = '/opt/stability-test-agent/agent/scripts/monkey_setup/v1.0.0/monkey_setup.py',
    updated_at = now()
WHERE id = 23 AND name = 'monkey_setup' AND version = '1.0.0';

UPDATE script
SET nfs_path = '/opt/stability-test-agent/agent/scripts/push_resources/v1.0.0/push_resources.py',
    updated_at = now()
WHERE id = 24 AND name = 'push_resources' AND version = '1.0.0';

UPDATE script
SET nfs_path = '/opt/stability-test-agent/agent/scripts/monkey_check/v1.0.0/monkey_check.py',
    updated_at = now()
WHERE id = 28 AND name = 'monkey_check' AND version = '1.0.0';

-- monkey_teardown：路径 + sha 同时更新（marker schema 升级后内容变化）
UPDATE script
SET nfs_path = '/opt/stability-test-agent/agent/scripts/monkey_teardown/v1.0.0/monkey_teardown.py',
    content_sha256 = 'dde7eda3c44744a59b61d24af504851516a2f86fd7fce604f1d9aa59cd262e7b',
    updated_at = now()
WHERE id = 67 AND name = 'monkey_teardown' AND version = '1.0.0';

\echo Step 1 done: 10 records nfs_path updated

-- ==================== Step 2: deactivate 7 条 dangling 记录 ====================

UPDATE script
SET is_active = false,
    updated_at = now()
WHERE id IN (30, 31, 59, 62, 64, 65, 66)
  AND is_active = true;

\echo Step 2 done: 7 dangling records deactivated

-- ==================== Step 3: INSERT monkey_launch v1.0.0 + v2.0.0 ====================

INSERT INTO script
  (name, display_name, category, script_type, version, nfs_path, entry_point,
   content_sha256, param_schema, is_active, description, created_at, updated_at)
VALUES
  ('monkey_launch', 'monkey_launch', 'device', 'python', '1.0.0',
   '/opt/stability-test-agent/agent/scripts/monkey_launch/v1.0.0/monkey_launch.py', '',
   'f566ddd49f2a26acf7a44499ef354936fe6505366658c70b784c44646bc0d4c6',
   '{}'::jsonb, true, 'monkey launch script v1.0.0', now(), now()),
  ('monkey_launch', 'monkey_launch', 'device', 'python', '2.0.0',
   '/opt/stability-test-agent/agent/scripts/monkey_launch/v2.0.0/monkey_launch.py', '',
   'a6170b7b92a61c65739f1994ee26374859c95cefba65520a516e57bbc045a643',
   '{}'::jsonb, true, 'monkey launch script v2.0.0', now(), now())
ON CONFLICT (name, version) DO UPDATE
  SET nfs_path = EXCLUDED.nfs_path,
      content_sha256 = EXCLUDED.content_sha256,
      is_active = true,
      updated_at = now();

\echo Step 3 done: monkey_launch v1.0.0 + v2.0.0 inserted/upserted

\echo
\echo === Pre-commit verification ===

SELECT 'active scripts under /opt/.../scripts/' AS metric, count(*) AS value
FROM script
WHERE is_active = true
  AND nfs_path LIKE '/opt/stability-test-agent/agent/scripts/%';

SELECT 'active scripts NOT under /opt/.../scripts/ (should be 0)' AS metric, count(*) AS value
FROM script
WHERE is_active = true
  AND nfs_path NOT LIKE '/opt/stability-test-agent/agent/scripts/%';

SELECT 'inactive scripts (dangling)' AS metric, count(*) AS value
FROM script
WHERE is_active = false;

SELECT id, name, version, nfs_path, is_active FROM script ORDER BY is_active DESC, id;

COMMIT;

\echo
\echo === COMMITTED ===
