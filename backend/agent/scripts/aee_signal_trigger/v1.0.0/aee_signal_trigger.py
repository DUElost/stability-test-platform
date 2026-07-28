"""诱发式 AEE 崩溃信号触发脚本（#72 实机验收用）。

通过 SIGSEGV 主动 kill 一个用户态 App，让 MTK AEE 机制自己生成
/data/aee_exp/db.<id>/ 事件目录并追加 /data/aee_exp/db_history 行，
供 Reconciler 在 60s 基线周期内 pull + 解析 + emit log_signal。

这比「手工 echo db_history + push 伪造目录」更真实：AEE 产出的 .dbg /
ZZ_INTERNAL 是设备实产物，reconciler 的 strict verify 与 _enrich_parsed
照常通过，risk rating S/A/B 也能在控制面正确聚合。

前置：
  - MTK 机型（展锐/高通无 /data/aee_exp，平台门禁 #73 会 skip Reconciler）
  - adb root 可用（kill -11 系统进程需 root；普通 shell 无权限）
  - 目标 App 可被 monkey 拉起（默认 com.android.settings，全机型稳定存在）

环境变量:
    STP_DEVICE_SERIAL      (required)
    STP_ADB_PATH           (default: adb)
    STP_STEP_PARAMS        (optional, JSON)

STP_STEP_PARAMS:
{
  "package_name": "com.android.settings",   // 被诱发崩溃的 App 包名
  "poll_timeout_seconds": 30,                // kill 后轮询 db_history 变化的最长等待
  "poll_interval_seconds": 1.0,              // 轮询间隔
  "signal": 11                                // 发给进程的信号，默认 11 (SIGSEGV)
}

输出 (stdout):
    {"success": true/false, "error_message": "...", "metrics": {
        "killed_pid": "...", "raw_event_type": "Native (NE)",
        "event_subtype": "NE", "db_path": "/data/aee_exp/db.01",
        "db_history_hash_before": "...", "db_history_hash_after": "...",
        "wait_seconds": 4.2, "db_dir_file_count": 3
    }}

设计依据（代码锚点，运行态以 Agent 实际解析为准）:
  - db_history 行格式见 backend/agent/aee/db_history.py:parse_db_history_line
    (≥10 逗号字段；parts[0]=db_path, parts[1]=raw_event_type,
     parts[8]=pkg_name, parts[9]=timestamp)
  - subtype 映射见 metadata.normalize_aee_subtype；S/A 风险评级规则见
    根 AGENTS.md §scan/upload/merge 跨进程契约（控制面 report_service 聚合）
"""

import hashlib
import subprocess
import sys
import time
from pathlib import Path

from _adb import adb_path, device_serial, output_result, params

# 设备脚本自包含：pipeline_engine 执行脚本时已把 parents[3](=backend/agent)
# 注入 PYTHONPATH，但手动 `python xxx.py` 调用时需兜底，故显式 insert。
_AGENT_ROOT = Path(__file__).resolve().parents[3]
if str(_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENT_ROOT))

# 与 backend/agent/aee/db_history.py:parse_db_history_line 对齐 —— 跑在
# Agent 运行机（rsync 全量推 backend/agent/，含 aee/）。
from aee.db_history import parse_db_history_line  # noqa: E402

AEE_EXP_DIR = "/data/aee_exp"
DEFAULT_PACKAGE = "com.android.settings"


# ── 基础 adb 原语 ──────────────────────────────────────────────

def _run_adb(*args: str, timeout: int = 30) -> tuple[int, str, str]:
    """Run adb with capture, return (returncode, stdout, stderr)."""
    cmd = [adb_path(), "-s", device_serial()] + list(args)
    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout,
    )
    return result.returncode, (result.stdout or "").strip(), (result.stderr or "").strip()


def _ensure_root() -> None:
    """kill -11 系统进程需 root；普通 shell 会 EPERM，导致 AEE 不落盘却误判成功。"""
    rc, out, _ = _run_adb("shell", "id -u", timeout=10)
    if rc == 0 and out.strip() == "0":
        return
    # 尝试 adb root（userdebug 固件会重启 adbd 为 root）
    _run_adb("root", timeout=15)
    time.sleep(2.0)
    rc, out, _ = _run_adb("shell", "id -u", timeout=10)
    if rc != 0 or out.strip() != "0":
        raise RuntimeError(
            f"adb root 不可用 (id -u 返回 {out!r})；SIGSEGV 系统进程需 root，"
            "否则 AEE 不会落盘，触发无效"
        )


def _resolve_pid(package: str) -> str:
    """拉起目标 App 并取其 PID。"""
    # 先确定包确实存在，避免 monkey 报 -p 后默默无 PID 浪费轮询
    rc, out, _ = _run_adb("shell", f"pm path {package}", timeout=15)
    if rc != 0 or not out.strip():
        raise RuntimeError(f"设备上未找到包 {package}（pm path 为空）")

    # monkey 拉起 LAUNCHER intent —— 比 resolve-activity 更稳，不需先解
    # launcher activity 名。1 表示只发一次事件。
    _run_adb("shell", f"monkey -p {package} -c android.intent.category.LAUNCHER 1", timeout=30)
    # 给 ActivityManager 一点时间真正 fork 出进程
    time.sleep(2.0)

    pid = _shell_first_line(f"pidof {package}")
    if pid:
        return pid.split()[0]

    # pidof 偶尔因进程名与包名不一致而空（多进程 App），退到 ps -A。
    # grep -w 精确匹配包名 token，避免误命中同前缀进程；head -1 取主进程。
    pid_line = _shell_first_line(
        f"ps -A -o PID -o NAME | grep -w {package} | head -1", timeout=15
    )
    if not pid_line:
        raise RuntimeError(
            f"已 monkey 拉起 {package} 但 pidof/ps 均未取到 PID；"
            "App 可能未真正启动或进程名与包名不一致"
        )
    return pid_line.split()[0]


def _shell_first_line(cmd: str, timeout: int = 15) -> str:
    rc, out, _ = _run_adb("shell", cmd, timeout=timeout)
    return out if rc == 0 else out


def _db_history_hash() -> str:
    """cat db_history 全文 sha256 —— 与 Reconciler._read_db_history_hashes 同源。"""
    rc, out, _ = _run_adb("shell", f"cat {AEE_EXP_DIR}/db_history", timeout=15)
    if rc != 0:
        raise RuntimeError(f"读取 {AEE_EXP_DIR}/db_history 失败 (rc={rc})：{out!r}")
    return hashlib.sha256(out.encode("utf-8", "replace")).hexdigest()


def _db_history_lines() -> list[str]:
    rc, out, _ = _run_adb("shell", f"cat {AEE_EXP_DIR}/db_history", timeout=15)
    if rc != 0:
        return []
    return [ln for ln in out.splitlines() if ln.strip()]


def _count_files_in_dir(remote_dir: str) -> int:
    """ls 远端目录文件计数；用于确认 AEE 真的落了事件目录。"""
    rc, out, _ = _run_adb("shell", f"ls -1 {remote_dir} 2>/dev/null", timeout=15)
    if rc != 0:
        return 0
    return len([ln for ln in out.splitlines() if ln.strip()])


# ── 主流程 ──────────────────────────────────────────────────────

def main() -> None:
    device_serial()  # 缺 STP_DEVICE_SERIAL 时 _adb 内部 sys.exit(1)
    args = params()

    package = args.get("package_name") or DEFAULT_PACKAGE
    signal = int(args.get("signal", 11))
    poll_timeout = float(args.get("poll_timeout_seconds", 30))
    poll_interval = float(args.get("poll_interval_seconds", 1.0))

    try:
        _ensure_root()

        before_hash = _db_history_hash()
        before_lines = _db_history_lines()
        before_line_set = set(before_lines)

        pid = _resolve_pid(package)

        # kill -<signal> <pid>：默认 SIGSEGV(11) 触发 native crash → AEE 落盘
        _run_adb("shell", f"kill -{signal} {pid}", timeout=10)

        # 轮询 db_history 变化 + 解析新增行。abort 友好：poll_interval 内可被
        # pipeline_engine SIGTERM 中断，subprocess.run 本身有 timeout 兜底。
        deadline = time.monotonic() + poll_timeout
        new_line = None
        waited = 0.0
        while time.monotonic() < deadline:
            time.sleep(poll_interval)
            waited += poll_interval
            after_lines = _db_history_lines()
            # 取按出现顺序第一个 before 不存在的新行（AEE 追加在末尾）
            for ln in after_lines:
                if ln not in before_line_set:
                    new_line = ln
                    break
            if new_line:
                break

        if not new_line:
            output_result(
                False,
                error_message=(
                    f"kill -{signal} {pid} 后 {poll_timeout:.0f}s 内 "
                    f"{AEE_EXP_DIR}/db_history 未出现新行"
                    "（可能信号被忽略/未触发 native crash，或 AEE 未落盘）"
                ),
                metrics={"killed_pid": pid, "wait_seconds": round(waited, 2)},
            )
            return

        after_hash = _db_history_hash()
        parsed = parse_db_history_line(new_line)
        if not parsed:
            output_result(
                False,
                error_message=f"新增 db_history 行解析失败（<10 字段或关键字段空）：{new_line!r}",
                metrics={
                    "killed_pid": pid,
                    "wait_seconds": round(waited, 2),
                    "raw_line": new_line,
                },
            )
            return

        db_path = parsed["db_path"]
        file_count = _count_files_in_dir(db_path)
        if file_count == 0:
            # 罕见：db_history 行已写但目录尚未落盘完；不直接失败，交给 reconciler
            # 下一轮处理，但明确标注让运维知晓
            output_result(
                True,
                warning=(
                    f"db_history 新增行已出现，但 {db_path} 暂为空（AEE 可能仍在落盘）；"
                    "Reconciler 下一周期会重试 pull"
                ),
                metrics={
                    "killed_pid": pid,
                    "raw_event_type": parsed["raw_event_type"],
                    "event_subtype": parsed["event_subtype"],
                    "event_type": parsed["event_type"],
                    "package_name": parsed["pkg_name"],
                    "db_path": db_path,
                    "db_history_hash_before": before_hash,
                    "db_history_hash_after": after_hash,
                    "wait_seconds": round(waited, 2),
                    "db_dir_file_count": 0,
                },
            )
            return

        output_result(
            True,
            metrics={
                "killed_pid": pid,
                "raw_event_type": parsed["raw_event_type"],
                "event_subtype": parsed["event_subtype"],
                "event_type": parsed["event_type"],
                "package_name": parsed["pkg_name"],
                "db_path": db_path,
                "db_history_hash_before": before_hash,
                "db_history_hash_after": after_hash,
                "wait_seconds": round(waited, 2),
                "db_dir_file_count": file_count,
            },
        )
    except subprocess.TimeoutExpired as exc:
        output_result(
            False,
            error_message=f"adb 命令超时：{exc}",
            metrics={"package_name": package},
        )
    except Exception as exc:
        output_result(False, error_message=f"{type(exc).__name__}: {exc}", metrics={"package_name": package})


if __name__ == "__main__":
    main()
