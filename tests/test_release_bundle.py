from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from backend.agent.artifact_digest import collect_artifact_entries, digest_entries
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
    shutil.copy2(REPO_ROOT / "backend/agent/artifact_digest.py", root / "backend/agent/artifact_digest.py")
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
    assert {platform["distribution"] for platform in manifest["compatibility"]["platforms"]} == {"debian", "ubuntu"}


def test_manifest_is_accepted_by_the_installer_side_loader(tmp_path):
    out, _ = built(tmp_path)
    manifest = load_release_manifest(out / MANIFEST_NAME)
    assert {component.name for component in manifest.components} == {"agent-code", "host-resources"}
    for component in manifest.components:
        assert component.digest.startswith("sha256:")


def test_digests_match_an_independent_adr0040_recomputation(tmp_path):
    out, result = built(tmp_path)
    expected = reference_digests(out)
    assert result["components"] == expected
    manifest = json.loads((out / MANIFEST_NAME).read_text(encoding="utf-8"))
    assert {item["name"]: item["digest"] for item in manifest["components"]} == expected


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
    assert set(payload["components"]) == {"agent-code", "host-resources"}
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
    source = (out / "backend/agent/artifact_digest.py").read_text(encoding="utf-8")
    assert "collect_artifact_entries" in source
    original = (REPO_ROOT / "backend/agent/artifact_digest.py").read_text(encoding="utf-8")
    assert source == original
    assert sys.version_info >= (3, 11)
