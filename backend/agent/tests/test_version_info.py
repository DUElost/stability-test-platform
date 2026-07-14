import backend.agent.version_info as version_info_mod


def test_read_agent_code_revision(tmp_path, monkeypatch):
    pkg_dir = tmp_path / "agent"
    pkg_dir.mkdir()
    (pkg_dir / "VERSION").write_text("1e449c4\n", encoding="utf-8")
    fake_module = pkg_dir / "version_info.py"
    fake_module.write_text("", encoding="utf-8")
    monkeypatch.setattr(version_info_mod, "__file__", str(fake_module))

    assert version_info_mod.read_agent_code_revision() == "1e449c4"
