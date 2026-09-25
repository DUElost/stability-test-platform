"""刷机前置归位 — ansible ensure_flash_prereqs.yml via RunConsole。

主机页入口（非热更新默认路径）。与 update_agent.yml 的
``agent_ensure_flash_prereqs`` opt-in 段同源；本通道强制执行。
ADR-0037 D5：provisioning 归位，不在 Agent 运行期脚本里 apt/usermod。
"""

from __future__ import annotations

import logging
import os
import shlex
import tempfile
import threading
from pathlib import Path
from typing import Any, Callable

from backend.services.audit_writer import record_audit
from backend.core.database import SessionLocal
from backend.core.ssh_security import resolve_host_ssh_credentials
from backend.models.host import Host
from backend.services.host_updater import _resolve_ssh_creds
from backend.services.run_console import RunConsole, RunConsoleError, RunKeyBusyError

logger = logging.getLogger(__name__)

_ACTIVE_LOCK = threading.Lock()
_ACTIVE_CONSOLE_BY_HOST: dict[str, str] = {}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def get_active_flash_prereqs_console_id(host_id: str) -> str | None:
    with _ACTIVE_LOCK:
        return _ACTIVE_CONSOLE_BY_HOST.get(host_id)


def _register_active(host_id: str, console_run_id: str) -> None:
    with _ACTIVE_LOCK:
        _ACTIVE_CONSOLE_BY_HOST[host_id] = console_run_id


def _clear_active(host_id: str) -> None:
    with _ACTIVE_LOCK:
        _ACTIVE_CONSOLE_BY_HOST.pop(host_id, None)


def prepare_ensure_flash_prereqs(host_id: str) -> dict[str, Any]:
    """解析 SSH 凭据并构造 ansible-playbook argv。"""
    db = SessionLocal()
    try:
        host = db.get(Host, host_id)
        if not host:
            return {"ok": False, "message": f"host {host_id} not found"}
        ip = host.ip or ""
        if not ip:
            return {"ok": False, "message": "Host has no IP address configured"}
        port = host.ssh_port or 22
        try:
            creds, _migrated = resolve_host_ssh_credentials(
                host, inventory_lookup=_resolve_ssh_creds
            )
        except Exception as exc:
            return {"ok": False, "message": f"resolve ssh creds failed: {exc}"}
        if not creds.password and not creds.key_path:
            return {"ok": False, "message": "no SSH credentials available"}
    finally:
        db.close()

    ansible_dir = _repo_root() / "tools" / "ansible"
    playbook = ansible_dir / "playbooks" / "ensure_flash_prereqs.yml"
    ansible_cfg = ansible_dir / "ansible.cfg"
    if not playbook.exists():
        return {"ok": False, "message": f"playbook missing: {playbook}"}

    inv_fd, inv_path = tempfile.mkstemp(
        prefix=".stp-flash-prereqs-", suffix=".ini", dir=str(ansible_dir), text=True
    )
    with os.fdopen(inv_fd, "w", encoding="utf-8") as fh:
        fh.write("[linux_hosts]\n")
        parts = [
            ip,
            f"ansible_host={ip}",
            f"ansible_port={port}",
            f"ansible_user={creds.user}",
        ]
        if creds.password:
            parts.append(f"ansible_password={creds.password}")
            parts.append(f"ansible_become_password={creds.password}")
        if creds.key_path:
            parts.append(f"ansible_ssh_private_key_file={creds.key_path}")
        fh.write(" ".join(parts) + "\n")

    env = dict(os.environ)
    if ansible_cfg.exists():
        env["ANSIBLE_CONFIG"] = str(ansible_cfg)
    env["ANSIBLE_NO_LOG"] = "true"

    cmd = [
        "ansible-playbook",
        str(playbook),
        "-i",
        inv_path,
        "--limit",
        ip,
        "-e",
        f"agent_host_id={host_id}",
    ]

    def cleanup() -> None:
        try:
            os.remove(inv_path)
        except OSError:
            pass

    return {
        "ok": True,
        "host_id": host_id,
        "ip": ip,
        "cmd": cmd,
        "env": env,
        "cwd": str(ansible_dir),
        "cleanup": cleanup,
        "cmd_line": " ".join(shlex.quote(c) for c in cmd),
    }


def start_ensure_flash_prereqs_runconsole(
    host_id: str,
    *,
    initiated_by: str | None = None,
) -> dict[str, Any]:
    """启动刷机前置归位（RunConsole）。"""
    prep = prepare_ensure_flash_prereqs(host_id)
    if not prep.get("ok"):
        return {"ok": False, "message": prep.get("message", "prepare failed")}

    cleanup: Callable[[], None] = prep["cleanup"]
    run_key = f"flash-prereqs:{host_id}"
    label = f"flash-prereqs {prep.get('ip') or host_id}"

    def on_complete(_run: Any) -> None:
        _record_outcome(host_id, _run, initiated_by)
        try:
            cleanup()
        finally:
            _clear_active(host_id)

    try:
        console_run_id = RunConsole.instance().start(
            run_key=run_key,
            cmd=prep["cmd"],
            cwd=prep["cwd"],
            env=prep["env"],
            label=label,
            on_complete=on_complete,
        )
    except RunKeyBusyError:
        cleanup()
        existing = get_active_flash_prereqs_console_id(host_id)
        return {
            "ok": False,
            "message": "flash prereqs already in progress",
            "console_run_id": existing,
        }
    except RunConsoleError as exc:
        cleanup()
        return {"ok": False, "message": str(exc)}

    _register_active(host_id, console_run_id)
    log_path = RunConsole.instance().log_file_path(console_run_id)
    try:
        with open(log_path, "w", encoding="utf-8") as fh:
            fh.write(f"$ {prep['cmd_line']}\n")
            if initiated_by:
                fh.write(f"# initiated_by={initiated_by}\n")
    except OSError:
        logger.warning("flash_prereqs_log_header_write_failed run_id=%s", console_run_id)

    logger.info(
        "flash_prereqs_runconsole_started host=%s run_id=%s", host_id, console_run_id
    )
    return {
        "ok": True,
        "console_run_id": console_run_id,
        "room": f"console:{console_run_id}",
        "message": "ok",
    }


def flash_prereqs_outcome_snapshot(console_run_id: str) -> dict[str, Any]:
    console = RunConsole.instance()
    state = console.status(console_run_id)
    log_path = str(console.log_file_path(console_run_id))
    if state is None:
        return {"found": False, "status": None, "exit_code": None, "log_path": log_path}
    return {
        "found": True,
        "status": state.get("status"),
        "exit_code": state.get("exit_code"),
        "log_path": log_path,
    }


def _record_outcome(host_id: str, run: Any, initiated_by: str | None) -> None:
    try:
        status = getattr(run, "status", None)
        exit_code = getattr(run, "exit_code", None)
        run_id = getattr(run, "run_id", None)
        ok = status == "SUCCESS"
        log_path = str(RunConsole.instance().log_file_path(run_id)) if run_id else None
        db = SessionLocal()
        try:
            host = db.get(Host, host_id)
            record_audit(
                db,
                action="ensure_flash_prereqs",
                resource_type="host",
                resource_id=host_id,
                details={
                    "host_id": host_id,
                    "ip": host.ip if host else None,
                    "ok": ok,
                    "rc": exit_code,
                    "console_status": status,
                    "log_path": log_path,
                    "console_run_id": run_id,
                    "message": "ok" if ok else f"console {status}",
                    "initiated_by": initiated_by,
                },
                username=initiated_by,
            )
            db.commit()
        finally:
            db.close()
        logger.info(
            "flash_prereqs_outcome host=%s status=%s ok=%s", host_id, status, ok
        )
    except Exception:
        logger.warning(
            "flash_prereqs_outcome_audit_failed host=%s", host_id, exc_info=True
        )
