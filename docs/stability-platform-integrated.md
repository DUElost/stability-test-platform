# 稳定性测试管理平台 - 整合实施计划

**生成时间**：2026-01-21
**计划版本**：v2.0 (Integrated)
**整合来源**：stability-test-platform.md + host-connectivity-verification.md

---

## 计划说明

本计划整合了两个互补的规划文档：
1. **stability-test-platform.md**：测试执行与任务调度
2. **host-connectivity-verification.md**：主机连通性验证与监控

原档保留作为参考，本计划提供统一的实施方案。

---

## 任务类型

- [x] 后端 (→ Codex)
- [x] 前端 (→ Gemini)
- [x] 全栈 (→ 并行)

---

## 环境背景

### 网络拓扑
```
                    ┌────────────────────────────────────┐
                    │   Windows 管理控制台 (Xshell/Xftp)  │
                    └─────────────┬──────────────────────┘
                                  │ SSH/WebSocket
                   ┌──────────────▼──────────────────────┐
                   │      中心服务 (FastAPI + React UI)  │
                   │   172.21.15.x (调度/状态管理/日志)   │
                   └──────────────┬─────────────────────┘
                                  │ API/心跳
        ┌─────────────────────────┼────────────────────────┐
        │                         │                        │
┌───────▼──────┐         ┌────────▼────────┐      ┌────────▼────────┐
│ Linux Host 1 │         │ Linux Host 2    │      │ Linux Host N    │
│ 172.21.15.*  │         │ 172.21.15.*     │      │ 172.21.15.*     │
│              │         │                 │      │                 │
│ ┌──────────┐ │         │ ┌──────────┐    │      │ ┌──────────┐    │
│ │  Agent   │ │         │ │  Agent   │    │      │ │  Agent   │    │
│ └────┬─────┘ │         │ └────┬─────┘    │      │ └────┬─────┘    │
│      │       │         │      │          │      │      │          │
│ ┌────▼─────┐ │         │ ┌────▼─────┐    │      │ ┌────▼─────┐    │
│ │   ADB    │ │         │ │   ADB    │    │      │ │   ADB    │    │
│ └────┬─────┘ │         │ └────┬─────┘    │      │ └────┬─────┘    │
└───────┼──────┘         └───────┼─────────┘      └──────┼──────────┘
        │                        │                         │
   ┌────▼─────┐          ┌───────▼──────┐          ┌───────▼──────┐
   │ Device 1 │          │  Device 2    │          │  Device N    │
   └──────────┘          └──────────────┘          └──────────────┘

                    ┌─────────────────────────────────────┐
                    │  中心存储服务器 172.21.15.4         │
                    │  (12TB 存储 - 日志归档)              │
                    └─────────────────────────────────────┘
                    ▲
                    │ NFS 挂载
        ┌───────────┴───────────┐
        │     所有 Linux Hosts  │
        └────────────────────────┘
```

### 配置概览
- **网络**：172.21.15.* 网段
- **中心存储**：172.21.15.4（8GB RAM，12TB 存储）
- **Linux 主机**：多台（每台 8GB RAM，256GB 存储）
- **访问方式**：SSH（Xshell/Xftp）
- **测试工具**：Monkey、MTBF、DDR、GPU、待机测试

---

## 技术方案

### 架构决策：中心调度 + Agent + 连通性验证

采用 **"中心调度 + 轻量 Agent + 分层连通性验证"** 架构：

```
┌─────────────────────────────────────────────────────────────────┐
│                        控制面 (Control Plane)                   │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐ │
│  │   API 服务   │  │   调度器     │  │   WebSocket 服务      │ │
│  │  (FastAPI)   │  │ (Dispatcher) │  │  (实时状态/日志推送)  │ │
│  └──────────────┘  └──────────────┘  └──────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
                                   │
                                   │ HTTP/WS
                                   ▼
┌─────────────────────────────────────────────────────────────────┐
│                    连通性验证层 (Connectivity Layer)            │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐ │
│  │ SSH 探测器   │  │ 心跳监控     │  │   挂载点检查器        │ │
│  │ (asyncssh)   │  │ (Heartbeat)  │  │  (Mount Checker)      │ │
│  └──────────────┘  └──────────────┘  └──────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
                                   │
                                   │ 心跳/任务
                                   ▼
┌─────────────────────────────────────────────────────────────────┐
│                     执行面 (Execution Plane)                    │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐ │
│  │ Host Agent 1 │  │ Host Agent 2 │  │   Host Agent N        │ │
│  │              │  │              │  │                       │ │
│  │ ┌──────────┐ │  │ ┌──────────┐ │  │ ┌──────────────────┐ │ │
│  │ │ ADB 封装 │ │  │ │ ADB 封装 │ │  │ │   ADB 封装        │ │ │
│  │ └────┬─────┘ │  │ └────┬─────┘ │  │ └────┬─────────────┘ │ │
│  │      │       │  │      │       │  │      │               │ │
│  │ ┌────▼─────┐ │  │ ┌────▼─────┐ │  │ ┌────▼─────────────┐ │ │
│  │ │任务执行器│ │  │ │任务执行器│ │  │ │  任务执行器       │ │ │
│  │ └──────────┘ │  │ └──────────┘ │  │ └──────────────────┘ │ │
│  └──────────────┘  └──────────────┘  └──────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

**技术栈**：
- **后端**：Python FastAPI + asyncio + asyncssh/paramiko
- **前端**：React + Tailwind CSS + WebSocket
- **数据库**：PostgreSQL（元数据）
- **存储**：中心存储服务器 172.21.15.4（日志归档）
- **通讯**：WebSocket（实时推送）+ HTTP REST（API调用）

---

## 实施步骤

### Phase 0：连通性验证层（基础优先）

> **说明**：主机连通性是整个平台的基础，必须优先实现。

#### 0.1 SSH 连通性验证

```python
# backend/connectivity/ssh_verifier.py

import paramiko
from paramiko.ssh_exception import AuthenticationException, SSHException
import socket
import logging
import time
from typing import Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)

@dataclass
class SSHVerifyResult:
    success: bool
    host: str
    reason: Optional[str] = None
    latency_ms: Optional[float] = None

class SSHVerifier:
    """SSH 连通性验证器（同步方案 - 适合小规模诊断）"""

    def __init__(self, timeout: int = 5):
        self.timeout = timeout

    def verify(self, host: str, username: str,
               key_path: Optional[str] = None,
               password: Optional[str] = None) -> SSHVerifyResult:
        """
        验证 SSH 连接

        返回状态：
        - ok: True - 连接成功
        - ok: False, reason: "auth_failed" - 认证失败
        - ok: False, reason: "ssh_failed_or_timeout" - SSH 失败或超时
        - ok: False, reason: "exec_failed" - 命令执行失败
        """
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.RejectPolicy())

        try:
            t0 = time.time()

            client.connect(
                hostname=host,
                username=username,
                key_filename=key_path,
                password=password,
                timeout=self.timeout,
                banner_timeout=self.timeout,
                auth_timeout=self.timeout,
                look_for_keys=False,
                allow_agent=False,
            )

            # 执行轻量命令验证
            stdin, stdout, stderr = client.exec_command("echo ok", timeout=self.timeout)
            output = stdout.read().decode().strip()
            latency = (time.time() - t0) * 1000

            if output == "ok":
                return SSHVerifyResult(success=True, host=host, latency_ms=round(latency, 2))
            else:
                return SSHVerifyResult(success=False, host=host, reason="exec_failed")

        except AuthenticationException:
            return SSHVerifyResult(success=False, host=host, reason="auth_failed")
        except (SSHException, socket.timeout, socket.error) as e:
            logger.error(f"SSH verification failed for {host}: {e}")
            return SSHVerifyResult(success=False, host=host, reason="ssh_failed_or_timeout")
        finally:
            client.close()


# backend/connectivity/async_ssh_verifier.py

import asyncio
import asyncssh
import logging
import time
from typing import Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)

@dataclass
class AsyncSSHResult:
    success: bool
    host: str
    reason: Optional[str] = None
    latency_ms: Optional[float] = None

class AsyncSSHVerifier:
    """异步 SSH 验证器（适合批量探测）"""

    def __init__(self, timeout: int = 5):
        self.timeout = timeout

    async def verify(self, host: str, username: str,
                    key_path: Optional[str] = None,
                    password: Optional[str] = None) -> AsyncSSHResult:
        """异步验证 SSH 连接"""
        try:
            t0 = time.time()

            async with asyncssh.connect(
                host,
                username=username,
                client_keys=[key_path] if key_path else None,
                password=password,
                known_hosts="~/.ssh/known_hosts",
                connect_timeout=self.timeout,
                login_timeout=self.timeout,
            ) as conn:
                result = await conn.run("echo ok", check=True, timeout=self.timeout)
                latency = (time.time() - t0) * 1000

                return AsyncSSHResult(
                    success=result.stdout.strip() == "ok",
                    host=host,
                    latency_ms=round(latency, 2)
                )

        except asyncssh.PermissionDenied:
            return AsyncSSHResult(success=False, host=host, reason="auth_failed")
        except (asyncssh.Error, asyncio.TimeoutError) as e:
            logger.error(f"Async SSH verification failed for {host}: {e}")
            return AsyncSSHResult(success=False, host=host, reason="ssh_failed_or_timeout")

    async def verify_batch(self, hosts: list, username: str, **kwargs) -> list:
        """批量验证多个主机"""
        tasks = [self.verify(host, username, **kwargs) for host in hosts]
        return await asyncio.gather(*tasks, return_exceptions=True)
```

#### 0.2 网络主机发现

```python
# backend/connectivity/network_discovery.py

import asyncio
from ipaddress import ip_network
import logging
from typing import list

logger = logging.getLogger(__name__)

class NetworkDiscovery:
    """网络主机发现器"""

    def __init__(self, subnet: str = "172.21.15.0/24", port: int = 22):
        self.subnet = subnet
        self.port = port

    async def tcp_probe(self, host: str, timeout: float = 1.5) -> bool:
        """TCP 端口探测（无 ICMP 依赖）"""
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, self.port),
                timeout=timeout
            )
            writer.close()
            await writer.wait_closed()
            return True
        except Exception:
            return False

    async def discover_hosts(self, concurrent_limit: int = 200) -> list:
        """发现子网内存活的主机"""
        sem = asyncio.Semaphore(concurrent_limit)
        alive_hosts = []

        async def worker(ip):
            async with sem:
                if await self.tcp_probe(str(ip)):
                    alive_hosts.append(str(ip))
                    logger.info(f"Found alive host: {ip}")

        tasks = [asyncio.create_task(worker(ip)) for ip in ip_network(self.subnet).hosts()]
        await asyncio.gather(*tasks, return_exceptions=True)

        logger.info(f"Discovery complete: {len(alive_hosts)} hosts alive")
        return alive_hosts
```

#### 0.3 挂载点健康检查

```python
# backend/connectivity/mount_checker.py

import os
import time
import logging
from typing import Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)

@dataclass
class MountCheckResult:
    success: bool
    mount_path: str
    reason: Optional[str] = None
    latency_ms: Optional[float] = None

class MountChecker:
    """挂载点健康检查器（中心存储 172.21.15.4）"""

    def check(self, mount_path: str) -> MountCheckResult:
        """
        检查挂载点健康状态

        检查项：
        1. 挂载存在
        2. 可读可写
        3. 延迟
        """
        if not os.path.ismount(mount_path):
            return MountCheckResult(success=False, mount_path=mount_path, reason="not_mounted")

        test_file = os.path.join(mount_path, ".healthcheck")

        try:
            t0 = time.time()

            # 写入测试
            with open(test_file, "w", encoding="utf-8") as f:
                f.write("ok")
                f.flush()
                os.fsync(f.fileno())

            # 读取测试
            with open(test_file, "r", encoding="utf-8") as f:
                content = f.read()

            latency = (time.time() - t0) * 1000

            if content != "ok":
                return MountCheckResult(success=False, mount_path=mount_path, reason="read_mismatch")

            os.remove(test_file)

            return MountCheckResult(success=True, mount_path=mount_path, latency_ms=round(latency, 2))

        except OSError as e:
            logger.error(f"Mount check failed for {mount_path}: {e}")
            return MountCheckResult(success=False, mount_path=mount_path, reason="io_error")

    def check_central_storage(self) -> MountCheckResult:
        """检查中心存储服务器挂载（172.21.15.4）"""
        return self.check("/mnt/central-storage")
```

#### 0.4 统一错误处理

```python
# backend/connectivity/error_handler.py

from enum import Enum
import random
import time
import logging

logger = logging.getLogger(__name__)

class ErrorReason(Enum):
    """错误原因分类"""
    AUTH_FAILED = "auth_failed"
    TIMEOUT = "timeout"
    HOST_OFFLINE = "host_offline"
    SSH_FAILED = "ssh_failed"
    IO_ERROR = "io_error"
    UNKNOWN = "unknown"

def classify_error(exception: Exception) -> ErrorReason:
    """统一错误分类"""
    if "Auth" in str(type(exception)):
        return ErrorReason.AUTH_FAILED
    if "Timeout" in str(type(exception)):
        return ErrorReason.TIMEOUT
    if "Connection" in str(type(exception)):
        return ErrorReason.HOST_OFFLINE
    if "OSError" in str(type(exception)):
        return ErrorReason.IO_ERROR
    return ErrorReason.UNKNOWN

def retry_with_backoff(func, max_retry: int = 3, base_delay: float = 0.5):
    """
    指数退避重试

    避免同一时间产生请求风暴
    """
    for attempt in range(max_retry):
        try:
            return func()
        except Exception as exc:
            reason = classify_error(exc)

            # 认证失败不重试
            if reason == ErrorReason.AUTH_FAILED:
                logger.error(f"Auth failed, no retry: {exc}")
                raise

            # 指数退避 + 随机抖动
            delay = base_delay * (2 ** attempt) + random.uniform(0, 0.5)
            logger.warning(f"Attempt {attempt + 1} failed ({reason}), retry in {delay:.2f}s")

            if attempt < max_retry - 1:
                time.sleep(delay)
            else:
                logger.error(f"All {max_retry} attempts failed")
                raise

    return None
```

---

### Phase 1：基础设施与数据模型 (Week 1-2)

#### 1.1 数据库设计

```sql
-- 主机表
CREATE TABLE hosts (
    id SERIAL PRIMARY KEY,
    ip_address VARCHAR(45) NOT NULL UNIQUE,
    hostname VARCHAR(100),
    status VARCHAR(20) DEFAULT 'OFFLINE',  -- ONLINE, OFFLINE, MAINTENANCE
    cpu_load DECIMAL(5,2),
    ram_usage DECIMAL(5,2),
    max_concurrent_tasks INT DEFAULT 10,
    last_heartbeat TIMESTAMP,
    latency_ms DECIMAL(10,2),  -- 连接延迟
    mount_status VARCHAR(20),   -- 挂载状态
    created_at TIMESTAMP DEFAULT NOW()
);

-- 设备表
CREATE TABLE devices (
    id SERIAL PRIMARY KEY,
    serial_number VARCHAR(100) NOT NULL UNIQUE,
    model VARCHAR(100),
    android_version VARCHAR(20),
    host_id INT REFERENCES hosts(id),
    status VARCHAR(20) DEFAULT 'OFFLINE',  -- IDLE, RUNNING, OFFLINE, ERROR
    battery_level INT,
    temperature DECIMAL(5,2),
    current_run_id INT,
    created_at TIMESTAMP DEFAULT NOW()
);

-- 任务模板表（对应现有测试工具）
CREATE TABLE task_templates (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    type VARCHAR(50) NOT NULL,  -- MONKEY, MTBF, DDR, GPU, STANDBY
    script_path VARCHAR(255),
    default_config JSONB,
    description TEXT
);

-- 任务表
CREATE TABLE tasks (
    id SERIAL PRIMARY KEY,
    template_id INT REFERENCES task_templates(id),
    name VARCHAR(200),
    config JSONB NOT NULL,
    status VARCHAR(20) DEFAULT 'PENDING',
    priority INT DEFAULT 5,
    created_by VARCHAR(100),
    created_at TIMESTAMP DEFAULT NOW(),
    scheduled_at TIMESTAMP
);

-- 任务运行记录表
CREATE TABLE task_runs (
    id SERIAL PRIMARY KEY,
    task_id INT REFERENCES tasks(id),
    host_id INT REFERENCES hosts(id),
    device_id INT REFERENCES devices(id),
    status VARCHAR(20) DEFAULT 'PENDING',
    started_at TIMESTAMP,
    finished_at TIMESTAMP,
    exit_code INT,
    error_message TEXT
);

-- 日志附件表
CREATE TABLE log_artifacts (
    id SERIAL PRIMARY KEY,
    run_id INT REFERENCES task_runs(id),
    file_path VARCHAR(500),
    file_size BIGINT,
    log_type VARCHAR(50),
    uploaded_at TIMESTAMP DEFAULT NOW()
);
```

#### 1.2 目录结构创建

```
stability-test-platform/
├── backend/
│   ├── api/
│   │   └── routes/
│   │       ├── hosts.py
│   │       ├── devices.py
│   │       ├── tasks.py
│   │       ├── logs.py
│   │       └── heartbeat.py
│   ├── connectivity/          # 连通性验证模块
│   │   ├── ssh_verifier.py
│   │   ├── async_ssh_verifier.py
│   │   ├── network_discovery.py
│   │   ├── mount_checker.py
│   │   └── error_handler.py
│   ├── core/
│   │   ├── config.py
│   │   ├── database.py
│   │   └── security.py
│   ├── models/
│   │   └── schemas.py
│   ├── scheduler/
│   │   ├── dispatcher.py
│   │   └── queue_manager.py
│   ├── agent/
│   │   ├── main.py
│   │   ├── heartbeat.py       # 心跳发送器
│   │   ├── adb_wrapper.py
│   │   ├── task_executor.py
│   │   └── log_collector.py
│   └── main.py
├── frontend/
│   └── src/
│       ├── components/
│       │   ├── device/         # 设备相关
│       │   │   ├── DeviceCard.tsx
│       │   │   └── DeviceSelector.tsx
│       │   ├── task/           # 任务相关
│       │   │   ├── CreateTaskForm.tsx
│       │   │   └── TaskList.tsx
│       │   ├── log/            # 日志相关
│       │   │   └── LogViewer.tsx
│       │   └── network/        # 网络相关
│       │       ├── ConnectivityBadge.tsx
│       │       ├── HostCard.tsx
│       │       └── NetworkTopology.tsx
│       ├── pages/
│       ├── hooks/
│       └── utils/
└── docker-compose.yml
```

---

### Phase 2：Agent 开发 + 心跳机制 (Week 2-3)

#### 2.1 ADB 命令封装

```python
# backend/agent/adb_wrapper.py

import subprocess
import logging
from typing import Optional, List
from dataclasses import dataclass

logger = logging.getLogger(__name__)

@dataclass
class ADBResult:
    success: bool
    output: str
    error: Optional[str] = None
    exit_code: int = 0

class ADBWrapper:
    """ADB 命令封装器，支持超时、重试、错误分类"""

    def __init__(self, serial: str, default_timeout: int = 30):
        self.serial = serial
        self.default_timeout = default_timeout

    def _build_cmd(self, cmd: str) -> List[str]:
        """强制使用 -s 参数指定设备"""
        return ["adb", "-s", self.serial] + cmd.split()

    def exec_command(self, cmd: str, timeout: Optional[int] = None) -> ADBResult:
        """执行 ADB 命令，支持超时和重试"""
        timeout = timeout or self.default_timeout
        full_cmd = self._build_cmd(cmd)

        try:
            result = subprocess.run(
                full_cmd,
                capture_output=True,
                text=True,
                timeout=timeout
            )

            output = result.stdout + result.stderr

            # 检测设备离线
            if "offline" in output.lower():
                return ADBResult(success=False, output=output, error="DEVICE_OFFLINE")

            # 检测未授权
            if "unauthorized" in output.lower():
                return ADBResult(success=False, output=output, error="PERMISSION_DENIED")

            return ADBResult(success=result.returncode == 0, output=output, exit_code=result.returncode)

        except subprocess.TimeoutExpired:
            logger.error(f"ADB command timeout: {cmd}")
            return ADBResult(success=False, output="", error="ADB_TIMEOUT")
        except Exception as e:
            logger.error(f"ADB command error: {e}")
            return ADBResult(success=False, output="", error=str(e))

    def get_devices(self) -> List[str]:
        """获取所有已连接设备列表"""
        result = subprocess.run(["adb", "devices"], capture_output=True, text=True)
        devices = []
        for line in result.stdout.split('\n')[1:]:
            if '\tdevice' in line:
                devices.append(line.split('\t')[0])
        return devices

    def install_apk(self, apk_path: str) -> ADBResult:
        """安装 APK"""
        return self.exec_command(f"install -r -g -t {apk_path}")

    def push_file(self, local_path: str, remote_path: str) -> ADBResult:
        """推送文件到设备"""
        return self.exec_command(f"push {local_path} {remote_path}")

    def pull_logs(self, remote_path: str, local_path: str) -> ADBResult:
        """拉取日志"""
        return self.exec_command(f"pull {remote_path} {local_path}")

    def shell_command(self, cmd: str) -> ADBResult:
        """执行 shell 命令"""
        return self.exec_command(f"shell {cmd}")
```

#### 2.2 心跳发送器（整合版）

```python
# backend/agent/heartbeat.py

import asyncio
import aiohttp
import logging
import psutil
import time
from datetime import datetime

logger = logging.getLogger(__name__)

class HeartbeatSender:
    """心跳发送器（Agent 端）- 整合连通性状态"""

    def __init__(self, server_url: str, host_id: int, interval: int = 10):
        self.server_url = server_url
        self.host_id = host_id
        self.interval = interval
        self.session = None
        self.mount_checker = None  # 可选：挂载点检查器

    async def start(self):
        """启动心跳循环"""
        self.session = aiohttp.ClientSession()

        while True:
            try:
                await self._send_heartbeat()
                await asyncio.sleep(self.interval)
            except Exception as e:
                logger.error(f"Heartbeat error: {e}")
                await asyncio.sleep(5)  # 失败后快速重试

    async def _send_heartbeat(self):
        """发送心跳数据包（整合版）"""
        payload = {
            "host_id": self.host_id,
            "timestamp": datetime.now().isoformat(),
            "cpu_load": psutil.cpu_percent(),
            "ram_usage": psutil.virtual_memory().percent,
            "disk_usage": self._get_disk_usage(),
            "uptime": self._get_uptime(),
            "ip_address": self._get_local_ip(),
            # 新增：挂载状态
            "mount_status": self._check_mount_status() if self.mount_checker else None
        }

        async with self.session.post(
            f"{self.server_url}/api/v1/hosts/{self.host_id}/heartbeat",
            json=payload,
            timeout=aiohttp.ClientTimeout(total=5)
        ) as resp:
            if resp.status != 200:
                logger.error(f"Heartbeat failed: {await resp.text()}")

    def _get_disk_usage(self) -> dict:
        """获取磁盘使用率"""
        disk = psutil.disk_usage('/')
        return {"total": disk.total, "used": disk.used, "percent": disk.percent}

    def _get_uptime(self) -> float:
        """获取系统运行时间（秒）"""
        return time.time() - psutil.boot_time()

    def _get_local_ip(self) -> str:
        """获取本机 IP 地址（172.21.15.*）"""
        for iface, addrs in psutil.net_if_addrs().items():
            for addr in addrs:
                if addr.family == 2 and addr.address.startswith("172.21.15"):
                    return addr.address
        return "unknown"

    def _check_mount_status(self) -> str:
        """检查挂载点状态"""
        if not self.mount_checker:
            return "unknown"
        result = self.mount_checker.check_central_storage()
        return "ok" if result.success else result.reason
```

#### 2.3 任务执行器

```python
# backend/agent/task_executor.py

import os
import logging
from typing import Dict, Any
from enum import Enum
from .adb_wrapper import ADBWrapper

logger = logging.getLogger(__name__)

class TaskStatus(Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"

class TaskExecutor:
    """任务执行器，封装各类测试工具的启动逻辑"""

    TASK_TYPES = ["MONKEY", "MTBF", "DDR", "GPU", "STANDBY"]

    def __init__(self, run_id: int, device_serial: str, config: Dict[str, Any]):
        self.run_id = run_id
        self.device_serial = device_serial
        self.config = config
        self.adb = ADBWrapper(device_serial)
        self.status = TaskStatus.PENDING

    def execute(self) -> TaskStatus:
        """执行任务"""
        self.status = TaskStatus.RUNNING

        try:
            task_type = self.config.get("type")

            if task_type == "MONKEY":
                return self._execute_monkey()
            elif task_type == "MTBF":
                return self._execute_mtbf()
            elif task_type == "DDR":
                return self._execute_ddr()
            elif task_type == "GPU":
                return self._execute_gpu()
            elif task_type == "STANDBY":
                return self._execute_standby()
            else:
                raise ValueError(f"Unknown task type: {task_type}")

        except Exception as e:
            logger.error(f"Task execution failed: {e}")
            self.status = TaskStatus.FAILED
            return self.status

    def _execute_monkey(self) -> TaskStatus:
        """执行 Monkey 测试"""
        logger.info(f"Starting Monkey test for device {self.device_serial}")

        apk_path = self.config.get("apk_path")
        if apk_path:
            result = self.adb.install_apk(apk_path)
            if not result.success:
                logger.error(f"Failed to install APK: {result.error}")
                return TaskStatus.FAILED

        duration = self.config.get("duration_hours", 24)
        throttle = self.config.get("throttle", 500)
        cmd = f"sh /data/local/tmp/MonkeyTest.sh --running-minutes {duration * 60} --throttle {throttle}"

        result = self.adb.shell_command(cmd)
        self.status = TaskStatus.COMPLETED if result.success else TaskStatus.FAILED
        return self.status

    def _execute_mtbf(self) -> TaskStatus:
        """执行 MTBF 测试"""
        logger.info(f"Starting MTBF test for device {self.device_serial}")

        apk_path = self.config.get("apk_path")
        if apk_path and os.path.exists(apk_path):
            result = self.adb.install_apk(apk_path)
            if not result.success:
                return TaskStatus.FAILED

        pkg = self.config.get("test_package")
        test_class = self.config.get("test_class")
        cmd = f"am instrument -w -r -e debug false {pkg}/{test_class}"

        result = self.adb.shell_command(cmd)
        self.status = TaskStatus.COMPLETED if result.success else TaskStatus.FAILED
        return self.status

    def _execute_ddr(self) -> TaskStatus:
        """执行 DDR 测试"""
        logger.info(f"Starting DDR test for device {self.device_serial}")

        result = self.adb.shell_command("su -c 'id'")
        if "uid=0" not in result.output:
            logger.error("DDR test requires root permission")
            return TaskStatus.FAILED

        memtest_path = self.config.get("memtest_path")
        result = self.adb.push_file(memtest_path, "/data/local/tmp/memtest")
        if not result.success:
            return TaskStatus.FAILED

        self.adb.shell_command("chmod 755 /data/local/tmp/memtest")

        duration = self.config.get("duration_hours", 24)
        cmd = f"su -c '/data/local/tmp/memtest -t {duration * 3600}'"

        result = self.adb.shell_command(cmd)
        self.status = TaskStatus.COMPLETED if result.success else TaskStatus.FAILED
        return self.status
```

#### 2.4 Agent 主程序

```python
# backend/agent/main.py

import asyncio
import logging
import aiohttp
import os
from typing import Dict, Any
from .heartbeat import HeartbeatSender
from .task_executor import TaskExecutor

logger = logging.getLogger(__name__)

class HostAgent:
    """主机 Agent，负责与中心服务通信并执行任务"""

    def __init__(self, server_url: str, host_id: int):
        self.server_url = server_url
        self.host_id = host_id
        self.running_tasks: Dict[int, TaskExecutor] = {}
        self.session = None

    async def start(self):
        """启动 Agent"""
        self.session = aiohttp.ClientSession()

        # 启动心跳
        heartbeat = HeartbeatSender(self.server_url, self.host_id)
        asyncio.create_task(heartbeat.start())

        # 启动任务拉取
        asyncio.create_task(self._task_poll_loop())

    async def _task_poll_loop(self):
        """任务拉取循环"""
        while True:
            try:
                await self._poll_tasks()
                await asyncio.sleep(5)
            except Exception as e:
                logger.error(f"Task poll error: {e}")

    async def _poll_tasks(self):
        """拉取待执行任务"""
        async with self.session.get(
            f"{self.server_url}/api/v1/hosts/{self.host_id}/pending-tasks"
        ) as resp:
            if resp.status == 200:
                tasks = await resp.json()
                for task in tasks:
                    asyncio.create_task(self._execute_task(task))

    async def _execute_task(self, task: Dict[str, Any]):
        """执行任务"""
        run_id = task["id"]
        device_serial = task["device_serial"]
        config = task["config"]

        logger.info(f"Executing task {run_id} on device {device_serial}")

        await self._update_run_status(run_id, "RUNNING")

        executor = TaskExecutor(run_id, device_serial, config)
        self.running_tasks[run_id] = executor

        status = executor.execute()

        await self._collect_logs(run_id, device_serial)
        await self._update_run_status(run_id, status.value)

        del self.running_tasks[run_id]

    async def _collect_logs(self, run_id: int, device_serial: str):
        """收集并上传日志到中心存储"""
        # 实现日志收集逻辑...

    async def _update_run_status(self, run_id: int, status: str):
        """更新任务状态"""

    def _get_cpu_load(self) -> float:
        """获取 CPU 负载"""
        import psutil
        return psutil.cpu_percent()

    def _get_ram_usage(self) -> float:
        """获取内存使用率"""
        import psutil
        return psutil.virtual_memory().percent()

if __name__ == "__main__":
    agent = HostAgent(
        server_url=os.getenv("SERVER_URL", "http://localhost:8000"),
        host_id=int(os.getenv("HOST_ID"))
    )
    asyncio.run(agent.start())
```

---

### Phase 3：中心服务 API (Week 3-4)

#### 3.1 心跳 API

```python
# backend/api/routes/heartbeat.py

from fastapi import APIRouter, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime

router = APIRouter(prefix="/api/v1/hosts", tags=["heartbeat"])

# 心跳宽限期（秒）
HEARTBEAT_GRACE_PERIOD = 90

@router.post("/{host_id}/heartbeat")
async def receive_heartbeat(
    host_id: int,
    payload: HeartbeatPayload,
    db: Session = Depends(get_db)
):
    """接收 Agent 心跳（整合挂载状态）"""
    host = db.query(Host).filter(Host.id == host_id).first()
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    # 更新心跳时间戳
    host.last_heartbeat = datetime.now()
    host.cpu_load = payload.cpu_load
    host.ram_usage = payload.ram_usage
    host.disk_usage = payload.disk_usage["percent"]
    host.uptime = payload.uptime
    host.ip_address = payload.ip_address

    # 新增：更新挂载状态
    if payload.mount_status:
        host.mount_status = payload.mount_status

    # 更新状态为在线
    if host.status != "ONLINE":
        host.status = "ONLINE"
        logger.info(f"Host {host_id} is now ONLINE")

    db.commit()
    return {"status": "ok"}

def is_host_alive(host: Host) -> bool:
    """判断主机是否在线（基于心跳）"""
    if host.last_heartbeat is None:
        return False
    elapsed = (datetime.now() - host.last_heartbeat).total_seconds()
    return elapsed <= HEARTBEAT_GRACE_PERIOD
```

#### 3.2 任务 API

```python
# backend/api/routes/tasks.py

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])

@router.post("/")
async def create_task(
    task_request: TaskCreateRequest,
    db: Session = Depends(get_db)
):
    """创建新任务"""
    # 验证设备可用性
    device = db.query(Device).filter(
        Device.id == task_request.device_id,
        Device.status == "IDLE"
    ).first()

    if not device:
        raise HTTPException(status_code=400, detail="Device not available")

    # 验证主机在线
    host = db.query(Host).filter(Host.id == device.host_id).first()
    if not host or host.status != "ONLINE":
        raise HTTPException(status_code=400, detail="Host is not online")

    # 创建任务和运行记录...
    return {"task_id": task.id, "run_id": run.id}
```

---

### Phase 4：前端 UI 组件 (Week 4-5)

#### 4.1 主机卡片（整合连接状态）

```tsx
// frontend/src/components/network/HostCard.tsx

import React from 'react';
import { ConnectivityBadge } from './ConnectivityBadge';

interface HostCardProps {
  host: {
    id: number;
    ip_address: string;
    status: 'ONLINE' | 'OFFLINE' | 'MAINTENANCE';
    cpu_load: number;
    ram_usage: number;
    disk_usage: number;
    last_heartbeat: string;
    latency_ms?: number;
    mount_status?: string;  // 新增：挂载状态
  };
}

export const HostCard: React.FC<HostCardProps> = ({ host }) => {
  const statusMap = {
    'ONLINE': 'online',
    'OFFLINE': 'offline',
    'MAINTENANCE': 'warning'
  } as const;

  const connectivityStatus = statusMap[host.status];

  return (
    <div className="bg-gray-800 rounded-lg p-4 border border-gray-700">
      {/* 头部：IP + 状态 */}
      <div className="flex justify-between items-start mb-3">
        <div>
          <h3 className="text-lg font-semibold text-gray-200">
            {host.ip_address}
          </h3>
          <p className="text-xs text-gray-500">Host ID: {host.id}</p>
        </div>
        <ConnectivityBadge
          status={connectivityStatus}
          latency={host.latency_ms}
        />
      </div>

      {/* 资源使用率 */}
      <div className="space-y-2">
        {/* CPU */}
        <div>
          <div className="flex justify-between text-xs text-gray-400 mb-1">
            <span>CPU</span>
            <span>{host.cpu_load.toFixed(1)}%</span>
          </div>
          <div className="w-full bg-gray-700 rounded-full h-1.5">
            <div
              className={`h-1.5 rounded-full transition-all ${
                host.cpu_load > 80 ? 'bg-red-500' : host.cpu_load > 50 ? 'bg-yellow-500' : 'bg-green-500'
              }`}
              style={{ width: `${host.cpu_load}%` }}
            />
          </div>
        </div>

        {/* RAM */}
        <div>
          <div className="flex justify-between text-xs text-gray-400 mb-1">
            <span>RAM</span>
            <span>{host.ram_usage.toFixed(1)}%</span>
          </div>
          <div className="w-full bg-gray-700 rounded-full h-1.5">
            <div
              className={`h-1.5 rounded-full transition-all ${
                host.ram_usage > 80 ? 'bg-red-500' : host.ram_usage > 50 ? 'bg-yellow-500' : 'bg-blue-500'
              }`}
              style={{ width: `${host.ram_usage}%` }}
            />
          </div>
        </div>

        {/* Disk */}
        <div>
          <div className="flex justify-between text-xs text-gray-400 mb-1">
            <span>Disk</span>
            <span>{host.disk_usage.toFixed(1)}%</span>
          </div>
          <div className="w-full bg-gray-700 rounded-full h-1.5">
            <div
              className={`h-1.5 rounded-full transition-all ${
                host.disk_usage > 80 ? 'bg-red-500' : host.disk_usage > 50 ? 'bg-yellow-500' : 'bg-purple-500'
              }`}
              style={{ width: `${host.disk_usage}%` }}
            />
          </div>
        </div>
      </div>

      {/* 新增：挂载状态 */}
      {host.mount_status && (
        <div className="mt-2 flex items-center text-xs">
          <span className="text-gray-500">Mount:</span>
          <span className={`ml-1 ${
            host.mount_status === 'ok' ? 'text-green-400' : 'text-red-400'
          }`}>
            {host.mount_status}
          </span>
        </div>
      )}

      {/* 最后心跳时间 */}
      <div className="mt-3 pt-3 border-t border-gray-700">
        <p className="text-xs text-gray-500">
          Last heartbeat: {new Date(host.last_heartbeat).toLocaleString()}
        </p>
      </div>
    </div>
  );
};
```

#### 4.2 连接状态徽章（带延迟）

```tsx
// frontend/src/components/network/ConnectivityBadge.tsx

import React from 'react';

interface ConnectivityBadgeProps {
  status: 'online' | 'offline' | 'warning';
  latency?: number;
}

export const ConnectivityBadge: React.FC<ConnectivityBadgeProps> = ({
  status,
  latency
}) => {
  const colors = {
    online: 'bg-emerald-500',
    offline: 'bg-rose-500',
    warning: 'bg-amber-500'
  };

  const statusText = {
    online: latency ? `${latency}ms` : 'ONLINE',
    offline: 'NO SIGNAL',
    warning: latency ? `${latency}ms` : 'POOR'
  };

  return (
    <div className="flex items-center gap-2">
      <div className="relative flex h-3 w-3">
        {status === 'online' && (
          <span className={`
            animate-ping absolute inline-flex h-full w-full rounded-full opacity-75
            ${colors[status]}
          `}></span>
        )}
        <span className={`
          relative inline-flex rounded-full h-3 w-3
          ${colors[status]}
        `}></span>
      </div>
      <span className="text-xs font-mono text-gray-300">
        {statusText[status]}
      </span>
    </div>
  );
};
```

#### 4.3 网络拓扑图（中心辐射）

```tsx
// frontend/src/components/network/NetworkTopology.tsx

import React from 'react';

interface NetworkTopologyProps {
  centralServer: string;  // 172.21.15.4
  hosts: Array<{
    id: number;
    ip_address: string;
    status: 'ONLINE' | 'OFFLINE';
  }>;
}

export const NetworkTopology: React.FC<NetworkTopologyProps> = ({
  centralServer,
  hosts
}) => {
  return (
    <div className="bg-gray-900 rounded-lg p-6 min-h-[400px] flex items-center justify-center">
      <div className="relative w-full h-full">
        {/* 中心服务器 */}
        <div className="absolute top-1/2 left-1/2 transform -translate-x-1/2 -translate-y-1/2">
          <div className="flex flex-col items-center">
            <div className="w-16 h-16 bg-blue-600 rounded-full flex items-center justify-center shadow-lg">
              <svg className="w-8 h-8 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 12h14M5 12a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v4a2 2 0 01-2 2M5 12a2 2 0 00-2 2v4a2 2 0 002 2h14a2 2 0 002-2v-4a2 2 0 00-2-2m-2-4h.01M17 16h.01" />
              </svg>
            </div>
            <p className="text-xs text-gray-400 mt-2">{centralServer}</p>
            <p className="text-xs text-gray-500">Central Storage</p>
          </div>
        </div>

        {/* 卫星主机 */}
        {hosts.map((host, index) => {
          const angle = (index / hosts.length) * 2 * Math.PI;
          const radius = 150;
          const x = 50 + radius * Math.cos(angle);
          const y = 50 + radius * Math.sin(angle);

          return (
            <React.Fragment key={host.id}>
              {/* 连接线 */}
              <svg className="absolute top-0 left-0 w-full h-full pointer-events-none">
                <line
                  x1="50%"
                  y1="50%"
                  x2={`${x}%`}
                  y2={`${y}%`}
                  stroke={host.status === 'ONLINE' ? '#10b981' : '#ef4444'}
                  strokeWidth="2"
                  strokeDasharray={host.status === 'ONLINE' ? '0' : '5,5'}
                  className="opacity-50"
                />
              </svg>

              {/* 主机节点 */}
              <div
                className="absolute transform -translate-x-1/2 -translate-y-1/2"
                style={{ left: `${x}%`, top: `${y}%` }}
              >
                <div className="flex flex-col items-center">
                  <div className={`
                    w-10 h-10 rounded-full flex items-center justify-center shadow-md
                    ${host.status === 'ONLINE' ? 'bg-emerald-600' : 'bg-rose-600'}
                  `}>
                    <svg className="w-5 h-5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
                    </svg>
                  </div>
                  <p className="text-xs text-gray-400 mt-1">{host.ip_address}</p>
                </div>
              </div>
            </React.Fragment>
          );
        })}
      </div>
    </div>
  );
};
```

#### 4.4 设备卡片（测试状态）

```tsx
// frontend/src/components/device/DeviceCard.tsx

import React from 'react';

interface DeviceCardProps {
  device: {
    id: number;
    serial_number: string;
    model: string;
    android_version: string;
    host_id: number;
    status: 'IDLE' | 'RUNNING' | 'OFFLINE' | 'ERROR';
    battery_level: number | null;
    temperature: number | null;
    current_task?: string;
  };
  onClick: (device: DeviceCardProps['device']) => void;
}

export const DeviceCard: React.FC<DeviceCardProps> = ({ device, onClick }) => {
  return (
    <div
      onClick={() => onClick(device)}
      className={`bg-gray-800 rounded-lg p-4 cursor-pointer border-2 hover:bg-gray-700 transition-colors
        ${device.status === 'RUNNING' ? 'border-green-500 animate-pulse' :
          device.status === 'ERROR' ? 'border-red-500' : 'border-gray-600'}`}
    >
      <div className="flex justify-between items-start mb-2">
        <h3 className="text-lg font-semibold text-gray-200">{device.model}</h3>
        <span className={`text-xs px-2 py-1 rounded ${
          device.status === 'RUNNING' ? 'bg-green-500/20 text-green-400' :
          device.status === 'ERROR' ? 'bg-red-500/20 text-red-400' :
          device.status === 'IDLE' ? 'bg-gray-500/20 text-gray-400' : 'bg-gray-700 text-gray-500'
        }`}>
          {device.status}
        </span>
      </div>

      <div className="space-y-1 text-sm text-gray-400">
        <p>SN: {device.serial_number}</p>
        <p>Android: {device.android_version}</p>
      </div>

      {device.battery_level !== null && (
        <div className="mt-3">
          <div className="flex justify-between text-xs text-gray-500 mb-1">
            <span>Battery</span>
            <span>{device.battery_level}%</span>
          </div>
          <div className="w-full bg-gray-700 rounded-full h-2">
            <div
              className={`h-2 rounded-full transition-all ${
                device.battery_level > 50 ? 'bg-green-500' :
                device.battery_level > 20 ? 'bg-yellow-500' : 'bg-red-500'
              }`}
              style={{ width: `${device.battery_level}%` }}
            />
          </div>
        </div>
      )}

      {device.current_task && (
        <div className="mt-3 pt-3 border-t border-gray-700">
          <p className="text-xs text-gray-500">Current Task</p>
          <p className="text-sm text-blue-400 truncate">{device.current_task}</p>
        </div>
      )}
    </div>
  );
};
```

#### 4.5 任务创建表单

```tsx
// frontend/src/components/task/CreateTaskForm.tsx

import React, { useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';

export const CreateTaskForm: React.FC = () => {
  const [selectedType, setSelectedType] = useState('');
  const [selectedDevices, setSelectedDevices] = useState<number[]>([]);
  const [config, setConfig] = useState<any>({});

  const { data: templates } = useQuery({
    queryKey: ['task-templates'],
    queryFn: () => fetch('/api/v1/task-templates').then(r => r.json())
  });

  const createTask = useMutation({
    mutationFn: (data: any) =>
      fetch('/api/v1/tasks', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data)
      }).then(r => r.json())
  });

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    await createTask.mutateAsync({
      template_id: templates?.find((t: any) => t.type === selectedType)?.id,
      device_ids: selectedDevices,
      config
    });
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-6">
      {/* 测试类型选择 */}
      <div>
        <label className="block text-sm font-medium text-gray-300 mb-2">
          Test Type
        </label>
        <div className="grid grid-cols-2 gap-3">
          {templates?.map((template: any) => (
            <button
              key={template.id}
              type="button"
              onClick={() => setSelectedType(template.type)}
              className={`p-4 rounded-lg border-2 text-left transition-colors ${
                selectedType === template.type
                  ? 'border-blue-500 bg-blue-500/10'
                  : 'border-gray-700 bg-gray-800 hover:border-gray-600'
              }`}
            >
              <h3 className="font-semibold text-gray-200">{template.name}</h3>
              <p className="text-sm text-gray-400 mt-1">{template.description}</p>
            </button>
          ))}
        </div>
      </div>

      {/* 设备选择 */}
      <DeviceSelector
        selected={selectedDevices}
        onChange={setSelectedDevices}
      />

      <button
        type="submit"
        disabled={!selectedType || selectedDevices.length === 0}
        className="w-full py-3 px-4 bg-blue-600 hover:bg-blue-700 disabled:bg-gray-700 disabled:cursor-not-allowed text-white font-medium rounded-lg transition-colors"
      >
        Create Task
      </button>
    </form>
  );
};
```

#### 4.6 日志查看器

```tsx
// frontend/src/components/log/LogViewer.tsx

import React, { useEffect, useRef, useState } from 'react';
import { useWebSocket } from '../hooks/useWebSocket';

interface LogViewerProps {
  runId: number;
  deviceSerial: string;
}

export const LogViewer: React.FC<LogViewerProps> = ({ runId, deviceSerial }) => {
  const [logs, setLogs] = useState<any[]>([]);
  const [autoScroll, setAutoScroll] = useState(true);
  const [filter, setFilter] = useState('');
  const containerRef = useRef<HTMLDivElement>(null);

  const { socket, isConnected } = useWebSocket(`/ws/logs/${runId}`);

  useEffect(() => {
    if (!socket) return;

    socket.onmessage = (event) => {
      const logLine = JSON.parse(event.data);
      setLogs(prev => [...prev, logLine]);

      if (autoScroll && containerRef.current) {
        containerRef.current.scrollTop = containerRef.current.scrollHeight;
      }
    };

    return () => socket.close();
  }, [socket, autoScroll]);

  const highlightKeywords = (text: string) => {
    const keywords = {
      'FATAL': 'text-red-500 font-bold',
      'ANR': 'text-orange-500 underline',
      'CRASH': 'text-red-400 font-bold',
      'TestRunner': 'text-blue-400'
    };

    let result = text;
    Object.entries(keywords).forEach(([keyword, className]) => {
      const regex = new RegExp(`(${keyword})`, 'g');
      result = result.replace(regex, `<span class="${className}">$1</span>`);
    });

    return result;
  };

  const filteredLogs = logs.filter(log =>
    !filter || log.content.toLowerCase().includes(filter.toLowerCase())
  );

  return (
    <div className="bg-gray-900 rounded-lg overflow-hidden">
      {/* Toolbar */}
      <div className="bg-gray-800 px-4 py-2 flex items-center gap-3">
        <div className="flex items-center gap-2">
          <span className={`w-2 h-2 rounded-full ${isConnected ? 'bg-green-500' : 'bg-red-500'}`} />
          <span className="text-sm text-gray-400">{isConnected ? 'Connected' : 'Disconnected'}</span>
        </div>

        <input
          type="text"
          placeholder="Filter logs..."
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          className="flex-1 px-3 py-1 bg-gray-700 text-gray-200 text-sm rounded border border-gray-600 focus:border-blue-500 focus:outline-none"
        />

        <button
          onClick={() => setAutoScroll(!autoScroll)}
          className={`px-3 py-1 text-sm rounded transition-colors ${
            autoScroll ? 'bg-blue-600 text-white' : 'bg-gray-700 text-gray-300'
          }`}
        >
          Auto-scroll
        </button>

        <a
          href={`/api/v1/runs/${runId}/logs/download`}
          className="px-3 py-1 text-sm bg-gray-700 text-gray-300 rounded hover:bg-gray-600 transition-colors"
        >
          Download
        </a>
      </div>

      {/* Log content */}
      <div
        ref={containerRef}
        className="h-[600px] overflow-y-auto p-4 font-mono text-sm"
        role="log"
        aria-live="polite"
      >
        {filteredLogs.map((log, index) => (
          <div key={index} className="flex hover:bg-gray-800/50 -mx-4 px-4">
            <span className="text-gray-500 select-none mr-3">{log.timestamp}</span>
            <span
              className="text-gray-300"
              dangerouslySetInnerHTML={{ __html: highlightKeywords(log.content) }}
            />
          </div>
        ))}
      </div>
    </div>
  );
};
```

---

### Phase 5：任务封装与测试 (Week 5-6)

#### 5.1 任务模板映射

```python
# backend/core/task_templates.py

TASK_TEMPLATES = {
    "MONKEY": {
        "name": "Monkey Stress Test",
        "description": "Random UI operations & AI traversal",
        "script_path": "Monkey_test/AIMonkeyTest.py",
        "default_config": {
            "mode": "ai_monkey",
            "duration_hours": 24,
            "throttle": 500,
            "whitelist": [],
            "blacklist": []
        }
    },
    "MTBF": {
        "name": "MTBF Stability Test",
        "description": "700-item comprehensive functionality test",
        "script_path": "MTBF_test/Start.bat",
        "default_config": {
            "task_file": "版测700_All.xml",
            "loop_count": 1
        }
    },
    "DDR": {
        "name": "DDR Memory Test",
        "description": "Memory stress test & DFS test",
        "script_path": "DDR_test/push---8.27.bat",
        "default_config": {
            "duration_hours": 48,
            "require_root": True
        }
    },
    "GPU": {
        "name": "GPU Stress Test",
        "description": "Antutu GPU performance test",
        "script_path": "GPU_stress_test/",
        "default_config": {
            "loop_count": 500,
            "duration_hours": 40
        }
    },
    "STANDBY": {
        "name": "Standby Stability Test",
        "description": "7-day YouTube standby test",
        "script_path": "Standby_test/run_7day_youtube_standby.bat",
        "default_config": {
            "duration_hours": 168
        }
    }
}
```

#### 5.2 测试策略

**单元测试**：
- SSH 连通性验证（mock paramiko）
- ADB 命令封装（mock subprocess）
- 任务状态机转换

**集成测试**：
- Agent ↔ 中心 API 通信
- 设备断连模拟
- 日志上传流程
- 挂载点健康检查

**故障注入测试**：
- ADB 超时
- Host 心跳中断
- SSH 连接失败
- 挂载点 IO 错误

---

## 关键文件清单

### 连通性验证模块
| 文件 | 操作 | 说明 |
|------|------|------|
| `backend/connectivity/ssh_verifier.py` | 新建 | SSH 连通性验证（paramiko） |
| `backend/connectivity/async_ssh_verifier.py` | 新建 | 异步 SSH 验证（asyncssh） |
| `backend/connectivity/network_discovery.py` | 新建 | 子网主机发现 |
| `backend/connectivity/mount_checker.py` | 新建 | 挂载点健康检查 |
| `backend/connectivity/error_handler.py` | 新建 | 统一错误处理 |

### Agent 模块
| 文件 | 操作 | 说明 |
|------|------|------|
| `backend/agent/main.py` | 新建 | Agent 主程序 |
| `backend/agent/heartbeat.py` | 新建 | 心跳发送器（整合挂载状态） |
| `backend/agent/adb_wrapper.py` | 新建 | ADB 命令封装 |
| `backend/agent/task_executor.py` | 新建 | 任务执行器 |

### API 路由
| 文件 | 操作 | 说明 |
|------|------|------|
| `backend/api/routes/heartbeat.py` | 新建 | 心跳 API |
| `backend/api/routes/tasks.py` | 新建 | 任务 API |
| `backend/api/routes/hosts.py` | 新建 | 主机管理 API |

### 前端组件
| 文件 | 操作 | 说明 |
|------|------|------|
| `frontend/src/components/network/ConnectivityBadge.tsx` | 新建 | 连接状态徽章 |
| `frontend/src/components/network/HostCard.tsx` | 新建 | 主机卡片（整合状态） |
| `frontend/src/components/network/NetworkTopology.tsx` | 新建 | 网络拓扑图 |
| `frontend/src/components/device/DeviceCard.tsx` | 新建 | 设备卡片 |
| `frontend/src/components/task/CreateTaskForm.tsx` | 新建 | 任务创建表单 |
| `frontend/src/components/log/LogViewer.tsx` | 新建 | 日志查看器 |

---

## 风险与缓解

| 风险 | 缓解措施 |
|------|----------|
| SSH 认证凭据泄露 | 使用 SSH 密钥而非密码，密钥文件权限 600 |
| 大规模扫描导致网络拥塞 | 限制并发数（200），添加超时和抖动 |
| 中心存储服务器单点故障 | 监控挂载点健康，设置告警阈值 |
| 心跳丢失误报 | 设置宽限期（90秒），连续失败才标记离线 |
| NFS stale file handle | 定期健康检查，检测到异常自动重新挂载 |
| ADB 不稳定导致设备频繁断连 | Agent 本地重连机制、离线检测、自动重试与熔断 |
| 现有工具启动方式不统一 | 为每类工具提供 Linux 兼容脚本与统一入口 |
| 日志体量巨大 | 热日志索引、冷日志归档、可配置留存周期 |

---

## SESSION_ID（供 /ccg:execute 使用）

### 来自 stability-test-platform.md
- **CODEX_SESSION**: `019bdf4e-5455-7cd2-8ea0-f1d8464b1bc1`
- **GEMINI_SESSION**: `ced7640d-abcc-4986-942b-0ade8649d61a`

### 来自 host-connectivity-verification.md
- **CODEX_SESSION**: `019bdfbf-6d1f-7852-a034-c43b92f5666c`
- **GEMINI_SESSION**: `e946a886-8922-432b-8509-62b956171b52`

---

## 原始参考文档

1. **stability-test-platform.md** - 测试执行与任务调度
2. **host-connectivity-verification.md** - 主机连通性验证

---

*本整合计划由多模型协作生成，综合了两次规划的全部内容，提供统一的实施方案。*
