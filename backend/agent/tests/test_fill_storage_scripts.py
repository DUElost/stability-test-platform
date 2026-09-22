# -*- coding: utf-8 -*-
"""#3085：fill_storage **v1.1.1**——按目标百分比**双向调节**（基线剔除自建文件 + 释放路径）。

背景（2026-09-22 设备满盘取证）：v1.0.0–v1.1.0 单向填充且把自建文件算进 used ⇒
按 60% 填过一次后文件永不回收（实测 45.5 GB），后续「填到 40%」永远短路报假绿。

本文件钉住 v1.1.1 语义（低占用填充 / 高占用释放 / 幂等 / 目标调低缩容 / 证据报文）
与 **v1.1.0 对照锚点**（旧版本不可变，只读断言其「高占用即短路、不动文件」）。
"""
from __future__ import annotations

import importlib.util
import json
import sys

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPTS = REPO_ROOT / "backend" / "agent" / "scripts"
_TOTAL_KB = 100_000  # 便于算百分比：1% = 1000KB


def _load(name: str, rel_path: str):
    path = _SCRIPTS / rel_path
    sys.path.insert(0, str(path.parent))
    try:
        sys.modules.pop("_adb", None)
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec and spec.loader, f"cannot locate {path}"
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.modules.pop("_adb", None)
        sys.path.remove(str(path.parent))


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("STP_DEVICE_SERIAL", "SERIAL-A")
    monkeypatch.delenv("STP_STEP_PARAMS", raising=False)


@pytest.fixture(scope="module")
def fill_v111():
    return _load("fill_storage_v111", "fill_storage/v1.1.1/fill_storage.py")


@pytest.fixture(scope="module")
def fill_v110_anchor():
    """对照锚点：v1.1.0 不可变——高占用时短路 already_met，绝不 rm/dd。"""
    return _load("fill_storage_v110_anchor", "fill_storage/v1.1.0/fill_storage.py")


class _Completed:
    def __init__(self, stdout: str = "", stderr: str = "", returncode: int = 0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


class _FakeAdb:
    """按命令子串派发；df 返回「按调用顺序消费的 (used_kb) 序列」，其余命令按路由。"""

    def __init__(self, *, total_kb: int = _TOTAL_KB, used_seq: list[int], fill_kb: int = 0,
                 dd_rc: int = 0, rm_rc: int = 0, dd_err: str = ""):
        self.total_kb = total_kb
        self.used_seq = list(used_seq)
        self.fill_kb = fill_kb
        self.dd_rc, self.rm_rc, self.dd_err = dd_rc, rm_rc, dd_err
        self.calls: list[str] = []

    def __call__(self, command: str, timeout: int = 30) -> _Completed:
        self.calls.append(command)
        if command.startswith("df /data"):
            used = self.used_seq[min(len([c for c in self.calls if c.startswith("df /data")]) - 1,
                                     len(self.used_seq) - 1)]
            return _Completed(stdout=f"Filesystem 1K-blocks Used Available Use% Mounted\n"
                                     f"/dev/block/dm-51 {self.total_kb} {used} {self.total_kb - used} 0% /data\n")
        if command.startswith("du -sk"):
            if self.fill_kb <= 0:
                return _Completed(stdout="", stderr="du: no such file", returncode=1)
            return _Completed(stdout=f"{self.fill_kb}\t{command.split()[-1]}\n")
        if command.startswith("dd if=/dev/zero"):
            return _Completed(stdout="", stderr=self.dd_err, returncode=self.dd_rc)
        if command.startswith("rm -f"):
            return _Completed(stdout="", returncode=self.rm_rc)
        raise AssertionError(f"unexpected adb command: {command}")

    def count(self, needle: str) -> int:
        return sum(1 for c in self.calls if needle in c)


def _patch(fill_mod, monkeypatch, fake: _FakeAdb, params: dict | None = None):
    monkeypatch.setattr(fill_mod, "adb_shell_quiet", fake)
    monkeypatch.setattr(fill_mod, "params", lambda: params or {})
    return fake


def _result(capsys) -> dict:
    out = capsys.readouterr().out.strip().splitlines()
    assert out, "脚本未输出 JSON"
    return json.loads(out[-1])


class TestV111FillToTarget:
    def test_below_target_fills_exactly_need(self, fill_v111, monkeypatch, capsys):
        """低占用（10%）→ 填到 60%：dd 块数=need/bs（向上取整），回读核验 used_after>=target。"""
        fake = _patch(fill_v111, monkeypatch, _FakeAdb(used_seq=[10_000, 60_000]))

        fill_v111.main()

        payload = _result(capsys)
        assert payload["success"] is True and payload["metrics"]["mode"] == "filled"
        assert "count=49" in fake.calls[[i for i, c in enumerate(fake.calls) if c.startswith("dd")][0]]  # ceil(50000/1024)=49
        assert payload["metrics"]["actual_pct"] == 60

    def test_verification_shortfall_fails_with_evidence(self, fill_v111, monkeypatch, capsys):
        """dd 成功但回读不足 → fail（沿用 #1554 的绝对量核验）。"""
        _patch(fill_v111, monkeypatch, _FakeAdb(used_seq=[10_000, 20_000]))

        fill_v111.main()

        payload = _result(capsys)
        assert payload["success"] is False
        assert "fill insufficient" in payload["error_message"]
        assert payload["metrics"]["target_used_kb"] == 60_000

    def test_dd_failure_keeps_rc_and_stderr(self, fill_v111, monkeypatch, capsys):
        _patch(fill_v111, monkeypatch, _FakeAdb(used_seq=[10_000], dd_rc=1, dd_err="No space left on device"))

        fill_v111.main()

        msg = _result(capsys)["error_message"]
        assert "dd failed: rc=1" in msg and "No space left on device" in msg


class TestV111ReleaseWhenAboveTarget:
    def test_above_target_releases_self_created_file(self, fill_v111, monkeypatch, capsys):
        """旧目标残留：used 90% 而其中 60% 是自建文件、base=30% ⇒ 目标 40% 时应**释放**文件。"""
        fake = _patch(fill_v111, monkeypatch, _FakeAdb(used_seq=[90_000, 30_000], fill_kb=60_000),
                      {"target_percentage": 25})

        fill_v111.main()

        payload = _result(capsys)
        assert payload["success"] is True and payload["metrics"]["mode"] == "released"
        assert payload["metrics"]["released_kb"] == 60_000
        assert fake.count("rm -f") == 1 and fake.count("dd if=/dev/zero") == 0

    def test_already_met_without_self_created_file_is_noop(self, fill_v111, monkeypatch, capsys):
        """真实占用本身 ≥ 目标且无自建文件 → skipped+already_met（不 rm、不 dd）。"""
        fake = _patch(fill_v111, monkeypatch, _FakeAdb(used_seq=[70_000], fill_kb=0))

        fill_v111.main()

        payload = _result(capsys)
        assert payload["success"] is True and payload["skipped"] is True
        assert payload["metrics"]["mode"] == "already_met"
        assert fake.count("rm -f") == 0 and fake.count("dd if=/dev/zero") == 0

    def test_release_failure_reports_rc(self, fill_v111, monkeypatch, capsys):
        _patch(fill_v111, monkeypatch, _FakeAdb(used_seq=[90_000], fill_kb=60_000, rm_rc=1),
               {"target_percentage": 25})

        fill_v111.main()

        payload = _result(capsys)
        assert payload["success"] is False and "release failed: rc=1" in payload["error_message"]


class TestV111IdempotentAndShrink:
    def test_same_target_second_run_is_idempotent(self, fill_v111, monkeypatch, capsys):
        """同目标重复运行：文件恰好补满、base=target ⇒ 第二轮释放，结果稳定在目标线。"""
        fake = _patch(fill_v111, monkeypatch, _FakeAdb(used_seq=[60_000, 60_000], fill_kb=50_000),
                      {"target_percentage": 10})

        fill_v111.main()

        payload = _result(capsys)
        assert payload["metrics"]["mode"] == "released"
        assert payload["metrics"]["base_pct"] == 10
        assert fake.count("dd if=/dev/zero") == 0

    def test_lower_target_shrinks_file(self, fill_v111, monkeypatch, capsys):
        """目标调低（60%→40%）：base=30% < 40% ⇒ dd 覆盖写到 need（文件从 60% 缩到 10%）。"""
        fake = _patch(fill_v111, monkeypatch, _FakeAdb(used_seq=[90_000, 40_000], fill_kb=60_000),
                      {"target_percentage": 40})

        fill_v111.main()

        payload = _result(capsys)
        assert payload["metrics"]["mode"] == "filled"
        dd = fake.calls[[i for i, c in enumerate(fake.calls) if c.startswith("dd")][0]]
        assert "count=10" in dd  # ceil(10000/1024)=10 → 覆盖写把文件缩到 10%
        assert payload["metrics"]["actual_pct"] == 40


class TestV110Anchor:
    def test_v110_above_target_is_false_green(self, fill_v110_anchor, monkeypatch, capsys):
        """锚点：v1.1.0 在「used 90%（含自建 60GB）」时报 already_met 且**不动文件**。"""
        fake = _FakeAdb(used_seq=[90_000], fill_kb=60_000)
        monkeypatch.setattr(fill_v110_anchor, "adb_shell_quiet", fake)
        monkeypatch.setattr(fill_v110_anchor, "params", lambda: {"target_percentage": 40})

        fill_v110_anchor.main()

        payload = _result(capsys)
        assert payload["skipped"] is True and payload["metrics"]["already_met"] is True
        assert fake.count("rm -f") == 0 and fake.count("dd if=/dev/zero") == 0
        assert fake.count("du -sk") == 0  # v1.1.0 连自建文件大小都不探测
