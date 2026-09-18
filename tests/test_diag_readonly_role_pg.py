"""#2632 缺口②（行为半边）：在真实 PG16 上证明诊断只读角色**真的管用**。

静态检查看不见的那两类，只有真库能抓：

1. `ALTER DEFAULT PRIVILEGES FOR ROLE stp` 是否对**脚本执行之后**才建的表生效——
   `FOR ROLE` 指错对象时字符串照样在场，静态判据恒绿；
2. `default_transaction_read_only=on` 是否**独立**构成第二道闸（测试故意先 `GRANT INSERT`
   再试写：仍须被会话参数拒）。

fixture 的形状刻意照生产：**表由应用属主 `stp` 建**、脚本由超级用户执行。
需要 docker，故列在 `ci.yml` / `scripts/run_gates.py` 的 `--ignore` 名单里（归夜间全量
`backend-test`），约定由 `tests/test_offline_subset_guard.py` 守。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SQL = REPO_ROOT / "deploy" / "postgres" / "diag-readonly.sql"

DIAG_ROLE = "stp_ro"
APP_OWNER = "stp"          # 生产里建表/跑迁移的属主角色


def _dsn(base: str, user: str, password: str) -> str:
    """把 testcontainers 的连接串换成指定身份（隔离实例，凭据是临时的、不进仓）。"""
    return re.sub(r"//[^@]*@", f"//{user}:{password}@", base, count=1)


@pytest.fixture(scope="module")
def diag_ready():
    """一个隔离 PG16，形状照生产：**表由应用属主 `stp` 建**、脚本由超级用户执行。

    `FOR ROLE` 指错对象时静态检查看不出来（字符串在场就行）——只有真建一张
    「脚本执行之后才出现的表」才能证明默认 ACL 是否真的生效，这正是本 fixture 的目的。
    """
    psycopg = pytest.importorskip("psycopg")
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16") as container:
        # testcontainers 的镜像初始化用户是 POSTGRES_USER=test（它才是这个实例的超级用户，
        # 镜像里**没有** postgres 角色）——硬换用户名只会得到 password authentication failed。
        base = container.get_connection_url().replace(
            "postgresql+psycopg2://", "postgresql://", 1
        )
        admin = base
        app = _dsn(base, APP_OWNER, "app-test-only")
        with psycopg.connect(admin, autocommit=True) as conn:
            conn.execute(f"CREATE ROLE {APP_OWNER} LOGIN PASSWORD 'app-test-only'")
            # PG15+ 起 public 默认不给 CREATE：生产里建表属主本来就有，这里显式给
            conn.execute(f"GRANT CREATE, USAGE ON SCHEMA public TO {APP_OWNER}")
            conn.execute(SQL.read_text(encoding="utf-8"))
            conn.execute(f"ALTER ROLE {DIAG_ROLE} PASSWORD 'diag-test-only'")
        with psycopg.connect(app, autocommit=True) as conn:
            conn.execute("CREATE TABLE t_before (id int PRIMARY KEY)")
            conn.execute("INSERT INTO t_before VALUES (1)")
        yield {"admin": admin, "app": app, "diag": _dsn(base, DIAG_ROLE, "diag-test-only")}


def _run_script(admin_dsn: str) -> None:
    import psycopg

    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        conn.execute(SQL.read_text(encoding="utf-8"))


def _diag(admin_dsn: str):
    """测试内需要重新执行脚本时，确保口令仍在（幂等分支不会重置它）。"""
    import psycopg

    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        conn.execute(f"ALTER ROLE {DIAG_ROLE} PASSWORD 'diag-test-only'")


def test_script_runs_and_diag_role_can_read_existing_table(diag_ready) -> None:
    import psycopg

    with psycopg.connect(diag_ready["diag"], autocommit=True) as conn:
        assert conn.execute("SELECT id FROM t_before").fetchall() == [(1,)]


def test_newly_created_table_is_readable_without_rerunning_the_script(diag_ready) -> None:
    """**本文件最要紧的一条**：迁移新建的表必须自动可读。

    少了 `ALTER DEFAULT PRIVILEGES FOR ROLE stp`，这张表对 stp_ro 就是
    `permission denied` → 人退回用 `stp` 手查 → 缺口②原地复活且无人记账。
    """
    import psycopg

    with psycopg.connect(diag_ready["app"], autocommit=True) as conn:
        conn.execute("CREATE TABLE t_after_migration (id int)")     # 模拟下一次 alembic
    with psycopg.connect(diag_ready["diag"], autocommit=True) as conn:
        assert conn.execute("SELECT count(*) FROM t_after_migration").fetchone()[0] == 0


def test_write_attempts_by_diag_role_are_rejected(diag_ready) -> None:
    """两道闸各挡一半：授权面（没有 INSERT/CREATE 权）+ 会话只读事务。"""
    import psycopg

    with psycopg.connect(diag_ready["diag"], autocommit=True) as conn:
        with pytest.raises(psycopg.Error):
            conn.execute("INSERT INTO t_before VALUES (99)")
        with pytest.raises(psycopg.Error):
            conn.execute("CREATE TABLE should_not_exist (id int)")


def test_read_only_session_gate_is_independent_of_grants(diag_ready) -> None:
    """只读事务这一层要单独验：即使将来有人放宽授权，写仍然会被会话参数拒。"""
    import psycopg

    with psycopg.connect(diag_ready["admin"], autocommit=True) as conn:
        conn.execute(f"GRANT INSERT ON t_before TO {DIAG_ROLE}")   # 故意只给写权限
    with psycopg.connect(diag_ready["diag"], autocommit=True) as conn:
        with pytest.raises(psycopg.Error) as exc:
            conn.execute("INSERT INTO t_before VALUES (100)")
    assert "read-only" in str(exc.value).lower() or "cannot execute" in str(exc.value).lower()
    with psycopg.connect(diag_ready["admin"], autocommit=True) as conn:
        conn.execute(f"REVOKE INSERT ON t_before FROM {DIAG_ROLE}")


def test_role_settings_and_default_privileges_actually_landed(diag_ready) -> None:
    import psycopg

    with psycopg.connect(diag_ready["admin"], autocommit=True) as conn:
        settings = conn.execute(
            "SELECT array_to_string(setconfig, ',') FROM pg_db_role_setting s "
            "JOIN pg_roles r ON r.oid = s.setrole WHERE r.rolname = %s",
            (DIAG_ROLE,),
        ).fetchone()
        assert settings, "pg_db_role_setting 里没有该角色的任何会话参数"
        assert "default_transaction_read_only=on" in settings[0]
        assert "log_statement=all" in settings[0]
        defaults = conn.execute(
            "SELECT pg_get_userbyid(defaclrole)::text, defaclacl::text FROM pg_default_acl"
        ).fetchall()
        assert any(
            row[0] == APP_OWNER and f"{DIAG_ROLE}=r/" in row[1] for row in defaults
        ), f"默认 ACL 没落在建表属主 {APP_OWNER} 上：{defaults}"


def test_script_is_idempotent(diag_ready) -> None:
    """可重复执行：迁移后重申授权是常规动作，跑第二次不能炸。"""
    import psycopg

    _run_script(diag_ready["admin"])
    _run_script(diag_ready["admin"])
    _diag(diag_ready["admin"])
    with psycopg.connect(diag_ready["admin"], autocommit=True) as conn:
        assert conn.execute(
            "SELECT count(*) FROM pg_roles WHERE rolname = %s", (DIAG_ROLE,)
        ).fetchone()[0] == 1
    with psycopg.connect(diag_ready["diag"], autocommit=True) as conn:
        assert conn.execute("SELECT count(*) FROM t_before").fetchone()[0] == 1
