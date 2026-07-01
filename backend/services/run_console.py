"""RunConsole — 控制面命令执行 + web 实时控制台（ADR-0025 §9 RunConsole）。

可复用的「Jenkins 式 web 实时日志」基础能力：把任意控制面长跑命令（subprocess）
的 stdout 行级实时推到前端 xterm，并落盘供断线 replay，支持取消/状态查询。
去重→Jira 提单是首个消费者（见 dedup 端点）；备份/演练/任意运维命令均可复用。

设计要点（见 ADR-0025 §8/§9）：
    - 行级流：reader 线程逐行读 → 批量 schedule_emit("console_log", room=console:{run_id})
      （沿用既有 sync→async 桥 socketio_server.schedule_emit + 前端 xterm）
    - 落盘 replay：每行追加日志文件，GET log?from_seq 支持断线补齐
    - 编码：text 模式 + 可配置 encoding + errors="replace"；子进程 PYTHONUNBUFFERED/IOENCODING
    - 取消：进程组 kill（Windows CTRL_BREAK / POSIX killpg）+ 超时兜底
    - run_key 串行：同 key 同时只允许一个 run（同 PlanRun 提单不并发）

线程模型：进程级单例 + 每个 run 一个 daemon reader 线程。schedule_emit 在主循环
未就绪时安全 no-op（测试/headless 友好）。
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class RunConsoleError(Exception):
    """RunConsole 启动/操作错误。"""


class RunKeyBusyError(RunConsoleError):
    """同 run_key 已有 RUNNING run（串行约束）。"""


_TERMINAL_STATUSES = {"SUCCESS", "FAILED", "CANCELED"}


@dataclass
class ConsoleRun:
    run_id: str
    run_key: str
    label: str
    status: str = "RUNNING"            # RUNNING | SUCCESS | FAILED | CANCELED
    exit_code: Optional[int] = None
    started_at: str = ""
    ended_at: Optional[str] = None
    seq: int = 0                        # 已 emit 的最后一行序号（单调）
    error: Optional[str] = None
    on_complete: Optional[Callable[["ConsoleRun"], None]] = None
    _proc: Optional[subprocess.Popen] = None
    _log_path: Optional[Path] = None
    _thread: Optional[threading.Thread] = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def to_status(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "run_key": self.run_key,
            "label": self.label,
            "status": self.status,
            "exit_code": self.exit_code,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "seq": self.seq,
            "error": self.error,
        }


class RunConsole:
    """进程级单例。configure() 注入日志根与编码；start() 起一个受控 subprocess。"""

    _instance: Optional["RunConsole"] = None
    _instance_lock = threading.Lock()

    # 批量 flush 阈值：≤这么多行或 ≤这么多秒就推一次，避免 SocketIO 洪泛
    _FLUSH_MAX_LINES = 50
    _FLUSH_MAX_INTERVAL = 0.1

    def __init__(self) -> None:
        self._runs: Dict[str, ConsoleRun] = {}
        self._inflight_keys: set[str] = set()
        self._lock = threading.Lock()
        self._log_root: Path = Path("logs/console")
        self._encoding: str = "utf-8"
        self._cancel_grace: float = 5.0
        self._configured = False
        # 可注入的 emit（测试替换；默认走 socketio schedule_emit）
        self._emit = None

    # ------------------------------------------------------------------
    # 单例
    # ------------------------------------------------------------------

    @classmethod
    def instance(cls) -> "RunConsole":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    @classmethod
    def _reset_for_tests(cls) -> None:
        with cls._instance_lock:
            inst = cls._instance
            cls._instance = None
        if inst is not None:
            for run in list(inst._runs.values()):
                try:
                    inst.cancel(run.run_id)
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # 配置
    # ------------------------------------------------------------------

    def configure(
        self,
        *,
        log_root: str,
        encoding: str = "utf-8",
        cancel_grace_seconds: float = 5.0,
        emit=None,
    ) -> "RunConsole":
        self._log_root = Path(log_root)
        self._log_root.mkdir(parents=True, exist_ok=True)
        self._encoding = encoding or "utf-8"
        self._cancel_grace = max(0.5, float(cancel_grace_seconds))
        self._emit = emit
        self._configured = True
        logger.info("run_console_configured log_root=%s encoding=%s", self._log_root, self._encoding)
        return self

    def _do_emit(self, event: str, data: Dict[str, Any], room: str) -> None:
        """推一条 SocketIO 事件；主循环未就绪时安全 no-op。"""
        if self._emit is not None:
            try:
                self._emit(event, data, room)
            except Exception:
                logger.exception("run_console_emit_injected_failed event=%s", event)
            return
        try:
            from backend.realtime.socketio_server import schedule_emit
            schedule_emit(event, data, namespace="/dashboard", room=room)
        except Exception:
            logger.exception("run_console_emit_failed event=%s", event)

    # ------------------------------------------------------------------
    # 启动
    # ------------------------------------------------------------------

    def start(
        self,
        *,
        run_key: str,
        cmd: List[str],
        cwd: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
        label: str = "",
        on_complete: Optional[Callable[["ConsoleRun"], None]] = None,
    ) -> str:
        """起一个受控 subprocess。返回 run_id。

        run_key 串行：同 key 已有 RUNNING run → 抛 RunKeyBusyError。
        cmd 必须是 argv 列表（不走 shell，避免注入）。
        env 在子进程 os.environ 之上叠加（凭据由调用方注入，本层不记录 env 值）。
        """
        if not self._configured:
            raise RunConsoleError("RunConsole not configured — call configure() first")
        if not cmd or not isinstance(cmd, list):
            raise RunConsoleError("cmd must be a non-empty argv list")

        with self._lock:
            if run_key in self._inflight_keys:
                raise RunKeyBusyError(f"run_key busy: {run_key}")
            self._inflight_keys.add(run_key)

        run_id = f"con-{uuid.uuid4().hex[:12]}"
        log_path = self._log_root / f"{run_id}.log"
        run = ConsoleRun(
            run_id=run_id,
            run_key=run_key,
            label=label or run_key,
            started_at=datetime.now(timezone.utc).isoformat(),
            _log_path=log_path,
            on_complete=on_complete,
        )
        with self._lock:
            self._runs[run_id] = run

        # 子进程环境：叠加凭据 + 强制无缓冲/UTF-8 输出
        proc_env = dict(os.environ)
        if env:
            proc_env.update(env)
        proc_env.setdefault("PYTHONUNBUFFERED", "1")
        proc_env.setdefault("PYTHONIOENCODING", "utf-8")

        popen_kwargs: Dict[str, Any] = dict(
            cwd=cwd or None,
            env=proc_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
            text=True,
            encoding=self._encoding,
            errors="replace",
        )
        # 进程组隔离，便于取消时整组 kill
        if os.name == "nt":
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        else:
            popen_kwargs["start_new_session"] = True

        try:
            proc = subprocess.Popen(cmd, **popen_kwargs)
        except Exception as exc:
            run.status = "FAILED"
            run.error = f"spawn_failed: {exc}"[:500]
            run.ended_at = datetime.now(timezone.utc).isoformat()
            self._release_key(run_key)
            logger.exception("run_console_spawn_failed run_id=%s", run_id)
            raise RunConsoleError(f"spawn failed: {exc}") from exc

        run._proc = proc
        thread = threading.Thread(
            target=self._reader_loop, args=(run,), name=f"run-console-{run_id}", daemon=True,
        )
        run._thread = thread
        thread.start()
        logger.info("run_console_started run_id=%s key=%s label=%s", run_id, run_key, run.label)
        return run_id

    # ------------------------------------------------------------------
    # reader 线程
    # ------------------------------------------------------------------

    def _reader_loop(self, run: ConsoleRun) -> None:
        room = f"console:{run.run_id}"
        proc = run._proc
        assert proc is not None and run._log_path is not None
        buf: List[str] = []
        last_flush = _monotonic()

        def flush() -> None:
            nonlocal buf, last_flush
            if not buf:
                return
            lines = buf
            buf = []
            last_flush = _monotonic()
            with run._lock:
                start_seq = run.seq + 1
                run.seq += len(lines)
            # 落盘（replay 源）
            try:
                with open(run._log_path, "a", encoding="utf-8") as f:
                    for ln in lines:
                        f.write(ln if ln.endswith("\n") else ln + "\n")
            except Exception:
                logger.exception("run_console_log_write_failed run_id=%s", run.run_id)
            # 实时推送
            self._do_emit(
                "console_log",
                {"run_id": run.run_id, "from_seq": start_seq, "lines": [ln.rstrip("\n") for ln in lines]},
                room,
            )

        try:
            for line in proc.stdout:  # type: ignore[union-attr]
                buf.append(line)
                if len(buf) >= self._FLUSH_MAX_LINES or (_monotonic() - last_flush) >= self._FLUSH_MAX_INTERVAL:
                    flush()
            flush()
            proc.wait()
        except Exception:
            logger.exception("run_console_reader_failed run_id=%s", run.run_id)
        finally:
            try:
                flush()
            except Exception:
                pass
            self._finalize(run, proc.returncode if proc.returncode is not None else -1)

    def _finalize(self, run: ConsoleRun, returncode: int) -> None:
        with run._lock:
            if run.status not in _TERMINAL_STATUSES:
                if run.status == "CANCELED" or returncode in (-15, -9):
                    run.status = "CANCELED"
                else:
                    run.status = "SUCCESS" if returncode == 0 else "FAILED"
            run.exit_code = returncode
            run.ended_at = datetime.now(timezone.utc).isoformat()
            status_snapshot = run.to_status()
        self._release_key(run.run_key)
        self._do_emit("console_status", status_snapshot, f"console:{run.run_id}")
        logger.info(
            "run_console_finished run_id=%s status=%s exit=%s seq=%d",
            run.run_id, status_snapshot["status"], returncode, status_snapshot["seq"],
        )
        if run.on_complete is not None:
            try:
                run.on_complete(run)
            except Exception:
                logger.exception("run_console_on_complete_failed run_id=%s", run.run_id)

    def _release_key(self, run_key: str) -> None:
        with self._lock:
            self._inflight_keys.discard(run_key)

    # ------------------------------------------------------------------
    # 操作
    # ------------------------------------------------------------------

    def cancel(self, run_id: str) -> bool:
        """取消运行中的 run（进程组 kill）。返回是否发起取消。"""
        run = self._runs.get(run_id)
        if run is None or run._proc is None:
            return False
        with run._lock:
            if run.status in _TERMINAL_STATUSES:
                return False
            run.status = "CANCELED"
        proc = run._proc
        try:
            if os.name == "nt":
                proc.terminate()  # NEW_PROCESS_GROUP 下 terminate 即对组生效
            else:
                import signal as _signal
                try:
                    os.killpg(os.getpgid(proc.pid), _signal.SIGTERM)
                except Exception:
                    proc.terminate()
            try:
                proc.wait(timeout=self._cancel_grace)
            except subprocess.TimeoutExpired:
                if os.name != "nt":
                    import signal as _signal
                    try:
                        os.killpg(os.getpgid(proc.pid), _signal.SIGKILL)
                    except Exception:
                        proc.kill()
                else:
                    proc.kill()
        except Exception:
            logger.exception("run_console_cancel_failed run_id=%s", run_id)
        # 等 reader 线程跑完 _finalize（释放 run_key + 写终态），使 cancel() 返回时
        # 调用方可立即用同 run_key 重起，不会撞 RunKeyBusyError。
        if run._thread is not None and run._thread.is_alive():
            run._thread.join(timeout=self._cancel_grace + 2.0)
        logger.info("run_console_cancel_requested run_id=%s", run_id)
        return True

    def status(self, run_id: str) -> Optional[Dict[str, Any]]:
        run = self._runs.get(run_id)
        return run.to_status() if run else None

    def read_log(self, run_id: str, *, from_seq: int = 0) -> Dict[str, Any]:
        """文件 replay：返回从 from_seq（1-based，含）起的行 + 当前 seq/status。

        run 不在内存（进程重启后的历史 run）时，仍尝试从 log_root/{run_id}.log
        读文件——status 回退为 UNKNOWN，由调用方按需从持久化层补全（如 jira_run 表）。
        """
        run = self._runs.get(run_id)
        if run is not None and run._log_path is not None:
            log_path = run._log_path
        else:
            # 历史记录 replay：run 不在内存，按约定路径找日志文件
            log_path = self._log_root / f"{run_id}.log"
        if not log_path or not log_path.exists():
            return {"run_id": run_id, "from_seq": from_seq, "lines": [],
                    "seq": run.seq if run else 0, "status": run.status if run else "UNKNOWN"}
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                all_lines = [ln.rstrip("\n") for ln in f.readlines()]
        except Exception:
            logger.exception("run_console_read_log_failed run_id=%s", run_id)
            all_lines = []
        start = max(0, int(from_seq) - 1) if from_seq > 0 else 0
        sliced = all_lines[start:]
        return {
            "run_id": run_id,
            "from_seq": start + 1,
            "lines": sliced,
            "seq": len(all_lines),
            "status": run.status if run else "UNKNOWN",
        }

    def log_file_path(self, run_id: str) -> Path:
        """返回 run 的日志文件路径（不依赖 run 是否在内存）。"""
        run = self._runs.get(run_id)
        if run is not None and run._log_path is not None:
            return run._log_path
        return self._log_root / f"{run_id}.log"

    def is_key_busy(self, run_key: str) -> bool:
        with self._lock:
            return run_key in self._inflight_keys

    def shutdown(self) -> None:
        """进程退出收尾：cancel 所有 inflight run 并 join reader 线程。

        幂等、安全——无 run 或已终止的直接跳过。在 lifespan shutdown 调用，
        避免子进程成孤儿（daemon reader 线程被强杀但 Popen 子进程不会随父退出）。
        不清空单例，与 _reset_for_tests（测试专用，清单例）区分。
        """
        if not self._configured:
            return
        with self._lock:
            runs = list(self._runs.values())
        if not runs:
            return
        logger.info("run_console_shutdown inflight=%d", len(runs))
        for run in runs:
            try:
                if run.status not in _TERMINAL_STATUSES:
                    self.cancel(run.run_id)
            except Exception:
                logger.exception("run_console_shutdown_cancel_failed run_id=%s", run.run_id)
        logger.info("run_console_shutdown_complete")


def _monotonic() -> float:
    import time
    return time.monotonic()


__all__ = ["RunConsole", "RunConsoleError", "RunKeyBusyError", "ConsoleRun"]
