-- ============================================================================
-- 生产库诊断只读角色（#2632 缺口②）
-- ============================================================================
--
-- 为什么要有这个脚本
-- ------------------
-- 2026-09-16/17 那批「猜 schema」的一次性 SQL 里，PG 日志只有 `user@db`：手工查询用的是
-- 应用共享凭据 `stp`，另有 **`postgres@stp`（超级用户）至少 3 条**。后果是两条：
--
-- 1. **无法归属**——「这条 SQL 是哪条链路发的」答不出来（`application_name` 只解决由代码
--    发起的连接，#2632 缺口①；手查不经过代码）；
-- 2. **误操作半径 = 全库**——超级用户与应用凭据被拿来跑临时 SELECT，一旦哪天「猜对了」
--    并顺手改了东西，没有任何东西挡着。
--
-- 观测面已经补上（`tools/dev/pg_error_guard.py` → 告警 `StabilityPgSchemaGuessing`），
-- 但 SOP 一直写着「临时诊断用专用只读角色」而**这个角色从来不存在**——那等于让 SOP 把人
-- 指回 `stp`。本脚本补的就是这一口。
--
-- 怎么执行（运维，人工授权）
-- --------------------------
-- ```bash
-- sudo -u postgres psql --set ON_ERROR_STOP=1 -d stp -f deploy/postgres/diag-readonly.sql
-- sudo -u postgres psql -c "\password stp_ro"        # 口令只落在库里，不进仓库/文档/日志
-- ```
--
-- `ON_ERROR_STOP` 放在**调用侧**而不是文件里：本文件是纯 SQL（不含 `\` 元命令），
-- 这样任何客户端都能执行它，测试也能直接把它喂给一个隔离实例跑真行为——写进文件反而
-- 让「能不能被程序化验证」取决于客户端。fail-fast 是调用约定，不是脚本内容。
--
-- **本脚本不含任何口令**（AGENTS.md 红线：凭据不得进入代码、文档、日志与 PR diff）。
-- 建出来的角色 `PASSWORD NULL` 状态下无法登录，必须由运维在库侧设置口令才可用——
-- 这是刻意的：没有「跑完脚本顺手得到一个可用凭据」的路径。
--
-- 回滚
-- ----
-- ```sql
-- DROP OWNED BY stp_ro;              -- 本角色不拥有任何对象，通常是空操作
-- REVOKE ALL ON SCHEMA public FROM stp_ro;
-- DELETE FROM pg_default_acl WHERE defaclrole = 'stp_ro'::regrole OR aclitem::text LIKE '%stp_ro=%';
-- DROP ROLE stp_ro;
-- ```
-- （`ALTER DEFAULT PRIVILEGES FOR ROLE stp ... REVOKE SELECT` 需要以 `stp` 身份执行；
--   若整套诊断面都不要了，直接 `DROP ROLE` 即可——默认 ACL 会随角色一起被清理。）
--
-- 幂等性：**可重复执行**——已存在角色时走 DO 块的 ALTER 分支，不中断（迁移后重跑
-- 本脚本重申授权是常规动作，见 §3 的默认 ACL 说明）。
--
-- 角色名与建表属主是**字面量**（`stp_ro` / `stp`），不用 psql 变量：变量与字面量混写
-- 会留下「改名只改了一半」的坑，而改名在这份脚本里本就该是一次全文替换 + 一次
-- `tests/test_prod_diag_readonly_role_contract.py` 的同步。
-- ============================================================================

BEGIN;

-- ── 1. 角色本体：只能登录，不能创建任何东西，不是任何角色的成员 ──────────────
-- NOSUPERUSER/CREATEROLE/CREATEDB/REPLICATION 全部显式写出：默认值随 PG 大版本与
-- 部署模板变化，靠默认等于赌。NOINHERIT 保证它不会因为将来被加进某个组而白拿权限。
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'stp_ro') THEN
    RAISE NOTICE 'role stp_ro 已存在，仅重申其属性与授权';
  ELSE
    CREATE ROLE stp_ro WITH LOGIN PASSWORD NULL
      NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION
      CONNECTION LIMIT 3;                 -- 诊断不该占用应用的连接预算
  END IF;
END
$$;

-- 已存在的角色也要把属性拉回本脚本的口径（CREDENTIAL 只重置为 NULL 之外的部分，
-- 不覆盖运维已设的口令）。
ALTER ROLE stp_ro NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION
  CONNECTION LIMIT 3;

-- ── 2. 会话默认值：这三条才是「误操作半径」真正的闸 ──────────────────────────
-- 只读事务：即使将来授权被放宽，BEGIN; UPDATE ... 也会被拒；
-- log_statement=all：本角色的**每一条**语句都进服务端日志——诊断量很小，
--   「无留痕」这一条从此不靠人自觉（#2632 的核心抱怨就是事后只能人读日志）；
-- statement_timeout：一条误写的 `SELECT * FROM step_trace` 不该把生产拖住。
ALTER ROLE stp_ro SET default_transaction_read_only = on;
ALTER ROLE stp_ro SET log_statement = 'all';
ALTER ROLE stp_ro SET statement_timeout = '60s';
ALTER ROLE stp_ro SET lock_timeout = '5s';

-- ── 3. 授权：只有读，且**未来新表也要能读** ─────────────────────────────────
GRANT USAGE ON SCHEMA public TO stp_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO stp_ro;

-- 这一条是本脚本里最容易被漏掉、漏掉后失效最安静的一条：
-- 只授「当前所有表」，则**下一次 alembic 迁移建出来的表这个角色读不到**。人的反应
-- 不会是「去补授权」，而是退回用 `stp` 或 `postgres` 手查——于是缺口②原地复活，
-- 而且没人会记得它复活了。默认 ACL 让「新对象自动可读」成为事实而不是约定。
-- FOR ROLE 指向**建表的那个角色**（生产由应用属主 `stp` 执行迁移），不是本诊断角色。
ALTER DEFAULT PRIVILEGES FOR ROLE stp IN SCHEMA public
  GRANT SELECT ON TABLES TO stp_ro;

-- 序列与视图：诊断只读不需要 nextval()，因此**不**授 USAGE ON SEQUENCE。
-- 大版本升级后若把系统视图移出 public，上面的 GRANT 覆盖不到，需要时按对象补，
-- 不要为了省事给整库 ALL。

COMMIT;

-- ── 4. 执行后自检（人工看输出；这几条红了就不要开始诊断）────────────────────
DO $$
DECLARE
  ro_settings text[];
  write_grants int;
BEGIN
  SELECT count(*) INTO write_grants
  FROM information_schema.role_table_grants
  WHERE grantee = 'stp_ro'
    AND privilege_type NOT IN ('SELECT', 'REFERENCES', 'TRIGGER');

  SELECT array_agg(setting) INTO ro_settings
  FROM pg_db_role_setting s, unnest(s.setconfig) AS setting
  WHERE s.setrole = 'stp_ro'::regrole;

  IF write_grants > 0 THEN
    RAISE EXCEPTION 'stp_ro 上存在非读权限 % 条——本脚本只授权读，请人工核对后再跑',
      write_grants;
  END IF;
  IF ro_settings IS NULL
     OR array_to_string(ro_settings, ',') NOT LIKE '%default_transaction_read_only=on%' THEN
    RAISE EXCEPTION 'stp_ro 缺少 default_transaction_read_only=on——只读闸没生效';
  END IF;
  IF ro_settings IS NULL OR array_to_string(ro_settings, ',') NOT LIKE '%log_statement=all%' THEN
    RAISE EXCEPTION 'stp_ro 缺少 log_statement=all——「无留痕」那条缺口没被补上';
  END IF;
  RAISE NOTICE '诊断只读角色就绪：stp_ro（只读事务 + 全语句留痕 + 未来新表自动可读）';
END
$$;
