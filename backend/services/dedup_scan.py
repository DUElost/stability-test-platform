"""Dedup scan/merge service — ADR-0025 Sprint 4 归档-2。

各 agent 单独 scan（start_log_scan）→ 集中 merge（-merge_files_list）。
产物（Result_*.xls）写 plan_run_artifact 表。
config-gated：未配置 scan 工具 env 则跳过 + 503。
"""
from __future__ import annotations

import fcntl
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Collection, Dict, Iterator, List, Mapping, Optional, Tuple

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from backend.core import metrics
from backend.core.dedup_platform import DEDUP_PLATFORMS, artifact_uri_matches_platform
from backend.models.plan_run_artifact import PlanRunArtifact

logger = logging.getLogger(__name__)

ARTIFACT_TYPE_SCAN = "scan_result_xls"
ARTIFACT_TYPE_MERGE = "merge_result_xls"

_merge_files_list_supported: Optional[bool] = None


def _read_backend_scan_tool_env() -> Tuple[str, str]:
    """控制面专用 scan 工具键（#295 / #518）。

    Agent 侧仍读无前缀 ``STP_DEDUP_SCAN_PYTHON/_SCRIPT``（hot-update 经
    ``STP_AGENT_*`` 源键映射下发）；控制面只认 ``STP_BACKEND_DEDUP_SCAN_*``。
    """
    python = os.getenv("STP_BACKEND_DEDUP_SCAN_PYTHON", "").strip()
    script = os.getenv("STP_BACKEND_DEDUP_SCAN_SCRIPT", "").strip()
    if python and script:
        return python, script
    return "", ""


def resolve_scan_tool() -> Optional[Dict[str, str]]:
    """从 env 解析**控制面** scan 工具解释器 + 脚本路径。未配置返回 None。"""
    python, script = _read_backend_scan_tool_env()
    if not python or not script:
        return None
    return {"python": python, "script": script}


def get_scan_env_defaults() -> Dict[str, str]:
    """scan 工具的部署级 env 默认值。"""
    return {
        "place": os.getenv("STP_DEDUP_PLACE", "SH"),
        "tag": os.getenv("STP_DEDUP_SCAN_TAG", ""),
    }


_HOST_PREFIX_RE = re.compile(r"^([A-Za-z0-9_-]+?)_")


def _register_scan_artifacts_from_nfs(
    db: Session, plan_run_id: int, dedup_dir: Path, *, scan_round_id: str | None = None,
) -> int:
    """扫 dedup_dir 取 *_org.xls → 提取 host_id → 写 plan_run_artifact。

    文件名约定: {host_id}_Result_*_org.xls (由 UploadManager.fill 放置)。
    返回注册数。
    """
    count = 0
    for xls in sorted(set(list(dedup_dir.glob("*_org.xls")) + list(dedup_dir.glob("*_org_*.xls")))):
        existing = db.execute(
            select(PlanRunArtifact).where(
                PlanRunArtifact.plan_run_id == plan_run_id,
                PlanRunArtifact.storage_uri == str(xls),
            )
        ).scalar_one_or_none()
        if existing:
            continue

        m = _HOST_PREFIX_RE.match(xls.name)
        host_id = m.group(1) if m else None
        size = xls.stat().st_size if xls.exists() else 0
        db.add(PlanRunArtifact(
            plan_run_id=plan_run_id,
            host_id=host_id,
            storage_uri=str(xls),
            artifact_type=ARTIFACT_TYPE_SCAN,
            size_bytes=size,
            scan_round_id=scan_round_id,
        ))
        count += 1
    if count:
        db.commit()
    return count


def run_scan_sync(
    plan_run_id: int,
    *,
    is_final: bool = False,
    scan_round_id: str | None = None,
) -> str:
    """扫描中心存储（CIFS）dedup/{plan_run_id}/ 目录，注册已上送的 *_org.xls 产物。

    Agent 已通过 scan_now → run_local_scan → UploadManager 上送文件到中心存储。
    本函数仅做文件发现 + DB 注册，不再调 subprocess / RunConsole。
    返回注册产物数（字符串化），空串表示无新产物。
    """
    from backend.core.database import SessionLocal

    db = SessionLocal()
    try:
        from backend.core.storage_root import resolve_shared_storage_root

        nfs_root = resolve_shared_storage_root()
        if not nfs_root:
            logger.warning("scan_skip_nfs_root_not_set plan_run=%d", plan_run_id)
            return ""

        dedup_base = Path(nfs_root) / "dedup" / str(plan_run_id)
        n = 0
        for dedup_dir in (dedup_base, dedup_base / "mtk", dedup_base / "unisoc"):
            if not dedup_dir.is_dir():
                continue
            n += _register_scan_artifacts_from_nfs(
                db, plan_run_id, dedup_dir, scan_round_id=scan_round_id,
            )
        logger.info("scan_artifacts_registered plan_run=%d count=%d", plan_run_id, n)
        return str(n) if n else ""
    finally:
        db.close()


@dataclass(frozen=True)
class ScanCompleteness:
    """本轮 scan 完备性快照。

    ``units_*`` 是 (host, platform) 对口径——**轮询屏障与下游展示都以它为准**（#2271：
    此前只有屏障用它，archive/前端仍用 host 级数字，于是「host 期望 2 平台、只交付 1
    平台」在前端显示 ok）。``hosts_with_artifacts`` 仍保留为 host 级计数，但语义收窄为
    「在**期望平台内**有产物的 host 数」——否则交了非期望平台产物的 host 也会算完成。

    ``hosts_expected`` 是「有期望平台映射的 host 数」：``hosts_triggered`` 是它的
    超集（无 dedup 平台映射的 host 不进 ``expected``，见
    ``plan_run_scan_scope``），拿 triggered 当分母会永远追不上（#2271 的永久 warn）。
    """

    hosts_with_artifacts: int
    units_satisfied: int
    units_expected: int
    hosts_expected: int = 0

    @property
    def complete(self) -> bool:
        return self.units_satisfied >= self.units_expected


def scan_completeness(
    plan_run_id: int,
    expected: Mapping[str, Collection[str]],
    *,
    since: datetime,
) -> ScanCompleteness:
    """本轮 scan 完备性：host 级产物覆盖 + (host, platform) 对级覆盖。

    ``expected`` 是 ``{host_id: {平台分区, ...}}``，由
    :func:`backend.services.plan_run_scan_scope.load_expected_scan_platforms`
    按 host 的**设备平台构成**派生。

    **为什么判据单位是 (host, platform) 而不是 host**：Agent 侧两个 runner 都跑、
    各自按 serial 过滤（``scan_runner._execute_job``）；纯 MTK host 的 UNISOC 工具
    扫不到 uniview 目录 → 永远产不出 unisoc 产物，反之亦然。按 host 要求「每个
    平台都有产物」会让纯平台 host 永远判不齐、每轮烧满轮询预算——ADR-0032 B1 的
    「MTK/UNISOC **分区各自**完备性判定」被收紧成「每 host 双平台齐」的回归。

    收窄维度一个都不能少，否则完备性会误报「齐了」，后果都一样：慢的 host 漏出
    合并，或者合并的是过期报告。

    - 限定在本轮 ``expected`` 的键内（=本轮 triggered host）：本轮只触发 host-b
      时，host-a 的旧产物会顶替 host-b 的名额。
    - 限定在本轮**水位线** ``since`` 之后：增量扫描复用同一个 ``plan_run_id``，所以
      同一台 host 上一轮留下的产物会在本轮首检就计数，轮询立刻跳出，该 host 这轮的
      新产物赶不上 merge。``since`` 取下发 ``scan_now`` 之前的时刻；``created_at``
      与它同为 backend 进程侧 UTC 时间（模型是 Python default，不是库端 now()），
      不存在时钟偏差。
    - 按**平台分区**分别判定（``scan_artifact_uri_platform``）：只看 host 有没有
      产物，会让 MTK 先到即满足「host 齐了」并进 merge，UNISOC 产物漏出本轮。

    host 级计数按 host 去重、不按产物文件数：每台 host 上送 2 个匹配文件
    （``_org.xls`` 与 ``_org_dedup_org_*.xls``），拿文件数跟 host 数比会让「一台
    上送完毕」冒充「全部齐了」。
    """
    hosts = [str(h) for h in expected if h]
    if not hosts:
        return ScanCompleteness(0, 0, 0)

    from backend.core.database import SessionLocal
    from backend.core.dedup_platform import scan_artifact_uri_platform

    db = SessionLocal()
    try:
        rows = db.execute(
            select(PlanRunArtifact.host_id, PlanRunArtifact.storage_uri).where(
                PlanRunArtifact.plan_run_id == plan_run_id,
                PlanRunArtifact.artifact_type == ARTIFACT_TYPE_SCAN,
                PlanRunArtifact.host_id.in_(hosts),
                PlanRunArtifact.created_at >= since,
            )
        ).all()
    finally:
        db.close()

    platforms_by_host: dict[str, set[str]] = {}
    for host_id, uri in rows:
        if not host_id:
            continue
        platforms_by_host.setdefault(str(host_id), set()).add(
            scan_artifact_uri_platform(uri or ""),
        )

    units_expected = sum(len(set(platforms)) for platforms in expected.values())
    units_satisfied = sum(
        len(set(platforms) & platforms_by_host.get(str(host_id), set()))
        for host_id, platforms in expected.items()
    )
    return ScanCompleteness(
        hosts_with_artifacts=sum(
            1
            for host_id, platforms in expected.items()
            if set(platforms) & platforms_by_host.get(str(host_id), set())
        ),
        units_satisfied=units_satisfied,
        units_expected=units_expected,
        hosts_expected=len(expected),
    )


def record_scan_archive_state(
    plan_run_id: int,
    *,
    hosts_triggered: int,
    artifacts_registered: int,
    hosts_with_artifacts: int,
    hosts_not_acked: int = 0,
    units_satisfied: int = 0,
    units_expected: int = 0,
    hosts_expected: int = 0,
) -> None:
    """记录本轮 scan 的产物计数到 ``PlanRun.run_context['archive']``。

    下发了 host 却零产物意味着这次执行没有任何报表，而 PlanRun 仍可能是
    SUCCESS —— 落到 run_context 是为了让该状态经 API 可见，而不是只剩一行
    Agent 本地日志（#118 同源）。

    写入走库端 ``jsonb_set`` 而非读改写整个 ``run_context``：这里是独立会话，
    而 abort / dispatch_state 可能在同一时刻更新同一行，整体写回会把它们抹掉。
    """
    import json

    from sqlalchemy import text

    from backend.core.database import SessionLocal

    db = SessionLocal()
    try:
        db.execute(
            text(
                "UPDATE plan_run "
                "SET run_context = jsonb_set("
                "  COALESCE(run_context, '{}'::jsonb), '{archive}', CAST(:archive AS jsonb), true"
                ") "
                "WHERE id = :run_id"
            ),
            {
                "run_id": plan_run_id,
                "archive": json.dumps(
                    {
                        "hosts_triggered": hosts_triggered,
                        "scan_artifacts_registered": artifacts_registered,
                        "hosts_with_artifacts": hosts_with_artifacts,
                        "hosts_not_acked": hosts_not_acked,
                        # #2271：unit 计数一并落库——前端阶段判定以它为准（host 级
                        # 数字在「期望 2 平台只交 1」时会显示 ok）。
                        "units_satisfied": units_satisfied,
                        "units_expected": units_expected,
                        "hosts_expected": hosts_expected,
                    }
                ),
            },
        )
        db.commit()
    except Exception:
        # Bookkeeping must not abort the scan → upload → merge chain.
        db.rollback()
        logger.exception("scan_archive_state_write_failed plan_run=%d", plan_run_id)
    finally:
        db.close()

    # P1-3/P3-1：该交的 (host, 平台) 一个都没交时，把「没有报表」显式挂到终态结果上，
    # 供 PlanRun 详情/前端展示（终态 status 本身不变，避免状态机额外转换）。
    #
    # #2271 两处修正：① 判据用 **unit** 口径（与轮询屏障同一事实源；host 级判据在
    # 「期望平台的产物一个都没有、只有非期望产物」时会漏报）；② **每轮显式重写**
    # （true 与 false 都写）——此前只写 true，一次零产物轮次之后即使后续补齐，前端
    # 仍永久显示「扫描未产生任何报表」。
    scan_failed = units_expected > 0 and units_satisfied == 0
    try:
        db2 = SessionLocal()
        try:
            db2.execute(
                text(
                    "UPDATE plan_run "
                    "SET result_summary = jsonb_set("
                    "  COALESCE(result_summary, '{}'::jsonb), "
                    "  '{scan_failed}', CAST(:flag AS jsonb), true"
                    ") "
                    "WHERE id = :run_id"
                ),
                {"run_id": plan_run_id, "flag": "true" if scan_failed else "false"},
            )
            db2.commit()
        finally:
            db2.close()
    except Exception:
        logger.exception("scan_failed_flag_write_failed plan_run=%d", plan_run_id)


def run_merge_sync(
    plan_run_id: int,
    *,
    scan_round_id: str | None = None,
    round_started_at: datetime | None = None,
    platform: str | None = None,
    allow_failed: bool = False,
) -> str:
    """同步执行 merge（-merge_files_list；工具不支持即配置错误，#291）。

    阻塞等待子进程完成，校验 merge_result/ 出现新产物目录，返回：

    - ``"ok"``：合并成功
    - ``"skipped_failed"``：PlanRun FAILED 且未 ``allow_failed``（ADR-0028 D2
      自动链故意跳过；与工具失败空串区分，避免 #1527 raise 误伤）
    - ``""``：工具/产物不足等真失败

    ``allow_failed=True``：手动 API（#697）对 FAILED 仍执行 merge。
    """
    from backend.core.database import SessionLocal
    from backend.core.storage_root import resolve_shared_storage_root
    from backend.models.enums import PlanRunStatus
    from backend.models.plan_run import PlanRun

    db = SessionLocal()
    try:
        run = db.get(PlanRun, plan_run_id)
    finally:
        db.close()
    if (
        run is not None
        and run.status == PlanRunStatus.FAILED.value
        and not allow_failed
    ):
        logger.info("merge_skip_failed_plan_run plan_run=%d", plan_run_id)
        metrics.merge_skip_failed_plan_run_total.inc()
        return "skipped_failed"

    tool = resolve_scan_tool()
    if tool is None:
        logger.warning("merge_skip_tool_not_configured plan_run=%d", plan_run_id)
        metrics.merge_skip_tool_not_configured_total.inc()
        return ""

    org_files = _load_org_files_for_merge(
        plan_run_id,
        scan_round_id=scan_round_id,
        round_started_at=round_started_at,
        platform=platform,
    )
    if not org_files:
        logger.warning("merge_skip_no_org_files plan_run=%d platform=%s", plan_run_id, platform)
        metrics.merge_skip_no_org_files_total.inc()
        return ""

    side = os.getenv("STP_DEDUP_SCAN_TAG", "shanghai")
    side_argv = ["-side", "factory"] if "factory" in side.lower() else ["-side", "shanghai"]
    script_parent = Path(tool["script"]).parent
    cwd = str(script_parent)
    merge_root = script_parent / "merge_result"

    # #1072 / R10-F03：工具固定写共享 merge_result/；snapshot→子进程→收割→
    # 发布→登记全程跨进程串行，避免另一 PlanRun 的更新目录被本轮误登记。
    with _exclusive_merge_tool_lock(script_parent):
        # I-13 方案 A 兜底：中心已配置时，本机 merge_result/ 只是**中转**（成功路径的那份在
        # 发布+登记后立即删除），而失败重试会不断产生新的 {ts}/ —— 这里顺手收敛超期残留。
        # 中心**未**配置时不清理：那时 artifact 就指向本机路径，删了即毁产物。
        # #2281：「此刻中心已配置」不等于「这些目录是中转」——中心配置**之前**登记的 run
        # 的 artifact 仍指向本机路径（#1074 有意保留的回退分支），故还要问一次
        # 「有没有持久引用指着它」。查询失败时不清理（删错不可逆，宁可留残留）。
        if resolve_shared_storage_root():
            try:
                protected = local_merge_artifact_refs(merge_root)
            except Exception:
                logger.warning(
                    "merge_local_sweep_skipped_ref_lookup_failed root=%s",
                    merge_root,
                    exc_info=True,
                )
            else:
                sweep_stale_local_merge_outputs(merge_root, protected=protected)

        before_names = _merge_output_dir_names(merge_root)
        baseline_mtime = latest_merge_output_mtime(merge_root)

        listfile: Path | None = None
        try:
            argv, listfile = build_merge_argv(tool, org_files, side_argv)
            logger.info(
                "merge_started plan_run=%d files=%d cwd=%s mode=%s",
                plan_run_id,
                len(org_files),
                cwd,
                argv[2] if len(argv) > 2 else "?",
            )
            proc = subprocess.run(
                argv,
                cwd=cwd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=300,
            )
        except subprocess.TimeoutExpired:
            logger.error("merge_timeout plan_run=%d timeout=300s", plan_run_id)
            raise
        except Exception:
            logger.exception("merge_spawn_failed plan_run=%d", plan_run_id)
            raise
        finally:
            if listfile is not None:
                try:
                    listfile.unlink(missing_ok=True)
                except Exception:
                    pass

        stderr_snip = (proc.stderr or "")[:500]
        if proc.returncode != 0:
            logger.error(
                "merge_failed plan_run=%d exit=%d stderr=%s",
                plan_run_id, proc.returncode, stderr_snip,
            )
            raise RuntimeError(f"merge subprocess failed (exit={proc.returncode})")
        if merge_stderr_indicates_failure(proc.stderr or ""):
            logger.error(
                "merge_failed plan_run=%d exit=0 stderr=%s",
                plan_run_id, stderr_snip,
            )
            raise RuntimeError("merge subprocess reported errors in stderr")

        try:
            latest = find_fresh_merge_output_dir(merge_root, baseline_mtime, before_names)
        except RuntimeError:
            logger.exception("merge_output_validation_failed plan_run=%d", plan_run_id)
            raise

        # ── merge 产物中心化（2026-08-31）：工具固定输出到本机
        # {工具目录}/merge_result/{ts}/——但 artifact 应指向中心持久路径
        # （设计 adr-0025: `{CIFS}/dedup/{plan_run_id}/merge/`）。
        # #1074：中心已配置时发布失败必须抛错（可重试），不得回退登记本机
        # 路径并返回 "ok"；仅中心未配置时才回退本机路径（历史开发行为）。
        published = _publish_merge_to_center(plan_run_id, latest, platform=platform)
        artifact_dir = published if published is not None else latest

        try:
            from backend.core.database import SessionLocal

            inner_db = SessionLocal()
            try:
                n = _register_merge_artifacts(inner_db, plan_run_id, artifact_dir)
                logger.info(
                    "merge_artifacts_registered plan_run=%d count=%d dir=%s",
                    plan_run_id, n, artifact_dir,
                )
            finally:
                inner_db.close()
        except Exception:
            logger.exception("merge_register_artifacts_failed plan_run=%d", plan_run_id)
            raise

        # I-13 方案 A：产物已在中心并已登记 → 本机中转副本可以走了（E-3：稳态下本机不累积）。
        # 只在 `published is not None`（中心已配置）时删——未配置中心时 artifact_dir == latest，
        # 该目录就是交付物本身。发布失败（#1074）在 raise 之前返回，走不到这里 → 仍可重试。
        if published is not None:
            _discard_local_merge_output(latest)

    logger.info("merge_done plan_run=%d platform=%s", plan_run_id, platform or "all")
    return "ok"


#: 单平台「本轮无产出可归因报表」记录值（无工具 / 无本轮 org 文件，二者在聚合层等价）。
_MERGE_PLATFORM_NO_INPUT = "no_input"


def _record_merge_platforms(plan_run_id: int, outcomes: dict[str, str]) -> None:
    """逐平台 merge 结果写入 ``run_context.merge_platforms``（可观测，不改控制流）。

    与 ``record_scan_archive_state`` 同款取向：记录失败不得影响 merge 结论。
    """
    if not outcomes:
        return
    try:
        from backend.core.database import SessionLocal
        from backend.services.plan_run_context import write_run_context_section

        db = SessionLocal()
        try:
            write_run_context_section(db, plan_run_id, "merge_platforms", {
                "platforms": dict(outcomes),
                "recorded_at": datetime.now(timezone.utc).isoformat(),
            })
        finally:
            db.close()
    except Exception:
        logger.exception("merge_platforms_record_failed plan_run=%d", plan_run_id)


def run_merge_all_platforms_sync(
    plan_run_id: int,
    *,
    scan_round_id: str | None = None,
    round_started_at: datetime | None = None,
    allow_failed: bool = False,
) -> str:
    """各平台 merge；返回 ``ok`` / ``skipped_failed`` / ``""``。

    任一平台 ``ok`` → ``ok``；全部为 ``skipped_failed`` → ``skipped_failed``；
    否则 → ``""``。

    工具/校验/发布**真失败时 :func:`run_merge_sync` 直接 raise**——异常不在本函数
    吞掉，向上传播给 SAQ 重试与 ``#1527`` 的失败收敛。本函数只聚合「正常返回」的
    三种结果：``ok`` / ``skipped_failed`` / 空串（无工具或无本轮 org 文件）。

    逐平台结果写入 ``run_context.merge_platforms``（见
    :func:`_record_merge_platforms`）：多平台路由「哪个平台这一轮出了报表、哪个
    没有输入」此前只能靠查中心目录反推，ADR-0032 B1 的分平台语义缺一个可查落点。
    """
    any_ok = False
    saw_skip_failed = False
    saw_empty = False
    outcomes: dict[str, str] = {}
    for platform in DEDUP_PLATFORMS:
        result = run_merge_sync(
            plan_run_id,
            scan_round_id=scan_round_id,
            round_started_at=round_started_at,
            platform=platform,
            allow_failed=allow_failed,
        )
        if result == "ok":
            any_ok = True
            outcomes[platform] = "ok"
        elif result == "skipped_failed":
            saw_skip_failed = True
            outcomes[platform] = "skipped_failed"
        else:
            saw_empty = True
            outcomes[platform] = _MERGE_PLATFORM_NO_INPUT
    logger.info(
        "merge_platforms plan_run=%d %s",
        plan_run_id,
        " ".join(f"{name}={status}" for name, status in outcomes.items()),
    )
    _record_merge_platforms(plan_run_id, outcomes)
    if any_ok:
        return "ok"
    if saw_skip_failed and not saw_empty:
        return "skipped_failed"
    return ""


def _load_org_files_for_merge(
    plan_run_id: int,
    *,
    scan_round_id: str | None = None,
    round_started_at: datetime | None = None,
    platform: str | None = None,
) -> list[str]:
    from backend.core.database import SessionLocal

    db = SessionLocal()
    try:
        stmt = select(PlanRunArtifact.storage_uri).where(
            PlanRunArtifact.plan_run_id == plan_run_id,
            PlanRunArtifact.artifact_type == ARTIFACT_TYPE_SCAN,
        )
        if scan_round_id:
            legacy_floor = round_started_at
            if legacy_floor is None:
                try:
                    legacy_floor = datetime.fromisoformat(scan_round_id.replace("Z", "+00:00"))
                except ValueError:
                    logger.warning(
                        "merge_org_files_invalid_scan_round_id plan_run=%d id=%s",
                        plan_run_id, scan_round_id,
                    )
                    return []
            stmt = stmt.where(
                or_(
                    PlanRunArtifact.scan_round_id == scan_round_id,
                    and_(
                        PlanRunArtifact.scan_round_id.is_(None),
                        PlanRunArtifact.created_at >= legacy_floor,
                    ),
                )
            )
        elif round_started_at is not None:
            stmt = stmt.where(PlanRunArtifact.created_at >= round_started_at)
        else:
            logger.warning(
                "merge_org_files_no_round_filter plan_run=%d — refusing to load all history",
                plan_run_id,
            )
            return []
        rows = db.execute(stmt).all()
        paths = [r[0] for r in rows if "_org.xls" in r[0]]
        if platform:
            paths = [p for p in paths if artifact_uri_matches_platform(p, platform)]
        return paths
    finally:
        db.close()


def scan_tool_supports_merge_files_list(tool: Dict[str, str]) -> bool:
    """探测 start_log_scan 是否支持 -merge_files_list（结果进程内缓存）。"""
    global _merge_files_list_supported
    if _merge_files_list_supported is not None:
        return _merge_files_list_supported

    script = Path(tool["script"])
    if not script.is_file():
        _merge_files_list_supported = False
        return False

    try:
        proc = subprocess.run(
            [tool["python"], str(script), "-h"],
            cwd=str(script.parent),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        help_text = (proc.stdout or "") + (proc.stderr or "")
        _merge_files_list_supported = "merge_files_list" in help_text
    except Exception:
        logger.warning("merge_files_list_probe_failed script=%s", script, exc_info=True)
        _merge_files_list_supported = False

    logger.info("merge_files_list_supported=%s script=%s", _merge_files_list_supported, script)
    return _merge_files_list_supported


def build_merge_argv(
    tool: Dict[str, str],
    org_files: List[str],
    side_argv: List[str],
) -> Tuple[List[str], Optional[Path]]:
    """构建 merge 子进程 argv。

    #291：只走 ``-merge_files_list``。工具不支持（过旧 / 脚本缺失 / 探测
    失败）视为配置错误直接抛错，不再静默回落展开全部 xls 的
    ``-merge_files``——那条路有 argv 长度上限，host 规模上来必撞墙。
    """
    if not scan_tool_supports_merge_files_list(tool):
        raise RuntimeError(
            "scan tool does not support -merge_files_list "
            f"(script={tool.get('script')!r}); upgrade the scan tool — "
            "the legacy -merge_files fallback was removed (#291)"
        )
    with tempfile.NamedTemporaryFile(
        "w",
        suffix=".txt",
        prefix="merge_list_",
        dir=str(Path(tempfile.gettempdir())),
        delete=False,
        encoding="utf-8",
    ) as f:
        f.write("\n".join(org_files))
        listfile = Path(f.name)
    argv = [tool["python"], tool["script"], "-merge_files_list", str(listfile)] + side_argv
    return argv, listfile


def merge_stderr_indicates_failure(stderr: str) -> bool:
    """scan 工具可能在 stderr 打 error 但仍 exit 0。"""
    text = stderr.lower()
    if ": error:" in text or "error: argument" in text:
        return True
    # #798: 行首 ``ERROR:``（无前置冒号形态）与 Traceback 同样表达失败——
    # 仅靠 ": error:" 会漏判，exit 0 的残缺报表会被当成功入库。
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("error:") or stripped.startswith("error "):
            return True
    return "traceback (most recent call last)" in text


def multi_instance_merge_warning() -> Optional[str]:
    """#2189（ADR-0027 v1.8 清单第 7 条）：多实例形态下 merge 的**实例绑定**启动告警。

    ``None`` = 单实例（无告警）。判定口径与清单第 6 条一致：Redis adapter 开启即多实例形态
    （``socketio_redis_adapter_enabled``）。

    merge 的串行原语是**本机** ``flock``（``{工具目录}/merge_result/.stp_merge.lock``，见
    ``_exclusive_merge_tool_lock``），工具目录取自 ``STP_BACKEND_DEDUP_SCAN_SCRIPT`` 的父目录——
    **两者都只在同一实例内生效**。故多实例下同一 run 的「手动 API × SAQ ``merge_task``」两条路径
    没有跨实例互斥（可各自在本机跑工具、各自向中心同一路径发布）。SAQ 作业本身不会双跑
    （共享 Redis 队列由 SAQ 去重消费），故缺口只在**跨路径并发**。

    解除路径见 ADR-0027 清单第 7 条：B2（复用 P3-4 的 ``run_key`` 全局互斥原语）或
    B1（把 merge 迁到 worker/Agent，归 ADR-0033）。
    """
    try:
        from backend.realtime.socketio_redis import socketio_redis_adapter_enabled
    except Exception:  # pragma: no cover - 导入失败不应影响诊断路径
        return None
    if not socketio_redis_adapter_enabled():
        return None
    return (
        "multi_instance_mode_enabled merge_instance_bound=true "
        "affected=merge_result_lock,merge_tool_workdir "
        "notes=cross_instance_mutex_absent_for_manual_api_x_saq "
        "ref=#2189"
    )


@contextmanager
def _exclusive_merge_tool_lock(script_parent: Path) -> Iterator[None]:
    """跨进程独占锁：覆盖共用 ``merge_result/`` 的调用到发布窗口（#1072）。"""
    lock_dir = script_parent / "merge_result"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / ".stp_merge.lock"
    with open(lock_path, "a+", encoding="utf-8") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def latest_merge_output_mtime(merge_root: Path) -> float:
    """merge_result/ 下含 Result_MergeFiles*.xls 的子目录最大 mtime。"""
    if not merge_root.is_dir():
        return 0.0
    latest = 0.0
    for subdir in merge_root.iterdir():
        if not subdir.is_dir():
            continue
        if any(subdir.glob("Result_MergeFiles*.xls")):
            latest = max(latest, subdir.stat().st_mtime)
    return latest


def _merge_output_dir_names(merge_root: Path) -> set[str]:
    if not merge_root.is_dir():
        return set()
    return {
        p.name for p in merge_root.iterdir()
        if p.is_dir() and any(p.glob("Result_MergeFiles*.xls"))
    }


#: 本机中转目录（``merge_result/{ts}/``）的保留上限（小时）——**仅兜底**。
#:
#: 正常路径：发布到中心 + 登记后**立即删除**该目录（见 :func:`run_merge_sync`），故稳态下
#: 本机不累积（I-13 方案 A 的 E-3 判据）。失败/发布未完成时**必须保留**（#1074 的失败可重试
#: 前提），这类残留靠超期清理收敛。
#:
#: 为什么是常量而非 env 键：它只影响失败残留的清理节奏，不改变任何交付语义；等真有与
#: 中心容量挂钩的调参需求再升格为配置项（升格须同步 ``backend/.env.example`` 与
#: ``docs/development/environment-variables.md``，走 env-inventory 门禁）。
_MERGE_LOCAL_RETENTION_HOURS = 24.0


def _discard_local_merge_output(merge_dir: Path) -> bool:
    """删除**已发布**的本机中转产物目录；返回是否删成功。

    清理**不是交付前提**：此时产物已在中心且已登记，删除失败只留 warning，
    超期残留由 :func:`sweep_stale_local_merge_outputs` 兜底——不因为清理失败
    把一次成功的 merge 变成失败。
    """
    try:
        shutil.rmtree(merge_dir)
        logger.info("merge_local_intermediate_removed dir=%s", merge_dir)
        return True
    except OSError:
        logger.warning(
            "merge_local_intermediate_remove_failed dir=%s", merge_dir, exc_info=True,
        )
        return False


def _normalized_dir(path: Path) -> Path:
    """路径规范化后的目录身份（尾随分隔符 / ``.`` / 重复分隔符不产生两个身份）。"""
    return Path(os.path.normpath(str(path)))


def local_merge_artifact_refs(merge_root: Path) -> Dict[Path, set[int]]:
    """仍被 ``plan_run_artifact`` 指着的**本机** merge 产物目录 → 引用它的 run 号（#2281）。

    为什么需要：「此刻中心已配置」只说明**新**产物会去中心，不说明 ``merge_root`` 下的
    既有目录是中转。中心配置之前登记的 run，其 artifact 指向的正是这些本机目录
    （:func:`run_merge_sync` 里 ``published is None`` 的回退分支，`#1074` 有意保留）——
    对它们做 mtime 清理删的是**唯一副本**，而 ``dedup_extract`` 侧对缺失路径是静默跳过，
    产物丢失不会被任何计数暴露。

    **查询失败向上抛**：删错不可逆，调用点据此跳过本轮清理（留残留优于毁交付物）。
    """
    from backend.core.database import SessionLocal

    prefix = os.path.normpath(str(merge_root)) + os.sep
    db = SessionLocal()
    try:
        rows = db.execute(
            select(PlanRunArtifact.plan_run_id, PlanRunArtifact.storage_uri).where(
                PlanRunArtifact.artifact_type == ARTIFACT_TYPE_MERGE,
                # autoescape：路径里的 `_` / `%` 是字面量，不做 LIKE 通配。
                PlanRunArtifact.storage_uri.startswith(prefix, autoescape=True),
            )
        ).all()
    finally:
        db.close()

    refs: Dict[Path, set[int]] = {}
    for plan_run_id, uri in rows:
        refs.setdefault(_normalized_dir(Path(uri).parent), set()).add(plan_run_id)
    return refs


def sweep_stale_local_merge_outputs(
    merge_root: Path,
    *,
    retention_hours: float = _MERGE_LOCAL_RETENTION_HOURS,
    now: float | None = None,
    protected: Mapping[Path, Collection[int]] | None = None,
) -> int:
    """清理 ``merge_result/`` 下**超期残留**的中转目录（I-13 方案 A 的兜底）。

    **调用方必须先确认中心已配置**：中心未配置时 artifact 就指向本机路径，删除即毁产物。
    本函数不做这层判断（它只按 mtime 与"是否像产物目录"筛），判断留在调用点，避免把
    交付语义藏进一个清理函数里。

    **``protected`` 是第二条判据**（#2281）：中心已配置只排除了「中心未配置」那一种保留
    理由，排除不了「该目录仍被 artifact 指着」——仍在册的目录是交付物本身，不是中转残留。
    键取 :func:`_normalized_dir` 规范化后的目录，值为引用它的 run 号（仅用于留痕）；
    缺省空映射表示无引用，只用于无 DB 的调用场景与单测。

    只挑**含 ``Result_MergeFiles*.xls`` 的子目录**（工具产物形态）——锁文件
    ``.stp_merge.lock`` 与其它非目录内容一律不碰。
    """
    if not merge_root.is_dir():
        return 0
    cutoff = (now if now is not None else time.time()) - retention_hours * 3600
    removed = 0
    removed_dirs: list[str] = []
    for subdir in sorted(merge_root.iterdir()):
        if not subdir.is_dir():
            continue
        try:
            if not any(subdir.glob("Result_MergeFiles*.xls")):
                continue
            refs = protected.get(_normalized_dir(subdir)) if protected else None
            if refs:
                logger.info(
                    "merge_local_sweep_kept_referenced dir=%s plan_runs=%s",
                    subdir,
                    ",".join(str(run_id) for run_id in sorted(refs)),
                )
                continue
            if subdir.stat().st_mtime >= cutoff:
                continue
        except OSError:
            continue
        if _discard_local_merge_output(subdir):
            removed += 1
            removed_dirs.append(subdir.name)
    if removed:
        logger.info(
            "merge_local_stale_swept root=%s removed=%d dirs=%s retention_h=%s",
            merge_root, removed, ",".join(removed_dirs), retention_hours,
        )
    return removed


def find_fresh_merge_output_dir(
    merge_root: Path,
    baseline_mtime: float,
    before_names: set[str] | None = None,
) -> Path:
    """返回 merge 后新出现的产物目录；无新目录则抛 RuntimeError。"""
    if not merge_root.is_dir():
        raise RuntimeError(f"merge_result missing: {merge_root}")

    before = before_names or set()
    candidates: list[Path] = []
    for subdir in merge_root.iterdir():
        if not subdir.is_dir():
            continue
        if not any(subdir.glob("Result_MergeFiles*.xls")):
            continue
        if subdir.name not in before:
            candidates.append(subdir)
            continue
        if subdir.stat().st_mtime > baseline_mtime + 0.001:
            candidates.append(subdir)

    if not candidates:
        raise RuntimeError(
            f"no fresh merge output under {merge_root} "
            f"(baseline_mtime={baseline_mtime}, before={sorted(before)})"
        )
    return max(candidates, key=lambda p: p.stat().st_mtime)


def reset_merge_capability_cache_for_tests() -> None:
    """测试专用：清 -merge_files_list 探测缓存。"""
    global _merge_files_list_supported
    _merge_files_list_supported = None


def _map_agent_path_to_center(path: str, plan_run_id: int, center_root: str) -> str:
    """把 agent 本机 scan 路径映射为中心可达**事件目录根**。

    源形态: ``{hdd}/.stp-scan/{run_id}-{rand}/{folder}/{serial}/aee_exp/{event_dir}/{sub}``
    （或 ``vendor_aee_exp``）。目标: ``{center}/devices/{run_id}/{event_dir}/``——
    事件目录已按引用上送中心（目录名 = 事件目录名），映射后目录对外可达。

    **只映射到目录根**：源路径的文件级子结构（``dbg.DEC/__exp_main.txt`` 等）
    是 scan 工具 .stp-scan 副本的解密中间产物，中心事件目录没有该层——
    文件级映射不可达（2026-08-31 run 289 实证）；目录是 Jira 提单的消费单元。

    文档依据: ADR-0025 D2「开发通过同一共享只读访问」——Jira 提单引用的
    报表路径必须指向中心存储，不能是各 host 本机路径。
    """
    m = re.search(r"/(?:vendor_)?aee_exp/([^/]+)(/.*)?$", path)
    if not m:
        return path
    event_dir = m.group(1)
    return f"{center_root}/devices/{plan_run_id}/{event_dir}/"


def _resolve_center_event_path(
    center_root: str, plan_run_id: int, event_dir: str,
) -> str:
    """映射后可达性兜底：本 run 未上送时搜历史 run 的上送位置。

    场景（2026-08-31 验收发现）：scan 对同内容事件去重合并显示代表目录
    （如注入 cp 的 02/04 同内容——报表只显示 02），而 upload 上送的是
    实际引用的 04——``devices/{run_id}/02`` 不存在。此时搜
    ``devices/*/{event_dir}``（历史 run 上送位置）映射到存在的副本；
    搜不到保留原映射（尽力而为，中心不可达时由人工/提取路径兜底）。
    """
    candidate = f"{center_root}/devices/{plan_run_id}/{event_dir}/"
    if os.path.isdir(candidate):
        return candidate
    try:
        hits = sorted(Path(center_root, "devices").glob(f"*/{event_dir}"))
    except OSError:
        return candidate
    if hits:
        return str(hits[0]) + "/"
    return candidate


def _rewrite_merge_report_paths_to_center(
    xls_dir: Path, plan_run_id: int, center_root: str,
) -> int:
    """重写 merge 报告（Result_MergeFiles*.xls）的 Path 列为中心可达路径。

    在中心副本上执行（发布后重写）；返回重写行数。xls 为 OLE 格式——
    xlrd 读 + xlwt 写回（tools 的既有格式），其余列原样保留。
    """
    import xlrd
    import xlwt

    count = 0
    for xls in sorted(xls_dir.glob("Result_MergeFiles*.xls")):
        try:
            book = xlrd.open_workbook(str(xls), formatting_info=False)
        except Exception:
            logger.exception("merge_report_rewrite_read_failed path=%s", xls)
            continue
        sheet = book.sheet_by_index(0)
        headers = [str(sheet.cell_value(0, c)).strip() for c in range(sheet.ncols)]
        path_col = next(
            (idx for idx, header in enumerate(headers) if header.lower() == "path"),
            None,
        )
        if path_col is None:
            # #2256：按名依赖（表头契约）取不到列时**不再静默跳过**——列被改名/删除与
            # 本轮确实没有可重写的行必须可分得开；带上实际读到的表头便于定位漂移形态
            # （如 BOM `U+FEFF` 这类 `strip()` 不剥的不可见字符）。
            logger.warning(
                "merge_report_path_column_missing path=%s headers=%s",
                xls, headers[:32],
            )
            continue
        wb = xlwt.Workbook()
        ws = wb.add_sheet(sheet.name)
        for r in range(sheet.nrows):
            for c in range(sheet.ncols):
                val = sheet.cell_value(r, c)
                if c == path_col and r > 0 and val:
                    mapped = _map_agent_path_to_center(str(val), plan_run_id, center_root)
                    if mapped != str(val):
                        # 可达性兜底：本 run 未上送时映射到历史 run 上送位置
                        m = re.search(r"/devices/\d+/([^/]+)/$", mapped)
                        if m:
                            mapped = _resolve_center_event_path(
                                center_root, plan_run_id, m.group(1))
                        count += 1
                        val = mapped
                ws.write(r, c, val)
        try:
            wb.save(str(xls))
        except Exception:
            logger.exception("merge_report_rewrite_write_failed path=%s", xls)
    if count:
        logger.info(
            "merge_report_paths_rewritten plan_run=%d rows=%d center=%s",
            plan_run_id, count, center_root,
        )
    return count


def _publish_merge_to_center(
    plan_run_id: int, merge_dir: Path, *, platform: str | None = None,
) -> "Path | None":
    """把工具本机 merge_result 产物发布到中心 ``dedup/{run_id}/merge/``。

    工具（start_log_scan.py）固定输出到控制面本机
    ``{工具目录}/merge_result/{ts}/``——但 artifact 应指向中心持久路径
    （设计 adr-0025: ``{CIFS}/dedup/{plan_run_id}/merge/``）。发布 = 拷贝
    产物到中心并返回中心目录；中心未配置（无 ``STP_AEE_NFS_ROOT``）时
    返回 None（调用方回退注册本机路径——历史行为）。

    #1074：中心已配置但 ``OSError``（挂载满/权限/IO）时抛 ``RuntimeError``，
    不返回 None——避免调用方把本机回退当成成功交付。
    """
    from backend.core.storage_root import resolve_shared_storage_root

    root = resolve_shared_storage_root()
    if not root:
        logger.warning("merge_publish_skip_no_center plan_run=%d", plan_run_id)
        return None
    dest = Path(root) / "dedup" / str(plan_run_id) / "merge"
    if platform:
        dest = dest / platform
    try:
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copytree(merge_dir, dest, dirs_exist_ok=True)
    except OSError as exc:
        logger.exception("merge_publish_failed plan_run=%d dest=%s", plan_run_id, dest)
        raise RuntimeError(
            f"merge center publish failed plan_run={plan_run_id} dest={dest}: {exc}"
        ) from exc
    # 2026-08-31：报表 Path 列对外可达——重写中心副本（agent 本机
    # .stp-scan 路径 → 中心 devices/{run_id}/{event_dir}/）。失败不阻断
    # 发布（路径重写是增强，不是发布的前提）。
    try:
        _rewrite_merge_report_paths_to_center(dest, plan_run_id, root)
    except Exception:
        logger.exception("merge_report_rewrite_failed plan_run=%d", plan_run_id)
    logger.info("merge_published plan_run=%d dest=%s", plan_run_id, dest)
    return dest


def _register_merge_artifacts(db: Session, plan_run_id: int, merge_dir: Path) -> int:
    """扫 merge_dir 取 Result_MergeFiles*.xls → 写 plan_run_artifact。"""
    count = 0
    for xls in sorted(merge_dir.glob("Result_MergeFiles*.xls")):
        existing = db.execute(
            select(PlanRunArtifact).where(
                PlanRunArtifact.plan_run_id == plan_run_id,
                PlanRunArtifact.storage_uri == str(xls),
            )
        ).scalar_one_or_none()
        if existing:
            continue
        size = xls.stat().st_size if xls.exists() else 0
        db.add(PlanRunArtifact(
            plan_run_id=plan_run_id,
            host_id=None,
            storage_uri=str(xls),
            artifact_type=ARTIFACT_TYPE_MERGE,
            size_bytes=size,
        ))
        count += 1
    if count:
        db.commit()
    return count


# ── 终态触发 helpers（供 aggregator / aggregator_sync 调用）─────────────

_DEDUP_AUTO_ENV = "STP_DEDUP_AUTO_SCAN"
_DEDUP_AUTO_STATUSES = {"SUCCESS", "PARTIAL_SUCCESS", "FAILED"}


def should_trigger_dedup(run_status: str) -> bool:
    """ADR-0028 方案 A：SUCCESS/PARTIAL_SUCCESS/FAILED 均触发 scan→upload。

    FAILED 自动链只走到 upload；merge 由 ``run_merge_sync`` 返回
    ``skipped_failed``（#697：手动 API 可 ``allow_failed=True`` 放行）。
    """
    if os.getenv(_DEDUP_AUTO_ENV, "1") != "1":
        return False
    return run_status in _DEDUP_AUTO_STATUSES


def resolve_manual_merge_round(
    plan_run_id: int,
) -> tuple[str | None, datetime | None]:
    """#1077: derive a round filter for manual merge — never unconstrained history.

    Prefer the latest non-null ``scan_round_id`` among scan artifacts. If none
    are stamped (legacy), use ``min(created_at)`` of registered scan rows as
    ``round_started_at`` so ``_load_org_files_for_merge`` still has a floor.
    """
    from backend.core.database import SessionLocal

    db = SessionLocal()
    try:
        rows = db.execute(
            select(PlanRunArtifact).where(
                PlanRunArtifact.plan_run_id == plan_run_id,
                PlanRunArtifact.artifact_type == ARTIFACT_TYPE_SCAN,
            )
        ).scalars().all()
        if not rows:
            return None, None
        stamped = [r for r in rows if r.scan_round_id]
        if stamped:
            latest = max(
                stamped,
                key=lambda r: (r.created_at is not None, r.created_at or datetime.min),
            )
            floor = latest.created_at
            try:
                floor = datetime.fromisoformat(
                    str(latest.scan_round_id).replace("Z", "+00:00"),
                )
            except ValueError:
                # Non-ISO round ids still work via exact scan_round_id match;
                # created_at is only a legacy floor for unstamped siblings.
                pass
            return str(latest.scan_round_id), floor
        times = [r.created_at for r in rows if r.created_at is not None]
        if not times:
            return None, None
        return None, min(times)
    finally:
        db.close()


async def enqueue_dedup_terminal_async(
    plan_run_id: int, *, is_final: bool = True, allow_retired: bool = False,
) -> bool:
    """异步 enqueue scan_task（scan_task 完成后自行串行 enqueue upload + merge）。

    返回 True=本轮 scan_task 已在队列（新入队，或 SAQ 键去重返回 ``None`` 表示同轮
    任务已在跑——幂等成功）；False=入队失败（SAQ/Redis 不可用）。后台最佳努力调用方
    可忽略返回值；用户触发路径（#1274）必须据此区分真假成功。

    ADR-0038 D5：``allow_retired=True`` 仅由显式 admin 回收触发传入；目标集不同
    ⇒ SAQ 键追加 ``:ar`` 后缀，避免与自动轮次互相去重（策略不串台）。
    """
    suffix = ("" if is_final else ":inc") + (":ar" if allow_retired else "")
    key = f"scan:{plan_run_id}{suffix}"
    try:
        from backend.tasks.saq_worker import get_queue
        from saq import Job as SaqJob

        queue = get_queue()
        result = await queue.enqueue(
            SaqJob(
                function="scan_task",
                kwargs={
                    "plan_run_id": plan_run_id,
                    "is_final": is_final,
                    "allow_retired": allow_retired,
                },
                key=key,
                timeout=900,
                retries=2,
                retry_delay=10.0,
                retry_backoff=True,
            )
        )
        if result is None:
            # #1274: SAQ 对重复 key 返回 None = 同轮任务已在队列，属幂等成功而非失败。
            logger.info(
                "enqueue_dedup_terminal_async deduped plan_run=%d key=%s",
                plan_run_id, key,
            )
        return True
    except Exception as e:
        logger.error("enqueue_dedup_terminal_async failed plan_run=%d: %s", plan_run_id, e)
        return False


def enqueue_dedup_terminal_sync(
    plan_run_id: int, *, is_final: bool = True, allow_retired: bool = False,
) -> None:
    """同步 enqueue scan_task（scan_task 完成后自行串行 enqueue upload + merge）。

    自动/后台路径保持 ``allow_retired=False``（退役主机留给显式 admin 触发）。
    """
    try:
        from backend.tasks.saq_worker import enqueue_sync

        suffix = ("" if is_final else ":inc") + (":ar" if allow_retired else "")
        enqueue_sync(
            "scan_task",
            key=f"scan:{plan_run_id}{suffix}",
            timeout=900,
            retries=2,
            plan_run_id=plan_run_id,
            is_final=is_final,
            allow_retired=allow_retired,
        )
    except Exception as e:
        logger.error("enqueue_dedup_terminal_sync failed plan_run=%d: %s", plan_run_id, e)
