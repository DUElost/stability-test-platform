"""Log query endpoints: runtime log search and agent SSH log retrieval.

Extracted from tasks.py (Wave 8) — independent log functionality.
"""

import asyncio
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

import paramiko
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.api.schemas import AgentLogOut, AgentLogQuery
from backend.api.routes.auth import get_current_active_user, User, verify_agent_secret
from backend.core.database import get_db
from backend.core.ssh_security import (
    LOG_FILE_NOT_FOUND_MARKER,
    LOG_PATH_FORBIDDEN_MARKER,
    SshSecurityConfigError,
    build_remote_log_tail_command,
    create_ssh_client,
    resolve_host_ssh_credentials,
)
from backend.models.host import Host
from backend.services.host_updater import _resolve_ssh_creds

router = APIRouter(prefix="/api/v1", tags=["logs"])
logger = logging.getLogger(__name__)


def _parse_iso_timestamp(value: str) -> datetime:
    """Parse ISO timestamp while tolerating trailing `Z`."""
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)


def _read_log_file(path) -> List[str]:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.readlines()


_LOG_RE = re.compile(
    r"^(?P<ts>\S+)\s+\[(?P<level>\w+)\](?:\s+\[(?P<step_id>[^\]]*)\])?\s+(?P<msg>.*)$"
)


# ── Runtime Log Query ─────────────────────────────────────────────────────────


@router.get("/logs/query", response_model=Any)
async def query_runtime_logs(
    job_id: Optional[int] = Query(None, ge=1),
    job_ids: Optional[str] = Query(None, description="Comma-separated job ids"),
    level: Optional[str] = Query(None),
    q: Optional[str] = Query(None, description="Keyword search"),
    step_id: Optional[str] = Query(None),
    from_ts: Optional[str] = Query(None, description="ISO8601 start time"),
    to_ts: Optional[str] = Query(None, description="ISO8601 end time"),
    cursor: Optional[str] = Query(None, description="Line offset for pagination"),
    limit: int = Query(200, ge=20, le=1000),
    current_user: User = Depends(get_current_active_user),
):
    """Query runtime logs from persisted log files.

    Results are returned in chronological order (old -> new).
    ``cursor`` is a line offset (integer string) for pagination.
    """
    del current_user
    from backend.realtime.log_writer import LOG_BASE_DIR

    try:
        job_filter: Set[int] = set()
        if job_id:
            job_filter.add(job_id)
        if job_ids:
            for token in job_ids.split(","):
                token = token.strip()
                if not token:
                    continue
                try:
                    job_filter.add(int(token))
                except ValueError:
                    continue

        if not job_filter:
            return {"items": [], "next_cursor": None, "has_more": False, "scanned": 0}

        level_filter = (level or "").strip().upper()
        keyword = (q or "").strip().lower()
        step_filter = (step_id or "").strip().lower()

        from_dt: Optional[datetime] = None
        to_dt_dt: Optional[datetime] = None
        if from_ts:
            from_dt = _parse_iso_timestamp(from_ts.strip())
        if to_ts:
            to_dt_dt = _parse_iso_timestamp(to_ts.strip())

        offset = int(cursor) if cursor else 0
        items: List[Dict[str, Any]] = []
        total_scanned = 0

        for jid in sorted(job_filter):
            log_path = LOG_BASE_DIR / "jobs" / str(jid) / "console.log"
            if not log_path.exists():
                continue

            try:
                all_lines = await asyncio.to_thread(_read_log_file, log_path)
            except Exception:
                continue

            for idx, raw_line in enumerate(all_lines):
                if idx < offset:
                    continue
                total_scanned += 1
                m = _LOG_RE.match(raw_line.rstrip("\n"))
                if not m:
                    continue

                ts_text = m.group("ts")
                lvl = m.group("level").upper()
                step_val = m.group("step_id") or ""
                msg = m.group("msg")

                if level_filter and level_filter != "ALL" and lvl != level_filter:
                    continue
                if step_filter and step_filter not in step_val.lower():
                    continue
                if from_dt is not None or to_dt_dt is not None:
                    try:
                        ts_dt = _parse_iso_timestamp(ts_text)
                    except Exception:
                        continue
                    if from_dt is not None and ts_dt < from_dt:
                        continue
                    if to_dt_dt is not None and ts_dt > to_dt_dt:
                        continue
                if keyword:
                    haystack = f"{msg}\n{step_val}\n{jid}".lower()
                    if keyword not in haystack:
                        continue

                items.append({
                    "stream_id": str(idx),
                    "job_id": jid,
                    "step_id": step_val,
                    "level": lvl,
                    "timestamp": ts_text,
                    "message": msg,
                })
                if len(items) >= limit:
                    break

        next_cursor = str(offset + total_scanned) if len(items) >= limit else None
        return {
            "items": items,
            "next_cursor": next_cursor,
            "has_more": next_cursor is not None,
            "scanned": total_scanned,
        }
    except Exception as e:
        logger.warning("query_runtime_logs_failed: %s", e)
        raise HTTPException(status_code=500, detail="failed to query runtime logs")


# ── Agent SSH Log Query ───────────────────────────────────────────────────────


@router.post("/agent/logs", response_model=AgentLogOut)
def query_agent_logs(query: AgentLogQuery, db: Session = Depends(get_db), _: bool = Depends(verify_agent_secret)):
    """Query agent logs from a Linux host via SSH."""
    host = db.get(Host, query.host_id)
    if not host:
        raise HTTPException(status_code=404, detail="host not found")

    try:
        cmd = build_remote_log_tail_command(query.log_path, query.lines)
    except (ValueError, SshSecurityConfigError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    ssh_host = host.ip
    ssh_port = host.ssh_port or 22

    try:
        creds, migrated = resolve_host_ssh_credentials(
            host, inventory_lookup=_resolve_ssh_creds,
        )
        if migrated:
            db.commit()
        if not creds.password and not creds.key_path:
            return AgentLogOut(
                host_id=query.host_id,
                log_path=query.log_path,
                content="",
                lines_read=0,
                error="SSH credentials are not configured for this host.",
            )

        client = create_ssh_client(
            hostname=ssh_host,
            port=ssh_port,
            username=creds.user,
            password=creds.password,
            key_path=creds.key_path,
            known_hosts_path=creds.known_hosts_path,
            timeout=10,
        )
        stdin, stdout, stderr = client.exec_command(cmd)
        content = stdout.read().decode("utf-8", errors="replace")
        error = stderr.read().decode("utf-8", errors="replace")

        client.close()

        if content.strip() == LOG_PATH_FORBIDDEN_MARKER:
            raise HTTPException(
                status_code=400,
                detail="log_path must stay under configured SSH log roots",
            )

        if content.strip() == LOG_FILE_NOT_FOUND_MARKER:
            return AgentLogOut(
                host_id=query.host_id,
                log_path=query.log_path,
                content="",
                lines_read=0,
                error=f"Log file not found: {query.log_path}",
            )

        lines_read = len([l for l in content.split("\n") if l.strip()])

        return AgentLogOut(
            host_id=query.host_id,
            log_path=query.log_path,
            content=content,
            lines_read=lines_read,
            error=error if error else None,
        )

    except paramiko.AuthenticationException:
        return AgentLogOut(
            host_id=query.host_id,
            log_path=query.log_path,
            content="",
            lines_read=0,
            error="SSH authentication failed. Please check ssh credentials and known_hosts.",
        )
    except paramiko.SSHException as e:
        return AgentLogOut(
            host_id=query.host_id,
            log_path=query.log_path,
            content="",
            lines_read=0,
            error=f"SSH connection error: {str(e)}",
        )
    except (SshSecurityConfigError, FileNotFoundError) as e:
        return AgentLogOut(
            host_id=query.host_id,
            log_path=query.log_path,
            content="",
            lines_read=0,
            error=f"SSH security configuration error: {str(e)}",
        )
    except Exception as e:
        return AgentLogOut(
            host_id=query.host_id,
            log_path=query.log_path,
            content="",
            lines_read=0,
            error=f"Failed to query agent logs: {str(e)}",
        )
