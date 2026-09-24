"""Tests for tools/dev/check_db_pool_budget.py（ADR-0047 D1 / #2959 启动门禁）。

门禁的价值全在**判定**上，所以判定面（可用槽 / 预算不等式）用纯函数喂样本；
IO 面只验「非 PG 形态跳过、不误红」——真 PG 路径由部署窗的实测覆盖（本文件不连库，
避免把「环境问题」静默变成一条假绿或假红）。
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "dev" / "check_db_pool_budget.py"


def _clean_env() -> dict[str, str]:
    """去掉 ambient `DATABASE_URL`（契约：ambient 优先于 `--env-file`）。

    本机就是生产控制面，环境里若带 `DATABASE_URL`，子进程会真的连上生产库——
    只读 `SHOW` 也不该是测试的隐式依赖。
    """
    return {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}


def _load_module():
    spec = importlib.util.spec_from_file_location("check_db_pool_budget", TOOL)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_available_slots_subtracts_reserved_families():
    mod = _load_module()
    assert mod.available_slots(
        {
            "max_connections": 100,
            "superuser_reserved_connections": 3,
            "reserved_connections": 0,
        }
    ) == 97
    # PG<17 无 reserved_connections：缺键按 0，不得抛
    assert mod.available_slots({"max_connections": 100, "superuser_reserved_connections": 3}) == 97
    assert mod.available_slots({"max_connections": 200, "superuser_reserved_connections": 5, "reserved_connections": 10}) == 185


def test_evaluate_accepts_adr_defaults():
    """ADR-0047 v1.1 首轮值：80 + reserve 8 ≤ 97 ⇒ 通过。"""
    mod = _load_module()
    cap = {"per_engine": 40, "engines": 2, "app_total": 80, "pool_timeout": 2, "pool_size": 20, "max_overflow": 20}
    ok, line = mod.evaluate(capacity=cap, instances=1, reserve=8, available=97)
    assert ok is True
    assert "headroom=17" in line


def test_evaluate_rejects_legacy_180_budget():
    """R523 事故形态：180 预算 + reserve 远超 97 ⇒ 必须拒绝（这就是门禁要拦的回归）。"""
    mod = _load_module()
    cap = {"per_engine": 90, "engines": 2, "app_total": 180, "pool_timeout": 2, "pool_size": 30, "max_overflow": 60}
    ok, line = mod.evaluate(capacity=cap, instances=1, reserve=8, available=97)
    assert ok is False
    assert "FAIL" in line and "STP_DB_POOL_SIZE" in line


def test_evaluate_rejects_when_reserve_eats_headroom():
    """边界：预算本身不超，但加上运维预留就超 ⇒ 仍拒（预留不是可选装饰）。"""
    mod = _load_module()
    cap = {"per_engine": 45, "engines": 2, "app_total": 90, "pool_timeout": 2, "pool_size": 25, "max_overflow": 20}
    ok, _ = mod.evaluate(capacity=cap, instances=1, reserve=8, available=97)
    assert ok is False
    # 同一组容量在 reserve=0 时成立——证明拒绝来自预留项而非容量算术
    ok_no_reserve, _ = mod.evaluate(capacity=cap, instances=1, reserve=0, available=97)
    assert ok_no_reserve is True


def test_evaluate_multiplies_by_instances():
    """D6：多实例按实例数倍增（10 × (20+20) × 2 = 400 > 97 ⇒ 拒）。"""
    mod = _load_module()
    cap = {"per_engine": 40, "engines": 2, "app_total": 80, "pool_timeout": 2, "pool_size": 20, "max_overflow": 20}
    ok, line = mod.evaluate(capacity=cap, instances=10, reserve=8, available=97)
    assert ok is False
    assert "instances=10" in line


def test_cli_skips_non_postgres_url(tmp_path):
    """SQLite（开发机）⇒ WARN + 退出 0，不把开发挡住。"""
    env_file = tmp_path / "env.dev"
    env_file.write_text("DATABASE_URL=sqlite:///tmp/dev.db\n", encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(TOOL), "--env-file", str(env_file)],
        capture_output=True,
        text=True,
        cwd=ROOT,
        env=_clean_env(),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "跳过连接预算校验" in proc.stdout


def test_cli_skips_when_no_url(tmp_path):
    env_file = tmp_path / "env.empty"
    env_file.write_text("# 无 DATABASE_URL\n", encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(TOOL), "--env-file", str(env_file)],
        capture_output=True,
        text=True,
        cwd=ROOT,
        env=_clean_env(),
    )
    assert proc.returncode == 0
    assert "DATABASE_URL 未配置" in proc.stdout


def test_cli_fails_closed_when_env_file_missing(tmp_path):
    """显式 `--env-file` 读不到 ⇒ 非零退出（静默退回默认 = 假绿）。"""
    missing = tmp_path / "no-such.env"
    proc = subprocess.run(
        [sys.executable, str(TOOL), "--env-file", str(missing)],
        capture_output=True,
        text=True,
        cwd=ROOT,
        env=_clean_env(),
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "--env-file 不存在或不可读" in proc.stdout


# ── 2026-09-23 裁决补正①：fail-closed 的例外范围只能收在「旧 PG 缺 GUC」一处 ──


class _FakeCursor:
    def __init__(self, value: str):
        self._value = value

    def fetchone(self):
        return (self._value,)


class _FakeConn:
    """只实现 `_read_pg_settings` 用到的子集：上下文协议 + `execute(...).fetchone()`。"""

    def __init__(self, values: dict[str, str], raise_for: set[str]):
        self._values = values
        self._raise_for = raise_for

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def execute(self, sql: str):
        import psycopg

        key = sql.split()[-1]
        if key in self._raise_for:
            raise psycopg.errors.UndefinedObject(f'unrecognized configuration parameter "{key}"')
        return _FakeCursor(self._values.get(key, "0"))


def _patch_connect(monkeypatch, mod, conn):
    monkeypatch.setattr(mod.psycopg, "connect", lambda *a, **kw: conn)


def test_read_pg_settings_tolerates_legacy_missing_reserved_connections(monkeypatch):
    """旧 PG（无 reserved_connections）⇒ 按 0 计 + 留一条 INFO 说明，其余键照读。"""
    mod = _load_module()
    conn = _FakeConn(
        values={"max_connections": "200", "superuser_reserved_connections": "5"},
        raise_for={"reserved_connections"},
    )
    _patch_connect(monkeypatch, mod, conn)

    settings, notes = mod._read_pg_settings("postgresql://ignored")
    assert settings == {
        "max_connections": 200,
        "superuser_reserved_connections": 5,
        "reserved_connections": 0,
    }
    assert len(notes) == 1 and "reserved_connections" in notes[0]
    assert mod.available_slots(settings) == 195


def test_read_pg_settings_propagates_other_read_failures(monkeypatch):
    """非「缺 GUC」的读失败（掉线/权限/…) 必须向上抛——宽口径 except 是事实上的 fail-open。"""
    import psycopg

    mod = _load_module()
    conn = _FakeConn(values={}, raise_for=set())

    def _boom(sql: str):
        raise psycopg.OperationalError("connection closed mid-read")

    monkeypatch.setattr(conn, "execute", _boom)
    _patch_connect(monkeypatch, mod, conn)

    with pytest.raises(psycopg.OperationalError):
        mod._read_pg_settings("postgresql://ignored")


def test_read_pg_settings_rejects_undefined_guc_on_other_keys(monkeypatch):
    """`max_connections` 报「不存在」说明 PG 环境异常，不得当旧版本容忍掉。"""
    mod = _load_module()
    conn = _FakeConn(values={"superuser_reserved_connections": "3"}, raise_for={"max_connections"})
    _patch_connect(monkeypatch, mod, conn)

    with pytest.raises(mod.psycopg.errors.UndefinedObject):
        mod._read_pg_settings("postgresql://ignored")


# ── 2026-09-23 裁决补正②：`--env-file` 的预算键必须真的参与判定 ──────────────


@pytest.fixture
def _clean_budget_env(monkeypatch):
    for key in _load_module()._BUDGET_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    yield


def test_apply_env_file_config_injects_missing_and_respects_ambient(
    tmp_path, monkeypatch, _clean_budget_env
):
    mod = _load_module()
    env_file = tmp_path / "env.backend"
    env_file.write_text(
        "STP_DB_POOL_SIZE=30\n"
        "STP_DB_MAX_OVERFLOW=60\n"
        "STP_DB_POOL_INSTANCES=2\n"
        "STP_DB_CONNECTION_RESERVE=5\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("STP_DB_MAX_OVERFLOW", "99")  # ambient 优先

    injected = mod.apply_env_file_config(str(env_file))

    import os

    assert os.environ["STP_DB_POOL_SIZE"] == "30"
    assert os.environ["STP_DB_MAX_OVERFLOW"] == "99", "ambient 必须胜出"
    assert os.environ["STP_DB_POOL_INSTANCES"] == "2"
    assert env_file.name not in injected.values()
    assert set(injected) == {"STP_DB_POOL_SIZE", "STP_DB_POOL_INSTANCES", "STP_DB_CONNECTION_RESERVE"}
    assert mod.config_source(injected) == "env+env-file"


def test_env_file_pool_keys_change_the_verdict(tmp_path, monkeypatch, _clean_budget_env):
    """补正②的判据：同一个文件，修前判定看默认(20/20 通过)、修后看文件(30/60 拒绝)。"""
    mod = _load_module()
    env_file = tmp_path / "env.backend"
    env_file.write_text(
        "DATABASE_URL=postgresql://u:p@127.0.0.1:5432/stp\n"
        "STP_DB_POOL_SIZE=30\n"
        "STP_DB_MAX_OVERFLOW=60\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@127.0.0.1:5432/stp")

    injected = mod.apply_env_file_config(str(env_file))
    capacity = mod._pool_capacity()
    assert injected, "文件里的池键必须被注入"
    assert capacity["per_engine"] == 90, f"判定必须看到文件里的 30+60，实测 {capacity}"

    ok, line = mod.evaluate(capacity=capacity, instances=1, reserve=8, available=97)
    assert ok is False and "FAIL" in line
    assert mod.config_source(injected) == "env-file"
