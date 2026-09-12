"""#751 — e7f8 seed content_sha256 必须是入口脚本（非 _lib.py）。"""
from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
SEED = (
    BACKEND
    / "alembic/versions/e7f8a9b0c1d2_seed_gpu_power_sleep_resources_passthrough.py"
)
LIB_SHAS = {
    ("gpu_setup", "1.0.2"):
        "961f2f3929b26aae4213c879992c609bb71ae4ce88134125a74c88dce9043990",
    ("powercycle_setup", "1.0.1"):
        "38cb525fd5405b0c0f08c765a8d3a3bdbcea7aadf21c0733f59fedbfd9a960e3",
    ("sleep_setup", "1.0.1"):
        "c54ed4b371612e37af3954df07a5588a19adebc5edce74f6205b0f1557f2163e",
}


def _load_versions():
    spec = importlib.util.spec_from_file_location("e7f8_seed", SEED)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.VERSIONS


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_e7f8_seed_shas_match_entry_scripts_not_lib():
    for v in _load_versions():
        entry = (
            BACKEND
            / "agent/scripts"
            / v["name"]
            / f"v{v['ver']}"
            / f"{v['name']}.py"
        )
        lib = entry.with_name("_lib.py")
        assert entry.is_file(), entry
        assert lib.is_file(), lib
        entry_sha = _sha256(entry)
        lib_sha = _sha256(lib)
        assert v["sha"] == entry_sha, (
            f"{v['name']} v{v['ver']}: seed sha must be entry script, "
            f"got {v['sha'][:12]}… want {entry_sha[:12]}…"
        )
        assert v["sha"] != lib_sha
        assert LIB_SHAS[(v["name"], v["ver"])] == lib_sha
