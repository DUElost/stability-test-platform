"""Lightweight script classification tags for UI filtering (#4182 Phase 2).

Stored outside script release trees (same decoupling pattern as ``param_docs``
in ADR-0060 D2): updating UI filter tags never mutates published script packages
or requires a database migration.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


VALID_SCRIPT_ROLES = frozenset({
    "common_init",
    "specialty_init",
    "patrol",
    "teardown",
    "ops",
})


@dataclass(frozen=True)
class ScriptTagSpec:
    role: str
    specialties: tuple[str, ...] = ()
    platforms: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "specialties": list(self.specialties),
            "platforms": list(self.platforms),
        }


# Script family -> UI filtering metadata.
# Empty specialties means cross-specialty; empty platforms means universal.
SCRIPT_TAG_REGISTRY: dict[str, ScriptTagSpec] = {
    # Common init prerequisites
    "check_device": ScriptTagSpec(role="common_init"),
    "sync_device_time": ScriptTagSpec(role="common_init"),
    "ensure_root": ScriptTagSpec(role="common_init"),
    "unisoc_sysdump_config": ScriptTagSpec(role="common_init", platforms=("unisoc",)),
    "log_prepare": ScriptTagSpec(role="common_init"),
    "aee_prepare": ScriptTagSpec(role="common_init", platforms=("mtk",)),
    "clean_env": ScriptTagSpec(role="common_init"),
    "clear_recents": ScriptTagSpec(role="common_init"),
    "connect_wifi": ScriptTagSpec(role="common_init"),
    "fill_storage": ScriptTagSpec(role="common_init"),
    "install_apk": ScriptTagSpec(role="common_init"),
    "oobe_skip": ScriptTagSpec(role="common_init"),
    "push_resources": ScriptTagSpec(role="common_init"),
    # Monkey
    "monkey_setup": ScriptTagSpec(role="specialty_init", specialties=("monkey",)),
    "monkey_resource_push": ScriptTagSpec(role="specialty_init", specialties=("monkey",)),
    "monkey_launch": ScriptTagSpec(role="specialty_init", specialties=("monkey",)),
    "monkey_test": ScriptTagSpec(role="specialty_init", specialties=("monkey",)),
    "monkey_check": ScriptTagSpec(role="patrol", specialties=("monkey",)),
    "monkey_teardown": ScriptTagSpec(role="teardown", specialties=("monkey",)),
    "stop_aimonkey": ScriptTagSpec(role="teardown", specialties=("monkey",)),
    # MTBF
    "mtbf_setup": ScriptTagSpec(role="specialty_init", specialties=("mtbf",)),
    "mtbf_check": ScriptTagSpec(role="patrol", specialties=("mtbf",)),
    "mtbf_finish": ScriptTagSpec(role="teardown", specialties=("mtbf",)),
    # Power cycle
    "powercycle_setup": ScriptTagSpec(role="specialty_init", specialties=("power-cycle",)),
    "powercycle_check": ScriptTagSpec(role="patrol", specialties=("power-cycle",)),
    "powercycle_disarm": ScriptTagSpec(role="teardown", specialties=("power-cycle",)),
    "powercycle_finish": ScriptTagSpec(role="teardown", specialties=("power-cycle",)),
    # GPU
    "gpu_setup": ScriptTagSpec(role="specialty_init", specialties=("gpu",)),
    "gpu_check": ScriptTagSpec(role="patrol", specialties=("gpu",)),
    "gpu_disarm": ScriptTagSpec(role="teardown", specialties=("gpu",)),
    "gpu_finish": ScriptTagSpec(role="teardown", specialties=("gpu",)),
    # Sleep
    "sleep_setup": ScriptTagSpec(role="specialty_init", specialties=("sleep",)),
    "sleep_check": ScriptTagSpec(role="patrol", specialties=("sleep",)),
    "sleep_finish": ScriptTagSpec(role="teardown", specialties=("sleep",)),
    # Aging DDR (mapped to ops until dedicated specialty is seeded)
    "aging_ddr_setup": ScriptTagSpec(role="specialty_init", specialties=("ops",)),
    "aging_ddr_check": ScriptTagSpec(role="patrol", specialties=("ops",)),
    "aging_ddr_disarm": ScriptTagSpec(role="teardown", specialties=("ops",)),
    "aging_ddr_finish": ScriptTagSpec(role="teardown", specialties=("ops",)),
    # Flashing & ops diagnostics
    "flash_preflight": ScriptTagSpec(role="ops", specialties=("ops",), platforms=("mtk",)),
    "flash_firmware": ScriptTagSpec(role="ops", specialties=("ops",), platforms=("mtk",)),
    "flash_preflight_unisoc": ScriptTagSpec(role="ops", specialties=("ops",), platforms=("unisoc",)),
    "flash_firmware_unisoc": ScriptTagSpec(role="ops", specialties=("ops",), platforms=("unisoc",)),
    "flash_firmware_unisoc_bsl": ScriptTagSpec(role="ops", specialties=("ops",), platforms=("unisoc",)),
    "aee_signal_trigger": ScriptTagSpec(role="ops", specialties=("ops",), platforms=("mtk",)),
    "unisoc_probe": ScriptTagSpec(role="ops", specialties=("ops",), platforms=("unisoc",)),
    "unisoc_signal_trigger": ScriptTagSpec(role="ops", specialties=("ops",), platforms=("unisoc",)),
    "noop": ScriptTagSpec(role="ops", specialties=("ops",)),
}

_DEFAULT_TAG = ScriptTagSpec(role="ops")


def get_script_tags(script_name: str) -> dict[str, Any]:
    """Return UI filtering tags for a script family name."""
    spec = SCRIPT_TAG_REGISTRY.get(script_name, _DEFAULT_TAG)
    return spec.to_dict()
