"""
设备发现和采集模块 - 用于测试
"""
import logging
import os
import re
import signal
import subprocess
import sys
from typing import Dict, List, Any, Optional, Tuple

from .device_platform import PLATFORM_UNKNOWN, detect_device_platform

logger = logging.getLogger(__name__)

_STATIC_DEVICE_SERIALS_ENV = "STP_STATIC_DEVICE_SERIALS"
_ADB_SERVER_PORT_ENV = "ANDROID_ADB_SERVER_PORT"
_DEFAULT_ADB_SERVER_PORT = 5037
_ADB_FORK_SERVER_LINE_RE = re.compile(r"^\s*(\d+)\s+(\d+)\s+(.+)$")
_ADB_FORK_SERVER_ARGS_RE = re.compile(r"\bfork-server\s+server\b")
_ADB_PORT_RE = re.compile(r"(?:-L\s+tcp:(\d+)|-P\s+(\d+))")


def _static_device_serials() -> list[str]:
    """Optional dev/testing override: provide a static device list without ADB.

    When set (CSV), HeartbeatThread will report these devices as connected so
    control-plane smoke tests can run in environments without adb/real devices.
    """
    raw = os.getenv(_STATIC_DEVICE_SERIALS_ENV, "").strip()
    if not raw:
        return []
    return [s.strip() for s in raw.split(",") if s.strip()]


def get_adb_server_port() -> int:
    """Return the ADB server port Agent should own (env or default 5037)."""
    raw = os.getenv(_ADB_SERVER_PORT_ENV, "").strip()
    if not raw:
        return _DEFAULT_ADB_SERVER_PORT
    try:
        port = int(raw)
    except ValueError:
        logger.warning(
            "adb_server_port_invalid value=%r using_default=%d",
            raw, _DEFAULT_ADB_SERVER_PORT,
        )
        return _DEFAULT_ADB_SERVER_PORT
    if not 1 <= port <= 65535:
        logger.warning(
            "adb_server_port_out_of_range value=%d using_default=%d",
            port, _DEFAULT_ADB_SERVER_PORT,
        )
        return _DEFAULT_ADB_SERVER_PORT
    return port


def _parse_fork_server_line(line: str) -> Optional[Dict[str, Any]]:
    """Parse one `ps -eo pid=,uid=,args=` line into an adb fork-server record."""
    match = _ADB_FORK_SERVER_LINE_RE.match(line)
    if not match:
        return None
    pid_str, uid_str, args = match.groups()
    if not _ADB_FORK_SERVER_ARGS_RE.search(args):
        return None
    argv0 = args.split(maxsplit=1)[0] if args.split(maxsplit=1) else ""
    if not os.path.basename(argv0).startswith("adb"):
        return None
    port_match = _ADB_PORT_RE.search(args)
    port = int(port_match.group(1) or port_match.group(2)) if port_match else None
    return {
        "pid": int(pid_str),
        "uid": int(uid_str),
        "port": port,
        "cmdline": args,
    }


def list_adb_fork_servers() -> List[Dict[str, Any]]:
    """Enumerate running adb fork-server daemons (all users, detection only)."""
    try:
        result = subprocess.run(
            ["ps", "-eo", "pid=,uid=,args="],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception as exc:
        logger.warning(f"adb_fork_server_scan_failed: {exc}")
        return []

    servers = []
    for line in result.stdout.splitlines():
        server = _parse_fork_server_line(line)
        if server is not None:
            servers.append(server)
    if servers:
        logger.debug(
            "adb_fork_servers_found ports=%s",
            [s.get("port") for s in servers],
        )
    return servers


def _kill_adb_server(server: Dict[str, Any], adb_path: str) -> bool:
    """Gracefully kill one adb fork-server; falls back to SIGTERM on the pid."""
    port = server.get("port")
    if port is not None:
        env = dict(os.environ)
        env[_ADB_SERVER_PORT_ENV] = str(port)
        try:
            result = subprocess.run(
                [adb_path, "kill-server"],
                capture_output=True,
                text=True,
                timeout=10,
                env=env,
            )
            if result.returncode == 0:
                logger.info(
                    "adb_server_killed pid=%s port=%s",
                    server.get("pid"), port,
                )
                return True
            logger.warning(
                "adb_kill_server_failed pid=%s port=%s rc=%s stderr=%s",
                server.get("pid"), port, result.returncode, result.stderr.strip(),
            )
        except Exception as exc:
            logger.warning(
                "adb_kill_server_exception pid=%s port=%s error=%s",
                server.get("pid"), port, exc,
            )

    try:
        os.kill(int(server["pid"]), signal.SIGTERM)
        logger.warning(
            "adb_server_sigterm_fallback pid=%s port=%s",
            server.get("pid"), server.get("port"),
        )
        return True
    except ProcessLookupError:
        logger.info("adb_server_already_gone pid=%s", server.get("pid"))
        return True
    except OSError as exc:
        logger.warning(
            "adb_server_sigterm_failed pid=%s error=%s",
            server.get("pid"), exc,
        )
        return False


def _start_adb_server(adb_path: str, port: int) -> bool:
    """Start (or attach to) the ADB server on the desired port."""
    env = dict(os.environ)
    env[_ADB_SERVER_PORT_ENV] = str(port)
    try:
        result = subprocess.run(
            [adb_path, "start-server"],
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )
        if result.returncode == 0:
            logger.info("adb_server_started port=%s", port)
            return True
        logger.warning(
            "adb_start_server_failed port=%s rc=%s stderr=%s",
            port, result.returncode, result.stderr.strip(),
        )
        return False
    except Exception as exc:
        logger.warning("adb_start_server_exception port=%s error=%s", port, exc)
        return False


def ensure_single_adb_server(
    adb_path: str = "adb",
    port: Optional[int] = None,
) -> Dict[str, Any]:
    """Converge the host to a single ADB server on the Agent's configured port.

    Linux 上每台 USB 设备只能注册到一个 ADB server；多个 fork-server 并存会把
    设备拆分（如 5037=10 + 5039=6）。此函数先清理非目标端口的 daemon，再确保
    目标端口 server 存活并重新枚举全部 USB 设备。

    STP_STATIC_DEVICE_SERIALS 模式（开发/冒烟）下直接 no-op，不碰真实 adb。
    """
    desired_port = port or get_adb_server_port()
    result = {
        "port": desired_port,
        "servers": list_adb_fork_servers(),
        "killed": [],
        "started": False,
        "skipped": False,
    }
    if _static_device_serials():
        result["skipped"] = True
        logger.info(
            "adb_server_reconcile_skipped static_devices_mode port=%s",
            desired_port,
        )
        return result

    current_uid = os.geteuid()
    has_target_server = any(
        server.get("port") == desired_port for server in result["servers"]
    )
    for server in result["servers"]:
        if server.get("port") == desired_port:
            continue
        if server.get("uid") != current_uid:
            logger.warning(
                "adb_server_other_user pid=%s port=%s uid=%s cannot_reconcile",
                server.get("pid"), server.get("port"), server.get("uid"),
            )
            continue
        if _kill_adb_server(server, adb_path):
            result["killed"].append(server)

    if result["killed"] or not has_target_server:
        result["started"] = _start_adb_server(adb_path, desired_port)
    else:
        result["started"] = True
        logger.info("adb_server_already_single port=%s", desired_port)
    return result


def discover_devices(adb_path: str = "adb") -> List[Dict[str, Any]]:
    """
    发现所有 ADB 设备

    Returns:
        设备列表，每个设备包含 serial, adb_state, model
    """
    static_serials = _static_device_serials()
    if static_serials:
        logger.info(
            "discovered_static_devices: %d devices (env=%s)",
            len(static_serials),
            _STATIC_DEVICE_SERIALS_ENV,
        )
        return [
            {"serial": serial, "adb_state": "device", "model": "static"}
            for serial in static_serials
        ]

    try:
        result = subprocess.run(
            [adb_path, "devices", "-l"],
            capture_output=True,
            text=True,
            timeout=10
        )
        lines = result.stdout.splitlines()
    except Exception as e:
        logger.error(f"adb_devices_failed: {e}")
        return []

    devices = []
    for line in lines[1:]:  # 跳过第一行标题
        if not line.strip():
            continue

        parts = line.split()
        if len(parts) < 2:
            continue

        serial = parts[0]
        adb_state = parts[1] if len(parts) > 1 else "unknown"

        # 解析 model
        model = None
        for part in parts:
            if part.startswith("model:"):
                model = part.split(":", 1)[1]

        devices.append({
            "serial": serial,
            "adb_state": adb_state,
            "model": model,
        })

    logger.info(f"discovered_devices: {len(devices)} devices")
    return devices


def collect_device_info(adb_path: str, serial: str, raw_adb_state: str = "device") -> Dict[str, Any]:
    """
    采集单台设备的基础信息

    Args:
        adb_path: adb 命令路径
        serial: 设备序列号
        raw_adb_state: `adb devices -l` 原始上报状态（discover_devices 产出）。
            非 "device"（如 "unauthorized"/"no permissions"/"authorizing"）说明
            设备已被 ADB 发现但不可用，直接判定为 error，不再探测 shell（探测必然失败）。

    Returns:
        设备信息字典
    """
    if serial in set(_static_device_serials()):
        return {
            "serial": serial,
            "adb_state": "device",
            "adb_connected": True,
            "model": "static",
            "battery_level": None,
            "temperature": None,
            "network_latency": None,
            "build_display_id": None,
            "platform": PLATFORM_UNKNOWN,
        }

    info = {
        "serial": serial,
        "adb_state": "unknown",
        "adb_connected": False,
        "model": None,
        "battery_level": None,
        "temperature": None,
        "network_latency": None,
        "build_display_id": None,
        "platform": PLATFORM_UNKNOWN,
    }

    if raw_adb_state and raw_adb_state != "device":
        info["adb_state"] = raw_adb_state
        info["adb_connected"] = False
        logger.warning(f"adb_device_unusable: {serial}, raw_adb_state={raw_adb_state}, adb_connected=False")
        return info

    # 检查 ADB 连接状态
    try:
        result = subprocess.run(
            [adb_path, "-s", serial, "shell", "echo", "test"],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            info["adb_state"] = "device"
            info["adb_connected"] = True
            logger.info(f"adb_check_success: {serial}, adb_connected=True")
        else:
            info["adb_state"] = "offline"
            info["adb_connected"] = False
            logger.warning(f"adb_check_failed: {serial}, returncode={result.returncode}, adb_connected=False")
            return info
    except Exception as e:
        logger.warning(f"adb_check_exception: {serial}, error={e}, adb_connected=False")
        info["adb_state"] = "offline"
        info["adb_connected"] = False
        return info

    # 采集电池信息
    try:
        result = subprocess.run(
            [adb_path, "-s", serial, "shell", "dumpsys", "battery"],
            capture_output=True,
            text=True,
            timeout=10
        )
        battery_text = result.stdout
        info["battery_level"] = _parse_battery_level(battery_text)
        info["temperature"] = _parse_battery_temp(battery_text)
    except Exception as e:
        logger.warning(f"battery_parse_failed: {serial}, error={e}")

    # 采集版本信息
    try:
        result = subprocess.run(
            [adb_path, "-s", serial, "shell", "getprop", "ro.build.display.id"],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            info["build_display_id"] = result.stdout.strip()
    except Exception as e:
        logger.warning(f"build_display_id_failed: {serial}, error={e}")

    # 采集 SoC 平台 (#73) — 结果按 serial 缓存,只有首次探测真正走 adb
    info["platform"] = detect_device_platform(adb_path, serial)

    # 采集网络延迟 (主目标 223.5.5.5, 备用 8.8.8.8)
    latency = _ping_with_fallback(adb_path, serial, "223.5.5.5", fallback="8.8.8.8")
    if latency is not None:
        info["network_latency"] = latency

    return info


def _parse_battery_level(text: str) -> int:
    """从 dumpsys battery 输出中解析电量"""
    for line in text.splitlines():
        if "level:" in line:
            try:
                return int(line.split(":")[1].strip())
            except (ValueError, IndexError):
                pass
    return 0


def _parse_battery_temp(text: str) -> int:
    """从 dumpsys battery 输出中解析温度"""
    for line in text.splitlines():
        if "temperature:" in line:
            try:
                # 温度通常是 0.1摄氏度为单位
                temp = int(line.split(":")[1].strip()) / 10
                return int(temp)
            except (ValueError, IndexError):
                pass
    return 0


def _parse_ping_time(text: str) -> Optional[float]:
    """
    从 ping 输出中解析平均延迟时间

    优先解析 rtt 汇总行获取平均值，如果没有则使用单个 time= 值

    Args:
        text: ping 命令输出文本

    Returns:
        平均延迟时间（毫秒），解析失败返回 None
    """
    try:
        lines = text.splitlines()

        # 第一遍：查找 rtt min/avg/max/mdev 行 (Linux 格式，包含平均值)
        for line in lines:
            if "rtt min/avg/max/mdev" in line or "round-trip" in line:
                # 格式: rtt min/avg/max/mdev = 1.234/5.678/9.012/1.234 ms
                # 或: round-trip min/avg/max = 1.234/5.678/9.012 ms
                parts = line.split("=")[1].strip().split("/")
                if len(parts) >= 2:
                    # parts[1] 是 avg 值，可能包含 "ms" 后缀
                    avg_str = parts[1].strip().replace("ms", "").strip()
                    return float(avg_str)

        # 第二遍：查找 time=XXms 格式 (每行输出，返回最后一个值)
        last_time = None
        for line in lines:
            if "time=" in line and "bytes from" in line:
                # 提取 time=XXms 或 time=XX.Xms
                for part in line.split():
                    if part.startswith("time="):
                        time_str = part.split("=")[1].replace("ms", "").strip()
                        try:
                            last_time = float(time_str)
                        except (ValueError, TypeError):
                            pass

        if last_time is not None:
            return last_time

    except Exception as e:
        logger.debug(f"parse_ping_exception: {e}")

    return None


def _ping_with_fallback(adb_path: str, serial: str, target: str, fallback: Optional[str] = None) -> Optional[float]:
    """
    使用 ping 检测网络延迟，支持备用目标切换

    Args:
        adb_path: adb 命令路径
        serial: 设备序列号
        target: 主 ping 目标
        fallback: 备用 ping 目标（可选）

    Returns:
        平均延迟时间（毫秒），失败返回 None
    """
    def _ping(host: str) -> Tuple[Optional[float], bool]:
        """执行 ping 并返回 (延迟, 是否成功)"""
        try:
            result = subprocess.run(
                [adb_path, "-s", serial, "shell", "ping", "-c", "3", host],
                capture_output=True,
                text=True,
                timeout=15
            )
            # 记录原始输出用于调试
            logger.info(f"ping_raw_output: {serial}, target={host}, returncode={result.returncode}")
            logger.info(f"ping_stdout: {serial}, stdout={result.stdout[:500] if result.stdout else 'empty'}")
            if result.stderr:
                logger.warning(f"ping_stderr: {serial}, stderr={result.stderr[:200]}")

            # 检查是否成功
            if result.returncode != 0:
                logger.warning(f"ping_returncode_failed: {serial}, target={host}, returncode={result.returncode}")
                return None, False
            # 检查是否 100% 丢包
            if "100% packet loss" in result.stdout or "100.0% packet loss" in result.stdout:
                logger.warning(f"ping_packet_loss: {serial}, target={host}, 100% packet loss")
                return None, False
            # 解析延迟
            latency = _parse_ping_time(result.stdout)
            if latency is not None:
                logger.info(f"ping_parse_success: {serial}, target={host}, latency={latency}ms")
            else:
                logger.warning(f"ping_parse_failed: {serial}, target={host}, could not parse latency from output")
            return latency, latency is not None
        except Exception as e:
            logger.error(f"ping_exception: {serial}, target={host}, error={e}")
            return None, False

    # 尝试主目标
    latency, success = _ping(target)
    if success:
        return latency

    # 尝试备用目标
    if fallback:
        latency, success = _ping(fallback)
        if success:
            return latency

    return None


def main(argv: Optional[List[str]] = None) -> int:
    """CLI for inspecting/reconciling ADB fork-server daemons (agentctl 复用)."""
    import argparse
    import json

    def _emit(text: str) -> None:
        sys.stdout.write(text + "\n")

    parser = argparse.ArgumentParser(
        prog="python -m agent.device_discovery",
        description="Inspect/reconcile ADB fork-server daemons on this host.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect_parser = subparsers.add_parser(
        "inspect", help="list running adb fork-server daemons"
    )
    inspect_parser.add_argument("--json", action="store_true", help="output JSON")

    repair_parser = subparsers.add_parser(
        "repair", help="kill foreign adb fork-servers and ensure configured server"
    )
    repair_parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="desired adb server port (default: ANDROID_ADB_SERVER_PORT or 5037)",
    )
    repair_parser.add_argument("--json", action="store_true", help="output JSON")

    args = parser.parse_args(argv)

    if args.command == "inspect":
        servers = list_adb_fork_servers()
        if args.json:
            _emit(json.dumps(servers, indent=2))
        elif not servers:
            _emit("no adb fork-server daemons")
        else:
            for server in servers:
                _emit(
                    "pid={pid} uid={uid} port={port} {cmdline}".format(
                        pid=server["pid"],
                        uid=server["uid"],
                        port=server.get("port") or "-",
                        cmdline=server["cmdline"],
                    )
                )
        return 0

    if args.command == "repair":
        result = ensure_single_adb_server(port=args.port)
        if args.json:
            _emit(json.dumps(result, indent=2, default=str))
        else:
            _emit(f"desired port: {result['port']}")
            _emit(f"adb fork-servers: {len(result['servers'])}")
            if result.get("skipped"):
                _emit("skipped: STP_STATIC_DEVICE_SERIALS mode")
            for server in result.get("killed", []):
                _emit(
                    "killed: pid={pid} port={port}".format(
                        pid=server["pid"], port=server.get("port")
                    )
                )
            if result.get("started"):
                _emit("target adb server ready")
            else:
                _emit("target adb server NOT ready")
                return 1
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
