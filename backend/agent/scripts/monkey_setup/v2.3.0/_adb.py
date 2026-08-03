"""Lightweight ADB helpers for NFS-deployed device scripts.

Each script under scripts/device/ is self-contained but may import
this module for common ADB operations.  All configuration comes from
environment variables (STP_* contract).
"""

import logging
import os
import re
import subprocess
import sys
import threading
import time

logger = logging.getLogger(__name__)


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def adb_path() -> str:
    return _env("STP_ADB_PATH", "adb")


def device_serial() -> str:
    serial = _env("STP_DEVICE_SERIAL", "")
    if not serial:
        logger.error("STP_DEVICE_SERIAL is not set")
        sys.exit(1)
    return serial


def adb_shell(command: str, timeout: int = 30) -> str:
    """Run an ADB shell command on the target device, return stdout."""
    result = subprocess.run(
        [adb_path(), "-s", device_serial(), "shell", command],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return result.stdout or ""


def adb_shell_quiet(command: str, timeout: int = 30) -> subprocess.CompletedProcess:
    """Run ADB shell, return full CompletedProcess for exit-code checks."""
    return subprocess.run(
        [adb_path(), "-s", device_serial(), "shell", command],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def adb_push(local: str, remote: str, timeout: int = 120) -> None:
    subprocess.run(
        [adb_path(), "-s", device_serial(), "push", local, remote],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=True,
    )


_PUSH_PROGRESS_RE = re.compile(r"\[ *(\d+)%\]")


def adb_push_progress(
    local: str,
    remote: str,
    timeout: int = 120,
    on_progress: "callable | None" = None,
) -> None:
    """adb push，带进度回调（#115 阶段 2）。

    阻塞 subprocess.run(capture_output) 会吞掉 adb push 的 `[ NN%]` 进度行 ——
    而停滞判据是「只有 PROGRESS 戳才算活」，传输期间的戳必须来自这里。

    实现：Popen + reader 线程逐行读 stdout（防管道写满死锁），主线程轮询
    超时。进度行解析出百分比，变化时回调 on_progress(pct)；超时杀进程树。
    """
    proc = subprocess.Popen(
        [adb_path(), "-s", device_serial(), "push", local, remote],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    lines: list[str] = []

    def _reader() -> None:
        try:
            for line in proc.stdout:
                lines.append(line)
        except (ValueError, OSError):
            pass

    thread = threading.Thread(target=_reader, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + timeout
        last_pct = -1
        processed = 0
        while proc.poll() is None:
            if time.monotonic() >= deadline:
                _terminate(proc)
                raise subprocess.TimeoutExpired(
                    [str(proc.args)], timeout, output="".join(lines)
                )
            time.sleep(0.5)
            # 增量解析：processed 索引推进，每行只处理一次 ——
            # 从头重扫会让同一行重复触发，last_pct 回退（实测 seen=[0,20,...,0,20]）。
            while processed < len(lines):
                m = _PUSH_PROGRESS_RE.search(lines[processed])
                processed += 1
                if m:
                    pct = int(m.group(1))
                    if pct != last_pct:
                        last_pct = pct
                        if on_progress is not None:
                            on_progress(pct)
        proc.wait(timeout=10)
    finally:
        thread.join(timeout=2)
        try:
            stderr = proc.stderr.read()
        except Exception:
            stderr = ""
    if proc.returncode != 0:
        raise RuntimeError(
            f"adb push failed rc={proc.returncode}: {local} -> {remote} "
            f"stderr={stderr[:500]}"
        )


def _terminate(proc: subprocess.Popen) -> None:
    try:
        proc.kill()
    except Exception:
        pass


def _progress_stamp(seq: int, **fields) -> str:
    """构造 PROGRESS 戳行（#115 阶段 2 协议，stderr 输出）。

    seq 单调递增是唯一判据；语义字段仅供人读诊断。
    """
    import json

    payload = {"seq": seq, **fields}
    return "PROGRESS " + json.dumps(payload, ensure_ascii=False)


def adb_install(apk_path: str, flags: list[str] | None = None, timeout: int = 120) -> str:
    cmd = [adb_path(), "-s", device_serial(), "install"] + (flags or []) + [apk_path]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return (result.stdout or "").strip()


def params() -> dict:
    import json
    raw = _env("STP_STEP_PARAMS", "{}")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def output_result(success: bool, **kwargs) -> None:
    import json
    payload = {"success": success, **kwargs}
    print(json.dumps(payload, ensure_ascii=False))
