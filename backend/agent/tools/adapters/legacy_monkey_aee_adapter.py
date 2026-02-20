# -*- coding: utf-8 -*-
"""
旧版 MonkeyAEE 脚本适配器。
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Optional, TextIO


@dataclass
class LegacyMonkeyAEEConfig:
    python_executable: str
    script_path: str
    working_dir: str
    script_args: List[str] = field(default_factory=list)
    legacy_params: Dict[str, Any] = field(default_factory=dict)
    pass_serial_arg: bool = True
    serial_arg_name: str = "--serial"
    run_timeout_sec: int = 21600
    poll_interval_sec: float = 1.0
    progress_interval_sec: int = 15
    max_log_lines: int = 2000
    env: Dict[str, str] = field(default_factory=dict)


@dataclass
class LegacyRunResult:
    return_code: int
    duration_sec: float
    timed_out: bool
    log_path: str
    tail_lines: List[str]


class LegacyMonkeyAEEAdapter:
    """将外部脚本转为平台可控的执行模型。"""

    def __init__(self, config: LegacyMonkeyAEEConfig):
        self.config = config

    def build_command(self, serial: str) -> List[str]:
        command = [self.config.python_executable, self.config.script_path]
        if self.config.pass_serial_arg and self.config.serial_arg_name:
            command.extend([self.config.serial_arg_name, serial])
        command.extend(str(item) for item in self.config.script_args)
        command.extend(self._flatten_legacy_params(self.config.legacy_params))
        return command

    def run(
        self,
        serial: str,
        log_dir: str,
        on_log: Callable[[str], None],
        on_tick: Optional[Callable[[int], None]] = None,
    ) -> LegacyRunResult:
        Path(log_dir).mkdir(parents=True, exist_ok=True)
        run_log_path = Path(log_dir) / "legacy_monkey_aee.log"
        command = self.build_command(serial)
        env = os.environ.copy()
        env.update({k: str(v) for k, v in self.config.env.items()})

        tail: Deque[str] = deque(maxlen=max(200, int(self.config.max_log_lines)))
        write_lock = threading.Lock()
        timed_out = False
        started = time.monotonic()
        last_tick = started

        with run_log_path.open("w", encoding="utf-8", errors="replace") as fp:
            fp.write("# legacy monkey aee command\n")
            fp.write(" ".join(command) + "\n\n")
            fp.flush()

            process = subprocess.Popen(
                command,
                cwd=self.config.working_dir or None,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=env,
            )

            stdout_thread = threading.Thread(
                target=self._consume_stream,
                args=(process.stdout, "STDOUT", tail, fp, write_lock, on_log),
                daemon=True,
            )
            stderr_thread = threading.Thread(
                target=self._consume_stream,
                args=(process.stderr, "STDERR", tail, fp, write_lock, on_log),
                daemon=True,
            )
            stdout_thread.start()
            stderr_thread.start()

            while process.poll() is None:
                now = time.monotonic()
                if now - started >= max(1, int(self.config.run_timeout_sec)):
                    timed_out = True
                    self._terminate_process(process)
                    break

                if on_tick and now - last_tick >= max(5, int(self.config.progress_interval_sec)):
                    on_tick(int(now - started))
                    last_tick = now

                time.sleep(max(0.2, float(self.config.poll_interval_sec)))

            stdout_thread.join(timeout=2)
            stderr_thread.join(timeout=2)

            if process.poll() is None:
                self._terminate_process(process)

            try:
                return_code = process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._terminate_process(process)
                return_code = process.wait(timeout=5)
            duration_sec = time.monotonic() - started
            if timed_out and return_code == 0:
                return_code = 124
            if timed_out:
                timeout_msg = f"[SYSTEM] 超时终止: timeout={self.config.run_timeout_sec}s"
                tail.append(timeout_msg)
                with write_lock:
                    fp.write(timeout_msg + "\n")
                    fp.flush()
                on_log(timeout_msg)

        return LegacyRunResult(
            return_code=return_code,
            duration_sec=duration_sec,
            timed_out=timed_out,
            log_path=str(run_log_path),
            tail_lines=list(tail),
        )

    @staticmethod
    def _flatten_legacy_params(params: Dict[str, Any]) -> List[str]:
        args: List[str] = []
        for raw_key, value in (params or {}).items():
            key = str(raw_key).strip().replace("_", "-")
            if not key:
                continue
            option = f"--{key}"
            if value is None:
                continue
            if isinstance(value, bool):
                if value:
                    args.append(option)
                continue
            if isinstance(value, (list, tuple)):
                for item in value:
                    if item is None:
                        continue
                    args.extend([option, str(item)])
                continue
            args.extend([option, str(value)])
        return args

    @staticmethod
    def _consume_stream(
        stream: Optional[TextIO],
        label: str,
        tail: Deque[str],
        fp: TextIO,
        write_lock: threading.Lock,
        on_log: Callable[[str], None],
    ) -> None:
        if stream is None:
            return
        try:
            for line in iter(stream.readline, ""):
                text = line.strip()
                if not text:
                    continue
                formatted = f"[{label}] {text}"
                tail.append(formatted)
                with write_lock:
                    fp.write(formatted + "\n")
                    fp.flush()
                on_log(formatted)
        finally:
            stream.close()

    @staticmethod
    def _terminate_process(process: subprocess.Popen) -> None:
        if process.poll() is not None:
            return
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if process.poll() is None:
                    process.kill()
            else:
                process.kill()
        except Exception:
            process.kill()
