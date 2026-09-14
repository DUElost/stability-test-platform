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
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from backend.realtime import console_registry as _console_registry


def _multi_instance_enabled() -> bool:
    """Redis adapter（ADR-0027 P3-2）开启 = 多实例形态标志。"""
    try:
        from backend.realtime.socketio_redis import socketio_redis_adapter_enabled
    except Exception:  # pragma: no cover - 导入失败不应影响诊断路径
        return False
    return socketio_redis_adapter_enabled()


def console_run_miss_hint() -> str:
    """#1114（R11-F06）/ #1737 P2：console run 本地缺失时的诊断附加说明。

    多实例模式下本地缺失可能是「由其他实例持有」而非「不存在」。注册表启用
    （P2）后：跨实例 status（含 `console:` 房间订阅校验）读 owner 快照；cancel
    与日志 replay 仍为实例本地语义——提示按**当前剩余限制**措辞。
    """
    if not _multi_instance_enabled():
        return ""
    if _console_registry.console_registry_enabled():
        return (
            "；多实例模式下该运行可能由其他控制面实例持有——跨实例 status（含订阅校验）、"
            "cancel 与日志 replay 均可用（replay 要求各实例共享 "
            "`STP_RUN_CONSOLE_LOG_ROOT`；不共享时 replay 显式标记 unavailable，"
            "见 #1737 P4，ref=#1114）"
        )
    return "；多实例模式（STP_SOCKETIO_REDIS_ADAPTER=1）下 RunConsole 无 owner 路由，该运行可能由其他控制面实例持有（#1114）"


def multi_instance_console_warning() -> Optional[str]:
    """#1114 / #1737 P2：多实例模式下功能边界的启动告警（None = 单实例，无告警）。

    注册表启用后跨实例能力已部分归位（run_key 互斥 + owner 登记 + status 快照），
    剩余限制单独列出；未启用时保持 v1.2 的「单实例语义」告警不变。
    """
    if not _multi_instance_enabled():
        return None
    if _console_registry.console_registry_enabled():
        return (
            "multi_instance_mode_enabled console_run_key_mutex=true "
            "console_status_cross_instance=true console_cancel_forwarding=true "
            "console_replay_cross_instance=shared_log_root_only "
            "notes=log_replay_requires_shared_STP_RUN_CONSOLE_LOG_ROOT "
            "ref=#1737/#1114"
        )
    return (
        "multi_instance_mode_enabled console_features_single_instance_only=true "
        "affected=dedup_jira_serialization,agent_install_console,"
        "ai_assistant_console_actions,console_room_subscribe ref=#1114"
    )

logger = logging.getLogger(__name__)


class RunConsoleError(Exception):
    """RunConsole 启动/操作错误。"""


class RunKeyBusyError(RunConsoleError):
    """同 run_key 已有 RUNNING run（串行约束）。"""


_TERMINAL_STATUSES = {"SUCCESS", "FAILED", "CANCELED"}


def _parse_positive_int(raw: Optional[str], default: int) -> int:
    try:
        value = int((raw or "").strip())
        return value if value > 0 else default
    except ValueError:
        return default


def _parse_positive_float(raw: Optional[str], default: float) -> float:
    try:
        value = float((raw or "").strip())
        return value if value > 0 else default
    except ValueError:
        return default


# #1124：replay 有界 + 终态运行记录淘汰（进程生命周期内 `_runs` 不得无界增长）
_REPLAY_MAX_LINES_DEFAULT = 2000
_REPLAY_MAX_LINE_CHARS = 100_000
_TERMINAL_RETENTION_SECONDS_DEFAULT = 3600.0

# #1737 P3：跨实例取消——控制 tick（消费取消请求的检查节奏）与取消等待窗。
# 控制 tick 必须显著小于等待窗，否则请求方必然超时（默认 1s vs 3s）。
_CONTROL_TICK_SECONDS_DEFAULT = 1.0
_CANCEL_WAIT_SECONDS_DEFAULT = 3.0

# #1115：组级收敛判据与 #1003（pipeline_engine）同型 —— 「父进程已退出」不代表
# 「进程组已散」，组里忽略 SIGTERM 的子孙必须升级到 SIGKILL，否则界面已 CANCELED
# 而后代仍在跑，且它们握着 stdout 管道写端，reader 线程也永远等不到 EOF。


def _process_group_alive(pgid: int) -> bool:
    """进程组内是否还有存活成员（signal 0 探测）。

    ESRCH = 整组已散；其余（含探测本身失败）按「仍在」处理 —— 宁可多收敛一次。
    """
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except Exception:
        return True
    return True


def _await_group_exit(proc: subprocess.Popen, pgid: int, timeout: float) -> bool:
    """等到「父已回收 且 整组已散」；返回是否收敛。

    循环里的 poll() 顺带回收僵尸父进程 —— 否则父的僵尸项本身会让 killpg(0)
    一直成功，探不到真实残留。
    """
    import time

    deadline = _monotonic() + max(timeout, 0.0)
    while True:
        if proc.poll() is not None and not _process_group_alive(pgid):
            return True
        if _monotonic() >= deadline:
            return False
        time.sleep(0.05)


def _resolve_pgid(proc: subprocess.Popen) -> Optional[int]:
    """趁父进程还活着现取进程组身份。

    父进程一旦被回收，os.getpgid 就 ESRCH（pid 还可能被复用）—— 拿不到就返回
    None（宁可退化为单进程 kill，也不用可能已复用的 pgid 去打陌生进程组）。
    """
    try:
        pgid = os.getpgid(proc.pid)
    except ProcessLookupError:
        return None
    except Exception:
        logger.exception("run_console_getpgid_failed pid=%s", getattr(proc, "pid", None))
        return None
    return pgid if isinstance(pgid, int) else None


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
    # #1115：spawn 时刻留存的进程组身份（POSIX）—— 父被回收后 getpgid 会 ESRCH，
    # 而 cancel 恰恰要处理「父已退出、子孙还活着」的情形。
    _pgid: Optional[int] = None
    _lock: threading.Lock = field(default_factory=threading.Lock)
    # #1275: 串行化 flush（reader 线程 + 定时线程并发调用），保证 seq 序与落盘序一致。
    _flush_lock: threading.Lock = field(default_factory=threading.Lock)

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


#: #1228: 子进程环境白名单——控制面环境（含 DATABASE_URL/AGENT_SECRET/
#: JWT_SECRET_KEY 等服务凭据）绝不透传给被测/工具子进程。调用方需要什么
#: 就在 start(env=...) 显式注入；白名单只保留进程正常启动所需的通用键。
_CHILD_ENV_ALLOWLIST = (
    "PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "SHELL",
    "TMPDIR",
    "TZ",
    "LANG",
    "LANGUAGE",
    "LC_ALL",
    "LC_CTYPE",
    "TERM",
)


def _build_child_env(extra: Optional[Dict[str, str]]) -> Dict[str, str]:
    """Minimal child environment + caller-provided keys (#1228).

    Isolation boundary (documented in ``docs/design/06-realtime-and-background.md`` §5):
    argv-list exec (no shell), fixed ``cwd``, this env allowlist, and a new
    process group for group-kill. Same-UID filesystem permissions remain a
    known limit (stronger isolation needs a separate user/container).
    """
    child = {k: v for k, v in os.environ.items() if k in _CHILD_ENV_ALLOWLIST}
    if extra:
        child.update(extra)
    child.setdefault("PYTHONUNBUFFERED", "1")
    child.setdefault("PYTHONIOENCODING", "utf-8")
    return child


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
        # #1124：replay 有界 + 终态运行记录淘汰
        self._replay_max_lines = _parse_positive_int(
            os.getenv("STP_RUN_CONSOLE_REPLAY_MAX_LINES"), _REPLAY_MAX_LINES_DEFAULT,
        )
        self._replay_max_line_chars = _REPLAY_MAX_LINE_CHARS
        self._terminal_retention_seconds = _parse_positive_float(
            os.getenv("STP_RUN_CONSOLE_TERMINAL_RETENTION_SECONDS"),
            _TERMINAL_RETENTION_SECONDS_DEFAULT,
        )
        # #1124：replay 有界 + 终态运行记录淘汰
        self._replay_max_lines = _parse_positive_int(
            os.getenv("STP_RUN_CONSOLE_REPLAY_MAX_LINES"), _REPLAY_MAX_LINES_DEFAULT,
        )
        self._replay_max_line_chars = _REPLAY_MAX_LINE_CHARS
        self._terminal_retention_seconds = _parse_positive_float(
            os.getenv("STP_RUN_CONSOLE_TERMINAL_RETENTION_SECONDS"),
            _TERMINAL_RETENTION_SECONDS_DEFAULT,
        )
        # #1737 P1：多实例归属注册表（默认关闭；门控/语义见 console_registry）。
        # 互斥键与 owner 键由本进程续期 + CAS 释放；确认失去互斥时止损取消。
        self._registry_ticker: Optional[threading.Thread] = None
        self._registry_ticker_stop = threading.Event()
        # #1737 P3：跨实例取消——等待 owner ack 的上界（控制 tick 见 P3 常量）
        self._cancel_wait_seconds = _parse_positive_float(
            os.getenv("STP_RUN_CONSOLE_CANCEL_WAIT_SECONDS"),
            _CANCEL_WAIT_SECONDS_DEFAULT,
        )

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
            inst._registry_ticker_stop.set()

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
        run_id: Optional[str] = None,
    ) -> str:
        """起一个受控 subprocess。返回 run_id。

        run_key 串行：同 key 已有 RUNNING run → 抛 RunKeyBusyError。
        cmd 必须是 argv 列表（不走 shell，避免注入）。
        env 显式注入到子进程（#1228 起子进程环境为白名单 + env——
        控制面环境/凭据不再透传；隔离边界见
        docs/design/06-realtime-and-background.md §5）。
        run_id（#1084）：调用方可预生成并先行落库（「先写后启」），使 spawn 前
        外部表已能按 run_id 关联 —— 回调早于外部 INSERT 的竞态从根上消除。
        缺省仍由本层生成；调用方提供的 run_id 撞已有 run 时抛 RunConsoleError。
        """
        if not self._configured:
            raise RunConsoleError("RunConsole not configured — call configure() first")
        if not cmd or not isinstance(cmd, list):
            raise RunConsoleError("cmd must be a non-empty argv list")

        if run_id is None:
            run_id = f"con-{uuid.uuid4().hex[:12]}"
        with self._lock:
            if run_id in self._runs:
                raise RunConsoleError(f"run_id already exists: {run_id}")
            if run_key in self._inflight_keys:
                raise RunKeyBusyError(f"run_key busy: {run_key}")
            self._inflight_keys.add(run_key)
        # #1737 P1：多实例形态下 run_key 需全局互斥（本进程 `_inflight_keys` 只
        # 覆盖本实例）。fail-closed：注册表不可用 → 拒绝启动，不静默降级为本地互斥。
        if _console_registry.console_registry_enabled():
            try:
                _console_registry.acquire_run_key(run_key, run_id=run_id)
            except _console_registry.ConsoleRunKeyBusy as exc:
                with self._lock:
                    self._inflight_keys.discard(run_key)
                raise RunKeyBusyError(str(exc)) from None
            except _console_registry.ConsoleRegistryUnavailable as exc:
                with self._lock:
                    self._inflight_keys.discard(run_key)
                raise RunConsoleError(f"console registry unavailable: {exc}") from exc
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

        if _console_registry.console_registry_enabled():
            try:
                _console_registry.register_owner(run_id, run_key=run_key)
            except _console_registry.ConsoleRegistryUnavailable as exc:
                # owner 登记失败与「互斥不可用」同口径：fail-closed 清理后拒绝启动
                _console_registry.release_run_key(run_key, run_id=run_id)
                with self._lock:
                    self._inflight_keys.discard(run_key)
                    self._runs.pop(run_id, None)
                raise RunConsoleError(f"console registry unavailable: {exc}") from exc
            self._ensure_registry_ticker()
            # P2：发布 RUNNING 快照（跨实例 status / 订阅校验读它）
            self._publish_snapshot(
                run, ttl_seconds=_console_registry.console_registry_ttl_seconds(),
            )

        # 子进程环境：#1228 白名单（不继承控制面环境/凭据）+ 调用方注入 +
        # 强制无缓冲/UTF-8 输出。
        proc_env = _build_child_env(env)

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
            self._release_key(run_key, run_id=run_id)
            # #1931：RUNNING 快照发布在 Popen 之前（:431）——spawn 失败路径无
            # reader 线程、`_finalize` 永不执行，快照只能等 TTL 自然过期，
            # 其他实例的 status() 在此期间读到 RUNNING 假状态。显式删除。
            if _console_registry.console_registry_enabled():
                try:
                    _console_registry.delete_status_snapshot(run_id)
                except Exception:  # noqa: BLE001 - 清理失败不影响主错误路径
                    logger.warning(
                        "run_console_spawn_fail_snapshot_cleanup_failed run_id=%s",
                        run_id,
                    )
            logger.exception("run_console_spawn_failed run_id=%s", run_id)
            raise RunConsoleError(f"spawn failed: {exc}") from exc

        run._proc = proc
        if os.name != "nt":
            # #1115：组身份必须在任何 wait/poll 之前留存 —— 父被回收后
            # os.getpgid 就查不到了，而 cancel 要处理的正是「父已退出」的情形。
            try:
                run._pgid = os.getpgid(proc.pid)
            except Exception:
                logger.exception("run_console_pgid_capture_failed run_id=%s", run_id)
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
        buf_lock = threading.Lock()
        stop_timer = threading.Event()

        def flush() -> None:
            nonlocal buf
            # #1275: flush 由 reader 线程（buf 达阈值）与 timed_flush 定时线程并发
            # 调用——若不串行，「摘批 → 分配 seq → 落盘/推送」两步可交错，导致文件
            # 行序与 seq 序倒置（read_log 回溯/实时 from_seq 错位）。
            with run._flush_lock:
                with buf_lock:
                    if not buf:
                        return
                    lines = buf
                    buf = []
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

        def timed_flush() -> None:
            # #1118: flush on wall-clock interval even when stdout is quiet
            # (reader blocked on the next readline).
            while not stop_timer.wait(self._FLUSH_MAX_INTERVAL):
                flush()

        timer = threading.Thread(
            target=timed_flush,
            name=f"run-console-flush-{run.run_id}",
            daemon=True,
        )
        timer.start()
        try:
            for line in proc.stdout:  # type: ignore[union-attr]
                with buf_lock:
                    buf.append(line)
                    should_flush = len(buf) >= self._FLUSH_MAX_LINES
                if should_flush:
                    flush()
            flush()
            proc.wait()
        except Exception:
            logger.exception("run_console_reader_failed run_id=%s", run.run_id)
        finally:
            stop_timer.set()
            timer.join(timeout=self._FLUSH_MAX_INTERVAL + 1.0)
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
        self._release_key(run.run_key, run_id=run.run_id)
        self._do_emit("console_status", status_snapshot, f"console:{run.run_id}")
        # P2：终态快照保留（TTL=本地终态保留期）——跨实例 status 在 run 结束后
        # 仍可读（与本地 `_sweep_terminal_runs` 的保留语义对齐）。
        if _console_registry.console_registry_enabled():
            self._publish_snapshot(run, ttl_seconds=self._terminal_retention_seconds)
        logger.info(
            "run_console_finished run_id=%s status=%s exit=%s seq=%d",
            run.run_id, status_snapshot["status"], returncode, status_snapshot["seq"],
        )
        if run.on_complete is not None:
            try:
                run.on_complete(run)
            except Exception:
                logger.exception("run_console_on_complete_failed run_id=%s", run.run_id)

    def _release_key(self, run_key: str, run_id: Optional[str] = None) -> None:
        with self._lock:
            self._inflight_keys.discard(run_key)
        # #1737 P1：同步释放全局互斥与 owner 登记（CAS；幂等；失败仅告警、TTL 兜底）
        if run_id is not None and _console_registry.console_registry_enabled():
            _console_registry.release_run_key(run_key, run_id=run_id)
            _console_registry.release_owner(run_id, run_key=run_key)

    # ------------------------------------------------------------------
    # 操作
    # ------------------------------------------------------------------

    def _request_remote_cancel(self, run_id: str) -> bool:
        """P3：跨实例取消——投递请求位 + 有界等待 owner ack；超时 fail-closed。

        仅可在**非事件循环线程**调用（API 同步路由由线程池执行；事件循环内的
        调用方须经 ``asyncio.to_thread``，见 ``ai_assistant.cancel_action``），
        否则等待窗会阻塞整个事件循环。
        """
        snapshot = _console_registry.read_status_snapshot(run_id)
        if snapshot is not None and snapshot.get("status") in _TERMINAL_STATUSES:
            return False  # 已终态：无可取消
        requested_at = datetime.now(timezone.utc).isoformat()
        try:
            _console_registry.request_cancel(run_id, requested_at=requested_at)
        except _console_registry.ConsoleRegistryUnavailable as exc:
            logger.warning(
                "run_console_cancel_request_failed run_id=%s error=%s", run_id, exc,
            )
            return False
        deadline = _monotonic() + self._cancel_wait_seconds
        while _monotonic() < deadline:
            ack = _console_registry.read_cancel_ack(run_id, requested_at=requested_at)
            if ack is not None:
                canceled = bool(ack.get("canceled"))
                logger.info(
                    "run_console_cancel_forwarded run_id=%s canceled=%s by=%s",
                    run_id, canceled, ack.get("by"),
                )
                return canceled
            time.sleep(0.1)
        logger.warning(
            "run_console_cancel_timeout run_id=%s wait=%.1fs",
            run_id, self._cancel_wait_seconds,
        )
        return False

    def cancel(self, run_id: str) -> bool:
        """取消运行中的 run（进程组 kill）。返回是否发起取消。

        #1737 P3：本地无此 run 且注册表启用 → 跨实例转发（请求位 + 有界等待 ack；
        超时/注册表不可用 fail-closed 返回 False，绝不假装成功）。
        """
        run = self._runs.get(run_id)
        if run is None:
            if _console_registry.console_registry_enabled():
                return self._request_remote_cancel(run_id)
            return False
        if run._proc is None:
            return False
        with run._lock:
            if run.status in _TERMINAL_STATUSES:
                return False
            run.status = "CANCELED"
        proc = run._proc
        # #1115：组身份优先用 spawn 时留存的；现取只是兜底（父可能已被 reader 回收）
        pgid = run._pgid if isinstance(run._pgid, int) else _resolve_pgid(proc)
        try:
            if os.name == "nt":
                proc.terminate()  # NEW_PROCESS_GROUP 下 terminate 即对组生效
            elif pgid is None:
                # 拿不到可信 pgid（父已回收且无留存）—— 退化为单进程 kill，
                # 绝不用可能已复用的 pgid 去打陌生进程组
                proc.terminate()
                try:
                    proc.wait(timeout=self._cancel_grace)
                except subprocess.TimeoutExpired:
                    proc.kill()
            else:
                import signal as _signal
                try:
                    os.killpg(pgid, _signal.SIGTERM)
                except ProcessLookupError:
                    pgid = None  # 整组已散，无需收敛
                except Exception:
                    logger.exception("run_console_killpg_sigterm_failed run_id=%s pgid=%s", run_id, pgid)
                    proc.terminate()
                if pgid is not None and not _await_group_exit(
                    proc, pgid, self._cancel_grace,
                ):
                    # 父退出 ≠ 整组退出：忽略 SIGTERM 的子孙必须升级 SIGKILL，
                    # 否则界面已 CANCELED 而后代仍在跑（且握着 stdout 写端，
                    # reader 线程等不到 EOF，run_key 也释放不了）
                    logger.warning(
                        "run_console_group_alive_after_sigterm run_id=%s pgid=%s", run_id, pgid,
                    )
                    try:
                        os.killpg(pgid, _signal.SIGKILL)
                    except ProcessLookupError:
                        pgid = None
                    except Exception:
                        logger.exception("run_console_killpg_sigkill_failed run_id=%s pgid=%s", run_id, pgid)
                        proc.kill()
                    if pgid is not None and not _await_group_exit(
                        proc, pgid, self._cancel_grace,
                    ):
                        logger.error(
                            "run_console_group_alive_after_sigkill run_id=%s pgid=%s", run_id, pgid,
                        )
        except Exception:
            logger.exception("run_console_cancel_failed run_id=%s", run_id)
        # 等 reader 线程跑完 _finalize（释放 run_key + 写终态），使 cancel() 返回时
        # 调用方可立即用同 run_key 重起，不会撞 RunKeyBusyError。
        if run._thread is not None and run._thread.is_alive():
            run._thread.join(timeout=self._cancel_grace + 2.0)
        logger.info("run_console_cancel_requested run_id=%s", run_id)
        return True

    def status(self, run_id: str) -> Optional[Dict[str, Any]]:
        self._sweep_terminal_runs()
        run = self._runs.get(run_id)
        if run is not None:
            return run.to_status()
        # #1737 P2：本地无此 run——多实例形态下读 owner 快照（跨实例 status；
        # `console:` 房间订阅校验共用本方法，见 socketio_server）。
        if _console_registry.console_registry_enabled():
            snapshot = _console_registry.read_status_snapshot(run_id)
            if snapshot is not None:
                return snapshot
        return None

    def _sweep_terminal_runs(self) -> None:
        """#1124：淘汰终态超保留期的运行记录，`_runs` 不随进程生命周期无界增长。

        淘汰只移除内存条目 —— replay 仍可按 `log_root/{run_id}.log` 文件回读，
        status 届时退化为 UNKNOWN（调用方按需从持久化层补全，如 jira_run 表）。
        ended_at 解析失败的记录不淘汰（宁多留不误删）。
        """
        now = datetime.now(timezone.utc)
        evicted: List[str] = []
        with self._lock:
            for rid, r in list(self._runs.items()):
                if r.status not in _TERMINAL_STATUSES or not r.ended_at:
                    continue
                try:
                    ended = datetime.fromisoformat(r.ended_at)
                except ValueError:
                    continue
                if (now - ended).total_seconds() > self._terminal_retention_seconds:
                    self._runs.pop(rid, None)
                    evicted.append(rid)
        if evicted:
            logger.info(
                "run_console_sweep_evicted count=%d retention=%.0fs",
                len(evicted), self._terminal_retention_seconds,
            )
            # P2：同步清理跨实例快照（best-effort；TTL 兜底）
            if _console_registry.console_registry_enabled():
                for rid in evicted:
                    _console_registry.delete_status_snapshot(rid)

    @staticmethod
    def _mark_replay_unavailable(
        result: Dict[str, Any], snapshot: Optional[Dict[str, Any]], *, reason: str,
    ) -> None:
        """#1737 P4：文件侧证据少于 owner 报告时显式标记 + 告警。

        典型形态：`STP_RUN_CONSOLE_LOG_ROOT` 未在各实例间共享——本实例读到的
        文件缺失/落后于 owner 快照的 `seq`。**不把「读不到」伪装成「没有输出」**。
        """
        owner_seq = int(snapshot.get("seq") or 0) if snapshot else 0
        logger.warning(
            "run_console_replay_unavailable run_id=%s owner_instance=%s owner_seq=%d reason=%s "
            "hint=检查 STP_RUN_CONSOLE_LOG_ROOT 是否各实例共享",
            result.get("run_id"),
            snapshot.get("instance_id") if snapshot else None,
            owner_seq,
            reason,
        )
        result["replay_unavailable"] = True

    def read_log(self, run_id: str, *, from_seq: int = 0) -> Dict[str, Any]:
        """文件 replay：返回从 from_seq（1-based，含）起的行 + 当前 seq/status。

        run 不在内存（进程重启后的历史 run / 其他实例持有的 run）时，仍从
        log_root/{run_id}.log 读文件——**#1737 P4：跨实例 replay 的部署前提是
        `STP_RUN_CONSOLE_LOG_ROOT` 对全部实例可见**（同机多进程天然共享；多机需挂
        同一存储）。status 由 P2 快照补全；文件缺失/落后于 owner 报告的 seq 时
        显式标记 `replay_unavailable` 并告警。

        #1124：流式读取 —— 内存占用与响应体均有界（`_replay_max_lines` 上限 +
        单行截断），不再 `readlines()` 全量装进内存；`seq` 仍精确统计到文件末尾。
        """
        run = self._runs.get(run_id)
        snapshot: Optional[Dict[str, Any]] = None
        if run is not None and run._log_path is not None:
            log_path = run._log_path
        else:
            # 历史/跨实例 replay：按约定路径找日志文件（共享 log_root 前提）
            log_path = self._log_root / f"{run_id}.log"
            if _console_registry.console_registry_enabled():
                snapshot = _console_registry.read_status_snapshot(run_id)
        if run is not None:
            status = run.status
        elif snapshot and snapshot.get("status"):
            # P2 快照补全（跨实例 / 历史 run 的真实状态）
            status = str(snapshot["status"])
        else:
            status = "UNKNOWN"
        if not log_path or not log_path.exists():
            owner_seq = int(snapshot.get("seq") or 0) if snapshot else 0
            result: Dict[str, Any] = {
                "run_id": run_id,
                "from_seq": from_seq,
                "lines": [],
                "seq": owner_seq,
                "status": status,
            }
            if owner_seq > 0:
                self._mark_replay_unavailable(result, snapshot, reason="log_file_missing")
            return result
        start = max(0, int(from_seq) - 1) if from_seq > 0 else 0
        lines: List[str] = []
        total = 0
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                for ln in f:
                    total += 1
                    if total > start and len(lines) < self._replay_max_lines:
                        lines.append(ln.rstrip("\n")[: self._replay_max_line_chars])
        except Exception:
            logger.exception("run_console_read_log_failed run_id=%s", run_id)
        owner_seq = int(snapshot.get("seq") or 0) if snapshot else 0
        result = {
            "run_id": run_id,
            "from_seq": start + 1,
            "lines": lines,
            "seq": max(total, owner_seq),
            "status": status,
        }
        if owner_seq > total:
            self._mark_replay_unavailable(result, snapshot, reason="log_file_behind")
        return result

    def log_file_path(self, run_id: str) -> Path:
        """返回 run 的日志文件路径（不依赖 run 是否在内存）。"""
        run = self._runs.get(run_id)
        if run is not None and run._log_path is not None:
            return run._log_path
        return self._log_root / f"{run_id}.log"

    def is_key_busy(self, run_key: str) -> bool:
        with self._lock:
            return run_key in self._inflight_keys

    # ------------------------------------------------------------------
    # 多实例归属注册表（#1737 P1 / ADR-0027 P3-4）
    # ------------------------------------------------------------------

    def _registry_interval_seconds(self) -> float:
        """续期间隔 = TTL/3（下限 10s）——保证每个 TTL 窗口至少两次续期机会。"""
        return max(10.0, _console_registry.console_registry_ttl_seconds() / 3.0)

    def _snapshot_payload(self, run: ConsoleRun) -> Dict[str, Any]:
        """状态快照 = ``to_status()`` + ``updated_at``（跨实例读取的时点提示）。"""
        with run._lock:
            snapshot = run.to_status()
        snapshot["updated_at"] = datetime.now(timezone.utc).isoformat()
        return snapshot

    def _publish_snapshot(self, run: ConsoleRun, *, ttl_seconds: float) -> None:
        """best-effort 发布状态快照（P2）——失败仅告警，绝不影响 run 本身。"""
        try:
            _console_registry.publish_status_snapshot(
                run.run_id, self._snapshot_payload(run), ttl_seconds=int(ttl_seconds),
            )
        except _console_registry.ConsoleRegistryUnavailable as exc:
            logger.warning(
                "run_console_snapshot_publish_failed run_id=%s error=%s", run.run_id, exc,
            )

    def _ensure_registry_ticker(self) -> None:
        if not _console_registry.console_registry_enabled():
            return
        with self._lock:
            if self._registry_ticker is not None and self._registry_ticker.is_alive():
                return
            self._registry_ticker_stop.clear()
            ticker = threading.Thread(
                target=self._registry_ticker_loop,
                name="run-console-registry",
                daemon=True,
            )
            self._registry_ticker = ticker
        ticker.start()

    def _control_tick_seconds(self) -> float:
        """控制 tick：消费取消请求的检查节奏（默认 1s，须小于取消等待窗）。"""
        return max(
            0.2,
            _parse_positive_float(
                os.getenv("STP_CONSOLE_CONTROL_TICK_SECONDS"),
                _CONTROL_TICK_SECONDS_DEFAULT,
            ),
        )

    def _registry_ticker_loop(self) -> None:
        """控制 tick（1s 级）消费取消请求；注册表续期按 TTL/3 到期才做。"""
        refresh_interval = self._registry_interval_seconds()
        last_refresh = 0.0
        while not self._registry_ticker_stop.wait(self._control_tick_seconds()):
            try:
                self._process_cancel_requests_once()
            except Exception:
                logger.exception("run_console_cancel_tick_failed")
            now = time.monotonic()
            if now - last_refresh >= refresh_interval:
                try:
                    self._renew_registrations_once()
                except Exception:
                    logger.exception("run_console_registry_tick_failed")
                last_refresh = now

    def _process_cancel_requests_once(self) -> None:
        """P3 owner 侧：消费本实例非终态 run 的取消请求，执行后回写 ack。

        请求位由请求方投递（``stp:console:cancelreq:<run_id>``）；本 tick 发现即
        执行本地取消语义（进程组 kill），随后清请求位 + 回写带**同一指纹**的 ack。
        """
        with self._lock:
            runs = [
                r for r in self._runs.values() if r.status not in _TERMINAL_STATUSES
            ]
        for run in runs:
            request = _console_registry.read_cancel_request(run.run_id)
            if request is None:
                continue
            requested_at = str(request.get("requested_at") or "")
            logger.info(
                "run_console_cancel_request_received run_id=%s from=%s",
                run.run_id,
                request.get("instance_id"),
            )
            canceled = self.cancel(run.run_id)
            _console_registry.clear_cancel_request(run.run_id)
            _console_registry.publish_cancel_ack(
                run.run_id, requested_at=requested_at, canceled=bool(canceled),
            )

    def _renew_registrations_once(self) -> None:
        """续期本实例全部非终态 run 的互斥键与 owner 键。

        互斥键 ``lost``（确认外部持有/键丢失）→ 止损取消（裁决 ③ 窄化自杀）；
        Redis 瞬态错误（``unavailable``）→ 仅告警，等下个 tick（不误杀）。
        """
        with self._lock:
            runs = [
                r for r in self._runs.values() if r.status not in _TERMINAL_STATUSES
            ]
        for run in runs:
            key_status = _console_registry.renew_run_key(run.run_key, run_id=run.run_id)
            if key_status == _console_registry.RENEW_LOST:
                self._abort_run_key_lost(run)
                continue
            if key_status == _console_registry.RENEW_UNAVAILABLE:
                continue
            owner_status = _console_registry.renew_owner(run.run_id, run_key=run.run_key)
            if owner_status == "foreign":
                logger.error(
                    "run_console_owner_foreign run_id=%s instance_id=%s",
                    run.run_id,
                    _console_registry.control_plane_instance_id(),
                )
            # P2：刷新状态快照 TTL；键被淘汰/丢失 → 重发全文
            snapshot_ttl = _console_registry.console_registry_ttl_seconds()
            if not _console_registry.refresh_status_ttl(
                run.run_id, ttl_seconds=snapshot_ttl,
            ):
                self._publish_snapshot(run, ttl_seconds=snapshot_ttl)

    def _abort_run_key_lost(self, run: ConsoleRun) -> None:
        """确认失去全局互斥 → 止损取消（保「同 key 全局至多一个 RUNNING」不变量）。"""
        logger.error(
            "console_run_key_lost run_id=%s run_key=%s instance_id=%s action=abort",
            run.run_id,
            run.run_key,
            _console_registry.control_plane_instance_id(),
        )
        with run._lock:
            if run.status in _TERMINAL_STATUSES:
                return
            run.error = (
                "run_key_lost: 全局互斥已确认失效（键被外部持有或丢失），"
                "本 run 止损取消（#1737）"
            )[:500]
        self.cancel(run.run_id)

    def shutdown(self) -> None:
        """进程退出收尾：cancel 所有 inflight run 并 join reader 线程。

        幂等、安全——无 run 或已终止的直接跳过。在 lifespan shutdown 调用，
        避免子进程成孤儿（daemon reader 线程被强杀但 Popen 子进程不会随父退出）。
        不清空单例，与 _reset_for_tests（测试专用，清单例）区分。
        """
        if not self._configured:
            return
        self._registry_ticker_stop.set()
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
        # #1737 P1：cancel→reader→_finalize 之外再显式兜底释放（CAS 幂等）
        if _console_registry.console_registry_enabled():
            for run in runs:
                self._release_key(run.run_key, run_id=run.run_id)
        logger.info("run_console_shutdown_complete")


def _monotonic() -> float:
    import time
    return time.monotonic()


__all__ = ["RunConsole", "RunConsoleError", "RunKeyBusyError", "ConsoleRun"]
