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
