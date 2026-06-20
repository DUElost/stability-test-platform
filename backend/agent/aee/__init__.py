"""AEE log collection — aligned with monolithic MonkeyAEEinfo (D1).

Exports db_history incremental pull, mobilelog correlation, and bugreport export.
Does not include aee_extract decryption (out of scope for D1).
"""

from .processor import process_device_logs
from .paths import get_aee_nfs_root, get_aee_local_root, resolve_device_output_dir

__all__ = [
    "process_device_logs",
    "get_aee_nfs_root",
    "get_aee_local_root",
    "resolve_device_output_dir",
]
