"""ADR-0033 Phase B 第一切片（#3075）：打包器 + tool_manifest 门禁守卫。

覆盖：打包确定性（复跑同 sha / 内容变 sha 变 / 排除面生效）、登记的幂等与
append-only 冲突拒绝、仓内唯一事实源 lint 恒绿、门禁与 CI/run_gates 接线。
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, relpath: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relpath)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


packer = _load("package_tool_asset_under_test", "tools/dev/package_tool_asset.py")
gate = _load("check_tool_manifest_under_test", "tools/dev/check_tool_manifest.py")


def _make_src(root: Path) -> Path:
    src = root / "tool-src"
    (src / "modules").mkdir(parents=True)
    (src / "run.py").write_text("print('tool')\n", encoding="utf-8")
    (src / "modules" / "dep.py").write_text("X = 2\n", encoding="utf-8")
    # 运行态噪声：必须被排除面挡住
    (src / "logs").mkdir()
    (src / "logs" / "out.txt").write_text("noise", encoding="utf-8")
    (src / "__pycache__").mkdir()
    (src / "__pycache__" / "run.cpython-313.pyc").write_text("c", encoding="utf-8")
    return src


class TestPacker:
    def test_deterministic_across_mtime_and_dirs(self, tmp_path):
        a = _make_src(tmp_path / "one")
        b = _make_src(tmp_path / "two")
        os.utime(a / "run.py", (10, 10))
        os.utime(b / "run.py", (999999, 999999))  # mtime 不同 → sha 必相同
        ra = packer.build_deterministic_tar_gz(a, tmp_path / "oa.tar.gz")
        rb = packer.build_deterministic_tar_gz(b, tmp_path / "ob.tar.gz")
        assert ra["package_sha256"] == rb["package_sha256"]
        assert (tmp_path / "oa.tar.gz").read_bytes() == (tmp_path / "ob.tar.gz").read_bytes()

    def test_content_change_changes_sha(self, tmp_path):
        src = _make_src(tmp_path)
        r1 = packer.build_deterministic_tar_gz(src, tmp_path / "p1.tar.gz")
        (src / "run.py").write_text("print('patched')\n", encoding="utf-8")
        r2 = packer.build_deterministic_tar_gz(src, tmp_path / "p2.tar.gz")
        assert r1["package_sha256"] != r2["package_sha256"]

    def test_runtime_noise_excluded(self, tmp_path):
        src = _make_src(tmp_path)
        files = [p.as_posix() for p in packer.collect_package_files(src)]
        assert "run.py" in files and "modules/dep.py" in files
        assert not any(p.startswith(("logs/", "__pycache__/")) for p in files)

    def test_register_entry_idempotent_and_conflict(self):
        doc = {"schema_version": 1, "tools": {}}
        doc, added = packer.register_entry(doc, "t", "v1", "a" * 64, packer.artifact_path_for("t", "v1"), "py", "s.py")
        assert added and len(doc["tools"]["t"]["versions"]) == 1
        doc2, added2 = packer.register_entry(doc, "t", "v1", "a" * 64, packer.artifact_path_for("t", "v1"), "py", "s.py")
        assert not added2 and len(doc2["tools"]["t"]["versions"]) == 1  # 幂等
        with pytest.raises(SystemExit):
            packer.register_entry(doc, "t", "v1", "b" * 64, packer.artifact_path_for("t", "v1"), "py", "s.py")  # 异 sha 拒绝

    def test_relative_member_validation(self):
        assert packer.validate_relative_member("venv/bin/python", field="python") is None
        for bad in ("/abs/p", "", "a/../b", "a//b"):
            assert packer.validate_relative_member(bad, field="python")


class TestToolManifestGate:
    def test_self_test_red_green(self):
        proc = subprocess.run(
            [sys.executable, str(ROOT / "tools/dev/check_tool_manifest.py"), "--self-test"],
            capture_output=True, text=True,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr

    def test_repo_manifest_is_the_authority_and_lints_green(self):
        path = ROOT / "tool_manifest.json"
        assert path.is_file(), "唯一事实源不得消失（fail-closed）"
        doc = json.loads(path.read_text(encoding="utf-8"))
        assert gate.lint_manifest(doc) == []
        # 已登记样本字段自洽（C4 布局 / C1 字段名 / C5 恰好六个分发字段）
        entry = doc["tools"]["Start-Log-Scan"]["versions"][0]
        assert set(entry) == gate.ENTRY_FIELDS
        assert entry["artifact"] == packer.artifact_path_for("Start-Log-Scan", entry["version"])

    def test_gate_wiring(self):
        run_gates = _load("run_gates_under_test_pb", "scripts/run_gates.py")
        assert "tool-manifest" in run_gates.GATES
        cmd, cwd, _ = run_gates.GATES["tool-manifest"]
        assert "check_tool_manifest.py" in cmd and cwd == run_gates.ROOT
        for profile in ("check:quick", "check:pr"):
            assert "tool-manifest" in run_gates.PROFILES[profile]
        ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        assert "tools/dev/check_tool_manifest.py --self-test" in ci
        assert "tools/dev/check_tool_manifest.py \\\n            --base" in ci

    def test_append_only_pure(self):
        base = {"schema_version": 1, "tools": {"t": {"versions": [
            {"version": "v1", "package_sha256": "a" * 64, "artifact": "packages/t/v1.tar.gz",
             "python": "p", "script": "s", "retired": False},
        ]}}}
        import copy
        assert gate.append_only_diff(base, copy.deepcopy(base)) == []
        empty = {"schema_version": 1, "tools": {}}
        assert gate.append_only_diff(base, empty)  # 删除红
        retired = copy.deepcopy(base)
        retired["tools"]["t"]["versions"][0]["retired"] = True
        assert gate.append_only_diff(base, retired) == []  # 退役绿
