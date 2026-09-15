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
from urllib.parse import urlsplit

from backend.core.database import SessionLocal
from backend.core.ssh_security import resolve_host_ssh_credentials
from backend.services.host_updater import _resolve_ssh_creds
from backend.services.run_console import RunConsole, RunConsoleError, RunKeyBusyError

logger = logging.getLogger(__name__)

# 控制面公开入口（agent 回连地址）。安装脚本自本切片起非交互：该值必须由
# 调用方注入，且必须是 origin（Agent 自行拼接 /api/v1/... 与 WS 路径）。
INSTALL_API_URL_ENV = "STP_AGENT_INSTALL_API_URL"

# install_options 允许的键 → ansible 变量名。
_INSTALL_OPTION_VARS: dict[str, str] = {
    "agent_install_root": "agent_install_dir",
    "agent_local_aee_root": "agent_local_aee_root",
    # 中心存储挂载点：热更新（agent_env_sync）只在 CP 有非空值时下发，
    # 首次安装不写就会让 Agent 因缺 STP_AEE_NFS_ROOT 启动即崩。
    "agent_nfs_root": "agent_nfs_root",
}

_ACTIVE_LOCK = threading.Lock()
_ACTIVE_CONSOLE_BY_HOST: dict[str, str] = {}


class InstallConfigError(ValueError):
    """服务端配置或调用参数不可用：不启动安装（fail-closed）。"""


def normalize_install_api_url(raw: str | None = None) -> str:
    """校验并归一化 STP_AGENT_INSTALL_API_URL。非法即抛 InstallConfigError。"""
    value = (raw if raw is not None else os.environ.get("STP_AGENT_INSTALL_API_URL", "")).strip()
    if not value:
        raise InstallConfigError(
            f"{INSTALL_API_URL_ENV} 未配置：控制面驱动安装需要站点公开入口地址"
            f"（示例：https://stp.example.com）。"
        )
    if any(ch in value for ch in "<>{}$`\t\n "):
        raise InstallConfigError(
            f"{INSTALL_API_URL_ENV} 含空白或未展开的模板占位符：{value!r}"
        )
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https"):
        raise InstallConfigError(
            f"{INSTALL_API_URL_ENV} 必须是 http:// 或 https:// 地址：{value!r}"
        )
    if not parts.hostname:
        raise InstallConfigError(f"{INSTALL_API_URL_ENV} 缺少主机名：{value!r}")
    if parts.username or parts.password:
        raise InstallConfigError(
            f"{INSTALL_API_URL_ENV} 不得内嵌凭据：{value!r}"
        )
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        raise InstallConfigError(
            f"{INSTALL_API_URL_ENV} 必须是纯 origin（不含路径/查询/片段）：{value!r}"
        )
    try:
        _port = parts.port  # 触发端口校验（非数字/越界时抛 ValueError）
    except ValueError as exc:  # 端口非数字或越界
        raise InstallConfigError(
            f"{INSTALL_API_URL_ENV} 端口非法：{value!r}（{exc}）"
        ) from exc
    # netloc 原样保留（含 IPv6 方括号与大小写），此时已排除 userinfo。
    return f"{parts.scheme}://{parts.netloc.rsplit('@', 1)[-1]}"


def normalize_install_options(options: dict[str, Any] | None) -> dict[str, str]:
    """校验 install_options → ansible -e 变量。非法即抛 InstallConfigError。"""
    normalized: dict[str, str] = {}
    for key, raw in (options or {}).items():
        if raw is None:
            continue
        if key not in _INSTALL_OPTION_VARS:
            raise InstallConfigError(f"install_options 不支持的键：{key}")
        text = str(raw).strip()
        if not text:
            continue
        if not text.startswith("/"):
            raise InstallConfigError(f"install_options.{key} 必须是绝对路径：{text!r}")
        if text == "/":
            raise InstallConfigError(f"install_options.{key} 不得是文件系统根：{text!r}")
        if any(ch in text for ch in "<>{}$`\t\n "):
            raise InstallConfigError(
                f"install_options.{key} 含空白或未展开的模板占位符：{text!r}"
            )
        normalized[_INSTALL_OPTION_VARS[key]] = text.rstrip("/") or "/"
    return normalized


def install_request_problem(options: dict[str, Any] | None = None) -> str | None:
    """返回「本次安装无法启动」的原因；可用则返回 None。

    路由层在启动 RunConsole 之前调用，避免用 400 掩盖真实原因后留下半个运行。
    """
    try:
        normalize_install_api_url()
        normalize_install_options(options)
    except InstallConfigError as exc:
        return str(exc)
    return None


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


def prepare_install_agent(
    host_id: str,
    *,
    install_options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """解析 host SSH 凭据并构造 ansible-playbook argv。失败返回 {ok: False, message}."""
    try:
        api_url = normalize_install_api_url()
        extra_vars = normalize_install_options(install_options)
    except InstallConfigError as exc:
        return {"ok": False, "message": str(exc)}

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
            # #1252：仅私钥凭据必须把 key 路径写入 inventory，否则 Ansible
            # 只能依赖 SSH agent 恰好持有该密钥——准备阶段通过、连接失败
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
        # 安装脚本非交互：API_URL 由控制面注入，不得依赖目标机终端输入。
        "-e",
        f"agent_api_url={api_url}",
    ]
    for var, value in sorted(extra_vars.items()):
        cmd += ["-e", f"{var}={value}"]

    def cleanup() -> None:
        try:
            os.remove(inv_path)
        except OSError:
            pass

    return {
        "ok": True,
        "host_id": host_id,
        "ip": ip,
        "api_url": api_url,
        "install_options": extra_vars,
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
    install_options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """启动 ansible 安装（RunConsole 实时日志）。返回 console_run_id。"""
    prep = prepare_install_agent(host_id, install_options=install_options)
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
        # 未真正启动：临时 inventory（含 SSH 凭据）必须立刻删除，
        # 否则每次重复触发都会在 tools/ansible/ 残留一个 .stp-install-*.ini。
        cleanup()
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
    install_options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """同步执行安装：可传入已启动的 console_run_id，或在此函数内启动并等待。"""
    if console_run_id:
        return wait_install_agent_runconsole(console_run_id)
    started = start_install_agent_runconsole(
        host_id, initiated_by=initiated_by, install_options=install_options
    )
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
