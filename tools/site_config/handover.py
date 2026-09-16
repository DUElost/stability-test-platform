"""S7 handover summary and P1 acceptance evidence mapping (I5).

The handover stage reads the artifacts the installer and ``verify`` already
produce (``install-state.json`` plus an optional ``verify --json`` report) and
maps every P1 acceptance item (PRD MS-01/02/04/05/06/10/13) to concrete check
ids.  An item whose evidence is not present is reported ``BLOCKED`` with the
missing pieces spelled out — this file is *evidence*, never a certificate, and
it deliberately refuses to guess.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .stages import NAVIGATION_SITE_DIR
from .validation import Check, ConfigValidationError, blocked, failure, load_site_config

HANDOVER_FILE = "handover.json"
STATE_FILE = "install-state.json"


#: 一个证据槽位：单个 check_id，或「任一命中即可」的候选集（#2404）。
#: 候选集用于同一语义存在互斥发出的多条路径（见 ``_resolve._match``）。
CheckSlot = str | tuple[str, ...]


@dataclass(frozen=True)
class AcceptanceItem:
    """One PRD acceptance item and the artifacts that can evidence it."""

    key: str
    title: str
    stage_checks: tuple[CheckSlot, ...] = ()
    verify_checks: tuple[CheckSlot, ...] = ()
    requires_runs: int = 1
    pending: tuple[str, ...] = ()


ACCEPTANCE_ITEMS: tuple[AcceptanceItem, ...] = (
    AcceptanceItem(
        key="MS-01",
        title="空白站点装完，且非原作者可按指南接入受控设备",
        stage_checks=(
            "install.s0", "install.s1.deploy_root", "install.s2.release",
            "install.s2.env", ("install.s3.db", "install.s3.migrate"), "install.s3.admin",
            "install.s4.health", "install.s5",
        ),
        verify_checks=("verify.s6.hosts",),
        pending=(
            "按指南由非原作者（现场运维）重跑一遍安装与接入，作为 P1 签字条件",
            "受控设备真机主链（S6 真机部分）",
        ),
    ),
    AcceptanceItem(
        key="MS-02",
        title="同一发布物换站点输入即可复用；无其他站点的数据/秘密/机器状态泄入",
        stage_checks=("install.s0.digest", "install.s2.env", "install.bindings"),
        pending=(
            "第二个站点（城市 C）用同一发布物独立安装，作为跨站点隔离的现场证据",
        ),
    ),
    AcceptanceItem(
        key="MS-04",
        title="重跑不重置身份/密钥/数据；无效输入在破坏性步骤前失败",
        stage_checks=("install.s2.env", "install.s3.db", "install.s3.admin"),
        requires_runs=2,
        pending=(
            "幂等重跑现场记录（安装记录含 runs 计数，重跑后本项自动转 PASS）",
            "破坏性前失败负例：install_confirm / release_digest / db_unmanaged "
            "（tests/test_site_install.py 已覆盖，随 CI 复跑）",
        ),
    ),
    AcceptanceItem(
        key="MS-05",
        title="依赖与制品只来自已声明的介质/镜像，不暗中访问公网",
        stage_checks=("install.s1.dependencies", "install.s2.venv"),
        pending=(
            "完全离线（网络 profile）是否必选的最终裁定；受控镜像/离线 wheelhouse 的现场出网审计",
        ),
    ),
    AcceptanceItem(
        key="MS-06",
        title="登录/CSRF、Host/设备、Plan→claim→租约→终态、Watcher、scan/upload/merge 在隔离环境通过",
        verify_checks=(
            "verify.s6.auth", "verify.s6.csrf", "verify.s6.hosts",
            "verify.s6.devices", "verify.s6.chain",
        ),
        pending=(
            "Watcher 生命周期事件（需 patrol 阶段计划）",
            "scan/upload/merge 与存储写读探针（需真实日志工件与授权探针目录）",
            "真机设备主链（本项在无设备环境恒为部分证据）",
        ),
    ),
    AcceptanceItem(
        key="MS-10",
        title="无默认弱口令/明文秘密、凭据按站点隔离、入口可辨识",
        stage_checks=("install.s2.env", "install.bindings", "install.s2.navigation"),
        verify_checks=("verify.s6.csrf",),
        pending=(
            "SSH 主机密钥策略与账号口令策略的现场复审（模板默认不放宽校验）",
        ),
    ),
    AcceptanceItem(
        key="MS-13",
        title="站点与入口可辨识、跳转不传凭据；导航不可用时原入口仍可独立登录",
        stage_checks=("install.s2.navigation",),
        verify_checks=("verify.s6.navigation",),
        pending=(
            "浏览器实际点击核对（导航不可用 → 原入口仍可登录）",
        ),
    ),
)

class HandoverLeak(RuntimeError):
    """Generated handover content contains a fragment that must never be published."""


#: 任何报告都不允许出现的片段（凭据/秘密名/内部地址形态）。
_FORBIDDEN_FRAGMENTS = ("PRIVATE", "password=", "AGENT_SECRET", "JWT_SECRET", "FERNET")


def _read_json(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None, "install_state"
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeError):
        return None, "install_state"
    return (payload, None) if isinstance(payload, dict) else (None, "install_state")


def _stage_index(state: dict[str, Any]) -> dict[str, str]:
    """Map every recorded check id to the status of the stage that reported it."""
    index: dict[str, str] = {}
    for entry in state.get("stages", []):
        if not isinstance(entry, dict):
            continue
        status = str(entry.get("status") or "")
        for check_id in entry.get("checks", []):
            index[str(check_id)] = status
    return index


def _verify_index(report: dict[str, Any] | None) -> dict[str, str]:
    if not isinstance(report, dict):
        return {}
    index: dict[str, str] = {}
    for entry in report.get("checks", []):
        if isinstance(entry, dict) and entry.get("check_id"):
            index[str(entry["check_id"])] = str(entry.get("status") or "")
    return index


def _resolve(
    item: AcceptanceItem,
    *,
    stages: dict[str, str],
    verify: dict[str, str],
    runs: int,
) -> Check:
    """Turn one acceptance item into a three-state check."""
    evidence: list[str] = []
    missing: list[str] = []
    failed: list[str] = []
    blocked_evidence: list[str] = []

    def _match(slot: CheckSlot, index: dict[str, str], source: str) -> None:
        """一个槽位可给多个候选 ID（#2404）。

        同一语义在不同路径会发出不同 ID，而安装记录只保留**最近一次**运行的集合：
        例如 S3 在「数据库已在 head」时发 `install.s3.db`、在「本次应用了迁移」时发
        `install.s3.migrate`（互斥）。固定要求其一会让已装站点的交接证据永远缺失。
        命中规则：按候选顺序取**第一个存在**的 ID；都不存在才算缺失，缺失文案给出
        `A or B`，避免只报一半让人以为漏记。
        """
        candidates = (slot,) if isinstance(slot, str) else slot
        status = None
        hit = candidates[0]
        for candidate in candidates:
            if candidate in index:
                status = index[candidate]
                hit = candidate
                break
        if status is None:
            missing.append(" or ".join(candidates))
        elif status == "PASS":
            evidence.append(f"{hit}({source}:{status})")
        elif status == "BLOCKED":
            # #2283：BLOCKED 是**正常验收结果**（如零 Agent / 无 ONLINE 设备），
            # 不是失败——此前并入 failed 会让 handover 判 FAIL 且不写文件。
            blocked_evidence.append(f"{hit}({source}:{status})")
        else:
            failed.append(f"{hit}({source}:{status})")

    for slot in item.stage_checks:
        _match(slot, stages, "install")
    for slot in item.verify_checks:
        _match(slot, verify, "verify")

    check_id = f"handover.{item.key}"
    if failed:
        return failure(
            "evidence_failed", location="$.acceptance", role="site", check_id=check_id,
        )
    if blocked_evidence:
        return blocked(
            check_id, "site", "$.acceptance", "evidence_blocked",
            f"{item.title}：证据为 BLOCKED（{', '.join(sorted(blocked_evidence))}）——"
            "该结果不代表失败，但也不构成通过证据。",
            "；".join(item.pending),
        )
    if runs < item.requires_runs:
        return blocked(
            check_id, "site", "$.acceptance", "evidence_missing",
            f"{item.title}：安装记录 runs={runs} < {item.requires_runs}，尚无重跑证据。",
            "；".join(item.pending),
        )
    if missing:
        return blocked(
            check_id, "site", "$.acceptance", "evidence_missing",
            f"{item.title}：缺少证据 {', '.join(sorted(missing))}。",
            "；".join(item.pending),
        )
    return Check(
        check_id, "site", "PASS", "$.acceptance", "evidence_ready",
        f"{item.title}：证据 {', '.join(sorted(evidence))}。",
        "；".join(item.pending) or "保持本页与安装记录随站点留存。",
    )


def run_handover(
    config_path: str | Path,
    *,
    state_dir: str | Path,
    verify_report: str | Path | None = None,
    dry_run: bool = False,
    system_root: Path | None = None,
) -> dict:
    """Build the site handover summary from install/verify artifacts."""
    try:
        config = load_site_config(config_path)
    except ConfigValidationError as error:
        return _report(list(error.checks), runs=0)

    state_dir = Path(state_dir)
    state, state_problem = _read_json(state_dir / STATE_FILE)
    if state_problem is not None:
        return _report([failure(state_problem, location="$.state", role="site", check_id="handover.state")], runs=0)
    if state.get("site_id") != config.site.id:
        # 站点身份不符：不能把别的站点的记录当本站交接证据。
        return _report([failure("install_conflict", location="$.site.id", role="site", check_id="handover.state")], runs=0)

    runs = int(state.get("runs") or 1)
    stages = _stage_index(state)
    report_payload: dict[str, Any] | None = None
    if verify_report is not None:
        report_payload, problem = _read_json(Path(verify_report))
        if problem is not None:
            return _report([failure("verify_report", location="$.verification", role="site", check_id="handover.verify")], runs=runs)
    verify_index = _verify_index(report_payload)

    checks = [
        _resolve(item, stages=stages, verify=verify_index, runs=runs)
        for item in ACCEPTANCE_ITEMS
    ]
    saved: str | None = None
    if not dry_run and not any(check.status == "FAIL" for check in checks):
        try:
            saved = _write_handover(
                config=config, state=state, verify_status=(report_payload or {}).get("status"),
                checks=checks, runs=runs, system_root=system_root or Path("/"),
            )
        except HandoverLeak:
            # 宁可不出物，也不写出带凭据/秘密名的交接文件。
            checks.append(failure(
                "handover_redaction", location="$.acceptance", role="site", check_id="handover.redaction",
            ))
        except OSError:
            # 目录不可写（权限/挂载）时给出检查失败，而不是抛栈。
            checks.append(failure(
                "handover_write", location="$.navigation", role="site", check_id="handover.write",
            ))
    return _report(checks, runs=runs, saved=saved, verify_provided=verify_report is not None)


def _handover_payload(*, config, state, verify_status, checks, runs) -> dict[str, Any]:
    return {
        "site_id": config.site.id,
        "display_name": config.site.display_name,
        "release": config.release.expected_release,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "runs": runs,
        "install_stages": [
            {"stage": entry.get("stage"), "status": entry.get("status")}
            for entry in state.get("stages", []) if isinstance(entry, dict)
        ],
        "verification": {"provided": verify_status is not None, "status": verify_status},
        "acceptance": [
            {
                "item": check.check_id.split(".", 1)[-1],
                "status": check.status,
                "message": check.message,
                "pending": check.remediation,
            }
            for check in checks
        ],
    }


def _write_handover(*, config, state, verify_status, checks, runs, system_root: Path) -> str:
    payload = _handover_payload(
        config=config, state=state, verify_status=verify_status, checks=checks, runs=runs,
    )
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    for fragment in _FORBIDDEN_FRAGMENTS:
        if fragment in text:
            raise HandoverLeak(fragment)
    directory = system_root / NAVIGATION_SITE_DIR
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, 0o755)
    path = directory / HANDOVER_FILE
    _write_text(path, text + "\n")
    os.chmod(path, 0o644)
    return path.name


def _write_text(path: Path, text: str, mode: int = 0o644) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.replace(temporary, path)


def _report(
    checks: list[Check],
    *,
    runs: int,
    saved: str | None = None,
    verify_provided: bool = False,
) -> dict:
    failed = any(check.status == "FAIL" for check in checks)
    return {
        "stage": "handover",
        "status": "FAIL" if failed else "PASS",
        "summary": (
            "P1 acceptance evidence mapped from this site's own install and verify artifacts; "
            "entries without evidence stay BLOCKED and the pending list is part of the handover."
            # #2283：BLOCKED 不再阻断写文件（它是正常验收结果）；只有 FAIL 阻断，
            # 且此时显式说明「没写文件」——否则 /site/ 的固定链接只会静默 404。
            + (" No handover file was written because at least one item FAILED." if failed else "")
        ),
        "checks": [asdict(check) for check in checks],
        "deferred_checks": [],
        "runs": runs,
        "verify_report_provided": verify_provided,
        "saved_file": saved,
    }
