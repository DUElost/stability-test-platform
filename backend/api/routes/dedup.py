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
from backend.models.user import User
from backend.services.run_console import RunConsole, RunKeyBusyError, RunConsoleError

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
