"""ADR-0040 P1 切片①：部署 artifact digest 双侧镜像与输入集契约。

覆盖（#1911 / ADR §6）：
- 双侧字节级等价（控制面 backend/services/deployment_digest.py ↔ Agent 侧
  backend/agent/deployment_digest.py，先例 script_catalog_version）；
- 输入集边界（主机态/部署态文件、resources/ 分层、mtbf/ 主机本地不参与身份）；
- 身份敏感性（内容 / 可执行位 / 改名任一变化即变）；
- 控制面 desired 进程缓存（ADR D2：缓存键 = 输入集状态）；
- 输入集契约对账：agent-code digest 文件集 ≡ `_build_tarball` 载荷文件集
  （含 `stp_schemas/pipeline_schema.json`、不含 `resources/`，ADR §4.2 的
  「输入集契约与部署输入集由同一测试守护」）。
"""

from __future__ import annotations

import io
import os
import tarfile
from pathlib import Path

import pytest

from backend.agent import deployment_digest as agent_digest
from backend.services import deployment_digest as svc_digest


def _write(p: Path, content: bytes | str, *, exec_bit: bool = False) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content.encode() if isinstance(content, str) else content)
    if exec_bit:
        p.chmod(0o755)


def _build_tree(base: Path, *, source_like: bool = False) -> None:
    """合成 agent 树。

    source_like=False：额外包含主机态/部署态文件与垃圾目录，验证排除面。
    source_like=True：只含部署流程真实拥有的文件（对账 _build_tarball 用）。
    """
    _write(base / "main.py", "print('main')\n")
    _write(base / "pipeline_validator.py", "SCHEMA = {}\n")
    _write(base / "tools" / "flash.sh", "#!/bin/sh\n", exec_bit=True)
    _write(base / "resources" / "flashtool.bin", b"\x01\x02BIN", exec_bit=True)
    _write(base / "resources" / "tool" / "AIMonkey.apk", b"APK")
    _write(base / "resources" / "mtbf" / "AutoTestTool.apk", b"MTB")
    _write(base / "tests" / "test_x.py", "def test_x():\n    pass\n")
    _write(base / "__pycache__" / "m.pyc", b"\x00pyc")
    _write(base / "test_top.py", "def test_top():\n    pass\n")
    if not source_like:
        _write(base / "venv" / "lib" / "x.py", "venv\n")
        _write(base / "logs" / "a.log", "log\n")
        _write(base / "VERSION", "v9.9.9-test\n")
        _write(base / "ARTIFACT_DIGEST", "sha256:stale\n")
        _write(base / ".env", "SECRET=1\n")
        _write(base / ".deps_installed_sha", "abc\n")
        _write(base / ".env.example", "TEMPLATE=1\n")
        _write(base / "install_agent.sh", "#!/bin/sh\n")
        _write(base / "DEPLOY.md", "deploy\n")
        _write(base / "hosts.txt", "hostlist\n")


class TestMirrorEquivalence:
    """双侧字节级等价（ADR D1；先例 script_catalog_version 对照测试）。"""

    @pytest.fixture(scope="class")
    def tree(self, tmp_path_factory) -> Path:
        base = tmp_path_factory.mktemp("agent_tree")
        _build_tree(base)
        return base

    def test_agent_code_kind(self, tree: Path):
        assert (
            svc_digest.compute_artifact_digest(tree, svc_digest.AGENT_CODE)
            == agent_digest.compute_artifact_digest(tree, agent_digest.AGENT_CODE)
        )

    def test_host_resources_kind(self, tree: Path):
        assert (
            svc_digest.compute_artifact_digest(tree, svc_digest.HOST_RESOURCES)
            == agent_digest.compute_artifact_digest(tree, agent_digest.HOST_RESOURCES)
        )

    def test_with_extra_files(self, tree: Path, tmp_path: Path):
        schema = tmp_path / "pipeline_schema.json"
        schema.write_text('{"version": 1}')
        extra = {"stp_schemas/pipeline_schema.json": schema}
        assert (
            svc_digest.compute_artifact_digest(tree, svc_digest.AGENT_CODE, extra_files=extra)
            == agent_digest.compute_artifact_digest(tree, agent_digest.AGENT_CODE, extra_files=extra)
        )


class TestInputSetBoundary:
    """输入集边界（ADR D1：主机态/部署态不参与身份；分层归属）。"""

    @pytest.fixture(scope="class")
    def tree(self, tmp_path_factory) -> Path:
        base = tmp_path_factory.mktemp("boundary_tree")
        _build_tree(base)
        return base

    def test_agent_code_input_set(self, tree: Path):
        assert svc_digest.digest_input_files(tree, svc_digest.AGENT_CODE) == [
            "main.py",
            "pipeline_validator.py",
            "tools/flash.sh",
        ]

    def test_host_resources_input_set(self, tree: Path):
        assert svc_digest.digest_input_files(tree, svc_digest.HOST_RESOURCES) == [
            "resources/flashtool.bin",
            "resources/tool/AIMonkey.apk",
        ]

    def test_digest_equals_reduced_tree(self, tree: Path, tmp_path: Path):
        """身份等价于「剔除主机态/垃圾/resources 后」的纯代码树。"""
        reduced = tmp_path / "reduced"
        (reduced / "tools").mkdir(parents=True)
        (reduced / "main.py").write_text("print('main')\n")
        (reduced / "pipeline_validator.py").write_text("SCHEMA = {}\n")
        flash = reduced / "tools" / "flash.sh"
        flash.write_text("#!/bin/sh\n")
        flash.chmod(0o755)
        assert (
            svc_digest.compute_artifact_digest(tree, svc_digest.AGENT_CODE)
            == svc_digest.compute_artifact_digest(reduced, svc_digest.AGENT_CODE)
        )

    def test_host_resources_missing_dir_is_empty_digest(self, tmp_path: Path):
        d1 = svc_digest.compute_artifact_digest(tmp_path, svc_digest.HOST_RESOURCES)
        assert d1.startswith("sha256:")
        assert d1 == agent_digest.compute_artifact_digest(tmp_path, agent_digest.HOST_RESOURCES)

    def test_unknown_kind_rejected(self, tree: Path):
        with pytest.raises(ValueError):
            svc_digest.compute_artifact_digest(tree, "whole-tree")


class TestIdentitySensitivity:
    def _seed(self, base: Path) -> None:
        _write(base / "main.py", "v1\n")

    def test_content_change(self, tmp_path: Path):
        self._seed(tmp_path)
        d1 = svc_digest.compute_artifact_digest(tmp_path)
        (tmp_path / "main.py").write_text("v2\n")
        assert svc_digest.compute_artifact_digest(tmp_path) != d1

    def test_exec_bit_change(self, tmp_path: Path):
        self._seed(tmp_path)
        d1 = svc_digest.compute_artifact_digest(tmp_path)
        (tmp_path / "main.py").chmod(0o755)
        assert svc_digest.compute_artifact_digest(tmp_path) != d1

    def test_rename_change(self, tmp_path: Path):
        self._seed(tmp_path)
        d1 = svc_digest.compute_artifact_digest(tmp_path)
        (tmp_path / "main.py").rename(tmp_path / "renamed.py")
        assert svc_digest.compute_artifact_digest(tmp_path) != d1

    def test_format_stable(self, tmp_path: Path):
        self._seed(tmp_path)
        d1 = svc_digest.compute_artifact_digest(tmp_path)
        assert d1.startswith("sha256:")
        assert d1 == svc_digest.compute_artifact_digest(tmp_path)


class TestDesiredDigestCache:
    """ADR D2：desired 现算 + 进程缓存（缓存键 = 输入集状态）。"""

    def test_cache_hit_skips_hashing(self, tmp_path: Path, monkeypatch):
        _write(tmp_path / "main.py", "x\n")
        svc_digest.reset_cache()
        calls: list[Path] = []
        orig = svc_digest._file_sha256

        def counting(p: Path) -> str:
            calls.append(p)
            return orig(p)

        monkeypatch.setattr(svc_digest, "_file_sha256", counting)
        svc_digest.compute_desired_digest(tmp_path)
        assert len(calls) == 1
        assert svc_digest.compute_desired_digest(tmp_path) == svc_digest.compute_desired_digest(tmp_path)
        assert len(calls) == 1  # 指纹未变 → 不重算内容哈希

    def test_content_change_invalidates(self, tmp_path: Path):
        _write(tmp_path / "main.py", "x\n")
        svc_digest.reset_cache()
        d1 = svc_digest.compute_desired_digest(tmp_path)
        (tmp_path / "main.py").write_text("y\n")
        # 显式推进 mtime：缓存键 = 输入集状态（mtime_ns/size/mode），
        # 测试机制本身不依赖文件系统时间戳粒度
        st = (tmp_path / "main.py").stat()
        os.utime(tmp_path / "main.py", ns=(st.st_mtime_ns + 1_000_000,) * 2)
        assert svc_digest.compute_desired_digest(tmp_path) != d1

    def test_extra_files_change_invalidates(self, tmp_path: Path):
        _write(tmp_path / "main.py", "x\n")
        schema = tmp_path / "schema.json"
        schema.write_text("{}")
        svc_digest.reset_cache()
        d1 = svc_digest.compute_desired_digest(
            tmp_path, extra_files={"stp_schemas/pipeline_schema.json": schema}
        )
        schema.write_text('{"version": 2}')
        d2 = svc_digest.compute_desired_digest(
            tmp_path, extra_files={"stp_schemas/pipeline_schema.json": schema}
        )
        assert d2 != d1


class TestTarballContract:
    """输入集契约对账（ADR §4.2）：agent-code digest 文件集 ≡ 打包载荷文件集。

    打包载荷 = `_build_tarball` 成员（agent 树 + stp_schemas/pipeline_schema.json）；
    digest 侧按分层排除 `resources/**`（其归属 host-resources，P2 落独立通道）。
    """

    def test_agent_code_input_set_matches_tarball(self, tmp_path, monkeypatch):
        import backend.services.host_updater as hu

        _build_tree(tmp_path, source_like=True)
        schema = tmp_path / "_pipeline_schema_src.json"
        schema.write_text('{"version": 1}')
        monkeypatch.setattr(hu, "_AGENT_SOURCE_DIR", tmp_path)
        monkeypatch.setattr(hu, "_PIPELINE_SCHEMA_FILE", schema)

        buf = hu._build_tarball()
        with tarfile.open(fileobj=io.BytesIO(buf), mode="r:gz") as tar:
            tar_files = {
                m.name.replace("\\", "/")
                for m in tar.getmembers()
                if m.isfile()
                and not m.name.replace("\\", "/").startswith("resources/")
            }
        # 分层前提：agent-code 身份覆盖「打包载荷 − resources/**」（resources
        # 归 host-resources 身份，P2 落独立通道）；schema 以载荷内 arcname 参与
        expected_digest_files = set(svc_digest.digest_input_files(
            tmp_path,
            svc_digest.AGENT_CODE,
            extra_files={"stp_schemas/pipeline_schema.json": schema},
        ))
        assert tar_files == expected_digest_files
