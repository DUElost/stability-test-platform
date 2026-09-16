"""S6 controlled-run and storage verification over the site's own API (I4).

``verify`` is deliberately *not* a second execution path: it drives the same
public API an operator would use and reports what it could and could not check.

The distinction matters for acceptance.  A run that only exercised a synthetic
device must never be reported as a device acceptance, and storage write/read
probes need an explicitly authorized probe directory on the declared share —
so those checks report ``BLOCKED`` with the reason instead of passing quietly.
"""

from __future__ import annotations

import html
import os
import sys
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .agents import (
    INSTALL_POLL_INTERVAL_SECONDS,
    ApiClient,
    ApiError,
    HttpApiClient,
    heartbeat_fresh,
    host_field,
)
from .bindings import BindingError, load_binding, require_keys
from .ops import LocalOps
from .validation import (
    Check,
    ConfigValidationError,
    blocked,
    failure,
    load_site_config,
    passed,
)

RUN_TIMEOUT_SECONDS = 900.0
RUN_POLL_INTERVAL_SECONDS = INSTALL_POLL_INTERVAL_SECONDS
# 授权探针文件名前缀；探针只删自己创建的这个文件，绝不碰既有数据。
STORAGE_PROBE_PREFIX = "stp-storage-probe"
# 单步 noop 计划的受控探针；名称带 site_id 便于现场辨认与清理。
NOOP_SCRIPT = "noop"
# 目录名是 v1.0.0，脚本目录登记（/scripts/scan）与 PlanStep 用的版本号是 1.0.0。
NOOP_SCRIPT_VERSION = "1.0.0"
NOOP_STEP_TIMEOUT_SECONDS = 120
_TERMINAL_RUN_STATUSES = {"SUCCESS", "PARTIAL_SUCCESS", "FAILED"}
_SUCCESS_RUN_STATUSES = {"SUCCESS", "PARTIAL_SUCCESS"}


def _fail(check_id: str, code: str, *, location: str, role: str = "site") -> Check:
    return failure(code, location=location, role=role, check_id=check_id)


def _admin_problem(bindings_dir: Path, ref: str) -> tuple[dict[str, str], str | None]:
    try:
        values = load_binding(bindings_dir, ref)
        require_keys(values, {"USERNAME", "PASSWORD"})
    except BindingError as error:
        return {}, error.code
    return values, None


def probe_csrf(api: ApiClient) -> Check:
    """An unauthenticated write without Origin must be refused by the CSRF guard."""
    try:
        status, payload = api.raw_write_probe("/api/v1/hosts")
    except ApiError as error:
        return _fail("verify.s6.csrf", error.code, location="$.control_plane.public_url",
                     role="control_plane")
    if status == 403:
        return passed(
            "verify.s6.csrf", "control_plane", "$.control_plane.security_profile", "csrf_enforced",
            "A cookie-less write without Origin/Referer was refused with 403.",
            "Keep STP_CSRF_ENABLED=1 for production and internal profiles.",
        )
    return _fail("verify.s6.csrf", "csrf_not_enforced", location="$.control_plane.public_url",
                 role="control_plane")


def check_hosts(api: ApiClient, config: Any, *, now: float) -> list[Check]:
    """Every declared Agent must be a live Host on *this* control plane."""
    if not config.agents:
        # #2283：零 Agent 时此前落进下面的 `if not checks` 分支，产出
        # 「All 0 declared Agents are ONLINE」的 PASS——零元素证明不了任何事。
        return [blocked(
            "verify.s6.hosts", "agent", "$.agents", "no_agents_declared",
            "No Agent is declared for this site, so host liveness was not verified.",
            "Declare at least one Agent in the site config (then re-run); "
            "an empty set is not evidence of reachability.",
        )]
    try:
        hosts = api.list_hosts()
    except ApiError as error:
        return [_fail("verify.s6.hosts", error.code, location="$.control_plane.public_url",
                      role="control_plane")]
    checks: list[Check] = []
    for index, agent in enumerate(config.agents):
        location = f"$.agents[{index}]"
        expected = f"{config.site.id}-{agent.key}"
        match = next(
            (host for host in hosts if host_field(host, "ip", "ip_address") == agent.target),
            None,
        )
        if match is None:
            checks.append(_fail("verify.s6.hosts", "host_not_found", location=location, role="agent"))
            continue
        if host_field(match, "retired_at"):
            checks.append(_fail("verify.s6.hosts", "host_retired", location=location, role="agent"))
            continue
        if host_field(match, "name") != expected:
            checks.append(_fail("verify.s6.hosts", "host_conflict", location=location, role="agent"))
            continue
        alive = host_field(match, "status") == "ONLINE" and heartbeat_fresh(match, now=now)
        if not alive:
            checks.append(_fail("verify.s6.hosts", "agent_offline", location=location, role="agent"))
    if not checks:
        checks.append(passed(
            "verify.s6.hosts", "agent", "$.agents", "hosts_online",
            f"All {len(config.agents)} declared Agents are ONLINE on this control plane.",
            "Online hosts prove reachability only; the controlled chain below is the binding check.",
        ))
    return checks


def check_navigation(api: ApiClient, config: Any) -> Check:
    """MS-13：站点导航（/site/）可辨识、无凭据；缺了也不影响平台入口。"""
    try:
        status, text = api.fetch_navigation()
    except ApiError as error:
        return _fail("verify.s6.navigation", error.code,
                     location="$.control_plane.public_url", role="control_plane")
    if status != 200:
        return _fail("verify.s6.navigation", "navigation_missing", location="$.navigation")
    expected = (
        html.escape(config.site.id, quote=True),
        html.escape(config.site.display_name, quote=True),
        html.escape(config.navigation.contact, quote=True),
        html.escape(config.navigation.documentation_url, quote=True),
        "handover.json",
    )
    if any(needle not in text for needle in expected):
        return _fail("verify.s6.navigation", "navigation_missing", location="$.navigation")
    return passed(
        "verify.s6.navigation", "site", "$.navigation", "navigation_published",
        "The site navigation entry is reachable and shows site identity, owner and documentation link only.",
        "Keep the entry credential-free; platform login stays independent of it.",
    )


def select_device(
    api: ApiClient,
    *,
    serial: str | None,
) -> tuple[dict[str, Any] | None, Check]:
    """Pick the authorized test device, or report why the chain cannot run."""
    try:
        devices = api.list_devices()
    except ApiError as error:
        return None, _fail("verify.s6.devices", error.code,
                           location="$.control_plane.public_url", role="control_plane")
    if serial:
        match = next((item for item in devices if host_field(item, "serial") == serial), None)
        if match is None:
            return None, _fail("verify.s6.devices", "device_not_found", location="--device-serial")
        if host_field(match, "status") != "ONLINE":
            return None, _fail("verify.s6.devices", "device_unavailable", location="--device-serial")
        return match, passed(
            "verify.s6.devices", "agent", "$.agents", "device_selected",
            f"The declared test device {serial} is discovered and ONLINE.",
            "Use a device you are authorized to flash, run and cycle.",
        )
    online = [item for item in devices if host_field(item, "status") == "ONLINE"]
    if not online:
        return None, blocked(
            "verify.s6.devices", "agent", "$.agents", "no_device_available",
            "No ONLINE device is available for a controlled run.",
            "Attach an authorized test device (or declare STP_STATIC_DEVICE_SERIALS in isolation) "
            "and re-run; never present a simulated run as device acceptance.",
        )
    return online[0], passed(
        "verify.s6.devices", "agent", "$.agents", "device_selected",
        f"Using the first ONLINE device {host_field(online[0], 'serial') or '(serial hidden)'}.",
        "Pass --device-serial to pin the acceptance device explicitly.",
    )


def drive_noop_chain(
    api: ApiClient,
    *,
    device: dict[str, Any],
    site_id: str,
    run_timeout: float,
    poll_interval: float,
    sleep: Callable[[float], None],
    say: Callable[[str], None],
) -> tuple[dict[str, Any] | None, list[Check]]:
    """Create a one-step noop Plan, run it on the device, and follow it to terminal."""
    checks: list[Check] = []
    device_id = device.get("id")
    if not isinstance(device_id, int):
        return None, [_fail("verify.s6.chain", "device_unavailable", location="$.agents")]

    try:
        specialties = api.list_specialties()
    except ApiError as error:
        return None, [_fail("verify.s6.chain", error.code,
                            location="$.control_plane.public_url", role="control_plane")]
    if not specialties:
        return None, [_fail("verify.s6.chain", "specialty_missing", location="$.plans")]

    # 全新站点还没有脚本目录登记（Plan 引用脚本会以 INVALID_SCRIPT_REFS 422 被拒），
    # 先触发一次幂等扫描再建计划——这一步对站点可用性是必需的。
    try:
        scan_status, scan_data = api.scan_scripts()
    except ApiError as error:
        return None, [_fail("verify.s6.chain", error.code,
                            location="$.control_plane.public_url", role="control_plane")]
    if scan_status != 200:
        return None, [_fail("verify.s6.chain", "script_scan_failed", location="$.control_plane.target",
                            role="control_plane")]
    say(f"script catalog scan: {scan_data if isinstance(scan_data, dict) else 'ok'}")

    plan_name = f"s6-noop-{site_id}"
    status, payload = api.create_plan({
        "name": plan_name,
        "description": "S6 controlled noop probe created by tools.site_config verify (I4).",
        "specialty_key": specialties[0].get("key"),
        "steps": [{
            "step_key": "s6-noop",
            "script_name": NOOP_SCRIPT,
            "script_version": NOOP_SCRIPT_VERSION,
            "stage": "init",
            "sort_order": 0,
            # 组装后的 lifecycle 要求每步有具体超时：缺省 None 会被平台以
            # 422 INVALID_LIFECYCLE 拒绝（verify 不猜业务时长，给受控小值）。
            "timeout_seconds": NOOP_STEP_TIMEOUT_SECONDS,
        }],
    })
    plan = payload if isinstance(payload, dict) else None
    if status != 201 or not plan or not isinstance(plan.get("id"), int):
        return None, [_fail("verify.s6.chain", "plan_create_failed", location="$.plans")]
    plan_id = plan["id"]

    status, payload = api.run_plan(plan_id, [device_id])
    run = payload if isinstance(payload, dict) else None
    if status != 200 or not run or not isinstance(run.get("id"), int):
        return None, [_fail("verify.s6.chain", "run_trigger_failed", location="$.plans")]
    run_id = run["id"]
    say(f"driving noop plan {plan_id} as run {run_id} on device {device_id}")

    deadline = time.monotonic() + run_timeout
    terminal: dict[str, Any] | None = None
    while True:
        try:
            detail = api.plan_run(run_id)
        except ApiError as error:
            return None, [_fail("verify.s6.chain", error.code,
                                location="$.control_plane.public_url", role="control_plane")]
        run_status = str(detail.get("status") or "")
        if run_status in _TERMINAL_RUN_STATUSES:
            terminal = detail
            break
        if time.monotonic() >= deadline:
            return None, [_fail("verify.s6.chain", "run_timeout", location="$.plans")]
        say(f"run {run_id} status={run_status or 'pending'}")
        sleep(poll_interval)

    # 终态与执行证据可能相差数拍（job/step_trace 落库晚于状态翻转）：有界重读，
    # 读不到证据时如实报 run_evidence_missing，绝不把「没有证据」当通过。
    evidence_deadline = time.monotonic() + max(0.0, run_timeout)
    while True:
        if str(terminal.get("status")) not in _SUCCESS_RUN_STATUSES:
            return terminal, [_fail("verify.s6.chain", "run_failed", location="$.plans", role="agent")]
        try:
            jobs = api.plan_run_jobs(run_id)
        except ApiError as error:
            return None, [_fail("verify.s6.chain", error.code,
                                location="$.control_plane.public_url", role="control_plane")]
        executed = [job for job in jobs if job.get("device_id") == device_id]
        traces = [
            trace for job in executed for trace in job.get("step_traces", [])
            if isinstance(trace, dict)
        ]
        if executed and traces:
            break
        if time.monotonic() >= evidence_deadline:
            return terminal, [_fail("verify.s6.chain", "run_evidence_missing",
                                    location="$.plans", role="agent")]
        say(f"run {run_id} finished; waiting for execution evidence")
        sleep(poll_interval)
        try:
            terminal = api.plan_run(run_id)
        except ApiError as error:
            return None, [_fail("verify.s6.chain", error.code,
                                location="$.control_plane.public_url", role="control_plane")]
    checks.append(passed(
        "verify.s6.chain", "agent", "$.plans", "chain_completed",
        f"Plan {plan_name} (id {plan_id}) ran to {terminal.get('status')} on device {device_id} "
        f"with {len(traces)} recorded step trace(s).",
        "This is a controlled noop chain; it does not cover scan/upload/merge or firmware paths.",
    ))
    return terminal, checks


def check_watcher(api: ApiClient, run_id: int) -> Check:
    """Look for watcher lifecycle evidence in the run's own event stream."""
    try:
        events = api.plan_run_events(run_id)
    except ApiError as error:
        return _fail("verify.s6.watcher", error.code, location="$.control_plane.public_url",
                     role="control_plane")
    watcher = [
        event for event in events
        if "watcher" in f"{event.get('title', '')} {event.get('category', '')}".lower()
    ]
    if not watcher:
        return blocked(
            "verify.s6.watcher", "agent", "$.plans", "watcher_not_observed",
            "The controlled run produced no watcher lifecycle event.",
            "The noop probe has a single init step; watcher start/stop needs the patrol stages "
            "of a site plan and remains to be evidenced on the real device plan.",
        )
    return passed(
        "verify.s6.watcher", "agent", "$.plans", "watcher_observed",
        f"{len(watcher)} watcher event(s) were recorded for this run.",
        "Event text is evidence of lifecycle, not of patrol data quality.",
    )


def storage_probe(root: Path, subdir: str, *, is_mount: Callable[[Path], bool] | None = None) -> Check:
    """Write→read→remove one file under an authorized subdirectory of the share.

    Two ways a naive probe would lie, both refused here: a missing mount would
    be silently satisfied by a same-named local directory, and a path-shaped
    operator input could write outside the declared share.

    #2283：挂载判据与**安装器同源**——``LocalOps.is_mount`` 读
    ``/proc/self/mountinfo``，能认出同文件系统的 bind 挂载；``os.path.ismount``
    认不出，于是 S1 接受了一个真挂着的共享、这里却报
    ``shared_storage_not_mounted``，文档还把操作员指向「去挂一个已经挂着的共享」。
    """
    name = subdir.strip()
    if not name or "/" in name or "\\" in name or name in {".", ".."}:
        return _fail("verify.s6.storage", "storage_probe_subdir", location="--storage-probe-subdir")
    mounted = (is_mount or LocalOps().is_mount)(root)
    if not mounted:
        return _fail("verify.s6.storage", "shared_storage_not_mounted",
                     location="$.storage.mount_path", role="site")
    probe = root / name / f"{STORAGE_PROBE_PREFIX}.{os.getpid()}.{uuid.uuid4().hex[:8]}"
    payload = f"{datetime.now(timezone.utc).isoformat()} {STORAGE_PROBE_PREFIX}\n"
    try:
        probe.parent.mkdir(parents=True, exist_ok=True)
        probe.write_text(payload, encoding="utf-8")
        read_back = probe.read_text(encoding="utf-8")
    except OSError:
        return _fail("verify.s6.storage", "storage_unwritable",
                     location="$.storage.mount_path", role="site")
    finally:
        try:
            probe.unlink()
        except OSError:
            pass
    if read_back.strip() != payload.strip():
        return _fail("verify.s6.storage", "storage_probe_failed",
                     location="$.storage.mount_path", role="site")
    return passed(
        "verify.s6.storage", "site", "$.storage.mount_path", "storage_probe_ok",
        f"Wrote, read back and removed one probe file under {root / name}; nothing else was touched.",
        "This proves the declared share accepts writes from here; Agent-side mounts are asserted in S5.",
    )


def verify_site(
    config_path: str | Path,
    *,
    bindings_dir: str | Path,
    device_serial: str | None = None,
    run_timeout: float = RUN_TIMEOUT_SECONDS,
    poll_interval: float = RUN_POLL_INTERVAL_SECONDS,
    api: ApiClient | None = None,
    sleep: Callable[[float], None] = time.sleep,
    progress: Callable[[str], None] | None = None,
    now: float | None = None,
    storage_probe_subdir: str | None = None,
) -> dict:
    """S6 acceptance report for one declared site (read-mostly, fail-closed)."""
    say = progress or (lambda message: print(f"[s6] {message}", file=sys.stderr))
    checks: list[Check] = []
    try:
        config = load_site_config(config_path)
    except ConfigValidationError as error:
        return _report(list(error.checks))

    admin, problem = _admin_problem(Path(bindings_dir), config.security.initial_admin_ref)
    if problem is not None:
        return _report([_fail("verify.s6.auth", problem,
                              location="$.security.initial_admin_ref", role="control_plane")])

    api = api or HttpApiClient(config.control_plane.public_url)
    try:
        api.login(admin["USERNAME"], admin["PASSWORD"])
    except ApiError as error:
        return _report([_fail("verify.s6.auth", error.code,
                              location="$.security.initial_admin_ref", role="control_plane")])
    checks.append(passed(
        "verify.s6.auth", "control_plane", "$.security.initial_admin_ref", "admin_authenticated",
        "The initial administrator authenticated against this site's own API.",
        "Use a dedicated acceptance account once the site has more than the initial administrator.",
    ))
    checks.append(probe_csrf(api))
    if any(check.status == "FAIL" for check in checks):
        return _report(checks)

    checks.extend(check_hosts(api, config, now=now if now is not None else time.time()))
    checks.append(check_navigation(api, config))

    device, device_check = select_device(api, serial=device_serial)
    checks.append(device_check)

    terminal: dict[str, Any] | None = None
    if device is not None:
        terminal, chain_checks = drive_noop_chain(
            api, device=device, site_id=config.site.id, run_timeout=run_timeout,
            poll_interval=poll_interval, sleep=sleep, say=say,
        )
        checks.extend(chain_checks)
        if terminal is not None and isinstance(terminal.get("id"), int):
            checks.append(check_watcher(api, terminal["id"]))
    else:
        checks.append(blocked(
            "verify.s6.chain", "agent", "$.plans", "no_device_available",
            "The controlled chain was not driven: no authorized test device is available.",
            "Attach an authorized test device and re-run verify; the chain must not be simulated.",
        ))

    # 写读探针需要显式授权探针子目录（只清自己创建的文件）；未授权如实 BLOCKED，
    # 绝不退化成"写进本机同名目录"（那会掩盖"根本没挂上共享"）。
    if storage_probe_subdir:
        checks.append(storage_probe(Path(config.storage.mount_path), storage_probe_subdir))
    else:
        checks.append(blocked(
            "verify.s6.storage", "site", "$.storage.mount_path", "storage_probe_not_authorized",
            "No authorized storage write/read probe ran.",
            "Pass --storage-probe-subdir <name> to authorize writing one probe file under that "
            "subdirectory; only the probe's own file is removed, and it never writes elsewhere.",
        ))
    checks.append(blocked(
        "verify.s6.scan_upload_merge", "site", "$.agents", "not_covered",
        "scan/upload/merge was not exercised by this command.",
        "Run it with real device-log artifacts on an authorized device; a noop chain cannot cover it.",
    ))
    return _report(checks)


def _report(checks: list[Check]) -> dict:
    return {
        "stage": "verify",
        "status": "FAIL" if any(check.status == "FAIL" for check in checks) else "PASS",
        "summary": (
            "S6 verification only; BLOCKED checks state what this run could not verify and never "
            "certify a stage."
        ),
        "checks": [asdict(check) for check in checks],
        # 未覆盖路径已在 checks 里显式 BLOCKED；这里保持与其它子命令一致的报告形状。
        "deferred_checks": [],
    }
