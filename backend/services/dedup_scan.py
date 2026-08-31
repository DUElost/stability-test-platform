"""Dedup scan/merge service — ADR-0025 Sprint 4 归档-2。

各 agent 单独 scan（start_log_scan）→ 集中 merge（-merge_files_list）。
产物（Result_*.xls）写 plan_run_artifact 表。
config-gated：未配置 scan 工具 env 则跳过 + 503。
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from backend.core import metrics
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

        dedup_dir = Path(nfs_root) / "dedup" / str(plan_run_id)
        if not dedup_dir.is_dir():
            logger.warning("scan_skip_dedup_dir_missing plan_run=%d dir=%s", plan_run_id, dedup_dir)
            return ""

        n = _register_scan_artifacts_from_nfs(
            db, plan_run_id, dedup_dir, scan_round_id=scan_round_id,
        )
        logger.info("scan_artifacts_registered plan_run=%d count=%d", plan_run_id, n)
        return str(n) if n else ""
    finally:
        db.close()


def count_hosts_with_scan_artifacts(
    plan_run_id: int, host_ids: Sequence[str], *, since: datetime
) -> int:
    """``host_ids`` 中**本轮**已登记 scan 产物的去重 host 数。

    三个维度都必须收窄，否则完备性判定会误报「齐了」，后果都一样：慢的 host 漏出
    合并，或者合并的是过期报告。

    - 按 **host** 而非文件数：``_register_scan_artifacts_from_nfs`` 返回文件数，而
      每台 host 上送 2 个匹配文件（``_org.xls`` 与 ``_org_dedup_org_*.xls``），拿它
      跟 host 数比会让「一台上送完毕」冒充「全部齐了」。
    - 限定在本轮 ``host_ids`` 内：本轮只触发 host-b 时，host-a 的旧产物会顶替
      host-b 的名额。
    - 限定在本轮**水位线** ``since`` 之后：增量扫描复用同一个 ``plan_run_id``，所以
      同一台 host 上一轮留下的产物会在本轮首检就计数，轮询立刻跳出，该 host 这轮的
      新产物赶不上 merge。``since`` 取下发 ``scan_now`` 之前的时刻；``created_at``
      与它同为 backend 进程侧 UTC 时间（模型是 Python default，不是库端 now()），
      不存在时钟偏差。
    """
    if not host_ids:
        return 0

    from backend.core.database import SessionLocal
    from sqlalchemy import distinct, func

    db = SessionLocal()
    try:
        return int(
            db.execute(
                select(func.count(distinct(PlanRunArtifact.host_id))).where(
                    PlanRunArtifact.plan_run_id == plan_run_id,
                    PlanRunArtifact.artifact_type == ARTIFACT_TYPE_SCAN,
                    PlanRunArtifact.host_id.in_(list(host_ids)),
                    PlanRunArtifact.created_at >= since,
                )
            ).scalar()
            or 0
        )
    finally:
        db.close()


def record_scan_archive_state(
    plan_run_id: int,
    *,
    hosts_triggered: int,
    artifacts_registered: int,
    hosts_with_artifacts: int,
    hosts_not_acked: int = 0,
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

    # P1-3/P3-1：下发了 host 却零产物时，把「没有报表」显式挂到终态结果上，
    # 供 PlanRun 详情/前端展示（终态 status 本身不变，避免状态机额外转换）。
    if hosts_triggered > 0 and hosts_with_artifacts == 0:
        try:
            db2 = SessionLocal()
            try:
                db2.execute(
                    text(
                        "UPDATE plan_run "
                        "SET result_summary = jsonb_set("
                        "  COALESCE(result_summary, '{}'::jsonb), "
                        "  '{scan_failed}', 'true', true"
                        ") "
                        "WHERE id = :run_id"
                    ),
                    {"run_id": plan_run_id},
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
) -> str:
    """同步执行 merge（-merge_files_list；工具不支持即配置错误，#291）。

    阻塞等待子进程完成，校验 merge_result/ 出现新产物目录，返回 "ok" 或空串。

    ADR-0028 D2：PlanRun ``FAILED``（含 abort）不 merge——即使已有 scan artifact
    也不执行，显式门禁优先于「scan xls 不足自然跳过」。
    """
    from backend.core.database import SessionLocal
    from backend.models.enums import PlanRunStatus
    from backend.models.plan_run import PlanRun

    db = SessionLocal()
    try:
        run = db.get(PlanRun, plan_run_id)
    finally:
        db.close()
    if run is not None and run.status == PlanRunStatus.FAILED.value:
        logger.info("merge_skip_failed_plan_run plan_run=%d", plan_run_id)
        metrics.merge_skip_failed_plan_run_total.inc()
        return ""

    tool = resolve_scan_tool()
    if tool is None:
        logger.warning("merge_skip_tool_not_configured plan_run=%d", plan_run_id)
        metrics.merge_skip_tool_not_configured_total.inc()
        return ""

    org_files = _load_org_files_for_merge(
        plan_run_id,
        scan_round_id=scan_round_id,
        round_started_at=round_started_at,
    )
    if not org_files:
        logger.warning("merge_skip_no_org_files plan_run=%d", plan_run_id)
        metrics.merge_skip_no_org_files_total.inc()
        return ""

    side = os.getenv("STP_DEDUP_SCAN_TAG", "shanghai")
    side_argv = ["-side", "factory"] if "factory" in side.lower() else ["-side", "shanghai"]
    cwd = str(Path(tool["script"]).parent)
    merge_root = Path(tool["script"]).parent / "merge_result"
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
    # （设计 adr-0025: `{CIFS}/dedup/{plan_run_id}/merge/`）。发布到中心后
    # 注册中心路径；中心未配置（无 STP_AEE_NFS_ROOT）时回退本机路径。
    published = _publish_merge_to_center(plan_run_id, latest)

    try:
        from backend.core.database import SessionLocal

        inner_db = SessionLocal()
        try:
            n = _register_merge_artifacts(inner_db, plan_run_id, published or latest)
            logger.info(
                "merge_artifacts_registered plan_run=%d count=%d dir=%s",
                plan_run_id, n, published or latest,
            )
        finally:
            inner_db.close()
    except Exception:
        logger.exception("merge_register_artifacts_failed plan_run=%d", plan_run_id)
        raise

    logger.info("merge_done plan_run=%d", plan_run_id)
    return "ok"


def _load_org_files_for_merge(
    plan_run_id: int,
    *,
    scan_round_id: str | None = None,
    round_started_at: datetime | None = None,
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
        return [r[0] for r in rows if "_org.xls" in r[0]]
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
    return ": error:" in text or "error: argument" in text


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
    """把 agent 本机 scan 路径映射为中心可达路径。

    源形态: ``{hdd}/.stp-scan/{run_id}-{rand}/{folder}/{serial}/aee_exp/{event_dir}/{sub}``
    （或 ``vendor_aee_exp``）。目标: ``{center}/devices/{run_id}/{event_dir}/{sub}``——
    事件目录已按引用上送中心（EventUploader copytree 目录名 = 事件目录名），
    映射后路径对外可达。非事件路径（无 aee_exp 段）原样返回。

    文档依据: ADR-0025 D2「开发通过同一共享只读访问」——Jira 提单引用的
    报表路径必须指向中心存储，不能是各 host 本机路径。
    """
    m = re.search(r"/(?:vendor_)?aee_exp/([^/]+)(/.*)?$", path)
    if not m:
        return path
    event_dir = m.group(1)
    sub = m.group(2) or ""
    return f"{center_root}/devices/{plan_run_id}/{event_dir}{sub}"


def _resolve_center_event_path(
    center_root: str, plan_run_id: int, event_dir: str, sub: str,
) -> str:
    """映射后可达性兜底：本 run 未上送时搜历史 run 的上送位置。

    场景（2026-08-31 验收发现）：scan 对同内容事件去重合并显示代表目录
    （如注入 cp 的 02/04 同内容——报表只显示 02），而 upload 上送的是
    实际引用的 04——``devices/{run_id}/02`` 不存在。此时搜
    ``devices/*/{event_dir}``（历史 run 上送位置）映射到存在的副本；
    搜不到保留原映射（尽力而为，中心不可达时由人工/提取路径兜底）。
    """
    candidate = f"{center_root}/devices/{plan_run_id}/{event_dir}{sub}"
    if os.path.exists(candidate):
        return candidate
    try:
        hits = sorted(Path(center_root, "devices").glob(f"*/{event_dir}"))
    except OSError:
        return candidate
    if hits:
        return str(hits[0]) + sub
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
                        m = re.search(r"/devices/\d+/([^/]+)(/.*)?$", mapped)
                        if m:
                            mapped = _resolve_center_event_path(
                                center_root, plan_run_id, m.group(1), m.group(2) or "")
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


def _publish_merge_to_center(plan_run_id: int, merge_dir: Path) -> "Path | None":
    """把工具本机 merge_result 产物发布到中心 ``dedup/{run_id}/merge/``。

    工具（start_log_scan.py）固定输出到控制面本机
    ``{工具目录}/merge_result/{ts}/``——但 artifact 应指向中心持久路径
    （设计 adr-0025: ``{CIFS}/dedup/{plan_run_id}/merge/``）。发布 = 拷贝
    产物到中心并返回中心目录；中心未配置（无 ``STP_AEE_NFS_ROOT``）时
    返回 None（调用方回退注册本机路径——历史行为）。
    """
    from backend.core.storage_root import resolve_shared_storage_root

    root = resolve_shared_storage_root()
    if not root:
        logger.warning("merge_publish_skip_no_center plan_run=%d", plan_run_id)
        return None
    dest = Path(root) / "dedup" / str(plan_run_id) / "merge"
    try:
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copytree(merge_dir, dest, dirs_exist_ok=True)
    except OSError:
        logger.exception("merge_publish_failed plan_run=%d dest=%s", plan_run_id, dest)
        return None
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

    FAILED 只走到 upload（事件到达 CIFS）：手动 merge/extract 路由对 FAILED
    返回 409；SAQ 侧 run_merge_sync 显式跳过，extract 因 merge 无产物短路。
    """
    if os.getenv(_DEDUP_AUTO_ENV, "1") != "1":
        return False
    return run_status in _DEDUP_AUTO_STATUSES


async def enqueue_dedup_terminal_async(plan_run_id: int, *, is_final: bool = True) -> None:
    """异步 enqueue scan_task（scan_task 完成后自行串行 enqueue upload + merge）。"""
    try:
        from backend.tasks.saq_worker import get_queue
        from saq import Job as SaqJob

        suffix = "" if is_final else ":inc"
        queue = get_queue()
        await queue.enqueue(
            SaqJob(
                function="scan_task",
                kwargs={"plan_run_id": plan_run_id, "is_final": is_final},
                key=f"scan:{plan_run_id}{suffix}",
                timeout=900,
                retries=2,
                retry_delay=10.0,
                retry_backoff=True,
            )
        )
    except Exception as e:
        logger.error("enqueue_dedup_terminal_async failed plan_run=%d: %s", plan_run_id, e)


def enqueue_dedup_terminal_sync(plan_run_id: int, *, is_final: bool = True) -> None:
    """同步 enqueue scan_task（scan_task 完成后自行串行 enqueue upload + merge）。"""
    try:
        from backend.tasks.saq_worker import enqueue_sync

        suffix = "" if is_final else ":inc"
        enqueue_sync(
            "scan_task",
            key=f"scan:{plan_run_id}{suffix}",
            timeout=900,
            retries=2,
            plan_run_id=plan_run_id,
            is_final=is_final,
        )
    except Exception as e:
        logger.error("enqueue_dedup_terminal_sync failed plan_run=%d: %s", plan_run_id, e)
