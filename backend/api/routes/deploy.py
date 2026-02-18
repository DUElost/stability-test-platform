from __future__ import annotations
import logging
import os
import re
import subprocess
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.core.database import get_db, SessionLocal
from backend.models.schemas import Deployment, DeploymentStatus, Host
from backend.api.schemas import DeploymentCreate, DeploymentOut, DeploymentStatusOut
from backend.api.routes.auth import require_admin, User
from backend.api.routes.websocket import schedule_broadcast

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/deploy", tags=["deploy"])

INSTALL_PATH = "/opt/stability-test-agent"
AGENT_DIR = "backend/agent"

# 批量部署最大并发数
MAX_DEPLOY_CONCURRENCY = int(os.getenv("MAX_DEPLOY_CONCURRENCY", "5"))

# 安全路径验证：仅允许 POSIX 标准路径字符
_SAFE_PATH_PATTERN = re.compile(r"^/[a-zA-Z0-9_./-]+$")
# 禁止的路径模式
_FORBIDDEN_PATTERNS = ["..", "~", "$", "`", ";", "|", "&&", "||", "\n", "\r"]


def _validate_install_path(path: str) -> Tuple[bool, str]:
    """
    验证安装路径安全性。

    Returns:
        (is_valid, error_message)
    """
    if not path:
        return False, "Install path cannot be empty"

    # 检查禁止的模式
    for pattern in _FORBIDDEN_PATTERNS:
        if pattern in path:
            return False, f"Install path contains forbidden pattern: {pattern}"

    # 验证路径格式
    if not _SAFE_PATH_PATTERN.match(path):
        return False, "Install path must be an absolute POSIX path (alphanumeric, underscore, dot, slash, hyphen)"

    # 验证路径不以危险前缀开头
    dangerous_prefixes = ["/etc", "/usr/bin", "/usr/sbin", "/bin", "/sbin", "/root"]
    for prefix in dangerous_prefixes:
        if path.startswith(prefix):
            return False, f"Install path cannot start with {prefix}"

    return True, ""


def _get_ssh_alias(host_name: str) -> str:
    """Map host name to SSH config alias."""
    return host_name


def _run_ssh_command(alias: str, command: str, timeout: int = 60) -> Tuple[int, str, str]:
    """Execute SSH command via npx mcp-ssh."""
    try:
        result = subprocess.run(
            ["npx", "@laomeifun/mcp-ssh", "exec", alias, command],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "Command timeout"
    except Exception as e:
        return -1, "", str(e)


def _upload_file_via_mcp(alias: str, local_path: str, remote_path: str) -> bool:
    """Upload file via MCP SSH."""
    try:
        # Use scp via subprocess (MCP SSH may not have direct upload)
        result = subprocess.run(
            ["scp", "-o", "StrictHostKeyChecking=no", local_path, f"{alias}:{remote_path}"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        return result.returncode == 0
    except Exception as e:
        logger.error(f"Upload failed: {e}")
        return False


def _deploy_to_host(
    db: Session,
    host: Host,
    deployment: Deployment,
    install_path: str = INSTALL_PATH,
) -> bool:
    """Execute deployment to a single host."""
    # 验证安装路径安全性
    is_valid, error_msg = _validate_install_path(install_path)
    if not is_valid:
        deployment.status = DeploymentStatus.FAILED
        deployment.error_message = f"Invalid install path: {error_msg}"
        logger.error(f"Invalid install path for host {host.name}: {error_msg}")
        return False

    alias = _get_ssh_alias(host.name)
    logs = []

    def add_log(msg: str):
        logs.append(f"[{datetime.now().isoformat()}] {msg}")
        deployment.logs = "\n".join(logs)
        db.commit()

    # Step 1: Test SSH connection
    add_log(f"Connecting to {alias} ({host.ip})...")
    returncode, stdout, stderr = _run_ssh_command(alias, "echo ok", timeout=30)
    if returncode != 0:
        deployment.status = DeploymentStatus.FAILED
        deployment.error_message = f"SSH connection failed: {stderr}"
        add_log(f"ERROR: SSH connection failed - {stderr}")
        db.commit()
        return False

    add_log("SSH connected successfully")

    # Step 2: Create installation directory
    add_log(f"Creating directory {install_path}...")
    returncode, stdout, stderr = _run_ssh_command(
        alias, f"sudo mkdir -p {install_path} && sudo chown $(whoami):$(whoami) {install_path}"
    )
    if returncode != 0:
        deployment.status = DeploymentStatus.FAILED
        deployment.error_message = f"Failed to create directory: {stderr}"
        add_log(f"ERROR: Failed to create directory - {stderr}")
        db.commit()
        return False

    add_log(f"Directory {install_path} created")

    # Step 3: Upload agent code
    add_log("Uploading agent code...")
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    agent_src = os.path.join(project_root, AGENT_DIR)

    # Create temp tar of agent directory
    import tarfile
    with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
        tmp_path = tmp.name

    with tarfile.open(tmp_path, "w:gz") as tar:
        tar.add(agent_src, arcname="agent")

    # Upload via scp
    try:
        result = subprocess.run(
            ["scp", "-o", "StrictHostKeyChecking=no", tmp_path, f"{alias}:/tmp/agent.tar.gz"],
            capture_output=True,
            timeout=120,
        )
        if result.returncode != 0:
            raise Exception(f"SCP failed: {result.stderr.decode()}")

        # Extract on remote
        returncode, stdout, stderr = _run_ssh_command(
            alias, f"cd {install_path} && tar -xzf /tmp/agent.tar.gz && rm /tmp/agent.tar.gz"
        )
        if returncode != 0:
            raise Exception(f"Extract failed: {stderr}")

        add_log("Agent code uploaded and extracted")
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

    # Step 4: Fix line endings
    add_log("Fixing line endings...")
    _run_ssh_command(alias, f"sed -i 's/\\r$//' {install_path}/agent/install_agent.sh")

    # Step 5: Run installation script
    add_log("Running installation script...")
    returncode, stdout, stderr = _run_ssh_command(
        alias, f"cd {install_path}/agent && chmod +x install_agent.sh && ./install_agent.sh",
        timeout=300,
    )
    if returncode != 0:
        # Installation might fail due to sudo, but we can continue
        add_log(f"Installation script warning: {stderr}")

    add_log("Installation completed")

    # Step 6: Start service
    add_log("Starting agent service...")
    returncode, stdout, stderr = _run_ssh_command(
        alias, f"sudo systemctl start stability-test-agent || cd {install_path} && nohup python3 -m agent.main > /tmp/agent.log 2>&1 &"
    )
    if returncode != 0:
        deployment.status = DeploymentStatus.FAILED
        deployment.error_message = f"Failed to start service: {stderr}"
        add_log(f"ERROR: Failed to start service - {stderr}")
        db.commit()
        return False

    add_log("Agent service started")
    deployment.status = DeploymentStatus.SUCCESS
    deployment.finished_at = datetime.utcnow()
    add_log("Deployment completed successfully")
    db.commit()
    return True


@router.post("/hosts/{host_id}", response_model=DeploymentOut)
def deploy_to_host(
    host_id: int,
    payload: DeploymentCreate = DeploymentCreate(),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),  # Require admin auth
):
    """Trigger deployment to a specific host."""
    host = db.get(Host, host_id)
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    # Create deployment record
    deployment = Deployment(
        host_id=host_id,
        status=DeploymentStatus.PENDING,
        install_path=payload.install_path,
    )
    db.add(deployment)
    db.commit()
    db.refresh(deployment)

    # Execute deployment
    deployment.status = DeploymentStatus.RUNNING
    db.commit()

    # Run deployment synchronously (the function uses sync subprocess internally)
    _deploy_to_host(db, host, deployment, payload.install_path)

    return deployment


@router.get("/hosts/{host_id}/history", response_model=List[DeploymentOut])
def get_deployment_history(
    host_id: int,
    limit: int = 10,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),  # Require admin auth
):
    """Get deployment history for a host."""
    host = db.get(Host, host_id)
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    deployments = (
        db.query(Deployment)
        .filter(Deployment.host_id == host_id)
        .order_by(Deployment.created_at.desc())
        .limit(limit)
        .all()
    )
    return deployments


@router.get("/hosts/{host_id}/latest", response_model=DeploymentOut)
def get_latest_deployment(
    host_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),  # Require admin auth
):
    """Get latest deployment for a host."""
    host = db.get(Host, host_id)
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    deployment = (
        db.query(Deployment)
        .filter(Deployment.host_id == host_id)
        .order_by(Deployment.created_at.desc())
        .first()
    )
    if not deployment:
        raise HTTPException(status_code=404, detail="No deployment found")

    return deployment


# ---------------------------------------------------------------------------
# Batch Deploy
# ---------------------------------------------------------------------------

class BatchDeployCreate(BaseModel):
    host_ids: List[int]
    install_path: str = "/opt/stability-test-agent"


class BatchDeployOut(BaseModel):
    deployments: List[DeploymentOut]
    total: int


def _deploy_with_progress(deployment_id: int, host_id: int, install_path: str) -> None:
    """Run deployment in background thread, broadcasting progress via WS."""
    try:
        db = SessionLocal()
        try:
            deployment = db.get(Deployment, deployment_id)
            host = db.get(Host, host_id)
            if not deployment or not host:
                return

            deployment.status = DeploymentStatus.RUNNING
            db.commit()

            schedule_broadcast("/ws/dashboard", {
                "type": "DEPLOY_UPDATE",
                "payload": {
                    "deployment_id": deployment_id,
                    "host_id": host_id,
                    "status": "RUNNING",
                    "message": f"Starting deployment to {host.name}",
                },
            })

            success = _deploy_to_host(db, host, deployment, install_path)

            schedule_broadcast("/ws/dashboard", {
                "type": "DEPLOY_UPDATE",
                "payload": {
                    "deployment_id": deployment_id,
                    "host_id": host_id,
                    "status": "SUCCESS" if success else "FAILED",
                    "message": "Deployment completed" if success else (deployment.error_message or "Deployment failed"),
                },
            })
        finally:
            db.close()
    except Exception:
        logger.exception("deploy_with_progress_failed", extra={"deployment_id": deployment_id})
        # Ensure deployment is marked FAILED so it doesn't stay stuck in RUNNING
        try:
            recover_db = SessionLocal()
            try:
                dep = recover_db.get(Deployment, deployment_id)
                if dep and dep.status not in (DeploymentStatus.SUCCESS, DeploymentStatus.FAILED):
                    dep.status = DeploymentStatus.FAILED
                    dep.error_message = "Unexpected error during deployment (see server logs)"
                    dep.finished_at = datetime.utcnow()
                    recover_db.commit()
            finally:
                recover_db.close()
        except Exception:
            logger.exception("deploy_recovery_failed", extra={"deployment_id": deployment_id})
        schedule_broadcast("/ws/dashboard", {
            "type": "DEPLOY_UPDATE",
            "payload": {
                "deployment_id": deployment_id,
                "host_id": host_id,
                "status": "FAILED",
                "message": "Deployment failed due to unexpected error",
            },
        })


@router.post("/batch", response_model=BatchDeployOut)
def batch_deploy(
    payload: BatchDeployCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Trigger deployment to multiple hosts in parallel."""
    if not payload.host_ids:
        raise HTTPException(status_code=400, detail="No host IDs provided")

    hosts = db.query(Host).filter(Host.id.in_(payload.host_ids)).all()
    if not hosts:
        raise HTTPException(status_code=404, detail="No hosts found")

    found_ids = {h.id for h in hosts}
    missing = set(payload.host_ids) - found_ids
    if missing:
        raise HTTPException(status_code=404, detail=f"Hosts not found: {sorted(missing)}")

    deployments = []
    for host in hosts:
        deployment = Deployment(
            host_id=host.id,
            status=DeploymentStatus.PENDING,
            install_path=payload.install_path,
        )
        db.add(deployment)
        deployments.append(deployment)

    db.commit()
    for d in deployments:
        db.refresh(d)

    # 使用有界线程池限制并发数，避免线程风暴
    with ThreadPoolExecutor(max_workers=MAX_DEPLOY_CONCURRENCY) as executor:
        futures = {
            executor.submit(_deploy_with_progress, deployment.id, deployment.host_id, payload.install_path): deployment
            for deployment in deployments
        }
        # 等待所有任务完成（可选：记录失败的任务）
        for future in as_completed(futures):
            deployment = futures[future]
            try:
                future.result()  # 抛出异常让调用者知道
            except Exception as e:
                logger.error(f"Deployment {deployment.id} failed: {e}")

    return BatchDeployOut(
        deployments=deployments,
        total=len(deployments),
    )
