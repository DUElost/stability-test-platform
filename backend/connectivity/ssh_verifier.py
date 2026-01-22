import socket
import time
from typing import Any, Dict, List, Optional

import paramiko
from paramiko.ssh_exception import AuthenticationException, SSHException

from .error_handler import RetryConfig


def verify_ssh(
    host: str,
    port: int = 22,
    username: Optional[str] = None,
    password: Optional[str] = None,
    key_path: Optional[str] = None,
    timeout: float = 5.0,
) -> Dict[str, Any]:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.RejectPolicy())

    try:
        start = time.time()
        client.connect(
            hostname=host,
            port=port,
            username=username,
            password=password,
            key_filename=key_path,
            timeout=timeout,
            banner_timeout=timeout,
            auth_timeout=timeout,
            look_for_keys=False,
            allow_agent=False,
        )
        stdin, stdout, stderr = client.exec_command("echo ok", timeout=timeout)
        output = stdout.read().decode().strip()
        latency = (time.time() - start) * 1000

        if output == "ok":
            return {"ok": True, "host": host, "latency_ms": round(latency, 2)}
        return {"ok": False, "host": host, "error": "exec_failed"}

    except AuthenticationException:
        return {"ok": False, "host": host, "error": "auth_failed"}
    except (SSHException, socket.timeout, socket.error):
        return {"ok": False, "host": host, "error": "ssh_failed_or_timeout"}
    finally:
        client.close()
