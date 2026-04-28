from pathlib import Path

import backend.agent.tool_discovery as tool_discovery
from backend.agent.tool_discovery import ToolDiscovery


def _write_pipeline_action(path: Path, class_name: str, category: str) -> None:
    path.write_text(
        "from backend.agent.pipeline_engine import PipelineAction\n"
        f"class {class_name}(PipelineAction):\n"
        f"    TOOL_CATEGORY = {category!r}\n"
        "    TOOL_DESCRIPTION = 'test tool'\n"
        "    @classmethod\n"
        "    def get_default_params(cls):\n"
        "        return {'enabled': True}\n",
        encoding="utf-8",
    )


def test_discovery_scans_top_level_files_even_when_category_dirs_exist(tmp_path):
    _write_pipeline_action(tmp_path / "top_tool.py", "TopToolAction", "Top")

    category_dir = tmp_path / "Category"
    category_dir.mkdir()
    _write_pipeline_action(category_dir / "nested_tool.py", "NestedToolAction", "Nested")

    found = ToolDiscovery(tool_dir=str(tmp_path), include_builtin=False).scan()

    class_names = {item["class_name"] for item in found}
    assert {"TopToolAction", "NestedToolAction"} <= class_names


def test_discovery_maps_builtin_tool_paths_to_runtime_root(tmp_path, monkeypatch):
    _write_pipeline_action(tmp_path / "top_tool.py", "TopToolAction", "Top")
    monkeypatch.setattr(tool_discovery, "BUILTIN_TOOL_DIR", tmp_path)
    monkeypatch.setenv("STP_TOOL_RUNTIME_ROOT", "/opt/stability-test-agent/agent/tools")

    found = ToolDiscovery(tool_dir=str(tmp_path / "missing_external"), include_builtin=True).scan()

    top_tool = next(item for item in found if item["class_name"] == "TopToolAction")
    assert top_tool["script_path"] == "/opt/stability-test-agent/agent/tools/top_tool.py"


def test_discovery_skips_marked_tool_bundle_dirs(tmp_path, capsys):
    bundle_dir = tmp_path / "AIMonkeyTest_20260317"
    bundle_dir.mkdir()
    (bundle_dir / ".stp-tool-bundle").write_text("", encoding="utf-8")
    (bundle_dir / "legacy_python2.py").write_text("print 'legacy'\n", encoding="utf-8")

    found = ToolDiscovery(tool_dir=str(tmp_path), include_builtin=False).scan()

    assert found == []
    assert "解析脚本失败" not in capsys.readouterr().out
