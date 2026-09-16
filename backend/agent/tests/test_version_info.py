import backend.agent.version_info as version_info_mod


def test_read_agent_code_revision(tmp_path, monkeypatch):
    pkg_dir = tmp_path / "agent"
    pkg_dir.mkdir()
    (pkg_dir / "VERSION").write_text("1e449c4\n", encoding="utf-8")
    fake_module = pkg_dir / "version_info.py"
    fake_module.write_text("", encoding="utf-8")
    monkeypatch.setattr(version_info_mod, "__file__", str(fake_module))

    assert version_info_mod.read_agent_code_revision() == "1e449c4"


_CODE = "sha256:" + "a" * 64
_RESOURCES = "sha256:" + "b" * 64


def _two_identities(tmp_path, monkeypatch):
    """两份身份都真实存在——只有这样才区分得开「读不到」与「读错身份」。"""
    deployed = tmp_path / "agent"
    deployed.mkdir()
    (deployed / "ARTIFACT_DIGEST").write_text(_CODE + "\n", encoding="utf-8")
    (deployed / "ARTIFACT_DIGEST_RESOURCES").write_text(_RESOURCES + "\n", encoding="utf-8")
    monkeypatch.setattr(version_info_mod, "_ARTIFACT_DIGEST_DIRS", (deployed,))
    return deployed


def test_artifact_digest_reads_the_identity_named_by_kind(tmp_path, monkeypatch):
    _two_identities(tmp_path, monkeypatch)
    assert version_info_mod.read_artifact_digest("code") == _CODE
    assert version_info_mod.read_artifact_digest("resources") == _RESOURCES
    assert version_info_mod.read_artifact_digest() == _CODE  # 默认 code


def test_unknown_kind_never_reads_the_other_identity(tmp_path, monkeypatch, caplog):
    """#2016 本体：旧实现是 `else → 读 resources`，任何拼写错误都会**报出另一份真实身份**。

    控制面据此算 aligned/drift，错值比缺失更坏，所以表外 kind 必须是「读不到 + 说出来」。
    """
    _two_identities(tmp_path, monkeypatch)
    for kind in ("resource", "full", "", "CODE", "resources ", None):
        with caplog.at_level("WARNING"):
            value = version_info_mod.read_artifact_digest(kind)  # type: ignore[arg-type]
        assert value == "", f"kind={kind!r} 不该读到任何一份身份摘要"
        assert value != _RESOURCES, "静默退化到 resources 就是本单的故障"
        assert "unknown kind" in caplog.text, "缺失必须是可见的缺失，不是沉默"
        caplog.clear()


def test_artifact_digest_dirs_are_injectable():
    """守卫自身的前提：目录必须是模块常量，否则上面两条用例无从注入（恒真风险）。"""
    assert isinstance(version_info_mod._ARTIFACT_DIGEST_DIRS, tuple)
    assert set(version_info_mod._ARTIFACT_DIGEST_FILES) == {"code", "resources"}
