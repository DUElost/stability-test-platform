"""Patrol script: incremental db_history diff + AEE pull + mobilelog/bugreport correlation.

Aligned with monolithic MonkeyAEEinfo process_device_logs (D1).
Does NOT run aee_extract decryption.

Environment:
    STP_DEVICE_SERIAL, STP_ADB_PATH, STP_STEP_PARAMS, STP_JOB_ID
    STP_AGENT_STATE_DB  — agent_state SQLite path (incremental state)
    STP_AEE_NFS_ROOT / STP_WATCHER_NFS_BASE_DIR / STP_NFS_ROOT

STP_STEP_PARAMS:
{
    "filter_db_logs": false,
    "whitelist_file": "",
    "aee_paths": ["/data/aee_exp", "/data/vendor/aee_exp"],
    "export_mobilelog": true,
    "export_bugreport": true,
    "state_key_prefix": "scan_aee"
}
"""

from __future__ import annotations

import sys
from pathlib import Path

from _adb import device_serial, output_result, params


def _bootstrap_import():
    install = Path(__file__).resolve().parents[4]
    if str(install) not in sys.path:
        sys.path.insert(0, str(install))
    deploy = Path(__file__).resolve().parents[3]
    if deploy.name != "backend" and str(deploy) not in sys.path:
        sys.path.insert(0, str(deploy))


def main() -> None:
    _bootstrap_import()
    from agent.aee.processor import ProcessConfig, process_device_logs
    from agent.aee.state_store import ScriptStateStore

    serial = device_serial()
    args = params()
    job_id = int(__import__("os").environ.get("STP_JOB_ID", "0"))

    whitelist = set()
    whitelist_file = (args.get("whitelist_file") or "").strip()
    if whitelist_file:
        try:
            with open(whitelist_file, encoding="utf-8") as f:
                whitelist = {ln.strip() for ln in f if ln.strip()}
        except OSError as exc:
            output_result(False, error_message=f"whitelist_unreadable: {exc}")
            sys.exit(1)

    try:
        store = ScriptStateStore()
    except ValueError as exc:
        output_result(False, error_message=str(exc))
        sys.exit(1)

    cfg = ProcessConfig(
        aee_paths=args.get("aee_paths") or ProcessConfig().aee_paths,
        filter_db_logs=bool(args.get("filter_db_logs", False)),
        whitelist=whitelist or None,
        export_mobilelog=bool(args.get("export_mobilelog", True)),
        export_bugreport=bool(args.get("export_bugreport", True)),
        state_key_prefix=str(args.get("state_key_prefix") or "scan_aee"),
    )

    result = process_device_logs(
        serial=serial,
        job_id=job_id,
        state_store=store,
        config=cfg,
    )

    metrics = {
        "scanned": result.scanned,
        "pulled": result.pulled,
        "skipped_known": result.skipped_known,
        "new_timestamps": result.new_timestamps,
        "errors": result.errors[:20],
    }
    output_result(True, metrics=metrics)


if __name__ == "__main__":
    main()
