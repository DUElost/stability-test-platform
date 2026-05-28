"""Device folder name generation — aligned with MonkeyAEEinfo._get_aee_log_folder_name."""

from __future__ import annotations

import logging
import re
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

ShellFn = Callable[[str, int], str]
GetpropFn = Callable[[str, int], str]


def get_aee_log_folder_name(
    *,
    getprop: GetpropFn,
    run_date_stamp: str,
) -> Optional[str]:
    """Build `{product}_{version}_..._{MMDD}_MonkeyAEEinfo` folder name."""
    try:
        product_name_prop = getprop("ro.product.name", 10).strip()
        display_id_prop = getprop("ro.build.display.id", 10).strip()
        version_incremental_id_prop = getprop("ro.build.version.incremental", 10).strip()
        version_release_prop = getprop("ro.build.version.release", 10).strip()

        if not product_name_prop or not display_id_prop:
            return None

        folder_part_1 = product_name_prop
        folder_part_2_prefix = ""
        timestamp_from_display = ""
        su_suffix = ""

        if "(" in display_id_prop:
            display_prefix = display_id_prop.split("(", 1)[0].strip()
            if display_prefix.startswith(f"{product_name_prop}-"):
                folder_part_2_prefix = display_prefix[len(product_name_prop) + 1 :].strip()
            elif "-" in display_prefix:
                folder_part_2_prefix = display_prefix.split("-", 1)[1].strip()

            match_post_paren = re.search(r"\)(.*)", display_id_prop)
            post_paren_content = match_post_paren.group(1).strip() if match_post_paren else ""
            all_six_digit_numbers = re.findall(r"\d{6}", post_paren_content)
            if all_six_digit_numbers:
                timestamp_from_display = all_six_digit_numbers[-1]
            su_suffix = (
                "SU" if "_SU" in post_paren_content else ("UD" if "_UD" in post_paren_content else "")
            )
        else:
            remaining_display = ""
            if display_id_prop.startswith(f"{product_name_prop}-"):
                remaining_display = display_id_prop[len(product_name_prop) + 1 :].strip()
            elif "-" in display_id_prop:
                remaining_display = display_id_prop.split("-", 1)[1].strip()

            if remaining_display:
                display_tokens = [t.strip() for t in remaining_display.split("-") if t.strip()]
                if version_release_prop and display_tokens and display_tokens[0] == version_release_prop:
                    folder_part_2_prefix = version_release_prop
                    if len(display_tokens) > 1:
                        timestamp_from_display = "-".join(display_tokens[1:])
                else:
                    folder_part_2_prefix = display_tokens[0]
                    if len(display_tokens) > 1:
                        timestamp_from_display = "-".join(display_tokens[1:])

            if not timestamp_from_display:
                build_tokens = re.findall(
                    r"\d{6}(?:V\d+)?",
                    " ".join(filter(None, [display_id_prop, version_incremental_id_prop])),
                    flags=re.IGNORECASE,
                )
                if build_tokens:
                    timestamp_from_display = build_tokens[-1]

            su_suffix_source = " ".join(filter(None, [display_id_prop, version_incremental_id_prop]))
            su_suffix = (
                "SU" if "_SU" in su_suffix_source else ("UD" if "_UD" in su_suffix_source else "")
            )

        if product_name_prop in ("ELA-LX2", "ELA-LX3") and version_incremental_id_prop:
            folder_part_1, folder_part_2_prefix = "", version_incremental_id_prop
            timestamp_from_display = ""

        if "(" not in display_id_prop and not folder_part_2_prefix and version_incremental_id_prop:
            folder_part_1, folder_part_2_prefix = "", version_incremental_id_prop
            timestamp_from_display = ""

        if folder_part_2_prefix == timestamp_from_display:
            timestamp_from_display = ""

        parts = [
            folder_part_1,
            folder_part_2_prefix,
            timestamp_from_display,
            su_suffix,
            run_date_stamp,
            "MonkeyAEEinfo",
        ]
        return "_".join(filter(None, parts))
    except Exception as exc:
        logger.error("get_aee_log_folder_name_failed: %s", exc)
        return None


def make_getprop_from_shell(shell_fn: ShellFn) -> GetpropFn:
    def _getprop(name: str, timeout: int = 10) -> str:
        return shell_fn(f"getprop {name}", timeout)

    return _getprop
