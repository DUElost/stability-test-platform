from backend.agent.registry import script_registry
from backend.api.routes import plans, scripts
from backend.services import script_catalog


def test_legacy_aee_script_names_use_single_shared_source():
    from backend.core.legacy_aee import LEGACY_AEE_SCRIPT_NAMES

    assert plans.LEGACY_AEE_SCRIPT_NAMES is LEGACY_AEE_SCRIPT_NAMES
    assert scripts.LEGACY_AEE_SCRIPT_NAMES is LEGACY_AEE_SCRIPT_NAMES
    assert script_catalog.LEGACY_AEE_SCRIPT_NAMES is LEGACY_AEE_SCRIPT_NAMES
    assert script_registry.LEGACY_AEE_SCRIPT_NAMES is LEGACY_AEE_SCRIPT_NAMES

    assert "_LEGACY_AEE_SCRIPT_NAMES" not in plans.__dict__
    assert "_LEGACY_AEE_SCRIPT_NAMES" not in scripts.__dict__
    assert "_LEGACY_AEE_SCRIPT_NAMES" not in script_catalog.__dict__
    assert "_LEGACY_AEE_SCRIPT_NAMES" not in script_registry.__dict__
