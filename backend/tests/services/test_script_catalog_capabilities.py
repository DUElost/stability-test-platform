"""#171 — script.capabilities 由版本目录 capabilities.json 扫描登记。"""

import json
from pathlib import Path


from backend.services.script_catalog import read_capabilities


def _write_script_version(root: Path, name: str, version: str, capabilities=None) -> Path:
    version_dir = root / name / f"v{version}"
    version_dir.mkdir(parents=True)
    (version_dir / f"{name}.py").write_text("print('ok')\n", encoding="utf-8")
    if capabilities is not None:
        (version_dir / "capabilities.json").write_text(
            json.dumps({"capabilities": capabilities}), encoding="utf-8",
        )
    return version_dir


def test_read_capabilities_parses_metadata(tmp_path: Path):
    version_dir = _write_script_version(tmp_path, "demo", "1.0.0", ["progress_stamps"])
    assert read_capabilities(version_dir) == ["progress_stamps"]


def test_read_capabilities_empty_without_metadata(tmp_path: Path):
    version_dir = _write_script_version(tmp_path, "demo", "1.0.0")
    assert read_capabilities(version_dir) == []


def test_read_capabilities_malformed_metadata_is_empty(tmp_path: Path):
    version_dir = _write_script_version(tmp_path, "demo", "1.0.0", [])
    (version_dir / "capabilities.json").write_text("{not json", encoding="utf-8")
    assert read_capabilities(version_dir) == []



def _version_tuple(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split(".") if part.isdigit())


_PROGRESS_THRESHOLDS = {
    # monkey_setup v2.3.0 虽实现 PROGRESS，但 #138 push 回调缺陷到 v2.3.1
    # 才修复，因此从 v2.3.1 起强制声明；flash_firmware v1.1.0 起声明。
    "monkey_setup": (2, 3, 1),
    "flash_firmware": (1, 1, 0),
    # #1690 打戳收敛：首批三脚本 + 第二批五脚本，从各自引入打戳的版本起
    # 必须声明 progress_stamps（新版本漏放 capabilities.json 时此测试红掉）。
    "gpu_setup": (1, 1, 0),
    "powercycle_setup": (1, 1, 0),
    "fill_storage": (1, 1, 0),
    "push_resources": (1, 1, 0),
    "monkey_resource_push": (1, 1, 0),
    "install_apk": (1, 1, 0),
    "monkey_launch": (5, 1, 0),
    "clean_env": (1, 1, 0),
}


def _latest_versions() -> dict[str, str]:
    doc = json.loads((Path(__file__).resolve().parents[3] / "tool_manifest.json").read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for name, tool in doc["tools"].items():
        live = [e for e in tool["versions"] if e.get("python") is None and not e.get("retired")]
        if live:
            out[name] = max((str(e["version"]) for e in live), key=_version_tuple)
    return out


def test_repo_family_trees_declare_progress_capability_per_threshold():
    """族树（每族最新版本）的能力声明与阈值规则一致（ADR-0051 Phase 3：版本目录已退役，
    历史版本的声明冻结在包里，由 sync 从包内 capabilities.json 登记，不再从树上枚举）。"""
    repo_scripts = Path(__file__).resolve().parents[3] / "backend" / "agent" / "scripts"
    latest = _latest_versions()
    checked = 0
    for name, threshold in _PROGRESS_THRESHOLDS.items():
        tree = repo_scripts / name
        if not tree.is_dir() or name not in latest:
            continue
        should_declare = _version_tuple(latest[name]) >= threshold
        declared = "progress_stamps" in read_capabilities(tree)
        assert declared == should_declare, f"{name}@{latest[name]}: declared={declared}, expected={should_declare}"
        checked += 1
    assert checked > 0
