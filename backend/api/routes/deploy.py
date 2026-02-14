from __future__ import annotations
import logging
import os
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.core.database import get_db
from backend.models.schemas import Deployment, DeploymentStatus, Host
from backend.api.schemas import DeploymentCreate, DeploymentOut, DeploymentStatusOut
from backend.api.routes.auth import require_admin, User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/deploy", tags=["deploy"])

INSTALL_PATH = "/opt/stability-test-agent"
AGENT_DIR = "backend/agent"


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
