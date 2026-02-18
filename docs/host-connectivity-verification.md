# 主机通信连通性验证实施计划

**生成时间**：2026-01-21
**计划版本**：v1.0

---

## 任务类型

- [x] 后端 (→ Codex)
- [x] 前端 (→ Gemini)
- [x] 全栈 (→ 并行)

---

## 环境背景

- **网络配置**：所有 Linux 主机在 172.21.15.* 网段
- **中心存储服务器**：172.21.15.4（8GB RAM，12TB 存储空间）
- **其他 Linux 主机**：多台（每台 8GB RAM，256GB 存储）
- **挂载关系**：所有 Linux 主机挂载到 172.21.15.4
- **SSH 服务**：所有 Linux 主机已安装 SSH
- **访问方式**：Windows 设备通过 Xshell/Xftp 访问

---

## 技术方案

### 架构决策：分层验证 + Agent 推送

采用 **"分层验证 + Agent 推送心跳"** 方案：

- **批量发现层**：TCP 22 端口探测 + ARP/ICMP 加速
- **连通性验证层**：SSH 连接测试（paramiko/asyncssh）
- **常态监控层**：Agent 推送心跳 + 关键指标
- **存储层验证**：挂载点健康检查 + 日志传输 ACK

**技术栈**：
- **后端**：Python（paramiko/asyncssh）+ asyncio
- **前端**：React + Tailwind CSS
- **通讯**：WebSocket（实时状态推送）

---

## 实施步骤

### Phase 1：SSH 连通性验证（核心基础）

#### 1.1 同步方案（paramiko）- 适合小规模诊断

```python
# backend/connectivity/ssh_verifier.py

import paramiko
from paramiko.ssh_exception import AuthenticationException, SSHException
import socket
import logging
from typing import Dict, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)

@dataclass
class SSHVerifyResult:
    success: bool
    host: str
    reason: Optional[str] = None
    latency_ms: Optional[float] = None

class SSHVerifier:
    """SSH 连通性验证器"""

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
            import time
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
            stdin, stdout, stderr = client.exec_command(
                "echo ok",
                timeout=self.timeout
            )

            output = stdout.read().decode().strip()
            latency = (time.time() - t0) * 1000

            if output == "ok":
                return SSHVerifyResult(
                    success=True,
                    host=host,
                    latency_ms=round(latency, 2)
                )
            else:
                return SSHVerifyResult(
                    success=False,
                    host=host,
                    reason="exec_failed"
                )

        except AuthenticationException:
            return SSHVerifyResult(
                success=False,
                host=host,
                reason="auth_failed"
            )

        except (SSHException, socket.timeout, socket.error) as e:
            logger.error(f"SSH verification failed for {host}: {e}")
            return SSHVerifyResult(
                success=False,
                host=host,
                reason="ssh_failed_or_timeout"
            )

        finally:
            client.close()
```

#### 1.2 异步方案（asyncssh）- 适合批量探测

```python
# backend/connectivity/async_ssh_verifier.py

import asyncio
import asyncssh
import logging
from typing import Dict, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)

@dataclass
class AsyncSSHResult:
    success: bool
    host: str
    reason: Optional[str] = None
    latency_ms: Optional[float] = None

class AsyncSSHVerifier:
    """异步 SSH 验证器，支持高并发探测"""

    def __init__(self, timeout: int = 5):
        self.timeout = timeout

    async def verify(self, host: str, username: str,
                    key_path: Optional[str] = None,
                    password: Optional[str] = None) -> AsyncSSHResult:
        """异步验证 SSH 连接"""
        try:
            import time
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
                result = await conn.run(
                    "echo ok",
                    check=True,
                    timeout=self.timeout
                )

                latency = (time.time() - t0) * 1000

                return AsyncSSHResult(
                    success=result.stdout.strip() == "ok",
                    host=host,
                    latency_ms=round(latency, 2)
                )

        except asyncssh.PermissionDenied:
            return AsyncSSHResult(
                success=False,
                host=host,
                reason="auth_failed"
            )

        except (asyncssh.Error, asyncio.TimeoutError) as e:
            logger.error(f"Async SSH verification failed for {host}: {e}")
            return AsyncSSHResult(
                success=False,
                host=host,
                reason="ssh_failed_or_timeout"
            )

    async def verify_batch(self, hosts: list[str], username: str,
                          **kwargs) -> list[AsyncSSHResult]:
        """批量验证多个主机"""
        tasks = [
            self.verify(host, username, **kwargs)
            for host in hosts
        ]
        return await asyncio.gather(*tasks, return_exceptions=True)
```

---

### Phase 2：子网主机发现

#### 2.1 TCP 端口探测（无 ICMP 依赖）

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
        """
        TCP 端口探测

        尝试建立 TCP 连接，如果成功则认为主机在线
        """
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

    async def discover_hosts(self, concurrent_limit: int = 200) -> list[str]:
        """
        发现子网内存活的主机

        控制并发数，避免压测网络
        """
        sem = asyncio.Semaphore(concurrent_limit)
        alive_hosts = []

        async def worker(ip: str):
            async with sem:
                if await self.tcp_probe(str(ip)):
                    alive_hosts.append(str(ip))
                    logger.info(f"Found alive host: {ip}")

        tasks = [
            asyncio.create_task(worker(ip))
            for ip in ip_network(self.subnet).hosts()
        ]

        await asyncio.gather(*tasks, return_exceptions=True)

        logger.info(f"Discovery complete: {len(alive_hosts)} hosts alive")
        return alive_hosts
```

---

### Phase 3：心跳机制设计

#### 3.1 Agent 推送心跳

```python
# backend/agent/heartbeat.py

import asyncio
import aiohttp
import logging
import psutil
from datetime import datetime
from typing import Dict

logger = logging.getLogger(__name__)

class HeartbeatSender:
    """心跳发送器（Agent 端）"""

    def __init__(self, server_url: str, host_id: int, interval: int = 10):
        self.server_url = server_url
        self.host_id = host_id
        self.interval = interval
        self.session = None

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
        """发送心跳数据包"""
        payload = {
            "host_id": self.host_id,
            "timestamp": datetime.now().isoformat(),
            "cpu_load": psutil.cpu_percent(),
            "ram_usage": psutil.virtual_memory().percent,
            "disk_usage": self._get_disk_usage(),
            "uptime": self._get_uptime(),
            "ip_address": self._get_local_ip()
        }

        async with self.session.post(
            f"{self.server_url}/api/v1/hosts/{self.host_id}/heartbeat",
            json=payload,
            timeout=aiohttp.ClientTimeout(total=5)
        ) as resp:
            if resp.status != 200:
                logger.error(f"Heartbeat failed: {await resp.text()}")

    def _get_disk_usage(self) -> Dict:
        """获取磁盘使用率"""
        disk = psutil.disk_usage('/')
        return {
            "total": disk.total,
            "used": disk.used,
            "percent": disk.percent
        }

    def _get_uptime(self) -> float:
        """获取系统运行时间（秒）"""
        return time.time() - psutil.boot_time()

    def _get_local_ip(self) -> str:
        """获取本机 IP 地址"""
        # 获取 172.21.15.* 网段的 IP
        for iface, addrs in psutil.net_if_addrs().items():
            for addr in addrs:
                if addr.family == 2 and addr.address.startswith("172.21.15"):
                    return addr.address
        return "unknown"
```

#### 3.2 服务端心跳处理

```python
# backend/api/routes/heartbeat.py

from fastapi import APIRouter, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime, timedelta

router = APIRouter(prefix="/api/v1/hosts", tags=["heartbeat"])

# 心跳宽限期（秒）
HEARTBEAT_GRACE_PERIOD = 90

@router.post("/{host_id}/heartbeat")
async def receive_heartbeat(
    host_id: int,
    payload: HeartbeatPayload,
    db: Session = Depends(get_db)
):
    """
    接收 Agent 心跳

    更新主机的 last_seen 时间戳和状态指标
    """
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

    # 更新状态为在线
    if host.status != "ONLINE":
        host.status = "ONLINE"
        logger.info(f"Host {host_id} is now ONLINE")

    db.commit()
    return {"status": "ok"}

def is_host_alive(host: Host) -> bool:
    """
    判断主机是否在线

    基于 last_heartbeat 时间戳判断
    """
    if host.last_heartbeat is None:
        return False

    elapsed = (datetime.now() - host.last_heartbeat).total_seconds()
    return elapsed <= HEARTBEAT_GRACE_PERIOD
```

---

### Phase 4：挂载点健康检查

#### 4.1 NFS/挂载点验证

```python
# backend/connectivity/mount_checker.py

import os
import time
import logging
from typing import Dict
from dataclasses import dataclass

logger = logging.getLogger(__name__)

@dataclass
class MountCheckResult:
    success: bool
    mount_path: str
    reason: Optional[str] = None
    latency_ms: Optional[float] = None

class MountChecker:
    """挂载点健康检查器"""

    def check(self, mount_path: str) -> MountCheckResult:
        """
        检查挂载点健康状态

        检查项：
        1. 挂载存在
        2. 可读可写
        3. 延迟
        """
        if not os.path.ismount(mount_path):
            return MountCheckResult(
                success=False,
                mount_path=mount_path,
                reason="not_mounted"
            )

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
                return MountCheckResult(
                    success=False,
                    mount_path=mount_path,
                    reason="read_mismatch"
                )

            # 清理测试文件
            os.remove(test_file)

            return MountCheckResult(
                success=True,
                mount_path=mount_path,
                latency_ms=round(latency, 2)
            )

        except OSError as e:
            logger.error(f"Mount check failed for {mount_path}: {e}")
            return MountCheckResult(
                success=False,
                mount_path=mount_path,
                reason="io_error"
            )

    def check_central_storage(self) -> MountCheckResult:
        """检查中心存储服务器挂载（172.21.15.4）"""
        # 假设挂载到 /mnt/central-storage
        return self.check("/mnt/central-storage")
```

---

### Phase 5：前端 UI 组件

#### 5.1 连接状态徽章

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

#### 5.2 主机卡片（含连接状态）

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
    latency?: number;
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
          latency={host.latency}
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
              className={`
                h-1.5 rounded-full transition-all
                ${host.cpu_load > 80 ? 'bg-red-500' : host.cpu_load > 50 ? 'bg-yellow-500' : 'bg-green-500'}
              `}
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
              className={`
                h-1.5 rounded-full transition-all
                ${host.ram_usage > 80 ? 'bg-red-500' : host.ram_usage > 50 ? 'bg-yellow-500' : 'bg-blue-500'}
              `}
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
              className={`
                h-1.5 rounded-full transition-all
                ${host.disk_usage > 80 ? 'bg-red-500' : host.disk_usage > 50 ? 'bg-yellow-500' : 'bg-purple-500'}
              `}
              style={{ width: `${host.disk_usage}%` }}
            />
          </div>
        </div>
      </div>

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

#### 5.3 网络拓扑图（简化版）

```tsx
// frontend/src/components/network/NetworkTopology.tsx

import React from 'react';

interface NetworkTopologyProps {
  centralServer: string;
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

---

### Phase 6：错误处理与重试

#### 6.1 统一错误分类

```python
# backend/connectivity/error_handler.py

from enum import Enum
from typing import Optional
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

### Phase 7：集成测试

#### 7.1 连通性测试脚本

```python
# tests/test_connectivity.py

import asyncio
import pytest
from backend.connectivity.ssh_verifier import SSHVerifier
from backend.connectivity.async_ssh_verifier import AsyncSSHVerifier
from backend.connectivity.network_discovery import NetworkDiscovery
from backend.connectivity.mount_checker import MountChecker

class TestConnectivity:

    def test_ssh_verification_success(self):
        """测试 SSH 连接成功场景"""
        verifier = SSHVerifier(timeout=10)
        result = verifier.verify(
            host="172.21.15.4",
            username="testuser",
            key_path="/path/to/key"
        )
        assert result.success is True
        assert result.latency_ms is not None

    def test_ssh_verification_auth_failed(self):
        """测试 SSH 认证失败"""
        verifier = SSHVerifier(timeout=5)
        result = verifier.verify(
            host="172.21.15.4",
            username="wronguser",
            password="wrongpass"
        )
        assert result.success is False
        assert result.reason == "auth_failed"

    @pytest.mark.asyncio
    async def test_network_discovery(self):
        """测试子网主机发现"""
        discovery = NetworkDiscovery(subnet="172.21.15.0/24")
        hosts = await discovery.discover_hosts(concurrent_limit=50)
        assert len(hosts) > 0
        assert "172.21.15.4" in hosts

    def test_mount_check(self):
        """测试挂载点检查"""
        checker = MountChecker()
        result = checker.check("/mnt/central-storage")
        # 根据实际环境调整
        assert result.success is True or result.reason == "not_mounted"
```

---

## 关键文件

| 文件 | 操作 | 说明 |
|------|------|------|
| `backend/connectivity/ssh_verifier.py` | 新建 | SSH 连通性验证（paramiko） |
| `backend/connectivity/async_ssh_verifier.py` | 新建 | 异步 SSH 验证（asyncssh） |
| `backend/connectivity/network_discovery.py` | 新建 | 子网主机发现 |
| `backend/agent/heartbeat.py` | 新建 | Agent 心跳发送 |
| `backend/api/routes/heartbeat.py` | 新建 | 心跳 API 路由 |
| `backend/connectivity/mount_checker.py` | 新建 | 挂载点健康检查 |
| `backend/connectivity/error_handler.py` | 新建 | 统一错误处理 |
| `frontend/src/components/network/ConnectivityBadge.tsx` | 新建 | 连接状态徽章 |
| `frontend/src/components/network/HostCard.tsx` | 新建 | 主机卡片组件 |
| `frontend/src/components/network/NetworkTopology.tsx` | 新建 | 网络拓扑图 |

---

## 风险与缓解

| 风险 | 缓解措施 |
|------|----------|
| SSH 认证凭据泄露 | 使用 SSH 密钥而非密码，密钥文件权限 600 |
| 大规模扫描导致网络拥塞 | 限制并发数（200），添加超时和抖动 |
| 中心存储服务器单点故障 | 监控挂载点健康，设置告警阈值 |
| 心跳丢失误报 | 设置宽限期（90秒），连续失败才标记离线 |
| NFS stale file handle | 定期健康检查，检测到异常自动重新挂载 |

---

## SESSION_ID（供 /ccg:execute 使用）

- **CODEX_SESSION**: `019bdfbf-6d1f-7852-a034-c43b92f5666c`
- **GEMINI_SESSION**: `e946a886-8922-432b-8509-62b956171b52`

---

*本计划由多模型协作生成，综合了 Codex 的后端技术分析和 Gemini 的前端 UI/UX 设计。*
