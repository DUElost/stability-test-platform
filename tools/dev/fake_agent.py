#!/usr/bin/env python3
"""DEV ONLY：可复用的「假 Agent」夹具（#2402）。

为什么需要它
------------
job / step 级实时面在隔离 dev 栈里**曾经不可测**：派发门禁要 Agent 真实应答
（``verify_scripts`` 逐脚本 sha256）、``claim`` 返回的 ``fencing_token`` 只在
``device_leases`` 表里、认领后约 2 分钟不回报进度就被回收成 FAILED。上一轮测试者
为此在容器里现写三件套，自踩 4 个坑（见下），结论无法被下一次执行复用。本工具把
那三件套固化成一个 CLI，并把踩过的坑写进代码与文档。

红线（写死，不可协商）
----------------------
1. **只注册与回报，绝不执行脚本**：不 import ``subprocess``/``pty``、不碰 ADB、
   不跑任何脚本内容。``verify_scripts`` 只做「读文件 + sha256」，``execute_job`` /
   ``run_job`` / ``control`` 一律回 ``{"ok": True}`` 而不做任何事。
2. **只打 dev 栈**：默认 base 是 compose 在**宿主侧**映射的 ``127.0.0.1:18000``。指向
   ``:8000``（宿主上即本机生产控制面）**默认拒绝**，需 ``--allow-non-dev-target`` 显式
   越过。反过来，也**绝不把真机 Agent 指向 dev** ``:18000``——两边都会污染对方的事实源。
   ``--in-container`` 不是它的别名：只在 dev server 容器内、且命中真实容器指纹时放行
   loopback:8000（compose 里容器监听 8000），宿主上同地址照旧拒（#2470：让按文档操作
   的人不再必须靠越线开关才能跑通，否则守卫失去区分能力）。
3. **凭据不外泄**：``AGENT_SECRET`` 只从环境读、不打印；``fencing_token`` 由本文件
   唯一一处取用（``fencing_token_for``），**永不打印、永不落盘**。

自踩过的坑（前 5 条已在实现里处理；6–8 是观测类，#3034）
----------------------------------------------------------
- 后端镜像**没有** ``websocket-client`` → 生产 Agent 按 #1121 走 websocket-only，
  夹具连不上，只能退回 polling；polling 会话约 5 分钟掉一次 → 内置自愈重连。
  这不是 bug，是 dev 与生产的契约差异（``--transport`` 可显式指定）。
- 容器内 ``/app`` 对运行用户**只读** → 日志默认落 ``/tmp``。
- ``docker compose exec -d`` 起的进程会随会话回收 → 文档给 ``setsid nohup … </dev/null &``。
- ``fencing_token`` 不在 API 响应里 → 统一由本文件的 ``fencing_token_for`` 从
  ``device_leases`` 现取现用，避免每人写一份并顺手打印。
- **两台夹具默认会抢同一批设备**（#2570）：``device.serial`` 是全局唯一列、心跳按
  serial 认设备，早期版本的假 serial 写死成 ``DEVFIX###``（不带 host 维度）——于是
  ``DEVFIX001`` 在两台 host 之间来回改绑，第二台 host **一台自己的设备都拿不到**，
  ``claim`` 恒返回 ``[]``，测试者会把它误判成产品缺陷。现在 serial 缺省就带 host
  尾段（``192.0.2.12`` → ``DEVFIX012-001``），要固定前缀用 ``--serial-prefix``。
- **``serve`` 不发 coordinator 心跳**（#3034）：``_http_beat`` 只打
  ``/api/v1/heartbeat``。``EXECUTING_STEP`` 下执行时钟靠 extend-batch，别指望
  coordinator 心跳喂 ``last_execution_heartbeat_at``——否则 job 会被
  ``running_timeout`` 收掉，看起来像「回收器杀了正常 job」（ADR-0026 §3；技能
  ``test-env-self-check`` 有可抄配方）。
- **跨 worktree 判祖先必须显式传 commit**（#3034）：另一个 worktree 里的 ``HEAD``
  不是你以为的那份；``git merge-base`` / ``git merge-base --is-ancestor`` 要带
  具体 sha，不要裸 ``HEAD``。
- **dev server 镜像无 ``ps`` / ``pkill``**（#3034 / #2743）：清理夹具只能 Python 扫
  ``/proc`` + ``os.kill``；宿主上 ``pkill -f``「已 terminate」不等于容器内已死。

用法
----
    export AGENT_SECRET=...                      # 与 dev 栈同值，见 .env.server

    # 默认 base 是 compose 在**宿主侧**映射的 127.0.0.1:18000。在容器里跑要换寻址并
    # 声明 --in-container（容器内 18000 没有监听，实测 Connection refused #2470）：
    export FA="python /app/tools/dev/fake_agent.py --base http://127.0.0.1:8000 --in-container"
    docker compose exec -T server $FA heartbeat --count 3
    docker compose exec -T server $FA serve --lifetime 600

    # 在宿主上跑则不需要这两个参数：python tools/dev/fake_agent.py heartbeat --count 3
    docker compose exec -T server python /app/tools/dev/fake_agent.py claim --capacity 4
    docker compose exec -T server python /app/tools/dev/fake_agent.py step --job 12 \\
        --step step_init_1 --status RUNNING
    docker compose exec -T server python /app/tools/dev/fake_agent.py complete --job 12
    docker compose exec -T server python /app/tools/dev/fake_agent.py inject \\
        --event step_log --data '{"job_id":12,"line":"hello"}'

    # 多 host 形状（host 级 abort 扇出 / 跨 host 双驱动 / device_host_drift 整批阻断）：
    # 起两台、各带自己的 IP，serial 前缀自动分开，不需要额外参数（#2570/#2569）
    python tools/dev/fake_agent.py --host-ip 192.0.2.11 --host-id 192-0-2-11 --devices 30 serve
    python tools/dev/fake_agent.py --host-ip 192.0.2.12 --host-id 192-0-2-12 --devices 3 serve
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Callable

# 红线 1：本文件**不得**引入 subprocess/pty/shutil.rmtree 之类的执行能力。
# 由 tests/test_dev_fake_agent.py 以源码断言钉住（结构守卫），所以这里不需要注释解释为什么没有。

DEV_DEFAULT_BASE = "http://127.0.0.1:18000"
#: 本机生产控制面地址——夹具默认拒绝指向它（红线 2）
NON_DEV_PORTS = {"8000"}
AGENT_NS = "/agent"
#: 服务端会推给 Agent 的事件（夹具只回 ack，不做任何事；verify_scripts 例外：只读哈希）
PUSH_EVENTS = ("control", "verify_scripts", "execute_job", "run_job", "dispatch", "job_command")
#: Agent → 服务端的事件（`socketio_server.AgentNamespace` 的 on_* 集合）
AGENT_EMIT_EVENTS = ("step_log", "job_status", "heartbeat")


class FixtureRefused(RuntimeError):
    """夹具主动拒绝执行（红线触发），而不是崩在半路。"""


#: 假设备 serial 前缀的缺省基准（#2570）。真正的 serial 是 ``<前缀>-<序号>``。
SERIAL_PREFIX_BASE = "DEVFIX"


def default_serial_prefix(host_ip: str | None) -> str:
    """按 host 生成假设备 serial 前缀（#2570）——**host 维度是缺省行为，不是可选项**。

    ``device.serial`` 全局唯一且心跳按 serial 认设备：前缀不带 host 时，两台夹具
    必然争用同一批行（后果见模块 docstring 的坑 5）。取 IP 尾段做前缀，是因为
    dev 栈里 host 身份就是按 IP 认的（``host_id: "0"`` 自动注册哨兵）。

    ``192.0.2.12`` → ``DEVFIX012``；尾段非数字（传成主机名）→ 取其字母数字尾巴
    ``DEVFIX-<slug>``；连尾段都没有 → 退回裸 ``DEVFIX``（老形状）。**只取尾段**是
    已知边界：跨 /24 同尾段的两台假 host 仍会同前缀，那种拓扑用 ``--serial-prefix``
    显式区分——退化到老行为（争用同一批行），不会退化成崩。
    """
    if not host_ip:
        return SERIAL_PREFIX_BASE
    tail = str(host_ip).replace(":", ".").split(".")[-1].strip()
    digits = "".join(ch for ch in tail if ch.isdigit())
    if digits:
        return f"{SERIAL_PREFIX_BASE}{digits[-3:].zfill(3)}"
    slug = re.sub(r"[^0-9A-Za-z]", "", tail)[:6]
    return f"{SERIAL_PREFIX_BASE}-{slug}" if slug else SERIAL_PREFIX_BASE


def log(msg: str, log_file: str | None = None) -> None:
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    print(line, flush=True)
    if not log_file:
        return
    # 容器内 /app 只读：调用方给的路径应在 /tmp（见模块 docstring 的坑 2）
    try:
        with open(log_file, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def build_heartbeat_payload(
    seq: int, *, device_count: int = 3, host_ip: str = "192.0.2.11",
    host_name: str = "dev-fake-host", agent_version: str = "9.9.9-devfixture",
    serial_prefix: str | None = None,
) -> dict[str, Any]:
    """造一份能让 dev 栈「有主机 + 有设备」的心跳体（纯函数，可单测）。

    ``host_id: "0"`` 是老 Agent 的自动注册哨兵：后端按 IP 建/找主机，所以夹具
    不需要先在 UI 里点「添加主机」。设备平台交替 mtk / qualcomm，便于验证平台维度
    的筛选与染色；奇数轮把第 3 台设备置为 offline，制造一次可观察的状态翻转。

    ``serial_prefix`` 缺省由 ``host_ip`` 派生（#2570）：**不同 host 的假设备 serial
    必须互不相同**，否则两台夹具只是在互相抢行。显式传值（含 ``--serial-prefix``）
    优先，便于把某台夹具固定成可预测的一批 serial。
    """
    prefix = serial_prefix if serial_prefix is not None else default_serial_prefix(host_ip)
    devices: list[dict[str, Any]] = []
    for i in range(1, max(0, device_count) + 1):
        offline = bool(seq % 2) and i == 3
        devices.append({
            "serial": f"{prefix}-{i:03d}",
            "model": f"DevFix-Model-{i}",
            "platform": "qualcomm" if i % 2 else "mtk",
            "adb_state": "offline" if offline else "device",
            "adb_connected": not offline,
            "battery_level": 40 + (seq % 7) * 3 + i,
            "battery_temp": 280 + i,
            "wifi_rssi": -50 - i,
            "network_latency": 11 + (seq % 7),
        })
    return {
        "host_id": "0",
        "status": "ONLINE",
        "host": {"ip": host_ip, "hostname": host_name},
        "devices": devices,
        "capacity": {"permit_cap": 5},
        "health": {"disk_free_gb": 120},
        "agent_version": agent_version,
    }


def require_secret() -> str:
    secret = (os.environ.get("AGENT_SECRET") or "").strip()
    if not secret:
        raise FixtureRefused(
            "AGENT_SECRET 未设置：它只从环境读（dev 栈里与 server 容器同值，见 "
            "docs/development/local-development.md）。本工具不会打印它。"
        )
    return secret


#: 容器内可达的 dev 栈地址：compose 里 server 容器监听 8000，宿主才映射到 18000。
#: 同一个 `127.0.0.1:8000` 在宿主上是**生产控制面**（红线 2 要防的那条），在 dev
#: server 容器内才是本站——地址本身无法区分，只能靠「我在哪」显式声明（#2470）。
CONTAINER_DEV_HOSTS = {"127.0.0.1:8000", "localhost:8000", "0.0.0.0:8000"}


def _looks_like_container() -> bool:
    """容器指纹：``--in-container`` 只有配上它才放行，否则等于把红线变成随手开关。"""
    if os.path.exists("/.dockerenv") or os.path.exists("/run/.containerenv"):
        return True
    try:
        with open("/proc/1/cgroup", encoding="utf-8") as fh:
            probe = fh.read()
    except OSError:
        return False
    return any(k in probe for k in ("docker", "kubepods", "containerd", "lxc"))


def guard_target(
    base: str,
    *,
    allow_non_dev: bool = False,
    in_container: bool = False,
    container_probe: Callable[[], bool] = _looks_like_container,
) -> str:
    """红线 2：默认拒指生产控制面（宿主上的 ``:8000``）。

    `--in-container` 是**带指纹校验**的例外：只在容器内放行 loopback:8000，
    宿主上给同样的地址仍然拒绝（`container_probe()` 为假即拒）。越过红线仍需
    `--allow-non-dev-target`，两者语义不合并。
    """
    host_port = base.rsplit("//", 1)[-1].rstrip("/")
    port = host_port.rsplit(":", 1)[-1] if ":" in host_port else ""
    if in_container and not allow_non_dev and host_port in CONTAINER_DEV_HOSTS:
        if not container_probe():
            raise FixtureRefused(
                f"--in-container 但本机没有容器指纹：拒绝把 {base!r} 当 dev 栈。"
                "在宿主上 127.0.0.1:8000 就是生产控制面（红线 2）——容器内请用 "
                "`docker compose exec -T server python /app/tools/dev/fake_agent.py …`。"
            )
        return base.rstrip("/")
    # 无端口（走 80/443）或明确非 dev 端口都算越界，需显式越过
    if not allow_non_dev and (port in NON_DEV_PORTS or port == "" or port not in {"18000"}):
        raise FixtureRefused(
            f"目标 {base!r} 不是 dev 栈（期望 compose 映射的 127.0.0.1:18000）。"
            "把夹具指向生产会把真实主机/Job 事实写进生产库；确认无误才加 "
            "--allow-non-dev-target。"
        )
    return base.rstrip("/")


def target_from_args(args: argparse.Namespace) -> str:
    """按 args 上的两个开关解析控制面地址（容器内 dev 与越线放行各管各的）。"""
    return guard_target(
        args.base,
        allow_non_dev=args.allow_non_dev_target,
        in_container=args.in_container,
    )


def post_json(
    base: str, secret: str, path: str, body: dict[str, Any], *, timeout: float = 30.0,
) -> tuple[int, dict[str, Any]]:
    """POST 一个 Agent 面端点；HTTP 错误也回 (code, 解析体) 而不是抛。"""
    req = urllib.request.Request(
        base + path,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", "X-Agent-Secret": secret},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode() or "{}"
            try:
                return resp.status, json.loads(raw)
            except json.JSONDecodeError:
                return resp.status, {"raw": raw[:400]}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()[:400]
        try:
            return exc.code, json.loads(raw)
        except Exception:  # noqa: BLE001 - 非 JSON 错误体原样返回
            return exc.code, {"raw": raw}
    except Exception as exc:  # noqa: BLE001 - 传输失败按 0 报，调用方判
        return 0, {"error": f"{type(exc).__name__}: {exc}"}


def fencing_token_for(job_id: int, session_factory: Callable[[], Any] | None = None) -> str | None:
    """该 job 当前租约的 fencing token（**永不打印/落盘**）。

    为什么直连库：``claim`` 的响应里有 token，但后续每条回报都要它，而 API 不提供
    「按 job 查 token」的入口；上一轮每个测试者各写一份 SQL、有人顺手打印。收在这
    一处，红线由用例守（输出里不得出现 token 值）。
    """
    from sqlalchemy import text

    from backend.core.database import SessionLocal

    factory = session_factory or SessionLocal
    db = factory()
    try:
        row = db.execute(
            text(
                "select l.fencing_token from device_leases l "
                "join job_instance j on j.device_id = l.device_id "
                "where j.id = :job_id order by l.id desc limit 1"
            ),
            {"job_id": job_id},
        ).first()
        return str(row[0]) if row and row[0] is not None else None
    finally:
        db.close()


def push_ack(
    event: str, payload: dict[str, Any] | None, *, host_id: str, base: str = "",
) -> dict[str, Any]:
    """服务端推给 Agent 的事件 → ack。**只注册与回报，绝不执行脚本**（红线 1）。

    派发门禁 `verify_one_host` 用 `call_agent_rpc()` 等 ack：夹具此前从不注册入站
    handler，`serve` 会话因此「看得见、答不出」，plan-run 停在队列里一行 job 都不
    产生，而日志里连一条 `verify_scripts` 痕迹都没有（#2470）。

    ``control`` 不再只回 ack（#2518）：``abort`` 要**回报 job 终态**——回执语义见
    :func:`control_ack`。
    """
    if event == "verify_scripts":
        expected = (payload or {}).get("expected") or []
        return verify_scripts_ack(expected, host_id=host_id)
    if event == "control":
        return control_ack(payload, host_id=host_id, base=base)
    return {"ok": True}


def report_job_status(
    job_id: int, status: str, *, base: str, secret: str, exit_code: int = 0,
) -> tuple[int, dict[str, Any]]:
    """回报 job 终态（``complete`` 子命令与 ``control(abort)`` 应答共用，#2518）。"""
    token = fencing_token_for(job_id)
    payload: dict[str, Any] = {"update": {"status": status, "exit_code": exit_code}}
    if token:
        payload["fencing_token"] = token
    return post_json(base, secret, f"/api/v1/agent/jobs/{job_id}/complete", payload)


def control_ack(payload: Any, *, host_id: str, base: str) -> dict[str, Any]:
    """``control`` 事件的应答（#2518）：``abort`` 必须**回报终态**，不能只回 ack。

    只回 ``{"ok": True}`` 时服务端永远等不到 job 终态 → ``abort_reaper`` 判
    ``abort_ack_timeout`` → UNKNOWN → 再等 ``UNKNOWN_GRACE_SECONDS``(300s)：dev 上
    测中止面必然 6 分钟起步，而产品侧的快路径（Agent 回报 ABORTED → 即时释放租约）
    永远进不了回归。

    契约形状取自服务端扇出（``plan_run_abort.py``）：
    ``{"command": "abort", "payload": {"job_ids": [...], "reason": ...}}``。
    **只回报，不执行**——红线 1 不变。
    """
    body = payload if isinstance(payload, dict) else {}
    command = str(body.get("command") or "")
    inner = body.get("payload") if isinstance(body.get("payload"), dict) else {}
    reported: list[int] = []
    if command == "abort":
        secret = require_secret()
        for raw in inner.get("job_ids") or []:
            try:
                job_id = int(raw)
            except (TypeError, ValueError):
                continue
            code, _ = report_job_status(job_id, "ABORTED", base=base, secret=secret)
            if code < 400:
                reported.append(job_id)
    return {"ok": True, "command": command, "reported_aborted": reported}


def register_push_handlers(
    client: Any, *, host_id: str, base: str = "",
) -> tuple[str, ...]:
    """给 ``PUSH_EVENTS`` 逐个挂 handler（``namespace=/agent``，与生产 Agent 同形）。

    ``base`` 供 ``control(abort)`` 回报终态用（#2518）；不传则只回 ack。
    """
    for event in PUSH_EVENTS:
        # 默认参数绑定当前 event：闭包直接引用循环变量会晚绑（ruff B023）
        client.on(
            event,
            lambda data, _ev=event: push_ack(_ev, data, host_id=host_id, base=base),
            namespace=AGENT_NS,
        )
    return PUSH_EVENTS


def verify_scripts_ack(expected: list[dict[str, Any]], *, host_id: str) -> dict[str, Any]:
    """派发门禁要的真应答：**只读文件算 sha256**，不执行任何东西。

    复用生产实现 ``backend.agent.script_verifier.verify_scripts_payload``，夹具因此
    与真 Agent 在门禁面前等价（否则派发永远卡在 precheck，测不到 job 级实时面）。
    """
    from backend.agent.script_verifier import verify_scripts_payload

    return verify_scripts_payload(expected or [], host_id=host_id)


# ── 子命令 ────────────────────────────────────────────────────────────────

def cmd_heartbeat(args: argparse.Namespace) -> int:
    secret = require_secret()
    base = target_from_args(args)
    rc = 0
    for seq in range(1, max(1, args.count) + 1):
        status, body = post_json(
            base, secret, "/api/v1/heartbeat",
            build_heartbeat_payload(
                seq, device_count=args.devices, host_ip=args.host_ip,
                serial_prefix=args.serial_prefix,
            ),
            timeout=20.0,
        )
        log(f"heartbeat seq={seq} HTTP={status} body={json.dumps(body, ensure_ascii=False)[:200]}",
            args.log_file)
        if status >= 400:
            rc = 1
        if seq < args.count:
            time.sleep(args.gap)
    return rc


def cmd_claim(args: argparse.Namespace) -> int:
    secret = require_secret()
    base = target_from_args(args)
    status, body = post_json(base, secret, "/api/v1/agent/jobs/claim", {
        "host_id": args.host_id, "agent_version": args.agent_version,
        "capacity": args.capacity, "device_ids": [],
    })
    jobs = body.get("data") or []
    # 只报「有没有 token」，绝不报值（红线 3）
    log(f"claim HTTP={status} -> {[(j.get('id'), j.get('device_id'), j.get('status'), bool(j.get('fencing_token'))) for j in jobs][:8]}",
        args.log_file)
    return 0 if status < 400 else 1


def cmd_step(args: argparse.Namespace) -> int:
    secret = require_secret()
    base = target_from_args(args)
    token = fencing_token_for(args.job)
    if not token:
        log(f"step job={args.job} 无可用租约（可能已被回收为 FAILED），无法回报", args.log_file)
        return 1
    status, body = post_json(
        base, secret, f"/api/v1/agent/jobs/{args.job}/steps/{args.step}/status",
        {"status": args.status, "fencing_token": token,
         "exit_code": 0 if args.status == "COMPLETED" else None},
    )
    log(f"step job={args.job} {args.step} -> {args.status} HTTP={status} "
        f"{json.dumps(body, ensure_ascii=False)[:200]}", args.log_file)
    return 0 if status < 400 else 1


def cmd_complete(args: argparse.Namespace) -> int:
    secret = require_secret()
    base = target_from_args(args)
    status, body = report_job_status(
        args.job, args.status, base=base, secret=secret, exit_code=args.exit_code,
    )
    log(f"complete job={args.job} HTTP={status} {json.dumps(body, ensure_ascii=False)[:200]}",
        args.log_file)
    return 0 if status < 400 else 1


def cmd_inject(args: argparse.Namespace) -> int:
    """一次性连上 ``/agent`` 并 emit 一个事件（造 step_log / job_status 推送面）。"""
    if args.event not in AGENT_EMIT_EVENTS:
        log(f"inject 拒绝：事件 {args.event!r} 不在服务端接受的 {AGENT_EMIT_EVENTS}", args.log_file)
        return 2
    try:
        data = json.loads(args.data)
    except json.JSONDecodeError as exc:
        log(f"inject --data 不是合法 JSON: {exc}", args.log_file)
        return 2
    return _with_connection(args, lambda emit: (emit(args.event, data), None)[1])


def _transport_order(requested: str) -> list[str]:
    if requested == "polling":
        return ["polling"]
    if requested == "websocket":
        return ["websocket"]
    # auto：先试生产契约用的 websocket（#1121），镜像缺 websocket-client 时退 polling
    return ["websocket", "polling"]


def _with_connection(
    args: argparse.Namespace,
    work: Callable[[Callable[[str, dict], Any]], Any],
    *,
    with_push_handlers: bool = False,
) -> int:
    """建一次 ``/agent`` 连接，把 ``emit`` 交给 work；处理 transport 回退与自愈。

    ``with_push_handlers`` 只给常驻的 ``serve`` 用：短命令（heartbeat/claim/step）
    发完即走，挂入站 handler 只会让进程收一堆没人消费的 ack。
    """
    secret = require_secret()
    target_from_args(args)
    try:
        import socketio
    except ImportError:
        log("需要 python-socketio（dev 镜像内含）", args.log_file)
        return 2

    last_err = ""
    for transport in _transport_order(args.transport):
        sio = socketio.Client(reconnection=False, logger=False, engineio_logger=False)
        try:
            sio.connect(
                args.base,
                namespaces=[AGENT_NS],
                auth={"agent_secret": secret, "host_id": args.host_id},
                transports=[transport],
                wait_timeout=15,
            )
        except Exception as exc:  # noqa: BLE001 - 逐个 transport 试，失败原因要可读
            last_err = f"{type(exc).__name__}: {exc}"
            continue
        if with_push_handlers:
            register_push_handlers(sio, host_id=args.host_id, base=target_from_args(args))
        if transport == "polling" and args.transport == "auto":
            log("用 polling transport（dev 镜像无 websocket-client；生产 Agent 是 "
                "websocket-only #1121，多实例拓扑下本夹具与生产不等价）", args.log_file)
        # 默认参数绑定当前 transport 的 client：闭包直接引用循环变量会晚绑（ruff B023），
        # 那样 transport 回退时 emit 可能打到已废弃的连接上。
        def emit(event: str, payload: dict[str, Any], _client=sio) -> Any:
            if event == "control":
                return _client.call(event, payload or {}, namespace=AGENT_NS, timeout=15)
            _client.emit(event, payload or {}, namespace=AGENT_NS)
            return None

        try:
            result = work(emit)
            log(f"emit ok transport={transport} "
                f"result={json.dumps(result, ensure_ascii=False, default=str)[:200]}",
                args.log_file)
            return 0
        finally:
            try:
                sio.disconnect()
            except Exception:  # noqa: BLE001 - 收尾失败不掩盖主结果
                pass
    log(f"连接失败（试过的 transport 全部拒绝）：{last_err}", args.log_file)
    return 1


def cmd_serve(args: argparse.Namespace) -> int:
    """常驻：注册 ``/agent`` + 周期 HTTP 心跳 + 消费注入文件（mtime 去重）。"""
    # 可变盒子：注入文件的已消费 mtime 要跨重连保留（重连不该把同一批指令再发一遍）
    state = {"seen": None}
    # #2743：--lifetime 是 serve 启动即定的**绝对期限**，必须与 state 同级。
    # 原先放在 work() 里＝每次自愈重连重新计时，而 polling 会话约 5 分钟掉一次
    # （坑 2）⇒ 期限形同虚设，不存在的 host 长期 ONLINE 污染共享 dev 栈。
    stop = time.time() + args.lifetime

    def work(emit: Callable[[str, dict], Any]) -> None:
        seq = 0
        while time.time() < stop:
            seq += 1
            try:
                emit("heartbeat", {})
            except Exception as exc:  # noqa: BLE001 - polling 会话会掉，靠自愈重连
                log(f"ws heartbeat failed: {exc} — 需重连", args.log_file)
                raise
            state["seen"] = _drain_inject_file(
                args.inject_file, emit, state["seen"], args.log_file,
            )
            _http_beat(args, seq)
            time.sleep(args.tick)
        # 自退必须在日志里可见（#2743：此前无法归因退出时机，因为没有任何一行说它退了）
        log(f"serve 到达 lifetime={args.lifetime:g}s，主动退出", args.log_file)
        return None

    # 自愈重连：polling 会话约 5 分钟掉一次（坑 2），掉线即重连而不退出
    attempts = 0
    while True:
        try:
            return _with_connection(args, work, with_push_handlers=True)
        except Exception as exc:  # noqa: BLE001 - 掉线后重连，最多 retry 次
            attempts += 1
            if attempts > args.reconnect_limit:
                log(f"serve 放弃：连续 {attempts - 1} 次重连失败（{exc}）", args.log_file)
                return 1
            log(f"serve 重连 {attempts}/{args.reconnect_limit}：{exc}", args.log_file)
            time.sleep(args.reconnect_backoff)


def _http_beat(args: argparse.Namespace, seq: int) -> None:
    """WS 心跳之外还要 HTTP 心跳：主机/设备状态以 HTTP 为准（两套面不同源）。"""
    try:
        secret = require_secret()
        base = target_from_args(args)
        status, _body = post_json(
            base, secret, "/api/v1/heartbeat",
            build_heartbeat_payload(
                seq, device_count=args.devices, host_ip=args.host_ip,
                serial_prefix=args.serial_prefix,
            ),
            timeout=15.0,
        )
        if status >= 400:
            log(f"http heartbeat failed HTTP={status}", args.log_file)
    except FixtureRefused as exc:
        log(f"http heartbeat skipped: {exc}", args.log_file)


def _drain_inject_file(
    path: str | None, emit: Callable[[str, dict], Any], seen: str | None,
    log_file: str | None,
) -> str | None:
    """消费「反向注入」指令文件（宿主机写、夹具按 mtime 去重）。返回新的游标。"""
    if not path or not os.path.exists(path):
        return seen
    key = str(os.stat(path).st_mtime_ns)
    if key == seen:
        return seen
    try:
        with open(path, encoding="utf-8") as fh:
            cmds = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        log(f"inject file 读取失败：{exc}", log_file)
        time.sleep(1.0)
        return seen
    for item in cmds or []:
        event = item.get("event")
        if event not in AGENT_EMIT_EVENTS:
            log(f"inject file 忽略非白名单事件 {event!r}", log_file)
            continue
        try:
            emit(event, item.get("data") or {})
            log(f"sent {event}（来自 {path}）", log_file)
        except Exception as exc:  # noqa: BLE001
            log(f"inject emit {event} 失败：{exc}", log_file)
    return key


def _global_defaults() -> dict[str, Any]:
    """全局参数的取值来源（env 优先）。`_common_options` 与 `main` 共用，不留两套。"""
    return {
        # 假主机地址用 TEST-NET-1（文档保留段，RFC 5737）：夹具是**假**主机，占真实
        # 内网地址既会被 #538 那条内网地址门禁拦下，也可能与真实主机撞 IP。
        "base": os.environ.get("STP_DEV_BASE", DEV_DEFAULT_BASE),
        "host_id": os.environ.get("STP_DEV_HOST_ID", "192-0-2-11"),
        "host_ip": os.environ.get("STP_DEV_HOST_IP", "192.0.2.11"),
        "agent_version": "9.9.9-devfixture",
        "devices": 3,
        # 空串/未设 = 按 host_ip 自动派生（#2570）；显式值优先
        "serial_prefix": (os.environ.get("STP_DEV_SERIAL_PREFIX") or "").strip() or None,
        "transport": "auto",
        "log_file": "/tmp/stp-fake-agent.log",
        "allow_non_dev_target": False,
        "in_container": False,
    }


def _common_options() -> argparse.ArgumentParser:
    """全局参数抽成 parent：**子命令前后都能写**（少一个"参数顺序"坑）。

    默认值一律 `SUPPRESS`——argparse 的 subparser 会用**自己的 default 覆盖**顶层
    已解析出的同名值，于是 `--base http://x:8000 step …` 会静默退回默认端口
    （实测踩过，红线被绕过）。改成缺失即不写，由 `_global_defaults()` 统一补。
    """
    common = argparse.ArgumentParser(add_help=False)
    d = _global_defaults()
    S = argparse.SUPPRESS
    common.add_argument("--base", default=S, help=f"控制面地址（默认 {d['base']}）")
    common.add_argument("--host-id", default=S)
    common.add_argument("--host-ip", default=S)
    common.add_argument("--agent-version", default=S)
    common.add_argument("--devices", type=int, default=S, help="心跳里的假设备数")
    common.add_argument(
        "--serial-prefix", default=S,
        help="假设备 serial 前缀；缺省按 --host-ip 尾段自动带 host 维度"
             "（192.0.2.12 -> DEVFIX012，#2570）。两台夹具抢设备时先查这里",
    )
    common.add_argument(
        "--transport", choices=("auto", "polling", "websocket"), default=S,
        help="auto=先 websocket 后 polling；dev 镜像无 websocket-client 时会退 polling",
    )
    common.add_argument(
        "--log-file", default=S,
        help="容器内 /app 只读，日志默认落 /tmp；置空串则只打 stdout",
    )
    common.add_argument("--in-container", action="store_true", default=S,
                        help="声明在 dev 栈容器内（compose exec）：放行 loopback:8000，"
                             "需真实容器指纹；宿主上给同地址照旧拒绝")
    common.add_argument("--allow-non-dev-target", action="store_true", default=S,
                        help="越过红线 2（指向非 dev 栈）——默认拒绝。容器内跑不通不该"
                             "用它兜底，那是 --in-container 的职责")
    return common


def parse_cli(argv: list[str] | None = None) -> argparse.Namespace:
    """解析 CLI 并补齐全局参数（``SUPPRESS`` 的配套入口，见 ``_common_options``）。

    直接 ``build_parser().parse_args()`` 会拿到缺属性的 namespace——需要完整参数的
    调用方（含测试）一律走本函数。
    """
    args = build_parser().parse_args(argv)
    for key, value in _global_defaults().items():
        if not hasattr(args, key):
            setattr(args, key, value)
    if args.log_file == "":
        args.log_file = None
    return args


def build_parser() -> argparse.ArgumentParser:
    common = _common_options()
    parser = argparse.ArgumentParser(
        prog="fake_agent.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        parents=[common],
        epilog=(
            "红线：只注册与回报，绝不执行脚本；只打 dev 栈（默认 127.0.0.1:18000，"
            "指 :8000 生产默认拒绝）；AGENT_SECRET 与 fencing_token 永不打印。"
            "反向亦然——不得把真机 Agent 指向 dev 端口。"
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)
    # 每个子命令也挂 parent → `step --log-file x` 与 `--log-file x step` 都合法
    _sub_parser = lambda name, **kw: sub.add_parser(name, parents=[common], **kw)

    p = _sub_parser("heartbeat", help="只发 N 次 HTTP 心跳（造主机/设备）")
    p.add_argument("--count", type=int, default=1)
    p.add_argument("--gap", type=float, default=1.5)
    p.set_defaults(func=cmd_heartbeat)

    p = _sub_parser("claim", help="认领该 host 可跑的 job")
    p.add_argument("--capacity", type=int, default=4)
    p.set_defaults(func=cmd_claim)

    p = _sub_parser("step", help="回报某个 step 的状态（自动取 fencing token）")
    p.add_argument("--job", type=int, required=True)
    p.add_argument("--step", required=True)
    p.add_argument("--status", default="RUNNING",
                   choices=("RUNNING", "COMPLETED", "FAILED", "SKIPPED"))
    p.set_defaults(func=cmd_step)

    p = _sub_parser("complete", help="回报 job 终态")
    p.add_argument("--job", type=int, required=True)
    # #2518：ABORTED 是中止链上唯一正确的终态值（state_machine 允许 RUNNING→ABORTED），
    # 缺它就连「手工模拟守约 Agent 回报中止」都做不到。
    p.add_argument("--status", default="COMPLETED",
                   choices=("COMPLETED", "FAILED", "ABORTED"))
    p.add_argument("--exit-code", type=int, default=0)
    p.set_defaults(func=cmd_complete)

    p = _sub_parser("inject", help="一次性 emit 一个 agent→server 事件（step_log 等）")
    p.add_argument("--event", required=True)
    p.add_argument("--data", default="{}", help="JSON 事件体")
    p.set_defaults(func=cmd_inject)

    p = sub.add_parser("serve", help="常驻：注册 /agent + 周期心跳 + 消费注入文件")
    p.add_argument("--lifetime", type=float, default=600.0,
                   help="serve 的绝对期限（秒）——启动即定，自愈重连不重置（#2743）")
    p.add_argument("--tick", type=float, default=5.0)
    p.add_argument("--inject-file", default=None)
    p.add_argument("--reconnect-limit", type=int, default=5)
    p.add_argument("--reconnect-backoff", type=float, default=2.0)
    p.set_defaults(func=cmd_serve)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = parse_cli(argv)
    try:
        return int(args.func(args))
    except FixtureRefused as exc:
        print(f"[REFUSED] {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
