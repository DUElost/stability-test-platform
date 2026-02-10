from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .adb_wrapper import AdbError, AdbWrapper

DEFAULT_AEE_PATHS: Sequence[Tuple[str, str]] = (
    ("/data/aee_exp", "aee_exp"),
    ("/data/vendor/aee_exp", "aee_exp_vendor"),
)


@dataclass(frozen=True)
class AEEEntry:
    source: str
    name: str
    remote_path: str
    event_type: str


def _infer_event_type(name: str) -> str:
    upper = (name or "").upper()
    if "ANR" in upper:
        return "ANR"
    if "CRASH" in upper or "FATAL" in upper:
        return "CRASH"
    if "JE" in upper:
        return "JAVA_EXCEPTION"
    if "NE" in upper:
        return "NATIVE_EXCEPTION"
    if "SWT" in upper:
        return "WATCHDOG"
    return "AEE"


def _list_remote_dirnames(adb: AdbWrapper, serial: str, remote_base: str) -> List[str]:
    cmd = [
        "sh",
        "-c",
        (
            f'for p in "{remote_base}"/*; do '
            '[ -d "$p" ] && basename "$p"; '
            "done 2>/dev/null || true"
        ),
    ]
    result = adb.shell(serial, cmd)
    names: List[str] = []
    for raw in (result.stdout or "").splitlines():
        name = raw.strip()
        if not name:
            continue
        names.append(name)
    return sorted(set(names))


def scan_aee_entries(
    adb: AdbWrapper,
    serial: str,
    path_map: Sequence[Tuple[str, str]] = DEFAULT_AEE_PATHS,
) -> List[AEEEntry]:
    entries: List[AEEEntry] = []
    for remote_base, source in path_map:
        try:
            names = _list_remote_dirnames(adb, serial, remote_base)
        except AdbError:
            continue
        for name in names:
            entries.append(
                AEEEntry(
                    source=source,
                    name=name,
                    remote_path=f"{remote_base.rstrip('/')}/{name}",
                    event_type=_infer_event_type(name),
                )
            )
    entries.sort(key=lambda item: (item.source, item.name))
    return entries


def pull_aee_entries(
    adb: AdbWrapper,
    serial: str,
    log_dir: str,
    entries: Sequence[AEEEntry],
) -> List[Dict[str, Any]]:
    root = Path(log_dir)
    records: List[Dict[str, Any]] = []
    for entry in entries:
        source_dir = root / entry.source
        source_dir.mkdir(parents=True, exist_ok=True)
        local_dir = source_dir / entry.name
        record: Dict[str, Any] = {
            "source": entry.source,
            "name": entry.name,
            "event_type": entry.event_type,
            "remote_path": entry.remote_path,
            "local_path": str(local_dir),
            "pulled": False,
            "error": None,
        }
        try:
            adb.pull(serial, entry.remote_path, str(local_dir))
            record["pulled"] = True
        except AdbError as exc:
            record["error"] = str(exc)
        records.append(record)
    return records


def scan_and_pull_aee_entries(
    adb: AdbWrapper,
    serial: str,
    log_dir: str,
    entries: Optional[Sequence[AEEEntry]] = None,
) -> Tuple[List[AEEEntry], List[Dict[str, Any]]]:
    scanned = list(entries) if entries is not None else scan_aee_entries(adb, serial)
    pulled = pull_aee_entries(adb, serial, log_dir, scanned)
    return scanned, pulled
