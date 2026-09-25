"""#2983：控制面 host 健康探针——解析 / 对账 / SSH 白名单 / 调度 sweep。

切片①：纯解析与对账（可离线预演 .102 / 空柜）。
切片②：固定 argv 白名单 + ``sudo -S`` 远程执行。
切片③：APScheduler 周期 sweep（并发帽 + ``host.extra.health_probe`` 连续窗）。
告警规则另开（本模块只落库与指标）。

签名词表与 Agent 侧 ``backend.agent.kernel_usb_faults`` 同源。
"""
from __future__ import annotations

import logging
import shlex
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Iterable, Optional, Protocol, Sequence, Set

from backend.agent.kernel_usb_faults import (
    parse_kernel_usb_faults,
)
from backend.services.audit_writer import record_audit
from backend.core.database import SessionLocal
from backend.core.settings.scheduler import get_scheduler_settings
from backend.core.ssh_security import create_ssh_client, resolve_host_ssh_credentials
from backend.models.host import Host
from backend.services.host_maintenance import in_maintenance_window
from backend.services.host_updater import _resolve_ssh_creds

logger = logging.getLogger(__name__)

# host.extra 键：连续窗与最近一轮摘要（口令绝不进此块）
HEALTH_PROBE_EXTRA_KEY = "health_probe"
_RECENT_VERDICTS_CAP = 8
PROBE_ERROR_VERDICT = "probe_error"

# 拓扑判定词表（进对账信号；后续告警规则按此建）。
TOPOLOGY_OK = "ok"
TOPOLOGY_BLIND = "hub_present_phones_zero"  # hub 级联在树、手机/外设归零 → 该盲
TOPOLOGY_EMPTY_CABINET = "root_hub_only"  # 仅 root hub、无级联 → 机柜未接线（.90/.91）
TOPOLOGY_UNKNOWN = "unknown"

# 常见 USB hub 级联 VID（Realtek 等机柜级联）；root hub 是 Linux 1d6b。
_ROOT_HUB_VID = "1d6b"
_CASCADE_HUB_VIDS = frozenset({"0bda", "2109", "05e3", "1a40"})  # Realtek / VIA / Genesys / Terminus


class ProbeVerdict(str, Enum):
    """单轮探针相对 agent 自报的对账结论（连续 N 轮才转红由调用方做）。"""

    ALIGNED = "aligned"
    AGENT_MUTE = "agent_mute"  # 探针见故障、自报无对应 reason
    PROBE_QUIET = "probe_quiet"  # 自报有故障、探针本轮未见（单轮不当罪）
    TOPOLOGY_MISMATCH = "topology_mismatch"
    #: 探针**自己没采到数据**（非零 rc / 该有输出却为空）。必须与 ALIGNED 区分：
    #: 否则「采集失败」被读成「设备干净」，正是本模块存在的理由被绕过（#2983 复核）。
    PROBE_ERROR = "probe_error"


@dataclass(frozen=True)
class JournalProbeFacts:
    hc_dead_seen: bool
    link_errors: int
    cable_suspect: int
    lines: int


@dataclass(frozen=True)
class TopologyFacts:
    """lsusb / sysfs 摘要折成的拓扑事实。"""

    root_hub_count: int
    cascade_hub_count: int
    peripheral_count: int  # 非 hub、非 root 的节点（手机等）
    classification: str


@dataclass(frozen=True)
class ProbeRound:
    journal: JournalProbeFacts
    topology: TopologyFacts
    #: 采集失败的探针（``name:reason``）；空 = 本轮两条都拿到了数据（#2983 复核）。
    collect_failed: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReconcileResult:
    verdict: ProbeVerdict
    signals: tuple[str, ...]


def parse_journal_probe(lines: Iterable[str]) -> JournalProbeFacts:
    """内核 journal 行 → 探针事实（复用 Agent 纯解析，保证签名一致）。"""
    faults = parse_kernel_usb_faults(lines)
    return JournalProbeFacts(
        hc_dead_seen=faults.hc_dead_seen,
        link_errors=faults.link_errors,
        cable_suspect=faults.cable_suspect,
        lines=faults.lines,
    )


def classify_lsusb_topology(lsusb_lines: Iterable[str]) -> TopologyFacts:
    """``lsusb`` 文本 → 拓扑分类（#2983 §3：盲 vs 空柜）。

    行形如 ``Bus 001 Device 002: ID 1d6b:0002 Linux Foundation 2.0 root hub``。
    解析失败的行忽略；全空 → UNKNOWN。
    """
    root = cascade = peripheral = 0
    for raw in lsusb_lines:
        line = (raw or "").strip()
        if not line:
            continue
        vid = _vid_from_lsusb(line)
        if vid is None:
            continue
        lower = line.lower()
        if vid == _ROOT_HUB_VID or "root hub" in lower:
            root += 1
            continue
        if vid in _CASCADE_HUB_VIDS or "hub" in lower:
            cascade += 1
            continue
        peripheral += 1

    total = root + cascade + peripheral
    if total == 0:
        classification = TOPOLOGY_UNKNOWN
    elif cascade > 0 and peripheral == 0:
        classification = TOPOLOGY_BLIND
    elif root > 0 and cascade == 0 and peripheral == 0:
        classification = TOPOLOGY_EMPTY_CABINET
    else:
        classification = TOPOLOGY_OK

    return TopologyFacts(
        root_hub_count=root,
        cascade_hub_count=cascade,
        peripheral_count=peripheral,
        classification=classification,
    )


def _vid_from_lsusb(line: str) -> Optional[str]:
    # ``ID abcd:1234`` —— 取 VID
    marker = " ID "
    idx = line.find(marker)
    if idx < 0:
        return None
    rest = line[idx + len(marker) :].strip()
    if len(rest) < 4 or ":" not in rest[:9]:
        return None
    vid = rest.split(":", 1)[0].strip().lower()
    return vid if len(vid) == 4 and all(c in "0123456789abcdef" for c in vid) else None


def reconcile_agent_health(
    agent_reasons: Sequence[str] | Set[str],
    round_: ProbeRound,
    *,
    link_error_threshold: int = 20,
) -> ReconcileResult:
    """探针一轮 vs agent ``health.reasons`` 对账。

    - 探针**自己没采到数据** → ``PROBE_ERROR``（优先于一切：空结果不是「干净」）；
    - 探针见 HC died / 拓扑失明，而 agent 无 ``usb_host_controller_dead`` /
      ``usb_tree_empty`` → ``AGENT_MUTE``（医生哑了）；
    - 拓扑空柜不升为失明信号（.90/.91）；
    - agent 自报故障而探针本轮安静 → ``PROBE_QUIET``（单轮不当罪，供连续窗吸收）。
    """
    if round_.collect_failed:
        # 采集失败必须**最先**判：否则「采不到」会顺着下面的分支被读成 ALIGNED
        # 或（有 agent 故障时）PROBE_QUIET——两者都在替一个哑探针背书（#2983 复核）。
        return ReconcileResult(
            ProbeVerdict.PROBE_ERROR,
            tuple(f"collect_failed:{reason}" for reason in round_.collect_failed),
        )

    reasons = {str(r) for r in agent_reasons}
    signals: list[str] = []
    journal = round_.journal
    topo = round_.topology

    probe_hc = journal.hc_dead_seen
    probe_blind = topo.classification == TOPOLOGY_BLIND
    probe_link = journal.link_errors >= link_error_threshold
    probe_fault = probe_hc or probe_blind or probe_link

    agent_usb_fault = bool(
        reasons
        & {
            "usb_host_controller_dead",
            "usb_tree_empty",
            "usb_link_degraded",
        }
    )

    if topo.classification == TOPOLOGY_EMPTY_CABINET:
        # 空柜不是失明——即使 agent 报了 usb_tree_empty 也不升 AGENT_MUTE
        if "usb_tree_empty" in reasons:
            signals.append("empty_cabinet_not_blind")
        return ReconcileResult(ProbeVerdict.ALIGNED, tuple(signals))

    if probe_fault and not agent_usb_fault:
        if probe_hc:
            signals.append("probe_hc_dead")
        if probe_blind:
            signals.append("probe_topology_blind")
        if probe_link:
            signals.append("probe_link_degraded")
        return ReconcileResult(ProbeVerdict.AGENT_MUTE, tuple(signals))

    if agent_usb_fault and not probe_fault:
        signals.append("agent_reported_usb_fault")
        return ReconcileResult(ProbeVerdict.PROBE_QUIET, tuple(signals))

    if probe_blind and "usb_tree_empty" not in reasons and agent_usb_fault:
        signals.append("topology_vs_reason")
        return ReconcileResult(ProbeVerdict.TOPOLOGY_MISMATCH, tuple(signals))

    return ReconcileResult(ProbeVerdict.ALIGNED, tuple(signals))


def consecutive_strike_open(
    recent_verdicts: Sequence[ProbeVerdict],
    *,
    need: int = 2,
    strike_on: frozenset[ProbeVerdict] = frozenset({ProbeVerdict.AGENT_MUTE}),
) -> bool:
    """连续 N 轮命中才转红（SSH 抖动 / 单轮安静不定罪）。"""
    if need < 1 or len(recent_verdicts) < need:
        return False
    window = recent_verdicts[-need:]
    return all(v in strike_on for v in window)


# ---------------------------------------------------------------------------
# 切片②：SSH 白名单执行（固定 argv，非 shell 串）
# ---------------------------------------------------------------------------

class ProbeArgvRefused(ValueError):
    """argv 不在白名单——拒绝拼进 sudo。"""


# 只读探针命令：key → 完整 argv（不含 sudo）。journal 用相对窗口，游标由调度层另存。
PROBE_ARGV_WHITELIST: dict[str, tuple[str, ...]] = {
    "lsusb": ("lsusb",),
    "journal_kernel_2h": (
        "journalctl",
        "-k",
        "--no-pager",
        "-o",
        "cat",
        "--since",
        "-2h",
    ),
}


def resolve_probe_argv(name: str) -> tuple[str, ...]:
    """按名取白名单 argv；未知名拒绝。"""
    argv = PROBE_ARGV_WHITELIST.get(name)
    if not argv:
        raise ProbeArgvRefused(f"probe argv not whitelisted: {name!r}")
    return argv


def assert_argv_whitelisted(argv: Sequence[str]) -> tuple[str, ...]:
    """校验任意 argv 是否整表命中白名单（防调用方手拼）。"""
    key = tuple(argv)
    if key not in PROBE_ARGV_WHITELIST.values():
        raise ProbeArgvRefused(f"probe argv not whitelisted: {list(argv)!r}")
    return key


def build_sudo_s_command(argv: Sequence[str]) -> str:
    """拼 ``sudo -S -p '' -- <argv…>``；argv 必须已在白名单。"""
    safe = assert_argv_whitelisted(argv)
    # -p ''：关掉密码提示，避免混进 stdout；口令只走 stdin（调用方写入，不记日志）
    return "sudo -S -p '' -- " + " ".join(shlex.quote(part) for part in safe)


@dataclass(frozen=True)
class RemoteProbeOutput:
    name: str
    rc: int
    stdout: str
    stderr: str


def run_whitelisted_sudo(
    client: Any,
    name: str,
    *,
    sudo_password: str,
    timeout: int = 10,
) -> RemoteProbeOutput:
    """经已建立的 SSH client 跑一条白名单探针（``sudo -S``）。

    **绝不**把 ``sudo_password`` 写入日志或异常消息。``client`` 需提供
    ``exec_command(cmd, timeout=…)``（paramiko.SSHClient 同形）。
    """
    argv = resolve_probe_argv(name)
    cmd = build_sudo_s_command(argv)
    try:
        stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    except TypeError:
        # 部分 mock / 旧签名无 timeout 关键字
        stdin, stdout, stderr = client.exec_command(cmd)
    try:
        if sudo_password:
            stdin.write(sudo_password + "\n")
            stdin.flush()
        stdin.channel.shutdown_write()
    except Exception:
        # 写口令失败仍继续读，避免把口令带进异常链
        logger.warning("host_health_probe sudo stdin write failed name=%s", name)
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    rc = stdout.channel.recv_exit_status()
    return RemoteProbeOutput(name=name, rc=rc, stdout=out, stderr=err)


def collect_failure_reason(output: RemoteProbeOutput) -> Optional[str]:
    """采集失败的判据（#2983 复核）：非零 rc，或**该探针本该有输出却为空**。

    空 stdout 不得当成「干净」——非特权 ``journalctl`` 正是以
    ``rc=0 + 空 stdout + stderr 提示`` 的形态被判成「读过且干净」（#2957 的形态）。
    探针宁可报采集失败，也不能把「没采到」说成「没问题」。
    """
    if output.rc != 0:
        return f"{output.name}:rc={output.rc}"
    if not output.stdout.strip():
        return f"{output.name}:empty_stdout"
    return None


def collect_probe_round_via_ssh(
    client: Any,
    *,
    sudo_password: str,
    timeout: int = 10,
) -> ProbeRound:
    """一次 SSH 会话采集 lsusb + journal，折成 ``ProbeRound``。

    ``rc`` / ``stderr`` 此前被算出来却从不检查 ⇒ ``sudo`` 失败（密码错、sudoers
    缺规则、未装 sudo ⇒ rc 127）与「真的干净」不可区分，被判 ALIGNED（#2983 复核）。
    现在两条探针都过 ``collect_failure_reason``，失败写进 ``collect_failed``。
    """
    outputs = (
        run_whitelisted_sudo(client, "lsusb", sudo_password=sudo_password, timeout=timeout),
        run_whitelisted_sudo(
            client, "journal_kernel_2h", sudo_password=sudo_password, timeout=timeout
        ),
    )
    failures = tuple(
        reason for reason in (collect_failure_reason(o) for o in outputs) if reason
    )
    lsusb, journal = outputs
    return ProbeRound(
        journal=parse_journal_probe(journal.stdout.splitlines()),
        topology=classify_lsusb_topology(lsusb.stdout.splitlines()),
        collect_failed=failures,
    )


class _HostProbeCandidate(Protocol):
    id: Any
    status: Any
    retired_at: Any
    maintenance_until: Any


def select_probe_host_ids(
    hosts: Sequence[_HostProbeCandidate],
    *,
    now: Optional[datetime] = None,
) -> list[str]:
    """ONLINE ∧ 非 retired ∧ 非维护窗 → 探针目标 id 列表。"""
    selected: list[str] = []
    for host in hosts:
        if str(getattr(host, "status", "") or "") != "ONLINE":
            continue
        if getattr(host, "retired_at", None) is not None:
            continue
        if in_maintenance_window(
            getattr(host, "maintenance_until", None), now=now,
        ):
            continue
        selected.append(str(host.id))
    return selected


# ---------------------------------------------------------------------------
# 切片③：周期 sweep（并发帽 + extra 连续窗）
# ---------------------------------------------------------------------------


def append_probe_verdict(
    recent: Sequence[str],
    verdict: str,
    *,
    cap: int = _RECENT_VERDICTS_CAP,
) -> list[str]:
    """追加一轮 verdict 字符串，截断到 cap。"""
    out = [str(v) for v in recent if v] + [str(verdict)]
    return out[-cap:]


def _agent_reasons_from_extra(extra: dict[str, Any] | None) -> list[str]:
    health = (extra or {}).get("health") or {}
    reasons = health.get("reasons") or []
    if not isinstance(reasons, list):
        return []
    return [str(r) for r in reasons if r]


def apply_probe_result_to_extra(
    extra: dict[str, Any] | None,
    *,
    verdict: str,
    signals: Sequence[str],
    topology: str,
    now: datetime,
    strike_need: int,
) -> dict[str, Any]:
    """写 ``health_probe`` 摘要；返回可赋回 ``host.extra`` 的新 dict。"""
    payload = dict(extra or {})
    prev = dict(payload.get(HEALTH_PROBE_EXTRA_KEY) or {})
    recent = append_probe_verdict(prev.get("recent_verdicts") or [], verdict)
    verdict_enums: list[ProbeVerdict] = []
    for raw in recent:
        try:
            verdict_enums.append(ProbeVerdict(raw))
        except ValueError:
            continue
    strike = consecutive_strike_open(verdict_enums, need=strike_need)
    payload[HEALTH_PROBE_EXTRA_KEY] = {
        "checked_at": now.isoformat(),
        "verdict": verdict,
        "signals": list(signals),
        "topology": topology,
        "recent_verdicts": recent,
        "strike_open": strike,
        "strike_need": strike_need,
    }
    return payload


def probe_one_host(
    host_id: str,
    *,
    timeout: int,
    strike_need: int,
    ssh_connect=None,
    collect_round=None,
) -> dict[str, Any]:
    """单机探针：SSH → 对账 → 写 extra → 可选审计。返回摘要（无凭据）。"""
    ssh_connect = ssh_connect or create_ssh_client
    collect_round = collect_round or collect_probe_round_via_ssh
    now = datetime.now(timezone.utc)
    summary: dict[str, Any] = {"host_id": host_id, "ok": False}

    with SessionLocal() as db:
        host = db.get(Host, host_id)
        if host is None:
            summary["error"] = "host_not_found"
            return summary
        try:
            creds, migrated = resolve_host_ssh_credentials(
                host, inventory_lookup=_resolve_ssh_creds,
            )
            if migrated:
                db.add(host)
        except Exception:
            logger.warning(
                "host_health_probe_creds_failed host=%s", host_id,
            )
            summary["error"] = "creds_failed"
            host.extra = apply_probe_result_to_extra(
                host.extra,
                verdict=PROBE_ERROR_VERDICT,
                signals=("creds_failed",),
                topology=TOPOLOGY_UNKNOWN,
                now=now,
                strike_need=strike_need,
            )
            db.add(host)
            db.commit()
            return summary

        client = None
        try:
            host_ip = getattr(host, "ip_address", None) or getattr(host, "ip", None) or ""
            client = ssh_connect(
                hostname=host_ip,
                port=int(getattr(host, "ssh_port", None) or 22),
                username=creds.user,
                password=creds.password,
                key_path=creds.key_path,
                known_hosts_path=creds.known_hosts_path,
                timeout=timeout,
            )
            round_ = collect_round(
                client, sudo_password=creds.password, timeout=timeout,
            )
            result = reconcile_agent_health(
                _agent_reasons_from_extra(host.extra), round_,
            )
            host.extra = apply_probe_result_to_extra(
                host.extra,
                verdict=result.verdict.value,
                signals=result.signals,
                topology=round_.topology.classification,
                now=now,
                strike_need=strike_need,
            )
            strike_open = bool(
                (host.extra or {}).get(HEALTH_PROBE_EXTRA_KEY, {}).get("strike_open")
            )
            if strike_open and result.verdict == ProbeVerdict.AGENT_MUTE:
                record_audit(
                    db,
                    action="host_health_probe_agent_mute",
                    resource_type="host",
                    resource_id=host_id,
                    details={
                        "verdict": result.verdict.value,
                        "signals": list(result.signals),
                        "topology": round_.topology.classification,
                        "strike_need": strike_need,
                    },
                    username="system",
                )
            db.add(host)
            db.commit()
            summary.update(
                {
                    "ok": True,
                    "verdict": result.verdict.value,
                    "strike_open": strike_open,
                    "topology": round_.topology.classification,
                }
            )
            return summary
        except Exception as exc:
            logger.warning(
                "host_health_probe_failed host=%s err=%s",
                host_id, type(exc).__name__,
            )
            host.extra = apply_probe_result_to_extra(
                host.extra,
                verdict=PROBE_ERROR_VERDICT,
                signals=("ssh_or_remote_failed",),
                topology=TOPOLOGY_UNKNOWN,
                now=now,
                strike_need=strike_need,
            )
            db.add(host)
            db.commit()
            summary["error"] = type(exc).__name__
            return summary
        finally:
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass


def run_probe_sweep_once(
    *,
    concurrency: int | None = None,
    timeout: int | None = None,
    strike_need: int | None = None,
    ssh_connect=None,
    collect_round=None,
) -> dict[str, Any]:
    """一轮 fleet 探针：选机 → 并发执行 → 汇总。"""
    sched = get_scheduler_settings()
    workers = concurrency if concurrency is not None else sched.host_health_probe_concurrency
    to = timeout if timeout is not None else sched.host_health_probe_timeout_seconds
    need = strike_need if strike_need is not None else sched.host_health_probe_strike_need
    workers = max(1, int(workers))
    to = max(1, int(to))
    need = max(1, int(need))

    with SessionLocal() as db:
        hosts = db.query(Host).all()
        target_ids = select_probe_host_ids(hosts)

    tallies = {
        "selected": len(target_ids),
        "ok": 0,
        "errors": 0,
        "strike_open": 0,
        "by_verdict": {},
    }
    if not target_ids:
        return tallies

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [
            pool.submit(
                probe_one_host,
                hid,
                timeout=to,
                strike_need=need,
                ssh_connect=ssh_connect,
                collect_round=collect_round,
            )
            for hid in target_ids
        ]
        for fut in as_completed(futures):
            try:
                summary = fut.result()
            except Exception as exc:
                tallies["errors"] += 1
                logger.warning(
                    "host_health_probe_future_failed err=%s", type(exc).__name__,
                )
                continue
            if summary.get("ok"):
                tallies["ok"] += 1
                v = str(summary.get("verdict") or "")
                tallies["by_verdict"][v] = tallies["by_verdict"].get(v, 0) + 1
                if summary.get("strike_open"):
                    tallies["strike_open"] += 1
            else:
                tallies["errors"] += 1

    logger.info(
        "host_health_probe_sweep_done selected=%d ok=%d errors=%d strike_open=%d",
        tallies["selected"], tallies["ok"], tallies["errors"], tallies["strike_open"],
    )
    return tallies
