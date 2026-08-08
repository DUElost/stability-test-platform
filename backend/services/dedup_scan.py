"""Dedup scan/merge service — ADR-0025 Sprint 4 归档-2。

各 agent 单独 scan（start_log_scan）→ 集中 merge（-merge_files）。
产物（Result_*.xls）写 plan_run_artifact 表。
config-gated：未配置 scan 工具 env 则跳过 + 503。
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models.plan_run_artifact import PlanRunArtifact

logger = logging.getLogger(__name__)

ARTIFACT_TYPE_SCAN = "scan_result_xls"
ARTIFACT_TYPE_MERGE = "merge_result_xls"

# Windows CreateProcessW argv 拼接上限（留余量给解释器/引号开销）
_WIN_MERGE_ARGV_CHAR_LIMIT = 30_000

_merge_files_list_supported: Optional[bool] = None


def resolve_scan_tool() -> Optional[Dict[str, str]]:
    """从 env 解析 scan 工具解释器 + 脚本路径。未配置返回 None。"""
    python = os.getenv("STP_DEDUP_SCAN_PYTHON", "").strip()
    script = os.getenv("STP_DEDUP_SCAN_SCRIPT", "").strip()
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
    db: Session, plan_run_id: int, dedup_dir: Path
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
        ))
        count += 1
    if count:
        db.commit()
    return count


def run_scan_sync(plan_run_id: int, *, is_final: bool = False) -> str:
    """扫描 15.4 CIFS dedup/{plan_run_id}/ 目录，注册已上送的 *_org.xls 产物。

    Agent 已通过 scan_now → run_local_scan → UploadManager 上送文件到 NFS。
    本函数仅做文件发现 + DB 注册，不再调 subprocess / RunConsole。
    返回注册产物数（字符串化），空串表示无新产物。
    """
    from backend.core.database import SessionLocal

    db = SessionLocal()
    try:
        nfs_root = os.getenv("STP_AEE_NFS_ROOT", os.getenv("STP_WATCHER_NFS_BASE_DIR", "")).strip()
        if not nfs_root:
            logger.warning("scan_skip_nfs_root_not_set plan_run=%d", plan_run_id)
            return ""

        dedup_dir = Path(nfs_root) / "dedup" / str(plan_run_id)
        if not dedup_dir.is_dir():
            logger.warning("scan_skip_dedup_dir_missing plan_run=%d dir=%s", plan_run_id, dedup_dir)
            return ""

        n = _register_scan_artifacts_from_nfs(db, plan_run_id, dedup_dir)
        logger.info("scan_artifacts_registered plan_run=%d count=%d", plan_run_id, n)
        return str(n) if n else ""
    finally:
        db.close()


def count_hosts_with_scan_artifacts(plan_run_id: int, host_ids: Sequence[str]) -> int:
    """``host_ids`` 中已登记 scan 产物的**去重 host 数**。

    两处都必须收窄，否则完备性判定会误报「齐了」：

    - 按 **host** 而非文件数：``_register_scan_artifacts_from_nfs`` 返回文件数，而
      每台 host 上送 2 个匹配文件（``_org.xls`` 与 ``_org_dedup_org_*.xls``），拿它
      跟 host 数比会让「一台上送完毕」冒充「全部齐了」。
    - 限定在本轮 ``host_ids`` 内：增量扫描复用同一个 ``plan_run_id``，上一轮别的
      host 留下的产物会顶替本轮触发 host 的名额 —— 例如本轮只触发 host-b，而
      host-a 的旧产物已在表里，全局计数即为 1，轮询会在首检就跳出，host-b 的新
      产物赶不上这轮 merge。
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


def run_merge_sync(plan_run_id: int) -> str:
    """同步执行 merge（-merge_files 或 -merge_files_list，视工具能力）。

    阻塞等待子进程完成，校验 merge_result/ 出现新产物目录，返回 "ok" 或空串。
    """
    tool = resolve_scan_tool()
    if tool is None:
        logger.warning("merge_skip_tool_not_configured plan_run=%d", plan_run_id)
        return ""

    org_files = _load_org_files_for_merge(plan_run_id)
    if not org_files:
        logger.warning("merge_skip_no_org_files plan_run=%d", plan_run_id)
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

    try:
        from backend.core.database import SessionLocal

        inner_db = SessionLocal()
        try:
            n = _register_merge_artifacts(inner_db, plan_run_id, latest)
            logger.info("merge_artifacts_registered plan_run=%d count=%d dir=%s", plan_run_id, n, latest)
        finally:
            inner_db.close()
    except Exception:
        logger.exception("merge_register_artifacts_failed plan_run=%d", plan_run_id)
        raise

    logger.info("merge_done plan_run=%d", plan_run_id)
    return "ok"


def _load_org_files_for_merge(plan_run_id: int) -> list[str]:
    from backend.core.database import SessionLocal

    db = SessionLocal()
    try:
        rows = db.execute(
            select(PlanRunArtifact.storage_uri).where(
                PlanRunArtifact.plan_run_id == plan_run_id,
                PlanRunArtifact.artifact_type == ARTIFACT_TYPE_SCAN,
            )
        ).all()
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
    """构建 merge 子进程 argv；必要时回退 -merge_files。"""
    if scan_tool_supports_merge_files_list(tool):
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

    argv = [tool["python"], tool["script"], "-merge_files", *org_files, *side_argv]
    if sum(len(part) for part in argv) > _WIN_MERGE_ARGV_CHAR_LIMIT:
        raise RuntimeError(
            f"merge argv too long ({len(org_files)} files); "
            "upgrade scan tool for -merge_files_list or reduce org file count"
        )
    return argv, None


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
_DEDUP_AUTO_STATUSES = {"SUCCESS", "PARTIAL_SUCCESS"}


def should_trigger_dedup(run_status: str) -> bool:
    """Auto-scan successful runs; failed/aborted runs require confirmation."""
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
