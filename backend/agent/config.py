"""
Agent 集中路径配置模块

所有路径统一从 BASE_DIR 派生，消除各文件中的硬编码路径。
支持两种运行模式：
  - 开发模式: python -m backend.agent.main (BASE_DIR = 项目根目录)
  - 部署模式: python -m agent.main         (BASE_DIR = /opt/stability-test-agent)
"""

import os
from pathlib import Path


def _resolve_base_dir() -> Path:
    """自动检测运行模式并返回 BASE_DIR。

    优先级:
      1. AGENT_INSTALL_DIR 环境变量
      2. 基于 config.py 文件位置自动推导
    """
    env_dir = os.environ.get("AGENT_INSTALL_DIR")
    if env_dir:
        return Path(env_dir)

    # config.py 所在目录
    this_dir = Path(__file__).resolve().parent  # .../agent/

    # 部署模式: /opt/stability-test-agent/agent/config.py → parent.parent
    # 开发模式: <project>/backend/agent/config.py → parent.parent.parent
    if this_dir.parent.name == "backend":
        # 开发模式: backend/agent/ → 项目根目录
        return this_dir.parent.parent
    else:
        # 部署模式: agent/ → 安装根目录
        return this_dir.parent


BASE_DIR: Path = _resolve_base_dir()

# Agent 源码根（热更新落盘目录；与 BASE_DIR 在部署模式下不同）
AGENT_DIR: Path = Path(__file__).resolve().parent

# 日志目录
LOG_DIR: Path = BASE_DIR / "logs"
RUN_LOG_DIR: Path = LOG_DIR / "runs"

# 资源目录（install_agent.sh 遗留的 $INSTALL_DIR/resources；AIMonkey 以 AGENT_DIR 为准）
RESOURCE_DIR: Path = BASE_DIR / "resources"

def get_run_log_dir(run_id: int) -> str:
    """返回指定 run_id 的日志目录路径"""
    return str(RUN_LOG_DIR / str(run_id))


def get_aimonkey_resource_dir() -> str:
    """返回 AIMONKEY 资源根目录（热更新路径 ``agent/resources/aimonkey``）。"""
    from .aimonkey_paths import get_aimonkey_resource_root

    return str(get_aimonkey_resource_root())


def ensure_dirs() -> None:
    """确保关键目录存在"""
    from .aimonkey_paths import get_aimonkey_resource_root

    for d in (LOG_DIR, RUN_LOG_DIR, RESOURCE_DIR, get_aimonkey_resource_root()):
        d.mkdir(parents=True, exist_ok=True)
