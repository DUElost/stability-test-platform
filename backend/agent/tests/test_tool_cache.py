"""backend/agent/tool_cache.py 单测（ADR-0033 Phase B 第一切片，#3075）。

覆盖：拉取+整包 sha 核验、失败一律回退（缺包/坏 sha/退役/越界成员/根未配置）、
``.stp-verified`` 标记幂等、逃生阀默认关（无 ref 即 no-op）。
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import sys
import tarfile
from pathlib import Path

import pytest

from backend.agent.tool_cache import (
    ensure_package,
    parse_package_ref,
    resolve_packaged_scan_tool,
)


def _make_tar(path: Path, files: dict[str, bytes], symlinks: dict[str, str] | None = None,
              extra_members: list[tarfile.TarInfo] | None = None) -> str:
    """构造测试包并返回其 sha256（顺序确定性不重要，工具消费端只认 sha）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=buf, mtime=0) as gz:
        with tarfile.open(fileobj=gz, mode="w") as tar:
            for name, data in sorted(files.items()):
                info = tarfile.TarInfo(name)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
            for name, target in sorted((symlinks or {}).items()):
                info = tarfile.TarInfo(name)
                info.type = tarfile.SYMTYPE
                info.linkname = target
                tar.addfile(info)
            for mi in extra_members or []:
                if mi.isfile():
                    tar.addfile(mi, io.BytesIO(b"x"))
                else:
                    tar.addfile(mi)
    payload = buf.getvalue()
    path.write_bytes(payload)
    return hashlib.sha256(payload).hexdigest()


def _site(packages_root: Path, name: str, version: str, sha: str, *, retired: bool = False,
          python: str = "venv/bin/python", script: str = "start_log_scan.py") -> None:
    (packages_root / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "tools": {
                    name: {"versions": [{
                        "version": version, "package_sha256": sha,
                        "artifact": f"packages/{name}/{version}.tar.gz",
                        "python": python, "script": script, "retired": retired,
                    }]}
                },
            }
        ),
        encoding="utf-8",
    )


class TestParseRef:
    def test_shapes(self):
        assert parse_package_ref("Start-Log-Scan/2026.09.22") == ("Start-Log-Scan", "2026.09.22")
        assert parse_package_ref("  ") is None
        assert parse_package_ref("noSlash") is None
        assert parse_package_ref("a/b/c") is None
        assert parse_package_ref("../x/1") is None


class TestEnsurePackage:
    def test_happy_and_marker_idempotent(self, tmp_path):
        pkg_root = tmp_path / "packages"
        cache = tmp_path / "cache"
        sha = _make_tar(pkg_root / "t" / "v1.tar.gz", {"run.py": b"print(1)", "venv/bin/python": b"stub"})
        got = ensure_package("t", "v1", sha, pkg_root, cache)
        assert got is not None and (got / "run.py").is_file()
        assert (got / ".stp-verified").read_text().strip() == sha
        # 标记命中：即使包被换坏也不再重拷（核验过的缓存只信 marker）
        (pkg_root / "t" / "v1.tar.gz").write_bytes(b"corrupted-after-verify")
        again = ensure_package("t", "v1", sha, pkg_root, cache)
        assert again == got

    def test_sha_mismatch_rejected(self, tmp_path):
        pkg_root = tmp_path / "packages"
        cache = tmp_path / "cache"
        _make_tar(pkg_root / "t" / "v1.tar.gz", {"run.py": b"ok"})
        assert ensure_package("t", "v1", "0" * 64, pkg_root, cache) is None
        assert not (cache / "t" / "v1").exists()

    def test_traversal_member_rejected(self, tmp_path):
        pkg_root = tmp_path / "packages"
        cache = tmp_path / "cache"
        evil = tarfile.TarInfo("../evil")
        evil.size = 1
        sha = _make_tar(pkg_root / "t" / "v1.tar.gz", {"run.py": b"ok"}, extra_members=[evil])
        assert ensure_package("t", "v1", sha, pkg_root, cache) is None
        assert not (tmp_path / "evil").exists()

    def test_char_device_member_rejected(self, tmp_path):
        pkg_root = tmp_path / "packages"
        cache = tmp_path / "cache"
        dev = tarfile.TarInfo("weird")
        dev.type = tarfile.CHRTYPE
        sha = _make_tar(pkg_root / "t" / "v1.tar.gz", {"run.py": b"ok"}, extra_members=[dev])
        assert ensure_package("t", "v1", sha, pkg_root, cache) is None

    def test_missing_tarball(self, tmp_path):
        assert ensure_package("t", "v9", "a" * 64, tmp_path / "none", tmp_path / "cache") is None


class TestResolveEntry:
    def _env(self, tmp_path, **over):
        env = {
            "STP_DEDUP_SCAN_PACKAGE_REF": "Start-Log-Scan/2026.09.22",
            "STP_PACKAGES_ROOT": str(tmp_path / "packages"),
            "STP_TOOLS_CACHE_ROOT": str(tmp_path / "cache"),
        }
        env.update(over)
        return env

    def test_escape_valve_default_off(self):
        assert resolve_packaged_scan_tool({}) is None

    def test_happy_chain(self, tmp_path):
        pkg = tmp_path / "packages"
        sha = _make_tar(pkg / "Start-Log-Scan" / "2026.09.22.tar.gz",
                        {"start_log_scan.py": b"print('scan')", "venv/bin/python": b"stub"},
                        symlinks={"venv/bin/python3": "/usr/bin/python3"})
        _site(pkg, "Start-Log-Scan", "2026.09.22", sha)
        got = resolve_packaged_scan_tool(self._env(tmp_path))
        assert got is not None
        assert got.script.endswith("Start-Log-Scan/2026.09.22/start_log_scan.py")
        assert got.python.endswith("venv/bin/python")
        assert Path(got.script).is_file()

    def test_retired_entry_falls_back(self, tmp_path):
        pkg = tmp_path / "packages"
        sha = _make_tar(pkg / "Start-Log-Scan" / "2026.09.22.tar.gz", {"start_log_scan.py": b"x", "venv/bin/python": b"x"})
        _site(pkg, "Start-Log-Scan", "2026.09.22", sha, retired=True)
        assert resolve_packaged_scan_tool(self._env(tmp_path)) is None

    def test_sha_mismatch_falls_back(self, tmp_path):
        pkg = tmp_path / "packages"
        _make_tar(pkg / "Start-Log-Scan" / "2026.09.22.tar.gz", {"start_log_scan.py": b"x", "venv/bin/python": b"x"})
        _site(pkg, "Start-Log-Scan", "2026.09.22", "f" * 64)
        assert resolve_packaged_scan_tool(self._env(tmp_path)) is None

    def test_missing_site_manifest_falls_back(self, tmp_path):
        assert resolve_packaged_scan_tool(self._env(tmp_path)) is None  # packages/ 不存在

    def test_roots_from_aee_nfs_and_install_dir(self, tmp_path):
        pkg = tmp_path / "aee-nfs" / "packages"
        sha = _make_tar(pkg / "Start-Log-Scan" / "2026.09.22.tar.gz", {"start_log_scan.py": b"x", "venv/bin/python": b"x"})
        _site(pkg, "Start-Log-Scan", "2026.09.22", sha)
        env = {
            "STP_DEDUP_SCAN_PACKAGE_REF": "Start-Log-Scan/2026.09.22",
            "STP_AEE_NFS_ROOT": str(tmp_path / "aee-nfs"),
            "AGENT_INSTALL_DIR": str(tmp_path / "agent"),
        }
        got = resolve_packaged_scan_tool(env)
        assert got is not None and str(tmp_path / "agent" / "tools_cache") in got.script

    def test_entry_paths_missing_in_tar(self, tmp_path):
        pkg = tmp_path / "packages"
        sha = _make_tar(pkg / "Start-Log-Scan" / "2026.09.22.tar.gz", {"other.py": b"x"})
        _site(pkg, "Start-Log-Scan", "2026.09.22", sha)
        assert resolve_packaged_scan_tool(self._env(tmp_path)) is None


class TestResolvePackagedToolGeneric:
    """ADR-0051 Phase 4a：泛化 resolver——每个族一个引用键；python=null → Agent 自身解释器。"""

    def test_custom_ref_key_with_null_python(self, tmp_path, monkeypatch):
        import sys
        from backend.agent.tool_cache import resolve_packaged_tool

        pkg = tmp_path / "packages"
        sha = _make_tar(pkg / "Scan-Result-GT" / "2026.09.23.tar.gz", {"scan_result.py": b"print('r')"})
        _site(pkg, "Scan-Result-GT", "2026.09.23", sha, python=None, script="scan_result.py")
        got = resolve_packaged_tool("STP_UNISOC_SCAN_RESULT_PACKAGE_REF", {
            "STP_UNISOC_SCAN_RESULT_PACKAGE_REF": "Scan-Result-GT/2026.09.23",
            "STP_PACKAGES_ROOT": str(pkg),
            "STP_TOOLS_CACHE_ROOT": str(tmp_path / "cache"),
        })
        assert got is not None and got.python == sys.executable
        assert Path(got.script).name == "scan_result.py"

    def test_other_family_key_not_touched(self, tmp_path):
        """族隔离：只设 SCAN_RESULT 键不影响 DEDUP 入口（各自独立的 no-op 逃生阀）。"""
        from backend.agent.tool_cache import resolve_packaged_scan_tool

        pkg = tmp_path / "packages"
        sha = _make_tar(pkg / "Scan-Result-GT" / "2026.09.23.tar.gz", {"scan_result.py": b"x"})
        _site(pkg, "Scan-Result-GT", "2026.09.23", sha, python=None, script="scan_result.py")
        assert resolve_packaged_scan_tool({
            "STP_UNISOC_SCAN_RESULT_PACKAGE_REF": "Scan-Result-GT/2026.09.23",
            "STP_PACKAGES_ROOT": str(pkg), "STP_TOOLS_CACHE_ROOT": str(tmp_path / "cache"),
        }) is None


# ── #3169：摘要只证内容身份，不证解包边界 ────────────────────────────────────────
def _symlink(name: str, target: str) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.type = tarfile.SYMTYPE
    info.linkname = target
    return info


def _regular(name: str) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.size = 1  # _make_tar 为普通文件写入 b"x"
    return info


#: 两种解包姿势都必须守住边界：有 PEP 706 filter 的解释器（3.12+ 与 3.10.12/3.11.4 起的回移），
#: 以及只剩整包预检的旧解释器（Agent 只要求 3.10+，主机上的补丁级别不可假设）。
_EXTRACT_MODES = ["pep706-filter", "precheck-only"]


def _set_mode(monkeypatch, mode: str) -> None:
    from backend.agent import tool_cache

    if mode == "precheck-only":
        monkeypatch.setattr(tool_cache, "_extract_kwargs", lambda: {}, raising=False)


class TestArchiveBoundary3169:
    @pytest.mark.parametrize("mode", _EXTRACT_MODES)
    def test_symlinked_dir_then_member_cannot_escape(self, tmp_path, monkeypatch, mode):
        """#3169 原形态：`linked-dir → 根外目录` + `linked-dir/proof.txt`，逐成员字面检查全过。"""
        _set_mode(monkeypatch, mode)
        outside = tmp_path / "outside"
        outside.mkdir()
        sha = _make_tar(tmp_path / "packages" / "t" / "v1.tar.gz", {"run.py": b"ok"},
                        extra_members=[_symlink("linked-dir", str(outside)), _regular("linked-dir/proof.txt")])
        assert ensure_package("t", "v1", sha, tmp_path / "packages", tmp_path / "cache") is None
        assert not (outside / "proof.txt").exists(), "摘要匹配的包把文件写到了解包根之外"

    @pytest.mark.parametrize("mode", _EXTRACT_MODES)
    def test_member_under_inside_symlink_rejected(self, tmp_path, monkeypatch, mode):
        """成员一律不得经软链落盘——即使软链指向包内（判据不依赖解析，避免 realpath 竞态与链式绕行）。"""
        _set_mode(monkeypatch, mode)
        sha = _make_tar(tmp_path / "packages" / "t" / "v1.tar.gz", {"run.py": b"ok", "sub/keep.txt": b"k"},
                        extra_members=[_symlink("d", "sub"), _regular("d/x.txt")])
        assert ensure_package("t", "v1", sha, tmp_path / "packages", tmp_path / "cache") is None

    @pytest.mark.parametrize("mode", _EXTRACT_MODES)
    def test_regular_member_over_symlink_cannot_overwrite_target(self, tmp_path, monkeypatch, mode):
        """同名成员：先软链 `f → 根外文件`，再普通文件 `f`——不带 filter 的解包会顺着软链改写目标。"""
        _set_mode(monkeypatch, mode)
        victim = tmp_path / "victim.txt"
        victim.write_text("orig", encoding="utf-8")
        sha = _make_tar(tmp_path / "packages" / "t" / "v1.tar.gz", {"run.py": b"ok"},
                        extra_members=[_symlink("f", str(victim)), _regular("f")])
        assert ensure_package("t", "v1", sha, tmp_path / "packages", tmp_path / "cache") is None
        assert victim.read_text(encoding="utf-8") == "orig"

    def test_duplicate_member_names_rejected(self, tmp_path):
        """每条路径至多出现一次：确定性打包从不产出重名，出现即是误制或篡改。"""
        sha = _make_tar(tmp_path / "packages" / "t" / "v1.tar.gz", {"run.py": b"ok"},
                        extra_members=[_regular("run.py")])
        assert ensure_package("t", "v1", sha, tmp_path / "packages", tmp_path / "cache") is None

    @pytest.mark.parametrize("mode", _EXTRACT_MODES)
    def test_real_venv_shape_still_accepted(self, tmp_path, monkeypatch, mode):
        """已发布包里唯一的软链形态（Start-Log-Scan/2026.09.22 的 venv，2026-09-25 全站 214 包扫描）必须照常解出。"""
        _set_mode(monkeypatch, mode)
        sha = _make_tar(
            tmp_path / "packages" / "Start-Log-Scan" / "2026.09.22.tar.gz",
            {"start_log_scan.py": b"print('scan')"},
            symlinks={"venv/bin/python": "python3", "venv/bin/python3": "/usr/bin/python3",
                      "venv/bin/python3.13": "python3"},
        )
        got = ensure_package("Start-Log-Scan", "2026.09.22", sha, tmp_path / "packages", tmp_path / "cache")
        assert got is not None
        assert (got / "venv" / "bin" / "python3").readlink() == Path("/usr/bin/python3")
        assert (got / "venv" / "bin" / "python").readlink() == Path("python3")


class TestEntryFieldContainment3169:
    """#3169 第二洞：manifest 的 `python`/`script` 在**消费时刻**拼接，须与登记侧同判据。"""

    def _prepare(self, tmp_path, extra_files=None, **fields):
        pkg = tmp_path / "packages"
        sha = _make_tar(pkg / "Start-Log-Scan" / "2026.09.22.tar.gz",
                        {"start_log_scan.py": b"print('scan')", "venv/bin/python": b"stub", **(extra_files or {})})
        _site(pkg, "Start-Log-Scan", "2026.09.22", sha, **fields)
        return {
            "STP_DEDUP_SCAN_PACKAGE_REF": "Start-Log-Scan/2026.09.22",
            "STP_PACKAGES_ROOT": str(pkg),
            "STP_TOOLS_CACHE_ROOT": str(tmp_path / "cache"),
        }

    def test_absolute_python_field_rejected(self, tmp_path):
        """`pkg_dir / "/usr/bin/python3"` 会丢掉左段——包身份核验通过后去执行任意主机文件。"""
        assert resolve_packaged_scan_tool(self._prepare(tmp_path, python=sys.executable)) is None

    def test_dot_python_field_rejected(self, tmp_path):
        """`"."` 让解释器指向包目录本身（`exists()` 为真）。"""
        assert resolve_packaged_scan_tool(self._prepare(tmp_path, python=".")) is None

    def test_parent_escape_script_field_rejected(self, tmp_path):
        env = self._prepare(tmp_path, script="../escape.py")
        sibling = tmp_path / "cache" / "Start-Log-Scan"
        sibling.mkdir(parents=True)
        (sibling / "escape.py").write_text("print('outside')", encoding="utf-8")  # 让「越界即存在」成立
        assert resolve_packaged_scan_tool(env) is None

    def test_backslash_script_field_rejected(self, tmp_path):
        """与登记侧 `validate_relative_member` 同判据（#3197 的对拍集）。"""
        # 包里真有这个名字的文件（POSIX 上反斜杠是合法文件名字符）——否则「不存在」会让用例碰巧通过
        env = self._prepare(tmp_path, extra_files={"sub\\start_log_scan.py": b"x"}, script="sub\\start_log_scan.py")
        assert resolve_packaged_scan_tool(env) is None
