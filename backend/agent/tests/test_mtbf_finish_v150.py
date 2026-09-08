# -*- coding: utf-8 -*-
"""mtbf_finish v1.5.0：中心结果路径的跨设备唯一性与原子发布（#1030，R08-F12）。

v1.4.0 及以前由既有用例覆盖；这里验证增量：

- `_result_stem`：run_dir 之外带上 job_id / device_serial，缺失维度省略，特殊
  字符归一化；同一 Job 重跑得到同一名字（幂等覆盖）；
- 双设备同名 run_dir 落在两个文件、互不覆盖；
- `_write_json_atomic`：发布成功不留临时文件；发布失败也不留隐藏临时文件。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _load(name: str, rel_path: str):
    path = _SCRIPTS / rel_path
    sys.path.insert(0, str(path.parent))
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec and spec.loader, f"cannot locate {path}"
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path.remove(str(path.parent))


@pytest.fixture(scope="module")
def finish():
    return _load("mtbf_finish_v150", "mtbf_finish/v1.5.0/mtbf_finish.py")


class TestResultStem:
    def test_includes_job_and_serial(self, finish, monkeypatch):
        monkeypatch.setenv("STP_JOB_ID", "12345")
        monkeypatch.setenv("STP_DEVICE_SERIAL", "ABCDEF")
        assert finish._result_stem("20260908_101500") == (
            "20260908_101500__job12345__ABCDEF"
        )

    def test_missing_dimensions_omitted(self, finish, monkeypatch):
        monkeypatch.delenv("STP_JOB_ID", raising=False)
        monkeypatch.delenv("STP_DEVICE_SERIAL", raising=False)
        assert finish._result_stem("20260908_101500") == "20260908_101500"

    def test_unsafe_chars_normalized(self, finish, monkeypatch):
        monkeypatch.setenv("STP_JOB_ID", "77")
        monkeypatch.setenv("STP_DEVICE_SERIAL", "a:b/c d")
        stem = finish._result_stem("20260908_101500")
        assert stem == "20260908_101500__job77__a_b_c_d"
        assert "/" not in stem

    def test_same_job_rerun_is_idempotent(self, finish, monkeypatch):
        monkeypatch.setenv("STP_JOB_ID", "12345")
        monkeypatch.setenv("STP_DEVICE_SERIAL", "ABCDEF")
        first = finish._result_stem("20260908_101500")
        assert finish._result_stem("20260908_101500") == first


class TestNoCrossDeviceOverwrite:
    def test_two_devices_same_run_dir_separate_files(self, finish, monkeypatch, tmp_path):
        payload_a = {"run_dir": "20260908_101500", "metrics": {"passed": 1}}
        payload_b = {"run_dir": "20260908_101500", "metrics": {"passed": 2}}

        monkeypatch.setenv("STP_JOB_ID", "1001")
        monkeypatch.setenv("STP_DEVICE_SERIAL", "DEVICE-A")
        file_a = tmp_path / f"{finish._result_stem('20260908_101500')}.json"
        finish._write_json_atomic(file_a, payload_a)

        monkeypatch.setenv("STP_JOB_ID", "1002")
        monkeypatch.setenv("STP_DEVICE_SERIAL", "DEVICE-B")
        file_b = tmp_path / f"{finish._result_stem('20260908_101500')}.json"
        finish._write_json_atomic(file_b, payload_b)

        assert file_a != file_b
        assert json.loads(file_a.read_text(encoding="utf-8"))["metrics"]["passed"] == 1
        assert json.loads(file_b.read_text(encoding="utf-8"))["metrics"]["passed"] == 2


class TestAtomicPublish:
    def test_success_leaves_no_temp_file(self, finish, tmp_path):
        target = tmp_path / "20260908_101500__job1__SER.json"
        finish._write_json_atomic(target, {"ok": True})
        assert json.loads(target.read_text(encoding="utf-8")) == {"ok": True}
        assert [p.name for p in tmp_path.iterdir()] == [target.name]

    def test_failure_removes_temp_file(self, finish, tmp_path, monkeypatch):
        target = tmp_path / "20260908_101500__job1__SER.json"

        def boom(_src, _dst):
            raise OSError("replace failed")

        monkeypatch.setattr(finish.os, "replace", boom)
        with pytest.raises(OSError):
            finish._write_json_atomic(target, {"ok": True})
        assert list(tmp_path.iterdir()) == []
