from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from backend.agent.contracts.artifact_digest import collect_artifact_entries, collect_control_plane_entries, digest_entries
from tools.release.build_bundle import MANIFEST_NAME, BundleError, build_bundle, main
from tools.site_config.manifest import load_release_manifest

REPO_ROOT = Path(__file__).resolve().parents[1]
REVISION = "0123456789abcdef0123456789abcdef01234567"

REVISION_A = '''\
"""first"""
revision = "aaaa1111"
down_revision = None
'''

REVISION_B = '''\
"""second"""
revision = "bbbb2222"
down_revision = "aaaa1111"
'''

REVISION_BRANCH = '''\
"""second on another branch"""
revision = "cccc3333"
down_revision = "aaaa1111"
'''


def tree(tmp_path: Path, *, frontend: bool = True, migrations: dict[str, str] | None = None) -> Path:
    """一个最小工作树：与真实仓库同形的关键目录。"""
    root = tmp_path / "repo"
    if (root / "backend").exists():  # 允许同一测试里重建工作树
        shutil.rmtree(root)
    (root / "backend/agent").mkdir(parents=True)
    (root / "backend/agent/sample.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "backend/agent/contracts").mkdir(parents=True)
    shutil.copy2(
        REPO_ROOT / "backend/agent/contracts/artifact_digest.py",
        root / "backend/agent/contracts/artifact_digest.py",
    )
    (root / "backend/agent/AGENTS.md").write_text("# agent contract\n", encoding="utf-8")
    # 仓库约定：CLAUDE.md 是指向 AGENTS.md 的符号链接（不得被实体化）
    (root / "backend/agent/CLAUDE.md").symlink_to("AGENTS.md")
    (root / "backend/schemas").mkdir(parents=True)
    (root / "backend/schemas/pipeline_schema.json").write_text("{}\n", encoding="utf-8")
    (root / "backend/alembic/versions").mkdir(parents=True)
    versions = migrations if migrations is not None else {"aaaa_first.py": REVISION_A, "bbbb_second.py": REVISION_B}
    for name, text in versions.items():
        (root / "backend/alembic/versions" / name).write_text(text, encoding="utf-8")
    (root / "backend/requirements.txt").write_text("fastapi\n", encoding="utf-8")
    # resources 不在 git：合成树必须带上，否则 build_bundle 会拒绝打包
    (root / "backend/agent/resources/tools").mkdir(parents=True)
    (root / "backend/agent/resources/tools/tool.bin").write_bytes(b"binary\n")
    (root / "deploy/control-plane/systemd").mkdir(parents=True)
    (root / "deploy/control-plane/systemd/stability-backend.service").write_text("[Unit]\n", encoding="utf-8")
    (root / "tools/site_config").mkdir(parents=True)
    (root / "tools/site_config/__init__.py").write_text("", encoding="utf-8")
    (root / "ruff.toml").write_text("line-length = 120\n", encoding="utf-8")
    if frontend:
        (root / "frontend/dist-prod/assets").mkdir(parents=True)
        (root / "frontend/dist-prod/index.html").write_text("<html></html>\n", encoding="utf-8")
    return root


def reference_digests(bundle: Path) -> dict[str, str]:
    """独立按 ADR-0040 实现重算（与 S0 的 `_DIGEST_SCRIPT` 同基准）。"""
    extra = {"stp_schemas/pipeline_schema.json": str(bundle / "backend/schemas/pipeline_schema.json")}
    agent_dir = str(bundle / "backend/agent")
    return {
        "agent-code": digest_entries(collect_artifact_entries(agent_dir, extra, kind="code")),
        "host-resources": digest_entries(collect_artifact_entries(agent_dir, extra, kind="resources")),
        "control-plane": digest_entries(collect_control_plane_entries(str(bundle))),
    }


def built(tmp_path: Path, **kwargs) -> tuple[Path, dict]:
    out = tmp_path / "bundle"
    result = build_bundle(tree(tmp_path), out, revision=REVISION, **kwargs)
    return out, result


def test_bundle_carries_the_documented_layout_and_manifest(tmp_path):
    out, result = built(tmp_path)
    for name in ("backend", "deploy", "tools", "frontend/dist-prod", MANIFEST_NAME):
        assert (out / name).exists(), name
    manifest = json.loads((out / MANIFEST_NAME).read_text(encoding="utf-8"))
    assert manifest["manifest_version"] == 1
    assert manifest["product"]["version"] == result["version"]
    assert manifest["source"]["revision"] == REVISION
    assert manifest["database"]["schema_target"] == "bbbb2222"
    assert manifest["provenance"]["attestation"] == "controlled_channel"
    assert manifest["compatibility"]["agent_protocol"].startswith(">=1.0")
    platforms = manifest["compatibility"]["platforms"]
    assert {platform["distribution"] for platform in platforms} == {"debian", "ubuntu"}
    ubuntu = next(platform for platform in platforms if platform["distribution"] == "ubuntu")
    # 22.04 于 2026-09-15 实测纳入；24.04 一并保留（防回归）
    assert {"22.04", "24.04"} <= set(ubuntu["versions"])


def test_bundle_carries_agent_version_for_provenance(tmp_path, monkeypatch):
    """#3401 A4：bundle 不是 git 仓库——构建期写 `backend/agent/VERSION` 供
    `get_agent_code_version()` 回退；且 VERSION 不进任何摘要面（载荷元数据排除）。"""
    import backend.services.host_updater as hu_mod

    out, _ = built(tmp_path)
    version_file = out / "backend" / "agent" / "VERSION"
    assert version_file.read_text(encoding="utf-8").strip() == REVISION[:8]

    # 端到端：bundle 布局下取版本不再为空（热更新 code_version 由此而来）
    monkeypatch.setattr(hu_mod, "_AGENT_SOURCE_DIR", out / "backend" / "agent")
    assert hu_mod.get_agent_code_version() == REVISION[:8]

    # 不进身份：契约枚举（agent-code 口径）不含 VERSION
    extra = {
        "stp_schemas/pipeline_schema.json": str(
            out / "backend" / "schemas" / "pipeline_schema.json"
        )
    }
    arcnames = {
        entry[0]
        for entry in collect_artifact_entries(
            str(out / "backend" / "agent"), extra, kind="code"
        )
    }
    assert "VERSION" not in arcnames


def test_manifest_is_accepted_by_the_installer_side_loader(tmp_path):
    out, _ = built(tmp_path)
    manifest = load_release_manifest(out / MANIFEST_NAME)
    assert {component.name for component in manifest.components} == {"agent-code", "host-resources", "control-plane"}
    for component in manifest.components:
        assert component.digest.startswith("sha256:")


def test_digests_match_an_independent_adr0040_recomputation(tmp_path):
    out, result = built(tmp_path)
    expected = reference_digests(out)
    assert result["components"] == expected
    manifest = json.loads((out / MANIFEST_NAME).read_text(encoding="utf-8"))
    assert {item["name"]: item["digest"] for item in manifest["components"]} == expected


def test_control_plane_surface_isolates_agent_payload(tmp_path):
    """ADR-0051 Phase 4：control-plane 覆盖 backend/**（除 agent）——两面的漂移互不串扰。"""
    out, _ = built(tmp_path)
    cp = {c["name"]: c["digest"] for c in json.loads((out / "release-manifest.json").read_text(encoding="utf-8"))["components"]}
    assert "control-plane" in cp and cp["control-plane"] == reference_digests(out)["control-plane"]
    cp = {c["name"]: c["digest"] for c in json.loads((out / "release-manifest.json").read_text(encoding="utf-8"))["components"]}

    (out / "backend/api").mkdir(parents=True)
    (out / "backend/api/routes.py").write_text("X = 1\n", encoding="utf-8")
    again = reference_digests(out)
    assert again["control-plane"] != cp["control-plane"], "控制面载荷新增文件必须改变 control-plane 摘要"
    assert again["agent-code"] == cp["agent-code"], "backend/agent 之外的变化不得动 agent-code"

    shutil.rmtree(out / "backend/api")
    (out / "backend/agent/sample.py").write_text("VALUE = 2\n", encoding="utf-8")
    again = reference_digests(out)
    assert again["agent-code"] != cp["agent-code"]
    assert again["control-plane"] == cp["control-plane"], "agent 树内容不得进入控制面摘要（分区隔离）"


def test_control_plane_digest_covers_env_files(tmp_path):
    """#2269 根因是构建机 `.env`「不在任何摘要面内」：control-plane **不排除** .env——
    若 ignore 被误改让它溜进 bundle，摘要当场变化（S0 fail-closed 才有机会拦）。"""
    out, _ = built(tmp_path)
    before = digest_entries(collect_control_plane_entries(str(out)))
    (out / "backend/.env").write_text("SECRET=leaked\n", encoding="utf-8")
    assert digest_entries(collect_control_plane_entries(str(out))) != before, ".env 必须进 control-plane 摘要面"


def test_legacy_two_component_manifest_still_loads(tmp_path):
    """兼容性：旧 release-manifest（无 control-plane）仍是合法清单（REQUIRED_COMPONENTS 不变，
    S0 比对按 declared 全键，旧 declared 两键照旧可过）。"""
    from tools.site_config.manifest import parse_release_manifest

    out, _ = built(tmp_path)
    path = out / "release-manifest.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["components"] = [c for c in doc["components"] if c["name"] != "control-plane"]
    manifest = parse_release_manifest(json.dumps(doc))
    assert {c.name for c in manifest.components} == {"agent-code", "host-resources"}


def test_digest_is_content_addressed_not_a_tree_hash(tmp_path):
    out, first = built(tmp_path)
    (out / "backend/agent/sample.py").write_text("VALUE = 2\n", encoding="utf-8")
    assert reference_digests(out)["agent-code"] != first["components"]["agent-code"]
    # resources 是独立分区：改它只影响 host-resources
    (out / "backend/agent/resources/tools/tool.bin").write_bytes(b"changed\n")
    moved = reference_digests(out)
    assert moved["host-resources"] != first["components"]["host-resources"]
    assert moved["agent-code"] == reference_digests(out)["agent-code"]


def test_landed_tree_keeps_the_agent_symlink(tmp_path):
    """符号链接被实体化会让部署摘要与清单基准不一致（I4 实测）。"""
    out, _ = built(tmp_path)
    link = out / "backend/agent/CLAUDE.md"
    assert link.is_symlink()
    assert link.readlink() == Path("AGENTS.md")


def test_rebuild_is_idempotent(tmp_path):
    out, first = built(tmp_path)
    first_manifest = (out / MANIFEST_NAME).read_text(encoding="utf-8")
    second = build_bundle(tree(tmp_path), out, revision=REVISION)
    assert first["components"] == second["components"]
    assert (out / MANIFEST_NAME).read_text(encoding="utf-8") == first_manifest


def test_missing_agent_resources_refuse_to_package(tmp_path):
    """resources 不在 git：缺了它摘要必然与清单不符（238 实测），必须早暴露。"""
    import shutil as _shutil

    root = tree(tmp_path)
    _shutil.rmtree(root / "backend/agent/resources")
    with pytest.raises(BundleError) as caught:
        build_bundle(root, tmp_path / "bundle", revision=REVISION)
    assert caught.value.code == "bundle_resources"
    assert "not in git" in caught.value.detail


def test_missing_frontend_build_says_what_to_run(tmp_path):
    with pytest.raises(BundleError) as caught:
        build_bundle(tree(tmp_path, frontend=False), tmp_path / "bundle", revision=REVISION)
    assert caught.value.code == "bundle_frontend"
    assert "npm run build:prod" in caught.value.detail


def test_missing_tree_parts_are_listed(tmp_path):
    root = tree(tmp_path)
    shutil.rmtree(root / "tools")
    with pytest.raises(BundleError) as caught:
        build_bundle(root, tmp_path / "bundle", revision=REVISION)
    assert caught.value.code == "bundle_layout"
    assert "tools" in caught.value.detail


def test_two_branch_heads_are_refused_instead_of_picking_one(tmp_path):
    root = tree(tmp_path, migrations={
        "aaaa_first.py": REVISION_A, "bbbb_second.py": REVISION_B, "cccc_other.py": REVISION_BRANCH,
    })
    with pytest.raises(BundleError) as caught:
        build_bundle(root, tmp_path / "bundle", revision=REVISION)
    assert caught.value.code == "bundle_schema_target"


def test_explicit_schema_target_wins(tmp_path):
    out, result = built(tmp_path, schema_target="dddd4444")
    assert result["schema_target"] == "dddd4444"


def test_revision_comes_from_git_when_not_given(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("git is not available")
    root = tree(tmp_path)
    env = {"PATH": "/usr/bin:/bin", "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid", "HOME": str(tmp_path)}
    subprocess.run(["git", "init", "-q"], cwd=root, env=env, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, env=env, check=True)
    subprocess.run(["git", "commit", "-qm", "tree"], cwd=root, env=env, check=True)
    result = build_bundle(root, tmp_path / "bundle")
    assert result["revision"] == subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, env=env, capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert result["version"].startswith("local-")


def test_non_git_tree_requires_an_explicit_revision(tmp_path):
    with pytest.raises(BundleError) as caught:
        build_bundle(tree(tmp_path), tmp_path / "bundle")
    assert caught.value.code == "bundle_revision"


def test_cli_reports_the_bundle_as_json(tmp_path, capsys):
    out = tmp_path / "bundle"
    code = main(["--repo-root", str(tree(tmp_path)), "--out", str(out), "--revision", REVISION, "--json"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_target"] == "bbbb2222"
    assert set(payload["components"]) == {"agent-code", "host-resources", "control-plane"}
    assert (out / MANIFEST_NAME).is_file()


def test_cli_failure_is_a_single_line_and_a_nonzero_exit(tmp_path, capsys):
    code = main(["--repo-root", str(tree(tmp_path, frontend=False)), "--out", str(tmp_path / "b")])
    captured = capsys.readouterr()
    assert code == 1
    assert "bundle_frontend" in captured.err
    assert captured.err.count("\n") == 1


def test_wheelhouse_is_optional_and_off_by_default(tmp_path):
    out, result = built(tmp_path)
    assert result["wheelhouse"] is False
    assert not (out / "wheelhouse").exists()


def test_digest_helper_loads_the_implementation_from_the_bundle(tmp_path):
    """摘要必须由 bundle 内的实现计算：否则打包与安装两侧会各自演化算法。"""
    out, _ = built(tmp_path)
    source = (out / "backend/agent/contracts/artifact_digest.py").read_text(encoding="utf-8")
    assert "collect_artifact_entries" in source
    original = (REPO_ROOT / "backend/agent/contracts/artifact_digest.py").read_text(encoding="utf-8")
    assert source == original
    assert sys.version_info >= (3, 11)


# ── #2269：bundle 不得携带构建机本地状态（.env / 字节码 / 缓存）─────────────


def test_bundle_excludes_backend_dotenv(tmp_path):
    """构建机的 backend/.env 不得进入交付物（#2269 主症状）。"""
    root = tree(tmp_path)
    (root / "backend" / ".env").write_text("STP_FILE_SERVER_ADDRESS=build-host\n", encoding="utf-8")
    out = tmp_path / "bundle"
    build_bundle(root, out, revision=REVISION)
    assert not (out / "backend" / ".env").exists(), "构建机 backend/.env 被打进 bundle"
    assert not any(p.name == ".env" for p in out.rglob(".env")), "bundle 内仍存在 .env"


def test_bundle_excludes_env_variants_but_keeps_example(tmp_path):
    """`.env.local` 等变体排除；但**入库模板** `.env.example` 必须保留。

    `deploy/postgres/.env.example` 是部署文档要求 `cp` 的模板
    （`deploy/postgres/README.md`），宽泛的 `.env.*` 会破坏部署。
    """
    # 注意：built() 内部会自行调用 tree() 建树，故必须先建树再写文件、并直接
    # 用 build_bundle 复用**同一个** tree（否则写到另一份树里，断言会假失败）。
    root = tree(tmp_path)
    (root / "backend" / ".env.local").write_text("A=1\n", encoding="utf-8")
    # 覆盖仓库中**实际存在**的三种模板写法（不止 `.env.example`）——
    # `.env.backend.example` / `.env.backend.internal.example` 曾因
    # 只豁免 `.env.example` 而被误删（本仓实测 8 个入库模板全部以 `.example` 结尾）。
    templates = (".env.example", ".env.backend.example", ".env.backend.internal.example")
    for name in templates:
        (root / "backend" / name).write_text("A=\n", encoding="utf-8")
    out = tmp_path / "bundle"
    build_bundle(root, out, revision=REVISION)
    assert not (out / "backend" / ".env.local").exists(), ".env.local 未被排除"
    for name in templates:
        assert (out / "backend" / name).exists(), f"入库模板 {name} 被误删"


def test_bundle_excludes_bytecode_and_caches(tmp_path):
    """构建机字节码 / 缓存不得进入交付物。"""
    root = tree(tmp_path)
    cache = root / "backend" / "__pycache__"
    cache.mkdir(parents=True)
    (cache / "mod.cpython-313.pyc").write_bytes(b"\x00")
    (root / "backend" / "stale.pyc").write_bytes(b"\x00")
    out = tmp_path / "bundle"
    build_bundle(root, out, revision=REVISION)
    assert not (out / "backend" / "__pycache__").exists(), "__pycache__ 未被排除"
    assert not (out / "backend" / "stale.pyc").exists(), "游离 .pyc 未被排除"


def test_no_pycache_written_into_bundle_by_digesting(tmp_path):
    """摘要阶段加载 artifact_digest 不得**在 bundle 内**写出 __pycache__。

    修复前 `_load_agent_digest_module` 会 exec_module → 生成
    `backend/agent/contracts/__pycache__`，即构建副产物被写进交付物。
    """
    out, _ = built(tmp_path)
    assert not (out / "backend" / "agent" / "contracts" / "__pycache__").exists(), (
        "摘要阶段在 bundle 内写出了 __pycache__（构建副产物混入交付物）"
    )


def test_forbidden_entries_checker_flags_and_exempts(tmp_path):
    """`find_forbidden_bundle_entries` 的判据：拦 .env/字节码，放行 .env.example。"""
    from tools.release.build_bundle import find_forbidden_bundle_entries

    root = tmp_path / "b"
    (root / "backend").mkdir(parents=True)
    (root / "backend" / ".env").write_text("A=1\n", encoding="utf-8")
    (root / "backend" / ".env.example").write_text("A=\n", encoding="utf-8")
    (root / "backend" / "__pycache__").mkdir()
    (root / "backend" / "__pycache__" / "m.pyc").write_bytes(b"\x00")
    (root / "backend" / "ok.py").write_text("x = 1\n", encoding="utf-8")

    found = find_forbidden_bundle_entries(root)
    assert "backend/.env" in found
    assert "backend/__pycache__" in found
    assert any(p.endswith("m.pyc") for p in found)
    assert not any(".env.example" in p for p in found), "入库模板被误判为违规"
    assert not any(p.endswith("ok.py") for p in found), "正常源码被误判为违规"
