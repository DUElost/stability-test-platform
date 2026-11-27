import json

import pytest
from fastapi import HTTPException

import backend.api.routes.pipeline as pipeline_routes
from backend.api.routes.pipeline import TEMPLATES_DIR, _load_template
from backend.agent.contracts.pipeline_validator import validate_pipeline_def


def test_builtin_pipeline_templates_do_not_use_legacy_phases():
    template_files = sorted(TEMPLATES_DIR.glob("*.json"))
    assert template_files, "no builtin pipeline templates found"

    for path in template_files:
        template = _load_template(path)
        pipeline_def = template.pipeline_def

        assert "phases" not in pipeline_def, f"{path.name} should not contain legacy phases"


def test_builtin_pipeline_templates_follow_current_validator():
    template_files = sorted(TEMPLATES_DIR.glob("*.json"))
    assert template_files, "no builtin pipeline templates found"

    for path in template_files:
        template = _load_template(path)
        pipeline_def = template.pipeline_def
        is_valid, errors = validate_pipeline_def(pipeline_def)
        assert is_valid, f"{path.name} should be valid: {errors}"
        assert "lifecycle" in pipeline_def


def test_monkey_watcher_patrol_template_is_watcher_only_public_variant():
    data = json.loads((TEMPLATES_DIR / "monkey_watcher_patrol.json").read_text(encoding="utf-8"))
    patrol_steps = data["lifecycle"]["patrol"]["steps"]
    step_ids = [str(step.get("step_id") or "") for step in patrol_steps]
    actions = [str(step.get("action") or "") for step in patrol_steps]

    assert data["version"] == 2
    assert "AEE" not in data["description"]
    assert step_ids == ["monkey_check"]
    assert "script:scan_aee" not in actions
    assert "script:export_mobilelogs" not in actions


def test_legacy_aee_template_alias_files_removed_from_repo():
    removed = (
        "aimonkey.json",
        "aimonkey_launcher_lifecycle.json",
        "monkey_aee.json",
        "monkey_aee_patrol.json",
        "monkey_aee_init.json",
        "monkey_aee_lifecycle.json",
        "monkey_aee_teardown.json",
    )
    for name in removed:
        assert not (TEMPLATES_DIR / name).exists(), f"{name} should be removed after watcher 收口"


def test_list_pipeline_templates_filters_legacy_alias_files_even_if_reintroduced(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(pipeline_routes, "TEMPLATES_DIR", tmp_path)

    (tmp_path / "monkey_watcher_patrol.json").write_text(
        json.dumps({"description": "Watcher", "lifecycle": {"init": []}}),
        encoding="utf-8",
    )
    (tmp_path / "monkey_aee_patrol.json").write_text(
        json.dumps({"description": "Legacy", "lifecycle": {"init": []}}),
        encoding="utf-8",
    )

    templates = pipeline_routes.list_pipeline_templates()

    assert [template.name for template in templates] == ["monkey_watcher_patrol"]


def test_get_pipeline_template_rejects_legacy_alias_even_if_file_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline_routes, "TEMPLATES_DIR", tmp_path)
    (tmp_path / "monkey_aee_patrol.json").write_text(
        json.dumps({"description": "Legacy", "lifecycle": {"init": []}}),
        encoding="utf-8",
    )

    with pytest.raises(HTTPException) as excinfo:
        pipeline_routes.get_pipeline_template("monkey_aee_patrol")

    assert excinfo.value.status_code == 404


def test_get_pipeline_template_rejects_path_traversal_like_names(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline_routes, "TEMPLATES_DIR", tmp_path)
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "b.json").write_text(
        json.dumps({"description": "Nested", "lifecycle": {"init": []}}),
        encoding="utf-8",
    )
    for name in ("..", "../secret", "a/b", "a\\b", "bad name"):
        with pytest.raises(HTTPException) as excinfo:
            pipeline_routes.get_pipeline_template(name)
        assert excinfo.value.status_code == 404


def test_stability_templates_pin_sync_device_time_after_check_device():
    template_files = sorted(TEMPLATES_DIR.glob("*.json"))
    stability_templates = [p for p in template_files if not p.name.startswith("flash_")]
    flash_templates = [p for p in template_files if p.name.startswith("flash_")]
    assert stability_templates

    for path in stability_templates:
        data = json.loads(path.read_text(encoding="utf-8"))
        init = data["lifecycle"]["init"]
        assert len(init) >= 2, f"{path.name} should include check_device and sync_device_time"
        assert init[0]["action"] == "script:check_device"
        assert init[1]["step_id"] == "sync_device_time"
        assert init[1]["action"] == "script:sync_device_time"
        assert init[1]["version"] == "1.0.1"
        assert init[1]["params"].get("timezone") == "Asia/Shanghai"

    for path in flash_templates:
        data = json.loads(path.read_text(encoding="utf-8"))
        actions = [s.get("action") for s in data["lifecycle"]["init"]]
        assert "script:sync_device_time" not in actions, (
            f"flash template {path.name} should not force sync_device_time before flashing"
        )


def test_stability_templates_pin_unisoc_sysdump_config_after_sync_device_time():
    template_files = sorted(TEMPLATES_DIR.glob("*.json"))
    stability_templates = [p for p in template_files if not p.name.startswith("flash_")]
    flash_templates = [p for p in template_files if p.name.startswith("flash_")]
    assert stability_templates

    for path in stability_templates:
        data = json.loads(path.read_text(encoding="utf-8"))
        init = data["lifecycle"]["init"]
        assert len(init) >= 3, (
            f"{path.name} should include check_device, sync_device_time, and unisoc_sysdump_config"
        )
        assert init[2]["step_id"] == "unisoc_sysdump_config"
        assert init[2]["action"] == "script:unisoc_sysdump_config"
        assert init[2]["version"] == "1.0.0"
        assert init[2]["params"].get("enabled") is True

    for path in flash_templates:
        data = json.loads(path.read_text(encoding="utf-8"))
        actions = [s.get("action") for s in data["lifecycle"]["init"]]
        assert "script:unisoc_sysdump_config" not in actions, (
            f"flash template {path.name} should not enable sysdump before flashing"
        )


def test_builtin_templates_have_explicit_specialty_mapping_and_metadata():
    templates = pipeline_routes.list_pipeline_templates(specialty_key=None)
    by_name = {tpl.name: tpl for tpl in templates}
    assert len(by_name) == 11

    for tpl in templates:
        assert tpl.display_name.strip(), f"{tpl.name} missing display_name"
        assert tpl.specialty_key in pipeline_routes.KNOWN_SPECIALTY_KEYS, (
            f"{tpl.name} specialty_key={tpl.specialty_key!r} not in KNOWN_SPECIALTY_KEYS"
        )
        assert tpl.status in ("ready", "skeleton")

    # Explicit specialty -> template mappings (#4182 Phase 2)
    assert by_name["powercycle"].specialty_key == "power-cycle"
    assert by_name["monkey"].specialty_key == "monkey"
    assert by_name["monkey_watcher_patrol"].specialty_key == "monkey"
    assert by_name["monkey_watcher_patrol"].plan_defaults == {
        "watcher_policy": {"enabled": True},
    }
    assert by_name["mtbf"].specialty_key == "mtbf"
    assert by_name["mtbf"].requires_suite is True
    assert by_name["ddr"].status == "skeleton"
    assert by_name["standby"].status == "skeleton"

    # Optional specialty_key query filter
    monkey_only = pipeline_routes.list_pipeline_templates(specialty_key="monkey")
    assert {tpl.name for tpl in monkey_only} == {"monkey", "monkey_watcher_patrol"}
    power_only = pipeline_routes.list_pipeline_templates(specialty_key="power-cycle")
    assert [tpl.name for tpl in power_only] == ["powercycle"]


def test_mtbf_template_contains_complete_lifecycle_steps():
    tpl = pipeline_routes.get_pipeline_template("mtbf")
    lc = tpl.pipeline_def["lifecycle"]
    assert lc["timeout_seconds"] == 604800
    assert [s["action"] for s in lc["init"]] == [
        "script:check_device",
        "script:sync_device_time",
        "script:unisoc_sysdump_config",
        "script:ensure_root",
        "script:mtbf_setup",
    ]
    assert lc["init"][-1]["version"] == "1.4.2"
    assert lc["patrol"]["interval_seconds"] == 300
    assert [(s["action"], s["version"]) for s in lc["patrol"]["steps"]] == [
        ("script:mtbf_check", "1.5.0"),
    ]
    assert [(s["action"], s["version"]) for s in lc["teardown"]] == [
        ("script:mtbf_finish", "1.6.0"),
    ]


def test_script_tag_registry_covers_all_template_scripts_with_valid_metadata():
    from backend.services.script_tags import (
        SCRIPT_TAG_REGISTRY,
        VALID_SCRIPT_ROLES,
        get_script_tags,
    )

    for name, spec in SCRIPT_TAG_REGISTRY.items():
        assert spec.role in VALID_SCRIPT_ROLES, f"{name}: invalid role {spec.role!r}"
        for sp in spec.specialties:
            assert sp in pipeline_routes.KNOWN_SPECIALTY_KEYS, (
                f"{name}: unknown specialty {sp!r}"
            )

    template_scripts: set[str] = set()
    for tpl in pipeline_routes.list_pipeline_templates(specialty_key=None):
        lc = tpl.pipeline_def["lifecycle"]
        for step in (
            list(lc.get("init") or [])
            + list((lc.get("patrol") or {}).get("steps") or [])
            + list(lc.get("teardown") or [])
        ):
            action = str(step.get("action") or "")
            if action.startswith("script:"):
                template_scripts.add(action[len("script:"):])

    missing = sorted(template_scripts - set(SCRIPT_TAG_REGISTRY.keys()))
    assert not missing, f"template scripts missing from SCRIPT_TAG_REGISTRY: {missing}"

    fallback = get_script_tags("nonexistent_custom_script")
    assert fallback == {"role": "ops", "specialties": [], "platforms": []}
