"""flash_firmware v1.4.1（#3892 推广）：路由表补 MLD-NX3。"""
import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "flash_firmware" / "flash_firmware.py"


def _load():
    spec = importlib.util.spec_from_file_location("flash_firmware_v141", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_nx3_routes_to_mld_family_both_spellings():
    ff = _load()
    assert ff._MODEL_FAMILY_ROUTES["MLD_NX3"] == "MLD"
    assert ff._MODEL_FAMILY_ROUTES["MLD-NX3"] == "MLD"


def test_existing_routes_unchanged():
    ff = _load()
    for model in ("MLD_LX2", "MLD-LX2", "MLD_LX3", "MLD-LX3", "MLD_NX2", "MLD-NX2"):
        assert ff._MODEL_FAMILY_ROUTES[model] == "MLD"
    for model in ("ELA_LX2", "ELA-LX2", "ELA_LX3", "ELA-LX3"):
        assert ff._MODEL_FAMILY_ROUTES[model] == "ELA"
