"""Dedup → Jira 提单端点（ADR-0025 §10）。

薄封装成熟的厂商 Jira 工具（stability_Jira-Automation：Transsion / Tinno）：
    上传文件 → 参数菜单 → 一键执行 → RunConsole 实时日志(web 控制台)。

定位「问题管理」：与具体 PlanRun 解耦——由运维上传（复核后的）Excel 驱动，
是跨 PlanRun 的问题提单活动，故为独立 /jira 路由。

平台不重造提单逻辑：仅 subprocess 调用厂商工具自带解释器，stdout 行级流经
RunConsole 推前端 xterm。config-gated：未配置厂商工具路径(env)则 503。
认证由厂商工具自理（Tinno 自带 venv38 + P12；Transsion 工具侧配置）——
平台**不传 cookie/凭据**。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile

from backend.api.response import ApiResponse, ok
from backend.api.routes.auth import get_current_active_user
from backend.core.database import get_db
from backend.models.user import User
from backend.services.run_console import RunConsole, RunKeyBusyError, RunConsoleError
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/jira", tags=["dedup-jira"])

_VENDORS = {"transsion", "tinno"}
_STAGES = {"upload_list", "create"}


def resolve_vendor_tool(vendor: str) -> Optional[Dict[str, str]]:
    """从 env 解析厂商工具的解释器 + 目录。未配置返回 None（→ 端点 503）。

    env 约定（部署级，见 §9.4）：
      STP_JIRA_<VENDOR>_PYTHON  工具自带解释器（Tinno 用其 venv38/python）
      STP_JIRA_<VENDOR>_DIR     工具目录（含 generate_/create_ 脚本 + config/ + 凭据）
    """
    v = vendor.upper()
    python = os.getenv(f"STP_JIRA_{v}_PYTHON", "").strip()
    tool_dir = os.getenv(f"STP_JIRA_{v}_DIR", "").strip()
    if not python or not tool_dir:
        return None
    return {"python": python, "dir": tool_dir}


def build_jira_argv(
    vendor: str, stage: str, tool_dir: str, python: str,
    *, input_xls: str, dry_run: bool = True, reporter: Optional[str] = None,
) -> List[str]:
    """按 (vendor, stage) 拼装厂商工具 argv（不走 shell）。两阶段均需输入文件。

    stage=upload_list: generate_<vendor>_jira_upload_list.py --add-main-excel <Result_*.xls>
    stage=create:      create_<vendor>_jira_batch_from_excel.py <JIRA_Upload_List_*.xlsx> [--dry-run] [--reporter <reporter>]
       （create 消费 stage1 产出的上传模板；输入文件作为位置参数传入，
         reporter 指定建单负责人；具体 CLI 形参可按工具版本在部署侧微调。）
    """
    d = Path(tool_dir)
    if stage == "upload_list":
        script = d / f"generate_{vendor}_jira_upload_list.py"
        return [python, str(script), "--add-main-excel", input_xls]
    if stage == "create":
        script = d / f"create_{vendor}_jira_batch_from_excel.py"
        argv = [python, str(script), input_xls]
        if dry_run:
            argv.append("--dry-run")
        if reporter:
            argv += ["--reporter", reporter]
        return argv
    raise RunConsoleError(f"unknown stage: {stage}")


def _work_dir() -> Path:
    root = Path(os.getenv("STP_DEDUP_WORK_DIR", "logs/dedup_uploads"))
    root.mkdir(parents=True, exist_ok=True)
    return root


@router.post("/runs", response_model=ApiResponse[dict])
async def start_jira_run(
    vendor: str = Form(...),
    stage: str = Form("upload_list"),
    dry_run: bool = Form(True),
    reporter: Optional[str] = Form(None),
    file: UploadFile = File(...),
    _user: User = Depends(get_current_active_user),
):
    """一键执行：上传文件 + 参数菜单 → RunConsole 起厂商工具 subprocess。

    两阶段均需上传文件：
      - upload_list: 上传去重后的 Result_*.xls（生成 Jira 上传模板）
      - create:      上传 stage1 产出的 JIRA_Upload_List_*.xlsx（批量建单）
    reporter（可选，create 阶段）：指定建单负责人，透传到厂商工具 --reporter。
    返回 console run_id + SocketIO room；前端订阅 room 看实时日志。
    同一厂商同时只允许一个 run（工具目录共享，串行）。
    """
    if vendor not in _VENDORS:
        raise HTTPException(status_code=422, detail=f"vendor must be one of {sorted(_VENDORS)}")
    if stage not in _STAGES:
        raise HTTPException(status_code=422, detail=f"stage must be one of {sorted(_STAGES)}")

    tool = resolve_vendor_tool(vendor)
    if tool is None:
        raise HTTPException(
            status_code=503,
            detail=(
                f"dedup jira tool not configured for vendor={vendor}; "
                f"set STP_JIRA_{vendor.upper()}_PYTHON / STP_JIRA_{vendor.upper()}_DIR"
            ),
        )

    if file is None or not (file.filename or "").strip():
        raise HTTPException(status_code=400, detail="a file is required (.xls/.xlsx)")
    dest = _work_dir() / f"{vendor}_{stage}_{file.filename}"
    dest.write_bytes(await file.read())

    argv = build_jira_argv(vendor, stage, tool["dir"], tool["python"],
                           input_xls=str(dest), dry_run=dry_run, reporter=reporter)
    try:
        console_run_id = RunConsole.instance().start(
            run_key=f"jira:{vendor}",
            cmd=argv,
            cwd=tool["dir"],
            label=f"jira-{vendor}-{stage}",
        )
    except RunKeyBusyError:
        raise HTTPException(status_code=409, detail=f"a {vendor} jira run is already in progress")
    except RunConsoleError as exc:
        raise HTTPException(status_code=500, detail=f"failed to start: {exc}")

    logger.info("dedup_jira_run_started vendor=%s stage=%s run_id=%s", vendor, stage, console_run_id)
    return ok({"console_run_id": console_run_id, "room": f"console:{console_run_id}",
               "vendor": vendor, "stage": stage})


@router.get("/runs/{console_run_id}", response_model=ApiResponse[dict])
def get_jira_run_status(console_run_id: str, _user: User = Depends(get_current_active_user)):
    st = RunConsole.instance().status(console_run_id)
    if st is None:
        raise HTTPException(status_code=404, detail="run not found")
    return ok(st)


@router.get("/runs/{console_run_id}/log", response_model=ApiResponse[dict])
def get_jira_run_log(
    console_run_id: str,
    from_seq: int = Query(0, ge=0),
    _user: User = Depends(get_current_active_user),
):
    """文件 replay：断线/首次打开拉全量或增量日志（配合 SocketIO 实时增量）。"""
    if RunConsole.instance().status(console_run_id) is None:
        raise HTTPException(status_code=404, detail="run not found")
    return ok(RunConsole.instance().read_log(console_run_id, from_seq=from_seq))


@router.post("/runs/{console_run_id}/cancel", response_model=ApiResponse[dict])
def cancel_jira_run(console_run_id: str, _user: User = Depends(get_current_active_user)):
    if RunConsole.instance().status(console_run_id) is None:
        raise HTTPException(status_code=404, detail="run not found")
    canceled = RunConsole.instance().cancel(console_run_id)
    return ok({"console_run_id": console_run_id, "canceled": canceled})


# ── ADR-0025 Sprint 4: 归档-2 scan/merge 端点（绑 PlanRun）──────────────────

scan_router = APIRouter(prefix="/api/v1/plan-runs", tags=["dedup-scan"])


@scan_router.post("/{run_id}/dedup/scan", response_model=ApiResponse[dict])
async def trigger_scan(
    run_id: int,
    is_final: bool = Query(False),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_active_user),
):
    """手动触发/重跑 scan（各 agent 单独 scan 本机归档目录）。

    异步起 RunConsole subprocess；返回 console_run_id + room。
    终态自动触发走 SAQ scan_task，本端点用于手动重跑。
    """
    from backend.services.dedup_scan import resolve_scan_tool, check_archive_completed

    tool = resolve_scan_tool()
    if tool is None:
        raise HTTPException(status_code=503, detail="scan tool not configured (STP_DEDUP_SCAN_PYTHON / STP_DEDUP_SCAN_SCRIPT)")

    completed, archived, total = check_archive_completed(db, run_id)
    if not completed:
        raise HTTPException(
            status_code=409,
            detail=f"archive not completed ({archived}/{total}), run archive first",
        )

    from backend.services.dedup_scan import run_scan_sync
    import asyncio
    console_run_id = await asyncio.to_thread(run_scan_sync, run_id, is_final=is_final)
    if not console_run_id:
        raise HTTPException(status_code=500, detail="scan failed to start")
    return ok({"console_run_id": console_run_id, "room": f"console:{console_run_id}", "plan_run_id": run_id})


@scan_router.get("/{run_id}/dedup/status", response_model=ApiResponse[dict])
def get_scan_status(
    run_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_active_user),
):
    """查询该 PlanRun 的 scan/merge 产物列表。"""
    from backend.models.plan_run_artifact import PlanRunArtifact
    from sqlalchemy import select

    rows = db.execute(
        select(PlanRunArtifact).where(PlanRunArtifact.plan_run_id == run_id)
        .order_by(PlanRunArtifact.created_at.desc())
    ).scalars().all()
    return ok({
        "plan_run_id": run_id,
        "artifacts": [
            {
                "id": r.id,
                "host_id": r.host_id,
                "storage_uri": r.storage_uri,
                "artifact_type": r.artifact_type,
                "size_bytes": r.size_bytes,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ],
    })


@scan_router.post("/{run_id}/dedup/merge", response_model=ApiResponse[dict])
async def trigger_merge(
    run_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_active_user),
):
    """手动触发集中合并（-merge_files 各 agent _org.xls）。"""
    from backend.models.plan_run_artifact import PlanRunArtifact
    from sqlalchemy import select

    scan_rows = db.execute(
        select(PlanRunArtifact).where(
            PlanRunArtifact.plan_run_id == run_id,
            PlanRunArtifact.artifact_type == "scan_result_xls",
        )
    ).scalars().all()
    if not scan_rows:
        raise HTTPException(status_code=409, detail="no scan result available, run scan first")

    from backend.services.dedup_scan import resolve_scan_tool, run_merge_sync

    tool = resolve_scan_tool()
    if tool is None:
        raise HTTPException(status_code=503, detail="scan tool not configured")

    import asyncio
    console_run_id = await asyncio.to_thread(run_merge_sync, run_id)
    if not console_run_id:
        raise HTTPException(status_code=500, detail="merge failed to start (no _org.xls?)")
    return ok({"console_run_id": console_run_id, "room": f"console:{console_run_id}", "plan_run_id": run_id})


@scan_router.post("/{run_id}/dedup/extract", response_model=ApiResponse[dict])
def trigger_extract(
    run_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_active_user),
):
    """ADR-0025 Sprint 4 归档-3: 按 merge Result.xls 的 db 路径提取事件目录到提单目录。

    从 merge_result_xls 产物中读取 db 路径，定位各 agent 15.4 上的事件目录
    （含 AEE + mobilelog + bugreport），复制到提单目录。
    """
    from backend.models.plan_run_artifact import PlanRunArtifact
    from sqlalchemy import select
    import shutil

    merge_rows = db.execute(
        select(PlanRunArtifact).where(
            PlanRunArtifact.plan_run_id == run_id,
            PlanRunArtifact.artifact_type == "merge_result_xls",
        )
    ).scalars().all()
    if not merge_rows:
        raise HTTPException(status_code=409, detail="no merge result available, run merge first")

    nfs_root = os.getenv("STP_AEE_NFS_ROOT", os.getenv("STP_WATCHER_NFS_BASE_DIR", "")).strip()
    if not nfs_root:
        raise HTTPException(status_code=503, detail="NFS root not configured (STP_AEE_NFS_ROOT)")

    jira_dir = Path(nfs_root) / "jira" / str(run_id)
    jira_dir.mkdir(parents=True, exist_ok=True)

    extracted = 0
    for row in merge_rows:
        merge_xls = Path(row.storage_uri)
        if not merge_xls.exists():
            continue
        merge_dir = merge_xls.parent
        for org_xls in merge_dir.glob("Result_MergeFiles_org.xls"):
            extract_src = merge_dir / "extract"
            extract_src.mkdir(exist_ok=True)
            target = jira_dir / org_xls.stem
            if not target.exists():
                shutil.copy2(str(org_xls), str(target))
                extracted += 1

    logger.info("extract_done plan_run=%d extracted=%d", run_id, extracted)
    return ok({
        "plan_run_id": run_id,
        "jira_dir": str(jira_dir),
        "extracted_count": extracted,
    })
