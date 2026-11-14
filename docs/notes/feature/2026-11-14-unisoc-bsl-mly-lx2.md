# Agent Note: Generalize Unisoc BSL Flash Script for MLY-LX2 (P525AA)

Status: implemented
Class: feature

## Decision

- Extend [`tools/dev/prepare_unisoc_firmware.py`](../../../tools/dev/prepare_unisoc_firmware.py) so `parse_scheme()` preserves non-zero `Block/Base` load addresses and `build_plan()` records top-level `fdl` (`fdl1`/`fdl2` file + base) and `erases` (`EraseFlash2` target partitions) inside `images/plan.json` while retaining existing `entries[]` behavior.
- Generalize [`flash_firmware_unisoc_bsl.py`](../../../backend/agent/scripts/flash_firmware_unisoc_bsl/flash_firmware_unisoc_bsl.py) (`1.0.1` -> `1.0.2`, registered in [`tool_manifest.json`](../../../tool_manifest.json) and pinned in [`flash_unisoc_bsl.json`](../../../backend/schemas/pipeline_templates/flash_unisoc_bsl.json)):
  1. Resolve FDL binaries/load addresses, Scheme 2 `erase_part` targets, `userdata`, and writable partitions dynamically from `images/plan.json`, falling back to legacy `P532AA` defaults when `plan.json` omits them.
  2. Expand `is_protected_partition()` to cover 4G LTE NV partitions (`l_fixnv*`, `l_runtimenv*`, `l_deltanv*`) in addition to 5G NR/calibration partitions (`nr_*nv*`, `prodnv`, `calinv`, `miscdata`, `persist`, `splloader`), and block any protected partition from both `erase_part` and `write_part`.
  3. Isolate local SSD cache directories per firmware path (`<cache_dir>/<fw_label>_<sha256[:8]>/`) and determine `super.img` fallback trim size from `os.path.getsize(src_super)` instead of a hardcoded `P532AA` byte constant.

## Alternatives

1. **Fastboot / bootloader unlock path**: Rejected for unattended automation because `MLY-LX2` ships locked (`ro.boot.flash.locked=1`), `fastboot flashing get_unlock_ability` is unimplemented, and LK bootloader unlock requires physical key confirmation plus `lock_token`.
2. **Fork a separate `flash_firmware_unisoc_bsl_p525aa` script family**: Rejected to avoid duplicating host-lock serialization, progress heartbeats, sparse trimming, and cold-boot/root verification logic across SoCs; reading `plan.json` handles `ums9632` (`P532AA`) and `ums9230` (`P525AA`) cleanly through one contract.

## Verification

- Verified full Scheme 2 BSL flash, cold boot (`sys.boot_completed=1`, `uid=0(root)`), NV/calibration partition SHA-256 preservation (`persist`, `calinv`, `l_fixnv1_a`, `l_fixnv2_a`, `spl_a`), and `oobe_skip.py` end-to-end on a live `MLY-LX2` device (`ums9230`).
- Added unit tests in [`tests/test_prepare_unisoc_firmware.py`](../../../tests/test_prepare_unisoc_firmware.py) and [`backend/agent/tests/test_flash_firmware_unisoc_bsl.py`](../../../backend/agent/tests/test_flash_firmware_unisoc_bsl.py) covering FDL/erase XML extraction, LTE+NR NV partition protection, `P525AA` `plan.json`-driven `spd_dump` command generation, and isolated multi-firmware SSD caching.
- Verified manifest and pin invariants via `tools/dev/check_script_packages.py`, `tools/dev/check_tool_manifest.py --base origin/main`, and `tests/test_pipeline_template_script_pins_2865.py`.

## Revisit

- If a future Unisoc PAC carries custom erase targets or dual-slot NV names outside `nr_*nv*` / `l_*nv*`, update `PROTECTED_NV_PARTITIONS` / `_PROTECTED_NV_PREFIXES` and `PRESERVE_IDS` together before staging the firmware.
- After merging this PR, run `tools/dev/check_script_packages.py --publish flash_firmware_unisoc_bsl 1.0.2` on the production server and trigger `POST /api/v1/scripts/scan` so agents pull `1.0.2` automatically.
