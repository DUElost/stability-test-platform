"""#2865：pipeline 模板对关键脚本的 version pin 必须等于磁盘最新版。

背景：脚本执行按精确版本解析、无 latest 兜底；目录合入后若模板仍钉旧版，
新建 Plan 永远带不到修复（#2802 D0 / #2777 降级都曾因此「合入了但不生效」）。

本守卫只锁「已在模板里出现、且近期因零引用复发过」的脚本族；全仓所有脚本
一律追最新会误伤故意钉旧稳定版的步骤。名单扩张条件：再出现一次「版本已合
入、模板未钉、下一窗仍旧行为」的同形审计。
"""

from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = REPO_ROOT / "backend/schemas/pipeline_templates"
SCRIPTS_DIR = REPO_ROOT / "backend/agent/scripts"

# action → 脚本目录名。仅覆盖 #2865 点名的两族（模板里确有引用）。
PINNED_SCRIPTS: dict[str, str] = {
    "script:check_device": "check_device",
    "script:monkey_setup": "monkey_setup",
}


def _latest_on_disk(script_name: str) -> str:
    versions = []
    for path in (SCRIPTS_DIR / script_name).glob("v*"):
        if not path.is_dir():
            continue
        parts = path.name[1:].split(".")
        if not all(p.isdigit() for p in parts):
            continue
        versions.append((tuple(int(p) for p in parts), path.name[1:]))
    assert versions, f"no version dirs under {script_name}"
    versions.sort()
    return versions[-1][1]


def _iter_script_steps(obj: object):
    if isinstance(obj, dict):
        action = obj.get("action")
        if isinstance(action, str) and action in PINNED_SCRIPTS:
            yield action, obj.get("version")
        for value in obj.values():
            yield from _iter_script_steps(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from _iter_script_steps(item)


def test_pinned_template_scripts_track_latest_on_disk() -> None:
    expected = {action: _latest_on_disk(name) for action, name in PINNED_SCRIPTS.items()}
    template_files = sorted(TEMPLATES_DIR.glob("*.json"))
    assert template_files, "no pipeline templates"

    mismatches: list[str] = []
    seen: set[str] = set()
    for path in template_files:
        data = json.loads(path.read_text(encoding="utf-8"))
        for action, version in _iter_script_steps(data):
            seen.add(action)
            want = expected[action]
            if version != want:
                mismatches.append(f"{path.name}: {action} pin={version!r} latest={want!r}")

    assert not mismatches, (
        "pipeline 模板 pin 落后于磁盘最新版（#2865：合入新版本必须同批钉模板）:\n  "
        + "\n  ".join(mismatches)
    )
    missing = set(PINNED_SCRIPTS) - seen
    assert not missing, (
        f"守卫名单 {sorted(missing)} 在模板里已无引用——删名单项或恢复模板步骤，"
        "勿留空守卫"
    )


def test_guard_has_teeth_when_template_lags_disk() -> None:
    """变异自证：模板故意钉旧版时判据必须红。"""
    latest_check = _latest_on_disk("check_device")
    assert latest_check != "0.0.0"
    poisoned = {"lifecycle": {"init": [
        {"action": "script:check_device", "version": "0.0.0"},
    ]}}
    bad = [
        f"poison.json: script:check_device pin='0.0.0' latest={latest_check!r}"
        for action, version in _iter_script_steps(poisoned)
        if version != _latest_on_disk(PINNED_SCRIPTS[action])
    ]
    assert bad, "变异未命中：判据与模板遍历已脱钩"
