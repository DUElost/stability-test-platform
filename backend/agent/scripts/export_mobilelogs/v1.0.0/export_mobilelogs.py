"""Patrol script: export mobilelogs correlated with AEE timestamps from upstream scan_aee.

Reads new_timestamps from STP_SHARED_METRICS[timestamps_from_step] (default scan_aee).
"""

from __future__ import annotations

import sys
from pathlib import Path

from _adb import device_serial, output_result, params, shared_metrics


def _bootstrap_import():
    install = Path(__file__).resolve().parents[4]
    if str(install) not in sys.path:
        sys.path.insert(0, str(install))
    deploy = Path(__file__).resolve().parents[3]
    if deploy.name != "backend" and str(deploy) not in sys.path:
        sys.path.insert(0, str(deploy))


def main() -> None:
    _bootstrap_import()
    from agent.aee.folder_name import get_aee_log_folder_name, make_getprop_from_shell
    from agent.aee.mobilelog import export_correlated_mobilelogs, make_adb_pull_fn, make_adb_shell_fn
    from agent.aee.paths import get_aee_nfs_root, get_or_create_run_date_stamp, resolve_device_output_dir
    from agent.aee.state_store import ScriptStateStore

    serial = device_serial()
    args = params()
    job_id = int(__import__("os").environ.get("STP_JOB_ID", "0"))
    from_step = str(args.get("timestamps_from_step") or "scan_aee")

    upstream = shared_metrics().get(from_step) or {}
    timestamps = upstream.get("new_timestamps") or args.get("timestamps") or []
    if not timestamps:
        output_result(True, metrics={"matched": 0, "pulled": 0, "unmatched_timestamps": []})
        return

    try:
        store = ScriptStateStore()
    except ValueError as exc:
        output_result(False, error_message=str(exc))
        sys.exit(1)

    adb_path = __import__("os").environ.get("STP_ADB_PATH", "adb")
    shell_fn = make_adb_shell_fn(serial, adb_path)
    pull_fn = make_adb_pull_fn(serial, adb_path)
    stamp = get_or_create_run_date_stamp(store, job_id)
    folder_name = get_aee_log_folder_name(
        getprop=make_getprop_from_shell(lambda cmd, timeout: shell_fn(cmd, timeout) or ""),
        run_date_stamp=stamp,
    )
    if not folder_name:
        output_result(False, error_message="failed_to_resolve_aee_folder_name")
        sys.exit(1)

    base_dir = resolve_device_output_dir(
        nfs_root=get_aee_nfs_root(),
        folder_name=folder_name,
        serial=serial,
    )

    total_matched = 0
    total_pulled = 0
    unmatched = []
    remote_path = str(args.get("mobilelog_path") or "/data/debuglogger/mobilelog/")

    for ts in timestamps:
        metrics = export_correlated_mobilelogs(
            aee_ts_str=ts,
            output_dir=base_dir,
            remote_mobilelog_path=remote_path,
            shell_fn=shell_fn,
            pull_fn=pull_fn,
        )
        total_matched += metrics.get("matched", 0)
        total_pulled += metrics.get("pulled", 0)
        if metrics.get("files_selected", 0) == 0:
            unmatched.append(ts)

    output_result(
        True,
        metrics={
            "matched": total_matched,
            "pulled": total_pulled,
            "unmatched_timestamps": unmatched,
        },
    )


if __name__ == "__main__":
    main()
