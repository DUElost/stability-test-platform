import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

from .aimonkey_aee import AEEEntry

LOG_PATTERNS = {
    "ANR": re.compile(r"\bANR\b", re.IGNORECASE),
    "CRASH": re.compile(r"FATAL EXCEPTION|CRASH", re.IGNORECASE),
    "WATCHDOG": re.compile(r"watchdog", re.IGNORECASE),
}


def _tail_lines(path: Path, limit: int = 1500) -> List[str]:
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as fh:
            lines = fh.readlines()
        return [line.rstrip("\n") for line in lines[-limit:]]
    except OSError:
        return []


def _parse_restart_count(summary: str) -> int:
    values = [int(v) for v in re.findall(r"restart\s+(\d+)", summary or "", re.IGNORECASE)]
    return max(values) if values else 0


def _detect_keyword_events(lines: Iterable[str]) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    for line in lines:
        sample = line.strip()
        if not sample:
            continue
        for event_type, pattern in LOG_PATTERNS.items():
            if pattern.search(sample):
                severity = "HIGH" if event_type in {"ANR", "CRASH"} else "MEDIUM"
                events.append(
                    {
                        "type": event_type,
                        "severity": severity,
                        "source": "logcat",
                        "message": sample[:400],
                    }
                )
                break
    return events


def _from_aee_entries(entries: Sequence[AEEEntry]) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    for entry in entries:
        severity = "HIGH" if entry.event_type in {"ANR", "CRASH"} else "MEDIUM"
        events.append(
            {
                "type": entry.event_type,
                "severity": severity,
                "source": entry.source,
                "message": entry.name,
            }
        )
    return events


def build_risk_summary(
    monitor_summary: str,
    logcat_path: Path,
    aee_entries: Sequence[AEEEntry],
    restart_warn_threshold: int = 1,
) -> Dict[str, Any]:
    log_lines = _tail_lines(logcat_path)
    events = _detect_keyword_events(log_lines)
    events.extend(_from_aee_entries(aee_entries))

    restart_count = _parse_restart_count(monitor_summary)
    if restart_count > restart_warn_threshold:
        events.append(
            {
                "type": "RESTART",
                "severity": "MEDIUM",
                "source": "monitor",
                "message": f"monkey restarted {restart_count} times",
            }
        )
    if "monkey died" in (monitor_summary or "").lower():
        events.append(
            {
                "type": "MONKEY_DIED",
                "severity": "HIGH",
                "source": "monitor",
                "message": monitor_summary,
            }
        )

    risk_level = "LOW"
    if any(item["severity"] == "HIGH" for item in events):
        risk_level = "HIGH"
    elif events:
        risk_level = "MEDIUM"

    by_type: Dict[str, int] = {}
    for event in events:
        event_type = str(event["type"])
        by_type[event_type] = by_type.get(event_type, 0) + 1

    summary: Dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "risk_level": risk_level,
        "monitor_summary": monitor_summary or "",
        "counts": {
            "events_total": len(events),
            "aee_entries": len(aee_entries),
            "restart_count": restart_count,
            "by_type": by_type,
        },
        "events": events,
    }
    return summary


def write_risk_summary(summary: Dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)
