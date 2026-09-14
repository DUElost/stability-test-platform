"""ADR-0040 D1/D3（#1907）部署 artifact digest：双侧镜像等价 + 部署契约 + no-op gate。

- parity：控制面（host_updater 共享枚举 + artifact_digest）与 Agent 侧
  镜像实现（backend/agent/artifact_digest.py）对同一 fixture 树产出
  **字节级相同**的 digest；
- 契约：digest 输入集 == tarball 载荷集（同一 ``_iter_payload_files``），
  从 tarball 成员反算的 digest 必须等于树侧 digest；
- 边界：exec 位、元数据排除（VERSION/ARTIFACT_DIGEST/.env）、
  ``resources/mtbf/`` 主机本地保护、``__pycache__``/``tests``/``test_*.py``、
  空集、增删文件；
- no-op gate：``evaluate_convergence`` 的 converged / drift / 空列 / force 四态。
"""

from __future__ import annotations

import hashlib
import io
import os
import tarfile

import pytest

import backend.services.artifact_digest as ad
import backend.services.host_updater as hu
from backend.agent import artifact_digest as agent_ad


def _write(path, content: bytes, exec_bit: bool = False) -> None:
    path = str(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(content)
    if exec_bit:
        os.chmod(path, 0o755)


@pytest.fixture
def agent_tree(tmp_path):
    """Fixture agent 树：覆盖 digest 输入集的全部排除/保留边界。"""
    root = tmp_path / "agent"
    root.mkdir()
    _write(root / "main.py", b"print('main')\n")
    _write(root / "job_runner.py", b"print('runner')\n", exec_bit=True)
    _write(root / "same_content.py", b"same\n")
    # 排除面
    _write(root / "__pycache__" / "main.cpython-313.pyc", b"junk")
    _write(root / "tests" / "test_x.py", b"junk")
    _write(root / "test_top.py", b"junk")
    _write(root / "helper.pyc", b"junk")
    _write(root / ".env.example", b"junk")
    _write(root / "install_agent.sh", b"junk")
    _write(root / "agentctl.sh", b"junk")
    _write(root / "DEPLOY.md", b"junk")
    _write(root / "stability-test-agent.service", b"junk")
    _write(root / "hosts.txt", b"junk")
    # 元数据 / 主机本地
    _write(root / "VERSION", b"deadbeef\n")
    _write(root / "ARTIFACT_DIGEST", b"sha256:" + b"0" * 64 + b"\n")
    _write(root / ".env", b"AGENT_SECRET=x\n")
    _write(root / "resources" / "mtbf" / "AutoTestTool.apk", b"apk-bytes")
    _write(root / "resources" / "aimonkey" / "monkey.bin", b"monkey-bytes")
    # symlink 不进载荷
    os.symlink("main.py", str(root / "main_link.py"))
    return root


@pytest.fixture
def schema_file(tmp_path):
    schema = tmp_path / "pipeline_schema.json"
    schema.write_bytes(b'{"steps": []}')
    return schema


def _cp_digest(root, schema_file, monkeypatch) -> str:
    monkeypatch.setattr(hu, "_AGENT_SOURCE_DIR", root)
    monkeypatch.setattr(hu, "_PIPELINE_SCHEMA_FILE", schema_file)
    return ad.digest_entries(ad.collect_artifact_entries())


def test_parity_control_plane_vs_agent_byte_equivalent(agent_tree, schema_file, monkeypatch):
    """双侧镜像实现：同一 fixture 树 → 字节级相同 digest（ADR-0040 D1）。"""
    cp = _cp_digest(agent_tree, schema_file, monkeypatch)
    agent = agent_ad.digest_entries(
        agent_ad.collect_artifact_entries(
            str(agent_tree),
            extra_files={"stp_schemas/pipeline_schema.json": str(schema_file)},
        )
    )
    assert cp == agent
    assert cp.startswith("sha256:")
    assert len(cp) == len("sha256:") + 64


def test_parity_empty_tree(agent_tree, tmp_path, monkeypatch):
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setattr(hu, "_AGENT_SOURCE_DIR", empty)
    monkeypatch.setattr(hu, "_PIPELINE_SCHEMA_FILE", tmp_path / "none.json")
    cp = ad.digest_entries(ad.collect_artifact_entries())
    agent = agent_ad.digest_entries(agent_ad.collect_artifact_entries(str(empty)))
    assert cp == agent


def test_exec_bit_and_content_change_flip_digest(agent_tree, schema_file, monkeypatch):
    base = _cp_digest(agent_tree, schema_file, monkeypatch)

    # 内容不变、加可执行位 → digest 变化（可执行位参与身份）
    os.chmod(agent_tree / "same_content.py", 0o755)
    with_exec = _cp_digest(agent_tree, schema_file, monkeypatch)
    assert with_exec != base

    # 内容变化 → digest 变化
    _write(agent_tree / "new_file.py", b"new\n")
    with_new = _cp_digest(agent_tree, schema_file, monkeypatch)
    assert with_new != with_exec

    # 删除文件 → digest 回到加位后的值
    os.remove(agent_tree / "new_file.py")
    assert _cp_digest(agent_tree, schema_file, monkeypatch) == with_exec


def test_metadata_and_host_local_files_not_in_identity(agent_tree, schema_file, monkeypatch):
    """VERSION / ARTIFACT_DIGEST / .env / resources/mtbf/ 变化不影响 digest。"""
    base = _cp_digest(agent_tree, schema_file, monkeypatch)

    _write(agent_tree / "VERSION", b"ffffff0\n")
    _write(agent_tree / "ARTIFACT_DIGEST", b"sha256:" + b"f" * 64 + b"\n")
    _write(agent_tree / ".env", b"AGENT_SECRET=y\n")
    _write(agent_tree / "resources" / "mtbf" / "other.apk", b"other-apk")
    assert _cp_digest(agent_tree, schema_file, monkeypatch) == base


def test_digest_matches_tarball_payload(agent_tree, schema_file, monkeypatch):
    """契约守护：digest 输入集 == tarball 载荷集（ADR-0040 D1 / §4.2 缓解）。"""
    monkeypatch.setattr(hu, "_AGENT_SOURCE_DIR", agent_tree)
    monkeypatch.setattr(hu, "_PIPELINE_SCHEMA_FILE", schema_file)
    tarball = hu._build_tarball()

    entries = []
    with tarfile.open(fileobj=io.BytesIO(tarball), mode="r:gz") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                raise AssertionError(f"unexpected non-file member: {member.name}")
            content = tar.extractfile(member).read()
            entries.append(
                (member.name, bool(member.mode & 0o111), hashlib.sha256(content).hexdigest())
            )
    entries.sort()

    assert ad.digest_entries(entries) == ad.digest_entries(ad.collect_artifact_entries())


def test_desired_digest_cache_keyed_by_fingerprint(agent_tree, schema_file, monkeypatch):
    """进程缓存：输入集指纹未变 → 不重算；TESTING=1 且未显式开启时关缓存。"""
    monkeypatch.setenv("TESTING", "1")
    monkeypatch.delenv("STP_ARTIFACT_DIGEST_CACHE", raising=False)
    monkeypatch.setattr(hu, "_AGENT_SOURCE_DIR", agent_tree)
    monkeypatch.setattr(hu, "_PIPELINE_SCHEMA_FILE", schema_file)

    calls = {"n": 0}
    real_collect = ad.collect_artifact_entries

    def counting_collect(kind="full"):
        calls["n"] += 1
        return real_collect()

    monkeypatch.setattr(ad, "collect_artifact_entries", counting_collect)
    d1 = ad.compute_desired_artifact_digest()
    d2 = ad.compute_desired_artifact_digest()
    assert d1 == d2 and calls["n"] == 2  # TESTING=1 每次现算

    monkeypatch.setenv("STP_ARTIFACT_DIGEST_CACHE", "30")
    calls["n"] = 0
    d3 = ad.compute_desired_artifact_digest()
    d4 = ad.compute_desired_artifact_digest()
    assert d3 == d4 == d1 and calls["n"] == 1  # 指纹未变 → 命中缓存

    # 内容变化（mtime/size 变）→ 指纹变化 → 重算
    _write(agent_tree / "main.py", b"print('changed')\n")
    d5 = ad.compute_desired_artifact_digest()
    assert d5 != d3 and calls["n"] == 2


class _FakeHost:
    def __init__(self, digest):
        self.id = "h-1"
        self.agent_artifact_digest = digest


def test_evaluate_convergence_states(monkeypatch):
    desired = "sha256:" + "a" * 64
    monkeypatch.setattr(ad, "compute_desired_artifact_digest", lambda: desired)

    # 空列（新协议下从未部署）→ 全量部署
    d, converged = ad.evaluate_convergence(_FakeHost(None))
    assert d == desired and converged is None

    # drift
    d, converged = ad.evaluate_convergence(_FakeHost("sha256:" + "b" * 64))
    assert converged is None

    # digest-matched → no-op 结果（与 execute_hot_update 返回同构）
    d, converged = ad.evaluate_convergence(_FakeHost(desired))
    assert converged is not None
    assert converged["ok"] is True
    assert converged["converged"] is True
    assert converged["reason"] == "digest-matched"

    # force 跳过判定
    d, converged = ad.evaluate_convergence(_FakeHost(desired), force=True)
    assert converged is None and d == desired


def test_iter_payload_files_skip_rules(agent_tree, schema_file, monkeypatch):
    monkeypatch.setattr(hu, "_AGENT_SOURCE_DIR", agent_tree)
    monkeypatch.setattr(hu, "_PIPELINE_SCHEMA_FILE", schema_file)
    arcnames = [arc for _, arc in hu._iter_payload_files()]
    assert "main_link.py" not in arcnames  # symlink 不进载荷
    assert "main.py" in arcnames
    assert "resources/aimonkey/monkey.bin" in arcnames
    assert not any(a.startswith("resources/mtbf") for a in arcnames)
    assert "stp_schemas/pipeline_schema.json" in arcnames
    assert "VERSION" not in arcnames and "ARTIFACT_DIGEST" not in arcnames


# ── #1963 P2 切片①：身份分层（code / resources 分区） ──────────────────────


class TestKindPartition:
    """code ∪ resources == full 且互斥；两侧镜像等价覆盖两 kind。"""

    def test_partition_union_equals_full(self, agent_tree, schema_file, monkeypatch):
        # resources/ 大件不入 git（gitignore + 带外布放）——分区测试必须在
        # fixture 树上走（与 parity 同款 monkeypatch），真实树 resources 恒空
        monkeypatch.setattr(hu, "_AGENT_SOURCE_DIR", agent_tree)
        monkeypatch.setattr(hu, "_PIPELINE_SCHEMA_FILE", schema_file)
        full = ad.collect_artifact_entries()
        code = ad.collect_artifact_entries(kind="code")
        resources = ad.collect_artifact_entries(kind="resources")
        rel = lambda es: {e[0] for e in es}
        assert rel(code) | rel(resources) == rel(full)
        assert not rel(code) & rel(resources)
        # 分层语义：resources 身份只含 resources/**（且不含 mtbf/）
        assert rel(resources) and all(r.startswith("resources/") for r in rel(resources))
        assert all(not r.startswith("resources/mtbf/") for r in rel(resources))

    def test_partition_digests_differ_and_stable(self, agent_tree, schema_file, monkeypatch):
        full = _cp_digest(agent_tree, schema_file, monkeypatch)
        code = ad.compute_desired_artifact_digest(kind="code")
        resources = ad.compute_desired_artifact_digest(kind="resources")
        assert len({full, code, resources}) == 3
        assert ad.compute_desired_artifact_digest(kind="code") == code

    def test_mirror_parity_both_kinds(self, agent_tree, schema_file, monkeypatch):
        monkeypatch.setattr(hu, "_AGENT_SOURCE_DIR", agent_tree)
        monkeypatch.setattr(hu, "_PIPELINE_SCHEMA_FILE", schema_file)
        code_cp = ad.collect_artifact_entries(kind="code")
        res_cp = ad.collect_artifact_entries(kind="resources")
        extra = {"stp_schemas/pipeline_schema.json": str(schema_file)}
        code_ag = agent_ad.collect_artifact_entries(str(agent_tree), kind="code", extra_files=extra)
        res_ag = agent_ad.collect_artifact_entries(str(agent_tree), kind="resources")
        # code 身份含 schema arcname（extra），镜像侧对齐 extra 后比较
        assert ad.digest_entries(code_cp) == agent_ad.digest_entries(code_ag)
        assert ad.digest_entries(res_cp) == agent_ad.digest_entries(res_ag)

    def test_resources_digest_sensitivity_and_code_isolation(self, agent_tree, schema_file, monkeypatch):
        monkeypatch.setattr(hu, "_AGENT_SOURCE_DIR", agent_tree)
        monkeypatch.setattr(hu, "_PIPELINE_SCHEMA_FILE", schema_file)
        d1 = ad.compute_desired_artifact_digest(kind="resources")
        code_d1 = ad.compute_desired_artifact_digest(kind="code")
        _write(agent_tree / "resources" / "aimonkey" / "monkey.bin", b"CHANGED")
        assert ad.compute_desired_artifact_digest(kind="resources") != d1
        # 分层隔离的语义本体：resources 内容变化不影响 code 身份
        assert ad.compute_desired_artifact_digest(kind="code") == code_d1
