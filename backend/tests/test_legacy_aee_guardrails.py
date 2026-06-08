from backend.agent.registry import script_registry
from backend.api.routes import plans, scripts
from backend.services import script_catalog


def test_legacy_aee_script_names_use_single_shared_source():
    from backend.core.legacy_aee import LEGACY_AEE_SCRIPT_NAMES

    assert plans._LEGACY_AEE_SCRIPT_NAMES is LEGACY_AEE_SCRIPT_NAMES
    assert scripts._LEGACY_AEE_SCRIPT_NAMES is LEGACY_AEE_SCRIPT_NAMES
    assert script_catalog._LEGACY_AEE_SCRIPT_NAMES is LEGACY_AEE_SCRIPT_NAMES
    assert script_registry._LEGACY_AEE_SCRIPT_NAMES is LEGACY_AEE_SCRIPT_NAMES
