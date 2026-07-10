"""Agent 首次安装 — ansible-playbook via RunConsole（实时日志 + replay）。"""

from __future__ import annotations

import logging
import os
import shlex
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable

from backend.core.database import SessionLocal
from backend.core.ssh_security import resolve_host_ssh_credentials
from backend.services.host_updater import _resolve_ssh_creds
from backend.services.run_console import RunConsole, RunConsoleError, RunKeyBusyError

logger = logging.getLogger(__name__)

_ACTIVE_LOCK = threading.Lock()
_ACTIVE_CONSOLE_BY_HOST: dict[str, str] = {}


def get_active_install_console_id(host_id: str) -> str | None:
    with _ACTIVE_LOCK:
        return _ACTIVE_CONSOLE_BY_HOST.get(host_id)


def _register_active(host_id: str, console_run_id: str) -> None:
    with _ACTIVE_LOCK:
        _ACTIVE_CONSOLE_BY_HOST[host_id] = console_run_id


def _clear_active(host_id: str) -> None:
    with _ACTIVE_LOCK:
        _ACTIVE_CONSOLE_BY_HOST.pop(host_id, None)


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def prepare_install_agent(host_id: str) -> dict[str, Any]:
    """解析 host SSH 凭据并构造 ansible-playbook argv。失败返回 {ok: False, message}."""
    db = SessionLocal()
    try:
        from backend.models.host import Host

        host = db.get(Host, host_id)
        if not host:
            return {"ok": False, "message": f"host {host_id} not found"}
        ip = host.ip or ""
        port = host.ssh_port or 22
        try:
            creds, _migrated = resolve_host_ssh_credentials(host, inventory_lookup=_resolve_ssh_creds)
        except Exception as exc:
            return {"ok": False, "message": f"resolve ssh creds failed: {exc}"}
        if not creds.password and not creds.key_path:
            return {"ok": False, "message": "no SSH credentials available"}
    finally:
        db.close()

    ansible_dir = _repo_root() / "tools" / "ansible"
    playbook = ansible_dir / "playbooks" / "install_agent.yml"
    ansible_cfg = ansible_dir / "ansible.cfg"
    if not playbook.exists():
        return {"ok": False, "message": f"playbook missing: {playbook}"}

    inv_fd, inv_path = tempfile.mkstemp(
        prefix=".stp-install-", suffix=".ini", dir=str(ansible_dir), text=True
    )
    with os.fdopen(inv_fd, "w", encoding="utf-8") as fh:
        fh.write("[linux_hosts]\n")
        fh.write(
            f"{ip} ansible_host={ip} ansible_port={port} "
            f"ansible_user={creds.user} ansible_password={creds.password} "
            f"ansible_become_password={creds.password}\n"
        )

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


def start_install_agent_runconsole(
    host_id: str,
    *,
    initiated_by: str | None = None,
) -> dict[str, Any]:
    """启动 ansible 安装（RunConsole 实时日志）。返回 console_run_id。"""
    prep = prepare_install_agent(host_id)
    if not prep.get("ok"):
        return {"ok": False, "message": prep.get("message", "prepare failed")}

    cleanup: Callable[[], None] = prep["cleanup"]
    run_key = f"install:{host_id}"
    label = f"install-agent {prep.get('ip') or host_id}"

    def on_complete(_run: Any) -> None:
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
        existing = get_active_install_console_id(host_id)
        return {
            "ok": False,
            "message": "install already in progress",
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
        logger.warning("install_agent_log_header_write_failed run_id=%s", console_run_id)

    logger.info("install_agent_runconsole_started host=%s run_id=%s", host_id, console_run_id)
    return {
        "ok": True,
        "console_run_id": console_run_id,
        "room": f"console:{console_run_id}",
        "message": "ok",
    }


def wait_install_agent_runconsole(
    console_run_id: str,
    *,
    timeout: float = 900,
    poll_interval: float = 1.0,
) -> dict[str, Any]:
    """阻塞等待 RunConsole 安装结束（SAQ worker 线程内调用）。"""
    rc = RunConsole.instance()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        st = rc.status(console_run_id)
        if st is None:
            log_path = str(rc.log_file_path(console_run_id))
            return {
                "ok": False,
                "rc": -1,
                "console_run_id": console_run_id,
                "log_path": log_path,
                "message": "console run not found",
            }
        status = st.get("status")
        if status in ("SUCCESS", "FAILED", "CANCELED"):
            exit_code = st.get("exit_code")
            ok = status == "SUCCESS"
            log_path = str(rc.log_file_path(console_run_id))
            return {
                "ok": ok,
                "rc": exit_code if exit_code is not None else (0 if ok else 1),
                "console_run_id": console_run_id,
                "log_path": log_path,
                "message": "ok" if ok else f"ansible exit {exit_code}",
            }
        time.sleep(poll_interval)

    log_path = str(rc.log_file_path(console_run_id))
    return {
        "ok": False,
        "rc": -1,
        "console_run_id": console_run_id,
        "log_path": log_path,
        "message": f"install timeout after {int(timeout)}s",
    }


def run_install_agent_sync(
    host_id: str,
    initiated_by: str | None = None,
    *,
    console_run_id: str | None = None,
) -> dict[str, Any]:
    """同步执行安装：可传入已启动的 console_run_id，或在此函数内启动并等待。"""
    if console_run_id:
        return wait_install_agent_runconsole(console_run_id)
    started = start_install_agent_runconsole(host_id, initiated_by=initiated_by)
    if not started.get("ok"):
        return {
            "ok": False,
            "rc": -1,
            "console_run_id": started.get("console_run_id"),
            "log_path": None,
            "message": started.get("message", "start failed"),
        }
    cid = started["console_run_id"]
    result = wait_install_agent_runconsole(cid)
    result.setdefault("console_run_id", cid)
    return result
