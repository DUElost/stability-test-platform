"""#1300（R15-R01）—— 显式 TEST_DATABASE_URL 的机器护栏。"""
from __future__ import annotations

import logging

import pytest

from backend.core.db_url_guard import (
    UnsafeTestDatabaseUrl,
    control_plane_env_file_present,
    guard_test_database_url,
)


def test_isolated_naming_passes():
    url = "postgresql+psycopg://stp:pw@127.0.0.1:5432/stp_test"
    assert guard_test_database_url(url) == url


def test_isolated_naming_case_insensitive():
    url = "postgresql+psycopg://u:p@db.internal:5432/MyTestDB"
    assert guard_test_database_url(url) == url


@pytest.mark.parametrize("dbname", ["stp", "production", "appdb", ""])
def test_non_test_dbname_rejected(dbname):
    url = f"postgresql+psycopg://u:p@db.internal:5432/{dbname}"
    with pytest.raises(UnsafeTestDatabaseUrl, match="must contain 'test'"):
        guard_test_database_url(url)


def test_non_postgres_scheme_rejected():
    with pytest.raises(UnsafeTestDatabaseUrl, match="PostgreSQL"):
        guard_test_database_url("sqlite:///prod.db")


def test_identical_to_runtime_database_url_rejected():
    url = "postgresql+psycopg://stp:pw@127.0.0.1:5432/stp_test"
    with pytest.raises(UnsafeTestDatabaseUrl, match="identical to DATABASE_URL"):
        guard_test_database_url(url, runtime_database_url=url)


def test_different_from_runtime_database_url_passes():
    url = "postgresql+psycopg://stp:pw@127.0.0.1:5432/stp_test"
    runtime = "postgresql+psycopg://stp:pw@10.0.0.9:5432/stp_test"
    assert guard_test_database_url(url, runtime_database_url=runtime) == url


def test_override_env_bypasses_with_warning(monkeypatch, caplog):
    monkeypatch.setenv("STP_ALLOW_UNSAFE_TEST_DATABASE_URL", "1")
    url = "postgresql+psycopg://u:p@prod-host:5432/production"
    with caplog.at_level(logging.WARNING, logger="backend.core.test_db_guard"):
        assert guard_test_database_url(url) == url
    assert any("test_db_guard_bypassed" in r.message for r in caplog.records)


# ── #2632 缺口③：控制面 loopback 拒绝 ──────────────────────────────────────


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg://postgres:pw@127.0.0.1:5432/stp_test",
        "postgresql+psycopg://postgres:pw@localhost:5432/stp_test",
        "postgresql+psycopg://postgres:pw@[::1]:5432/stp_test",
        # host 为空 = libpq 走本机 unix socket，同样是本机实例
        "postgresql+psycopg:///stp_test",
    ],
)
def test_control_plane_loopback_rejected(url):
    """本机是控制面时，loopback 的显式测试库就是生产实例（库名不同也不隔离）。"""
    with pytest.raises(UnsafeTestDatabaseUrl, match="loopback"):
        guard_test_database_url(url, on_control_plane_host=True)


def test_control_plane_remote_host_allowed():
    url = "postgresql+psycopg://stp:pw@10.0.0.9:5432/stp_test"
    assert guard_test_database_url(url, on_control_plane_host=True) == url


def test_loopback_allowed_off_control_plane():
    """CI / 普通开发机形状（无 .env.backend）必须原样放行。"""
    url = "postgresql+psycopg://postgres:postgres@localhost:5432/stability_test"
    assert guard_test_database_url(url, on_control_plane_host=False) == url


def test_control_plane_loopback_override_bypasses(monkeypatch, caplog):
    monkeypatch.setenv("STP_ALLOW_UNSAFE_TEST_DATABASE_URL", "1")
    url = "postgresql+psycopg://postgres:pw@127.0.0.1:5432/stp_test"
    with caplog.at_level(logging.WARNING, logger="backend.core.db_url_guard"):
        assert guard_test_database_url(url, on_control_plane_host=True) == url
    assert any("test_db_guard_bypassed" in r.message for r in caplog.records)


def test_control_plane_env_file_present(tmp_path):
    """标记物只判存在、不读内容（仓库根的生产 env 源文件）。"""
    assert control_plane_env_file_present(tmp_path) is False
    (tmp_path / ".env.backend").write_text("DATABASE_URL='…'\n", encoding="utf-8")
    assert control_plane_env_file_present(tmp_path) is True


# ── #2794：判定加固（query 覆盖 / multihost / unix socket 路径）───────────────


@pytest.mark.parametrize(
    "url",
    [
        # query 覆盖 libpq 实际连接的 host（authority 段只是幌子）
        "postgresql+psycopg://postgres:pw@db.example:5432/stp_test?host=127.0.0.1",
        "postgresql+psycopg://postgres:pw@db.example:5432/stp_test?hostaddr=127.0.0.1",
        # multihost：libpq 逗号分隔逐个尝试，任一为 loopback 即可能连到本机实例
        "postgresql+psycopg://postgres:pw@db.example:5432,127.0.0.1:5432/stp_test",
        # unix socket 路径形态（本机实例的另一种写法）
        "postgresql+psycopg://postgres:pw@db.example:5432/stp_test?host=/var/run/postgresql",
        # 127.0.0.0/8 非 .1 也是本机；IPv4-mapped IPv6 同样落回本机
        "postgresql+psycopg://postgres:pw@127.0.0.2:5432/stp_test",
        "postgresql+psycopg://postgres:pw@[::ffff:127.0.0.1]:5432/stp_test",
    ],
)
def test_control_plane_loopback_bypass_forms_rejected(url):
    """#2794：旧判定只看 `urlsplit().hostname`——query 覆盖 / multihost / socket 路径
    都能绕过它，把 TRUNCATE 打到本机生产实例，必须一并拒。"""
    with pytest.raises(UnsafeTestDatabaseUrl, match="loopback"):
        guard_test_database_url(url, on_control_plane_host=True)


def test_bypass_forms_allowed_off_control_plane():
    """加固不得误伤 CI / 普通开发机（第 3 道闸不参与判定）。"""
    for url in (
        "postgresql+psycopg://u:p@db.example:5432/stp_test?host=127.0.0.1",
        "postgresql+psycopg://u:p@h1:5432,127.0.0.1:5432/stp_test",
    ):
        assert guard_test_database_url(url, on_control_plane_host=False) == url


def test_control_plane_remote_multihost_without_loopback_allowed():
    """多 host 但都不在 loopback（共享验证库形态）仍按原样放行。"""
    url = "postgresql+psycopg://u:p@db1.example:5432,db2.example:5432/stp_test"
    assert guard_test_database_url(url, on_control_plane_host=True) == url


def test_control_plane_env_file_found_through_worktree(tmp_path):
    """worktree 里没有未跟踪的 .env.backend——必须回溯主检出再判（#2632）。

    实测教训：只看当前 worktree 根时，这道闸恰好漏掉所有并行 worktree，而本机
    跑测试的形态正是 worktree——守卫静默失效后真的连上了生产实例。
    """
    main = tmp_path / "main"
    (main / ".git" / "worktrees" / "wt").mkdir(parents=True)
    (main / ".env.backend").write_text("DATABASE_URL='…'\n", encoding="utf-8")
    worktree = tmp_path / "wt"
    worktree.mkdir()
    (worktree / ".git").write_text(
        f"gitdir: {main / '.git' / 'worktrees' / 'wt'}\n", encoding="utf-8"
    )

    assert control_plane_env_file_present(worktree) is True

    # 主检出没有该文件（CI/开发机）时不得误判
    (main / ".env.backend").unlink()
    assert control_plane_env_file_present(worktree) is False
