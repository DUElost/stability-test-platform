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


def _popen_kwargs() -> dict:
    """跨平台进程组隔离（与引擎 _popen_isolation_kwargs 同构）。

    kill 必须能覆盖 adb 派生的子进程；不隔离的话超时杀进程时 adb server
    及其子进程可能残留，继续占用设备/端口。
    """
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
    return {"start_new_session": True}


def _terminate(proc: subprocess.Popen) -> None:
    """杀整个进程组，并 wait 回收，避免僵尸。"""
    try:
        if os.name == "nt":
            proc.kill()
        else:
            try:
                os.killpg(os.getpgid(proc.pid), 15)  # SIGTERM
            except ProcessLookupError:
                pass
            except Exception:
                proc.kill()
    except Exception:
        pass
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except Exception:
            pass
        try:
            proc.wait(timeout=5)
        except Exception:
            pass


def _pump_lines(
    proc: subprocess.Popen,
    timeout: int,
    on_line: "callable | None" = None,
) -> list[str]:
    """读两条流的全部行，主线程轮询超时。

    readline 是阻塞的，直接在主循环里读会让 deadline 检查失效（卡住的行
    永远读不完）。所以两条流各一个 reader 线程，主线程每 0.5s 检查超时。
    超时由调用方负责杀进程（本函数只负责收尾线程与返回已收集的行）。
    """
    collected: list[str] = []

    def _reader(stream) -> None:
        try:
            for line in stream:
                collected.append(line)
                if on_line is not None:
                    try:
                        on_line(line)
                    except Exception:
                        pass
        except (ValueError, OSError):
            pass
        finally:
            try:
                stream.close()
            except Exception:
                pass

    threads = [
        threading.Thread(target=_reader, args=(proc.stdout,), daemon=True),
        threading.Thread(target=_reader, args=(proc.stderr,), daemon=True),
    ]
    for th in threads:
        th.start()
    deadline = time.monotonic() + timeout
    while proc.poll() is None:
        if time.monotonic() >= deadline:
            break
        time.sleep(0.5)
    for th in threads:
        th.join(timeout=2)
    return collected


def adb_push_progress(
    local: str,
    remote: str,
    timeout: int = 120,
    on_progress: "callable | None" = None,
) -> None:
    """adb push，带进度回调（#115 阶段 2）。

    阻塞 subprocess.run(capture_output) 会吞掉 adb push 的 `[ NN%]` 进度行 ——
    而停滞判据是「只有 PROGRESS 戳才算活」，传输期间的戳必须来自这里。

    实现：Popen + 双 reader 线程（stdout 与 stderr 都读 —— 真实 adb 的进度
    行可能走任一流，常见是 stderr 带 \r），主线程轮询超时；超时杀整个进程组
    并 wait 回收。两条流的进度行都解析，百分比变化回调 on_progress(pct)。
    """
    proc = subprocess.Popen(
        [adb_path(), "-s", device_serial(), "push", local, remote],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        **_popen_kwargs(),
    )
    last_pct = -1
    collected: list[str] = []

    def _on_line(line: str) -> None:
        nonlocal last_pct
        m = _PUSH_PROGRESS_RE.search(line)
        if not m:
            return
        pct = int(m.group(1))
        if pct != last_pct:
            last_pct = pct
            if on_progress is not None:
                on_progress(pct)

    lines = _pump_lines(proc, timeout, on_line=_on_line)
    collected.extend(lines)
    if proc.poll() is None:
        _terminate(proc)
        raise subprocess.TimeoutExpired(
            [str(proc.args)], timeout, output="".join(collected)
        )
    if proc.returncode != 0:
        raise RuntimeError(
            f"adb push failed rc={proc.returncode}: {local} -> {remote} "
            f"stderr={' '.join(collected)[-500:]}"
        )


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
