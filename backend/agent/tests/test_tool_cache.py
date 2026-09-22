"""backend/agent/tool_cache.py 单测（ADR-0033 Phase B 第一切片，#3075）。

覆盖：拉取+整包 sha 核验、失败一律回退（缺包/坏 sha/退役/越界成员/根未配置）、
``.stp-verified`` 标记幂等、逃生阀默认关（无 ref 即 no-op）。
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import tarfile
from pathlib import Path

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
