import os
import shutil
from typing import Any, Dict, Iterable


def check_mounts(
    mount_points: Iterable[str],
    min_free_gb: float = 1.0,
) -> Dict[str, Dict[str, Any]]:
    results: Dict[str, Dict[str, Any]] = {}
    for path in mount_points:
        entry: Dict[str, Any] = {"path": path}
        if not os.path.exists(path):
            entry.update({"ok": False, "reason": "not_found"})
        elif not os.path.ismount(path):
            entry.update({"ok": False, "reason": "not_mounted"})
        else:
            usage = shutil.disk_usage(path)
            free_gb = usage.free / (1024**3)
            entry.update(
                {
                    "ok": free_gb >= min_free_gb,
                    "free_gb": round(free_gb, 3),
                    "total_gb": round(usage.total / (1024**3), 3),
                }
            )
        results[path] = entry
    return results
