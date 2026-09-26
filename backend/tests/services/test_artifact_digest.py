"""ADR-0040 D1/D3（#1907）部署 artifact digest：单实现算法 + 枚举对拍 + 部署契约 + no-op gate。

- **单实现**：算法（``digest_entries`` / kind 词表）只有契约包
  ``backend/agent/contracts/artifact_digest.py`` 一份（ADR-0054 §5 第 3 步）；
  控制面 services 直接 import 同一函数，旧 ``backend/agent/artifact_digest.py``
  已删（本文件有断点断言）；两侧不再互为参照，格式改由 known-answer 固定向量
  钉住（``test_digest_serialization_format_is_pinned``）；
- **枚举对拍**：控制面（host_updater 共享枚举 + services）与契约树枚举
  （``collect_artifact_entries``）对同一 fixture 树产出相同 digest——算法同源后
  仍可能漂移的只剩输入集定义，本判据继续钉住它；
- 契约：digest 输入集 == tarball 载荷集（同一 ``_iter_payload_files``），
  从 tarball 成员反算的 digest 必须等于树侧 digest；
- 边界：exec 位、元数据排除（VERSION/ARTIFACT_DIGEST/.env）、
  ``resources/mtbf/`` 主机本地保护、``__pycache__``/``tests``/``test_*.py``、
  空集、增删文件；
- no-op gate：``plan_convergence`` 的单层（agent-code）drift / force / no-op 同构；
  ADR-0040 D8 R1 起 host-resources 层不参与判定（反向守卫）。
"""

from __future__ import annotations

import hashlib
import io
import os
import tarfile
from pathlib import Path

import pytest

import backend.services.artifact_digest as ad
import backend.services.host_updater as hu
from backend.agent.contracts import artifact_digest as contract_ad

_REPO_ROOT = Path(__file__).resolve().parents[3]
_LEGACY_COPIES = (
    _REPO_ROOT / "backend" / "agent" / "artifact_digest.py",
)


def test_single_algorithm_implementation_without_legacy_copy():
    """ADR-0054 §5 第 3 步/D5：算法只有契约包一份，旧 agent 镜像已删。"""
    assert ad.digest_entries is contract_ad.digest_entries
    assert contract_ad.__file__.endswith("backend/agent/contracts/artifact_digest.py")
    for legacy in _LEGACY_COPIES:
        assert not legacy.exists(), (
            f"旧副本仍在：{legacy}——ADR-0054 D5 要求删除且不留再导出壳"
        )


#: 格式锚（known-answer）：序列化参数（紧凑分隔符 / ensure_ascii / 条目顺序）的等价
#: 改写会静默改变全机队与发布物身份——收敛判定、manifest、Ansible 写入值一体联动，
#: 只能随显式身份迁移动作更新（hot-update + manifest 重算），不能顺手改。
_FORMAT_VECTOR = [
    ("backend/agent/main.py", False, "0" * 64),
    ("backend/agent/附件.py", True, "1" * 64),
]
_FORMAT_VECTOR_DIGEST = "sha256:2c635368c12f9d9187a447616625e80ad92bb6cd87ade8c25191518521cb9ae3"
#: 空集 digest 也是身份（空输入集的恒定值，随格式锚一并钉住）。
_EMPTY_DIGEST = "sha256:4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"


def test_digest_serialization_format_is_pinned():
    """known-answer：``digest_entries`` 的序列化格式就是部署身份本体。

    双端对拍改为单实现（ADR-0054 第 3 步）后，两侧不再互为参照——用固定向量把
    格式钉死：改序列化参数即红，而不是等 48 台主机集体报 drift 才发现。
    """
    assert ad.digest_entries(_FORMAT_VECTOR) == _FORMAT_VECTOR_DIGEST
    assert ad.digest_entries([]) == _EMPTY_DIGEST


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
    # #2030：部署通道不传输的文件（契约包 / wrapper / Ansible / 热更新 rsync 同源）
    _write(root / "stp_agent_priv.py", b"junk")
    _write(root / "venv" / "lib.py", b"junk")
    _write(root / "logs" / "a.log", b"junk")
    _write(root / "stp_schemas" / "stale.json", b"junk")
    _write(root / ".deps_installed_sha", b"junk")
    # ADR-0051 Phase 3：脚本族树不随 agent-code 下发（digest 与 tar/rsync 同口径）
    _write(root / "scripts" / "scan_aee" / "v1.0.0" / "scan_aee.py", b"junk")
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


def test_enumeration_parity_control_plane_vs_contract_tree(agent_tree, schema_file, monkeypatch):
    """两侧输入集枚举：同一 fixture 树 → 相同 digest（算法已同源，剩枚举漂移面）。"""
    cp = _cp_digest(agent_tree, schema_file, monkeypatch)
    contract = contract_ad.digest_entries(
        contract_ad.collect_artifact_entries(
            str(agent_tree),
            extra_files={"stp_schemas/pipeline_schema.json": str(schema_file)},
        )
    )
    assert cp == contract
    assert cp.startswith("sha256:")
    assert len(cp) == len("sha256:") + 64


def test_enumeration_parity_empty_tree(agent_tree, tmp_path, monkeypatch):
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setattr(hu, "_AGENT_SOURCE_DIR", empty)
    monkeypatch.setattr(hu, "_PIPELINE_SCHEMA_FILE", tmp_path / "none.json")
    cp = ad.digest_entries(ad.collect_artifact_entries())
    contract = contract_ad.digest_entries(contract_ad.collect_artifact_entries(str(empty)))
    assert cp == contract


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
    """契约守护：热更新 tarball 载荷集 == code 身份输入集（枚举同源，ADR-0040 D1 / §4.2 缓解）。

    ADR-0040 D8 R1 起热更新只打 code 层：fixture 树的 ``resources/`` 非空（即「控制面树仍带
    资源」的形态），载荷里也不得出现 ``resources/**``。分区并集见 ``TestKindPartition``。
    """
    monkeypatch.setattr(hu, "_AGENT_SOURCE_DIR", agent_tree)
    monkeypatch.setattr(hu, "_PIPELINE_SCHEMA_FILE", schema_file)

    def _members(buf):
        entries = []
        with tarfile.open(fileobj=io.BytesIO(buf), mode="r:gz") as tar:
            for member in tar.getmembers():
                if not member.isfile():
                    raise AssertionError(f"unexpected non-file member: {member.name}")
                content = tar.extractfile(member).read()
                entries.append(
                    (member.name, bool(member.mode & 0o111), hashlib.sha256(content).hexdigest())
                )
        entries.sort()
        return entries

    code_members = _members(hu._build_tarball(kind="code"))
    assert ad.digest_entries(code_members) == ad.digest_entries(
        ad.collect_artifact_entries(kind="code")
    )
    assert ad.collect_artifact_entries(kind="resources"), "fixture 必须带非空 resources/"
    assert not [
        name for name, _, _ in code_members
        if name == "resources" or name.startswith("resources/")
    ]


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


def test_plan_convergence_states(monkeypatch):
    """单层判定（agent-code）：drift / no-op 同构 / force。"""
    from backend.services.artifact_digest import ConvergencePlan

    desired = "sha256:" + "a" * 64

    class _FakeHost:
        def __init__(self, code=None):
            self.id = "h-1"
            self.agent_artifact_digest = code

    monkeypatch.setattr(
        ad, "compute_desired_artifact_digest",
        lambda kind="full": {"code": desired}[kind],
    )

    # current 缺失 → drift
    plan = ad.plan_convergence(_FakeHost())
    assert plan.code_drift and not plan.converged and plan.no_op_result is None

    # current 不等 → drift
    plan = ad.plan_convergence(_FakeHost("sha256:" + "b" * 64))
    assert plan.code_drift and not plan.converged

    # 匹配 → no-op 结果（与 execute 返回同构）
    plan = ad.plan_convergence(_FakeHost(desired))
    assert plan.converged and not plan.code_drift
    assert plan.no_op_result is not None
    assert plan.no_op_result["reason"] == "digest-matched"
    assert plan.no_op_result["artifact_digest"] == desired

    # force：匹配也强制 agent-code 全量
    plan = ad.plan_convergence(_FakeHost(desired), force=True)
    assert plan.code_drift and not plan.converged and plan.no_op_result is None

    assert set(ConvergencePlan.__dataclass_fields__) == {
        "code_digest", "code_drift", "converged", "no_op_result",
    }


def test_plan_convergence_ignores_host_resources_layer(monkeypatch):
    """ADR-0040 D8 R1 反向守卫：host-resources 层退役，不参与任何判定。

    控制面树的 ``resources/`` 分区非空、主机上报的 ``agent_resources_digest`` 缺失或
    不等时，旧实现判 ``resources_drift`` → 不收敛并下发资源层；现在 code 匹配即 no-op，
    且判定过程根本不计算 resources 身份。
    """
    desired = "sha256:" + "a" * 64
    kinds_computed: list[str] = []

    def _fake(kind="full"):
        kinds_computed.append(kind)
        return {"code": desired, "resources": "sha256:" + "c" * 64}[kind]

    monkeypatch.setattr(ad, "compute_desired_artifact_digest", _fake)

    class _FakeHost:
        id = "h-1"
        agent_artifact_digest = desired

    for reported in (None, "", "sha256:" + "d" * 64):
        host = _FakeHost()
        host.agent_resources_digest = reported
        plan = ad.plan_convergence(host)
        assert plan.converged and plan.no_op_result is not None, reported
        assert "resources_digest" not in plan.no_op_result
        forced = ad.plan_convergence(host, force=True)
        assert not [f for f in vars(forced) if f.startswith("resources")]
    assert set(kinds_computed) == {"code"}, kinds_computed
    assert not hasattr(ad, "compute_desired_resources_digest")


def test_iter_payload_files_skip_rules(agent_tree, schema_file, monkeypatch):
    monkeypatch.setattr(hu, "_AGENT_SOURCE_DIR", agent_tree)
    monkeypatch.setattr(hu, "_PIPELINE_SCHEMA_FILE", schema_file)
    arcnames = [arc for _, arc in hu._iter_payload_files(kind="full")]
    assert "main_link.py" not in arcnames  # symlink 不进载荷
    assert "main.py" in arcnames
    assert "resources/aimonkey/monkey.bin" in arcnames
    assert not any(a.startswith("resources/mtbf") for a in arcnames)
    assert "stp_schemas/pipeline_schema.json" in arcnames
    assert "VERSION" not in arcnames and "ARTIFACT_DIGEST" not in arcnames
    # #2030 / ADR-0051 Phase 3：部署通道不传输的文件不得进载荷/身份（契约包为单一源）
    for excluded in (
        "stp_agent_priv.py", "venv/lib.py", "logs/a.log",
        "stp_schemas/stale.json", ".deps_installed_sha",
        "scripts/scan_aee/v1.0.0/scan_aee.py",
    ):
        assert excluded not in arcnames, f"{excluded} 不应进载荷（#2030）"
    # stp_schemas/ 目录排除不影响 extra_files 的 schema 附加（独立通道）
    assert "stp_schemas/pipeline_schema.json" in arcnames


# ── #1963 P2 切片①：身份分层（code / resources 分区） ──────────────────────


class TestKindPartition:
    """code ∪ resources == full 且互斥；两侧枚举等价覆盖两 kind。"""

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

    def test_enumeration_parity_both_kinds(self, agent_tree, schema_file, monkeypatch):
        monkeypatch.setattr(hu, "_AGENT_SOURCE_DIR", agent_tree)
        monkeypatch.setattr(hu, "_PIPELINE_SCHEMA_FILE", schema_file)
        code_cp = ad.collect_artifact_entries(kind="code")
        res_cp = ad.collect_artifact_entries(kind="resources")
        extra = {"stp_schemas/pipeline_schema.json": str(schema_file)}
        code_ag = contract_ad.collect_artifact_entries(str(agent_tree), kind="code", extra_files=extra)
        res_ag = contract_ad.collect_artifact_entries(str(agent_tree), kind="resources")
        # code 身份含 schema arcname（extra），契约侧对齐 extra 后比较
        assert ad.digest_entries(code_cp) == contract_ad.digest_entries(code_ag)
        assert ad.digest_entries(res_cp) == contract_ad.digest_entries(res_ag)

    def test_resources_digest_sensitivity_and_code_isolation(self, agent_tree, schema_file, monkeypatch):
        monkeypatch.setattr(hu, "_AGENT_SOURCE_DIR", agent_tree)
        monkeypatch.setattr(hu, "_PIPELINE_SCHEMA_FILE", schema_file)
        d1 = ad.compute_desired_artifact_digest(kind="resources")
        code_d1 = ad.compute_desired_artifact_digest(kind="code")
        _write(agent_tree / "resources" / "aimonkey" / "monkey.bin", b"CHANGED")
        assert ad.compute_desired_artifact_digest(kind="resources") != d1
        # 分层隔离的语义本体：resources 内容变化不影响 code 身份
        assert ad.compute_desired_artifact_digest(kind="code") == code_d1
