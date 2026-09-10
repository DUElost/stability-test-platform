"""monkey_setup v2.3.7：push 解包退出码检查 + bundle marker 后置条件（#1027，R08-F07）。

v2.3.6 及以前由既有用例覆盖；这里验证增量：

- `adb_shell_progress`（tar 解包）非零退出码 → push 步骤失败，error 带退出码与
  stderr 摘要（空格不足 / 权限 / 损坏归档场景）——此前直接返回 success=True，
  准备阶段带病继续（#812 同族）；
- 解包超时（TimeoutExpired）→ 显式失败，不再外溢成半途而废的成功；
- 后置条件：marker `.stp_bundle_sha256` 内容 == manifest 期望值——复合命令的
  echo 段没执行（rc 可能为 0）同样判失败；
- rc=0 且 marker 匹配 → 成功（不回归正常路径）。
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

_SCRIPT_DIR = (
    Path(__file__).resolve().parents[2] / "agent" / "scripts" / "monkey_setup" / "v2.3.7"
)


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


# monkey_setup 模块级 `from _adb import ...`：先加载 _adb 并占住 sys.modules
_adb = _load_module("_adb_v237", _SCRIPT_DIR / "_adb.py")
sys.modules["_adb"] = _adb
mod = _load_module("monkey_setup_v237", _SCRIPT_DIR / "monkey_setup.py")


def _make_bundle(tmp_path: Path) -> tuple[Path, Path, str]:
    """造 bundle + manifest，返回 (bundle_path, manifest_path, expected_sha)。"""
    bundle = tmp_path / "bundle.tar.gz"
    bundle.write_bytes(b"fake-bundle-bytes")
    expected_sha = hashlib.sha256(bundle.read_bytes()).hexdigest()
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "name": "monkey-resources",
        "bundle_sha256": expected_sha,
        "file_count": 3,
    }), encoding="utf-8")
    return bundle, manifest, expected_sha


def _run_step_push(
    tmp_path: Path, *, tar_result=None, tar_raises=None,
    marker: str = "", precheck_marker: str = "no-marker-yet",
) -> dict:
    """驱动 step_push 的 bundle 分支：patch 掉 adb 传输与 shell，专注解包结果。

    cat 调用按序出队：第 1 次 = skip_if_match 的前置比对，第 2 次 = 解包后的
    marker 后置条件校验。
    """
    bundle, manifest_path, expected_sha = _make_bundle(tmp_path)
    cat_results = [precheck_marker, marker]

    def fake_push_or_timeout(local, remote, timeout, progress=None):
        return None  # 传输成功

    tar_calls: list = []

    def fake_tar(command, timeout, on_progress=None, progress_interval=30.0):
        tar_calls.append(command)
        if tar_raises is not None:
            raise tar_raises
        return tar_result

    shell_calls: list = []

    def fake_shell(command, timeout=30):
        shell_calls.append(command)
        if command.startswith("cat "):
            return cat_results.pop(0) if cat_results else ""
        return ""  # mkdir 等

    mod._push_or_timeout = fake_push_or_timeout
    mod.adb_shell_progress = fake_tar
    mod.adb_shell = fake_shell
    cfg = {
        "bundle": str(bundle),
        "manifest": str(manifest_path),
        "remote_dir": "/sdcard/test_resources",
    }
    return mod.step_push("SERIAL", cfg)


def test_happy_path_rc0_marker_matches(tmp_path):
    """rc=0 且 marker 内容匹配 → 成功（正常路径不回归）。"""
    _bundle, _manifest, expected_sha = _make_bundle(tmp_path)
    result = _run_step_push(
        tmp_path,
        tar_result=subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
        marker=expected_sha + "\n",
        precheck_marker="not-yet",
    )
    assert result["success"] is True
    assert result["bundle"] == "monkey-resources"
    assert result["files"] == 3


def test_tar_failure_reports_failure_not_success(tmp_path):
    """#1027 核心：解包非零退出（如空间不足）→ 步骤失败，不再报成功。"""
    result = _run_step_push(
        tmp_path,
        tar_result=subprocess.CompletedProcess(
            args=[], returncode=1, stdout="",
            stderr="tar: /sdcard/test_resources/x.db: No space left on device",
        ),
        marker="",
    )
    assert result["success"] is False
    assert "rc=1" in result["error"]
    assert "No space left on device" in result["error"]


def test_tar_stderr_empty_falls_back_to_stdout(tmp_path):
    result = _run_step_push(
        tmp_path,
        tar_result=subprocess.CompletedProcess(
            args=[], returncode=2, stdout="gzip: stdin: unexpected end of file",
            stderr="",
        ),
        marker="",
    )
    assert result["success"] is False
    assert "rc=2" in result["error"]
    assert "unexpected end of file" in result["error"]


def test_tar_timeout_reports_failure(tmp_path):
    """解包超时显式转失败——不再让 TimeoutExpired 外溢成半途而废的成功。"""
    result = _run_step_push(
        tmp_path,
        tar_raises=subprocess.TimeoutExpired(cmd=["adb"], timeout=600),
        marker="",
    )
    assert result["success"] is False
    assert "timed out" in result["error"]


def test_marker_mismatch_is_failure(tmp_path):
    """rc=0 但 marker 内容不符（echo 段没执行/被篡改）→ 失败。"""
    result = _run_step_push(
        tmp_path,
        tar_result=subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
        marker="deadbeef" * 8,
    )
    assert result["success"] is False
    assert "marker mismatch" in result["error"]


def test_v237_inherits_att_clean_step():
    """v2.3.7 继承 v2.3.6 的 att_clean 默认步骤（不改 #894 语义）。"""
    assert "att_clean" in mod.STEPS
