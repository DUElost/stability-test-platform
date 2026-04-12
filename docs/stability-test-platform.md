# 稳定性测试管理平台实施计划

**生成时间**：2026-01-21
**计划版本**：v1.0

---

## 任务类型

- [x] 后端 (→ Codex)
- [x] 前端 (→ Gemini)
- [x] 全栈 (→ 并行)

---

## 技术方案

### 架构决策：中心调度 + Host Agent

采用 **"中心调度 + 轻量 Agent"** 架构：

- **控制面**：中心服务（API + 调度 + 状态管理）
- **执行面**：每台 Linux 主机运行 Agent（常驻进程，管理 ADB 与任务）
- **数据面**：数据库存元数据，日志与附件走对象存储或文件系统

**技术栈**：
- **后端**：Python FastAPI（复用现有 `.py` 脚本）
- **前端**：React + Tailwind CSS（组件化优势）
- **通讯**：WebSocket（实时日志/状态推送）
- **数据库**：PostgreSQL（元数据）+ MinIO/文件系统（日志）

---

## 实施步骤

### Phase 1：基础设施与数据模型 (Week 1-2)

#### 1.1 数据库设计

```sql
-- 主机表
CREATE TABLE hosts (
    id SERIAL PRIMARY KEY,
    ip_address VARCHAR(45) NOT NULL UNIQUE,
    hostname VARCHAR(100),
    status VARCHAR(20) DEFAULT 'ONLINE',  -- ONLINE, OFFLINE, MAINTENANCE
    cpu_load DECIMAL(5,2),
    ram_usage DECIMAL(5,2),
    max_concurrent_tasks INT DEFAULT 10,
    last_heartbeat TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW()
);

-- 设备表
CREATE TABLE devices (
    id SERIAL PRIMARY KEY,
    serial_number VARCHAR(100) NOT NULL UNIQUE,
    model VARCHAR(100),
    android_version VARCHAR(20),
    host_id INT REFERENCES hosts(id),
    status VARCHAR(20) DEFAULT 'IDLE',  -- IDLE, RUNNING, OFFLINE, ERROR
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
    script_path VARCHAR(255),   -- 指向现有脚本路径
    default_config JSONB,
    description TEXT
);

-- 任务表
CREATE TABLE tasks (
    id SERIAL PRIMARY KEY,
    template_id INT REFERENCES task_templates(id),
    name VARCHAR(200),
    config JSONB NOT NULL,
    status VARCHAR(20) DEFAULT 'PENDING',  -- PENDING, QUEUED, RUNNING, COMPLETED, FAILED, CANCELLED
    priority INT DEFAULT 5,
    created_by VARCHAR(100),
    created_at TIMESTAMP DEFAULT NOW(),
    scheduled_at TIMESTAMP
);

-- 任务运行记录表（每次实例化一个任务）
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
    log_type VARCHAR(50),  -- LOGCAT, CRASH, ANR, SYSTEM
    uploaded_at TIMESTAMP DEFAULT NOW()
);
```

#### 1.2 目录结构创建

```
stability-test-platform/
├── backend/
│   ├── api/
│   │   ├── __init__.py
│   │   ├── routes/
│   │   │   ├── hosts.py
│   │   │   ├── devices.py
│   │   │   ├── tasks.py
│   │   │   └── logs.py
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
│   │   ├── main.py           # Agent 入口
│   │   ├── adb_wrapper.py    # ADB 命令封装
│   │   ├── task_executor.py  # 任务执行器
│   │   └── log_collector.py  # 日志收集器
│   └── main.py
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   ├── pages/
│   │   ├── hooks/
│   │   └── utils/
│   └── package.json
└── docker-compose.yml
```

---

### Phase 2：后端核心 - Agent 开发 (Week 2-3)

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
        """
        执行 ADB 命令，支持超时和重试

        错误分类：
        - DEVICE_OFFLINE: 设备离线
        - ADB_TIMEOUT: 命令超时
        - PERMISSION_DENIED: 权限不足
        - COMMAND_FAILED: 命令执行失败
        """
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
                return ADBResult(
                    success=False,
                    output=output,
                    error="DEVICE_OFFLINE"
                )

            # 检测未授权
            if "unauthorized" in output.lower():
                return ADBResult(
                    success=False,
                    output=output,
                    error="PERMISSION_DENIED"
                )

            return ADBResult(
                success=result.returncode == 0,
                output=output,
                exit_code=result.returncode
            )

        except subprocess.TimeoutExpired:
            logger.error(f"ADB command timeout: {cmd}")
            return ADBResult(
                success=False,
                output="",
                error="ADB_TIMEOUT"
            )
        except Exception as e:
            logger.error(f"ADB command error: {e}")
            return ADBResult(
                success=False,
                output="",
                error=str(e)
            )

    def get_devices(self) -> List[str]:
        """获取所有已连接设备列表"""
        result = subprocess.run(
            ["adb", "devices"],
            capture_output=True,
            text=True
        )
        # 解析设备列表
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

    def check_process(self, process_name: str) -> bool:
        """检查进程是否存在"""
        result = self.shell_command(f"ps -A | grep {process_name}")
        return process_name in result.output

    def kill_process(self, pid: int) -> ADBResult:
        """终止进程"""
        return self.shell_command(f"kill -9 {pid}")
```

#### 2.2 任务执行器

```python
# backend/agent/task_executor.py

import os
import logging
import subprocess
from typing import Dict, Any
from enum import Enum

logger = logging.getLogger(__name__)

class TaskStatus(Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"

class TaskExecutor:
    """任务执行器，封装各类测试工具的启动逻辑"""

    def __init__(self, run_id: int, device_serial: str, config: Dict[str, Any]):
        self.run_id = run_id
        self.device_serial = device_serial
        self.config = config
        self.adb = ADBWrapper(device_serial)
        self.status = TaskStatus.PENDING
        self.process: Optional[subprocess.Popen] = None

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

        # 检查并安装 APK
        apk_path = self.config.get("apk_path")
        if apk_path:
            result = self.adb.install_apk(apk_path)
            if not result.success:
                logger.error(f"Failed to install APK: {result.error}")
                return TaskStatus.FAILED

        # 推送测试脚本
        script_path = self.config.get("script_path")
        if script_path:
            result = self.adb.push_file(script_path, "/data/local/tmp/")
            if not result.success:
                logger.error(f"Failed to push script: {result.error}")
                return TaskStatus.FAILED

        # 启动测试
        duration = self.config.get("duration_hours", 24)
        throttle = self.config.get("throttle", 500)
        cmd = f"sh /data/local/tmp/MonkeyTest.sh --running-minutes {duration * 60} --throttle {throttle}"

        result = self.adb.shell_command(cmd)
        if result.success:
            self.status = TaskStatus.COMPLETED
        else:
            self.status = TaskStatus.FAILED

        return self.status

    def _execute_mtbf(self) -> TaskStatus:
        """执行 MTBF 测试"""
        logger.info(f"Starting MTBF test for device {self.device_serial}")

        # 安装 APK
        apk_path = self.config.get("apk_path")
        if apk_path and os.path.exists(apk_path):
            result = self.adb.install_apk(apk_path)
            if not result.success:
                logger.error(f"Failed to install MTBF APK: {result.error}")
                return TaskStatus.FAILED

        # 推送测试资源
        resource_dir = self.config.get("resource_dir")
        if resource_dir:
            for file in os.listdir(resource_dir):
                self.adb.push_file(
                    os.path.join(resource_dir, file),
                    f"/sdcard/{file}"
                )

        # 启动测试（通过 am instrument）
        pkg = self.config.get("test_package")
        test_class = self.config.get("test_class")
        cmd = f"am instrument -w -r -e debug false {pkg}/{test_class}"

        result = self.adb.shell_command(cmd)
        self.status = TaskStatus.COMPLETED if result.success else TaskStatus.FAILED
        return self.status

    def _execute_ddr(self) -> TaskStatus:
        """执行 DDR 测试"""
        logger.info(f"Starting DDR test for device {self.device_serial}")

        # DDR 测试需要 Root 权限
        result = self.adb.shell_command("su -c 'id'")
        if "uid=0" not in result.output:
            logger.error("DDR test requires root permission")
            return TaskStatus.FAILED

        # 推送 memtest 二进制
        memtest_path = self.config.get("memtest_path")
        result = self.adb.push_file(memtest_path, "/data/local/tmp/memtest")
        if not result.success:
            return TaskStatus.FAILED

        # 设置执行权限
        self.adb.shell_command("chmod 755 /data/local/tmp/memtest")

        # 启动测试
        duration = self.config.get("duration_hours", 24)
        cmd = f"su -c '/data/local/tmp/memtest -t {duration * 3600}'"

        result = self.adb.shell_command(cmd)
        self.status = TaskStatus.COMPLETED if result.success else TaskStatus.FAILED
        return self.status

    def stop(self):
        """停止任务"""
        if self.process:
            self.process.terminate()
            self.process.wait()
        self.status = TaskStatus.CANCELLED
```

#### 2.3 Agent 主程序

```python
# backend/agent/main.py

import asyncio
import logging
import aiohttp
from typing import Dict, Any

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
        asyncio.create_task(self._heartbeat_loop())

        # 启动任务拉取
        asyncio.create_task(self._task_poll_loop())

    async def _heartbeat_loop(self):
        """心跳循环"""
        while True:
            try:
                await self._send_heartbeat()
                await asyncio.sleep(10)
            except Exception as e:
                logger.error(f"Heartbeat error: {e}")

    async def _task_poll_loop(self):
        """任务拉取循环"""
        while True:
            try:
                await self._poll_tasks()
                await asyncio.sleep(5)
            except Exception as e:
                logger.error(f"Task poll error: {e}")

    async def _send_heartbeat(self):
        """发送心跳"""
        # 获取主机资源信息
        cpu_load = self._get_cpu_load()
        ram_usage = self._get_ram_usage()

        async with self.session.post(
            f"{self.server_url}/api/v1/hosts/{self.host_id}/heartbeat",
            json={"cpu_load": cpu_load, "ram_usage": ram_usage}
        ) as resp:
            if resp.status != 200:
                logger.error(f"Heartbeat failed: {await resp.text()}")

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

        # 更新状态为 RUNNING
        await self._update_run_status(run_id, "RUNNING")

        # 执行任务
        executor = TaskExecutor(run_id, device_serial, config)
        self.running_tasks[run_id] = executor

        status = executor.execute()

        # 收集日志
        await self._collect_logs(run_id, device_serial)

        # 更新最终状态
        await self._update_run_status(run_id, status.value)

        del self.running_tasks[run_id]

    async def _collect_logs(self, run_id: int, device_serial: str):
        """收集并上传日志"""
        adb = ADBWrapper(device_serial)

        # 拉取各类日志
        log_paths = [
            ("/sdcard/systeminfo/", "logcat"),
            ("/sdcard/ReliabilityTest/", "mtbf"),
            ("/data/local/tmp/", "system")
        ]

        for remote_path, log_type in log_paths:
            local_path = f"/tmp/logs/{run_id}/{log_type}/"
            os.makedirs(local_path, exist_ok=True)

            result = adb.pull_logs(remote_path, local_path)
            if result.success:
                await self._upload_logs(run_id, local_path, log_type)

    async def _upload_logs(self, run_id: int, local_path: str, log_type: str):
        """上传日志到中心服务"""
        # 压缩日志
        import zipfile
        zip_path = f"{local_path}.zip"
        with zipfile.ZipFile(zip_path, 'w') as zipf:
            for root, dirs, files in os.walk(local_path):
                for file in files:
                    zipf.write(os.path.join(root, file), file)

        # 上传
        with open(zip_path, 'rb') as f:
            async with self.session.post(
                f"{self.server_url}/api/v1/runs/{run_id}/logs",
                data={"file": f, "type": log_type}
            ) as resp:
                if resp.status != 200:
                    logger.error(f"Log upload failed: {await resp.text()}")

    def _get_cpu_load(self) -> float:
        """获取 CPU 负载"""
        import psutil
        return psutil.cpu_percent()

    def _get_ram_usage(self) -> float:
        """获取内存使用率"""
        import psutil
        return psutil.virtual_memory().percent

if __name__ == "__main__":
    agent = HostAgent(
        server_url=os.getenv("SERVER_URL", "http://localhost:8000"),
        host_id=int(os.getenv("HOST_ID"))
    )
    asyncio.run(agent.start())
```

---

### Phase 3：后端核心 - 中心服务 (Week 3-4)

#### 3.1 API 路由

```python
# backend/api/routes/tasks.py

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

router = APIRouter(prefix="/api/v1/workflows", tags=["orchestration"])

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

    # 创建任务记录
    task = Task(
        template_id=task_request.template_id,
        name=task_request.name,
        config=task_request.config,
        priority=task_request.priority
    )
    db.add(task)
    db.commit()

    # 创建运行记录并锁定设备
    run = TaskRun(
        task_id=task.id,
        device_id=device.id,
        host_id=device.host_id,
        status="QUEUED"
    )
    db.add(run)

    device.status = "LOCKED"
    device.current_run_id = run.id
    db.commit()

    # 加入调度队列
    await task_queue.enqueue(run.id)

    return {"task_id": task.id, "run_id": run.id}

@router.get("/{task_id}")
async def get_task(task_id: int, db: Session = Depends(get_db)):
    """获取任务详情"""
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task

@router.post("/{task_id}/cancel")
async def cancel_task(task_id: int, db: Session = Depends(get_db)):
    """取消任务"""
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if task.status not in ["PENDING", "QUEUED", "RUNNING"]:
        raise HTTPException(status_code=400, detail="Task cannot be cancelled")

    # 通知 Agent 停止任务
    run = db.query(TaskRun).filter(
        TaskRun.task_id == task_id,
        TaskRun.status == "RUNNING"
    ).first()

    if run:
        await notify_agent_stop(run.host_id, run.id)

    task.status = "CANCELLED"
    db.commit()

    return {"message": "Task cancelled"}
```

#### 3.2 调度器

```python
# backend/scheduler/dispatcher.py

import asyncio
from typing import Optional
from sqlalchemy.orm import Session

class TaskDispatcher:
    """任务调度器"""

    def __init__(self, db: Session):
        self.db = db
        self.queue = asyncio.Queue()

    async def enqueue(self, run_id: int):
        """将任务加入队列"""
        await self.queue.put(run_id)

    async def start(self):
        """启动调度循环"""
        while True:
            run_id = await self.queue.get()
            try:
                await self._dispatch(run_id)
            except Exception as e:
                logger.error(f"Dispatch error for run {run_id}: {e}")

    async def _dispatch(self, run_id: int):
        """分发任务到对应主机"""
        run = self.db.query(TaskRun).filter(TaskRun.id == run_id).first()
        if not run:
            return

        # 检查主机状态
        host = self.db.query(Host).filter(Host.id == run.host_id).first()
        if host.status != "ONLINE":
            # 主机离线，重新排队
            await self.enqueue(run_id)
            return

        # 检查并发限制
        running_count = self.db.query(TaskRun).filter(
            TaskRun.host_id == host.id,
            TaskRun.status == "RUNNING"
        ).count()

        if running_count >= host.max_concurrent_tasks:
            # 主机已满，重新排队
            await asyncio.sleep(60)
            await self.enqueue(run_id)
            return

        # 下发任务到 Agent
        await self._notify_agent(run, host)

    async def _notify_agent(self, run: TaskRun, host: Host):
        """通知 Agent 执行任务"""
        # 更新运行状态
        run.status = "PENDING"
        self.db.commit()

        # WebSocket 通知或 Agent 轮询时会获取到这个任务
        logger.info(f"Dispatched run {run.id} to host {host.id}")
```

---

### Phase 4：前端核心 - UI 组件 (Week 4-5)

#### 4.1 设备卡片组件

```tsx
// frontend/src/components/DeviceCard.tsx

import React from 'react';
import { Device } from '../types';

interface DeviceCardProps {
  device: Device;
  onClick: (device: Device) => void;
}

export const DeviceCard: React.FC<DeviceCardProps> = ({ device, onClick }) => {
  const getStatusColor = (status: string) => {
    switch (status) {
      case 'RUNNING': return 'border-green-500 animate-pulse';
      case 'ERROR': return 'border-red-500';
      case 'IDLE': return 'border-gray-600';
      default: return 'border-gray-600';
    }
  };

  const getStatusBadge = (status: string) => {
    switch (status) {
      case 'RUNNING': return <span className="text-green-400">运行中</span>;
      case 'ERROR': return <span className="text-red-400">异常</span>;
      case 'IDLE': return <span className="text-gray-400">空闲</span>;
      default: return <span>{status}</span>;
    }
  };

  return (
    <div
      onClick={() => onClick(device)}
      className={`
        bg-gray-800 rounded-lg p-4 cursor-pointer
        border-2 ${getStatusColor(device.status)}
        hover:bg-gray-700 transition-colors
      `}
      tabIndex={0}
      onKeyPress={(e) => e.key === 'Enter' && onClick(device)}
      role="button"
      aria-label={`设备 ${device.model}, 序列号 ${device.serial_number}, 状态 ${device.status}`}
    >
      <div className="flex justify-between items-start mb-2">
        <h3 className="text-lg font-semibold text-gray-200">{device.model}</h3>
        {getStatusBadge(device.status)}
      </div>

      <div className="space-y-1 text-sm text-gray-400">
        <p>SN: {device.serial_number}</p>
        <p>Android: {device.android_version}</p>
      </div>

      {device.battery_level !== null && (
        <div className="mt-3">
          <div className="flex justify-between text-xs text-gray-500 mb-1">
            <span>电量</span>
            <span>{device.battery_level}%</span>
          </div>
          <div className="w-full bg-gray-700 rounded-full h-2">
            <div
              className={`
                h-2 rounded-full transition-all
                ${device.battery_level > 50 ? 'bg-green-500' : device.battery_level > 20 ? 'bg-yellow-500' : 'bg-red-500'}
              `}
              style={{ width: `${device.battery_level}%` }}
            />
          </div>
        </div>
      )}

      {device.temperature !== null && (
        <div className="mt-2 flex items-center text-xs">
          <span className="text-gray-500">温度:</span>
          <span className={`
            ml-1 ${device.temperature > 45 ? 'text-red-400' : device.temperature > 35 ? 'text-yellow-400' : 'text-green-400'}
          `}>
            {device.temperature.toFixed(1)}°C
          </span>
        </div>
      )}

      {device.current_task && (
        <div className="mt-3 pt-3 border-t border-gray-700">
          <p className="text-xs text-gray-500">当前任务</p>
          <p className="text-sm text-blue-400 truncate">{device.current_task}</p>
        </div>
      )}
    </div>
  );
};
```

#### 4.2 工作流执行表单

```tsx
// frontend/src/components/CreateTaskForm.tsx

import React, { useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';

interface TaskConfig {
  type: string;
  [key: string]: any;
}

export const CreateTaskForm: React.FC = () => {
  const [selectedWorkflowId, setSelectedWorkflowId] = useState<number | null>(null);
  const [selectedDevices, setSelectedDevices] = useState<number[]>([]);
  const [config, setConfig] = useState<TaskConfig>({});

  const { data: workflows } = useQuery({
    queryKey: ['workflows'],
    queryFn: () => fetch('/api/v1/workflows').then(r => r.json())
  });

  const createRun = useMutation({
    mutationFn: (data: any) =>
      fetch(`/api/v1/workflows/${selectedWorkflowId}/run`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data)
      }).then(r => r.json())
  });

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    await createRun.mutateAsync({
      device_ids: selectedDevices,
      trigger_source: 'ui',
      config
    });
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-6">
      {/* 测试类型选择 */}
      <div>
        <label className="block text-sm font-medium text-gray-300 mb-2">
          测试类型
        </label>
        <div className="grid grid-cols-2 gap-3">
          {templates?.map((template: any) => (
            <button
              key={template.id}
              type="button"
              onClick={() => setSelectedType(template.type)}
              className={`
                p-4 rounded-lg border-2 text-left transition-colors
                ${selectedType === template.type
                  ? 'border-blue-500 bg-blue-500/10'
                  : 'border-gray-700 bg-gray-800 hover:border-gray-600'
                }
              `}
            >
              <h3 className="font-semibold text-gray-200">{template.name}</h3>
              <p className="text-sm text-gray-400 mt-1">{template.description}</p>
            </button>
          ))}
        </div>
      </div>

      {/* 动态配置表单 */}
      {selectedType && (
        <DynamicConfigForm
          type={selectedType}
          config={config}
          onChange={setConfig}
        />
      )}

      {/* 设备选择 */}
      <DeviceSelector
        selected={selectedDevices}
        onChange={setSelectedDevices}
      />

      {/* 提交按钮 */}
      <button
        type="submit"
        disabled={!selectedType || selectedDevices.length === 0}
        className="w-full py-3 px-4 bg-blue-600 hover:bg-blue-700 disabled:bg-gray-700 disabled:cursor-not-allowed text-white font-medium rounded-lg transition-colors"
      >
        创建任务
      </button>
    </form>
  );
};
```

#### 4.3 实时日志查看器

```tsx
// frontend/src/components/LogViewer.tsx

import React, { useEffect, useRef, useState } from 'react';
import { useWebSocket } from '../hooks/useWebSocket';

interface LogViewerProps {
  runId: number;
  deviceSerial: string;
}

export const LogViewer: React.FC<LogViewerProps> = ({ runId, deviceSerial }) => {
  const [logs, setLogs] = useState<LogLine[]>([]);
  const [autoScroll, setAutoScroll] = useState(true);
  const [filter, setFilter] = useState('');
  const containerRef = useRef<HTMLDivElement>(null);

  const { socket, isConnected } = useWebSocket(`/ws/logs/${runId}`);

  useEffect(() => {
    if (!socket) return;

    socket.onmessage = (event) => {
      const logLine: LogLine = JSON.parse(event.data);
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
      {/* 工具栏 */}
      <div className="bg-gray-800 px-4 py-2 flex items-center gap-3">
        <div className="flex items-center gap-2">
          <span className={`w-2 h-2 rounded-full ${isConnected ? 'bg-green-500' : 'bg-red-500'}`} />
          <span className="text-sm text-gray-400">{isConnected ? '已连接' : '未连接'}</span>
        </div>

        <input
          type="text"
          placeholder="过滤日志..."
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          className="flex-1 px-3 py-1 bg-gray-700 text-gray-200 text-sm rounded border border-gray-600 focus:border-blue-500 focus:outline-none"
        />

        <button
          onClick={() => setAutoScroll(!autoScroll)}
          className={`
            px-3 py-1 text-sm rounded transition-colors
            ${autoScroll ? 'bg-blue-600 text-white' : 'bg-gray-700 text-gray-300'}
          `}
        >
          自动滚动
        </button>

        <a
          href={`/api/v1/runs/${runId}/logs/download`}
          className="px-3 py-1 text-sm bg-gray-700 text-gray-300 rounded hover:bg-gray-600 transition-colors"
        >
          下载
        </a>
      </div>

      {/* 日志内容 */}
      <div
        ref={containerRef}
        className="h-[600px] overflow-y-auto p-4 font-mono text-sm"
        role="log"
        aria-live="polite"
      >
        {filteredLogs.map((log, index) => (
          <div
            key={index}
            className="flex hover:bg-gray-800/50 -mx-4 px-4"
          >
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

### Phase 5：集成与测试 (Week 5-6)

#### 5.1 任务封装映射

将现有测试工具封装为标准任务模板：

```python
# backend/core/task_templates.py

TASK_TEMPLATES = {
    "MONKEY": {
        "name": "Monkey 压力测试",
        "description": "随机 UI 操作与 AI 智能遍历",
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
        "name": "MTBF 综合稳定性测试",
        "description": "700 项综合功能测试",
        "script_path": "MTBF_test/Start.bat",
        "default_config": {
            "task_file": "版测700_All.xml",
            "loop_count": 1
        }
    },
    "DDR": {
        "name": "DDR 专项测试",
        "description": "内存压力测试与 DFS 测试",
        "script_path": "DDR_test/push---8.27.bat",
        "default_config": {
            "duration_hours": 48,
            "require_root": True
        }
    },
    "GPU": {
        "name": "GPU 压力测试",
        "description": "Antutu GPU 性能测试",
        "script_path": "GPU_stress_test/",
        "default_config": {
            "loop_count": 500,
            "duration_hours": 40
        }
    },
    "STANDBY": {
        "name": "待机稳定性测试",
        "description": "7 天 YouTube 待机测试",
        "script_path": "Standby_test/run_7day_youtube_standby.bat",
        "default_config": {
            "duration_hours": 168
        }
    }
}
```

#### 5.2 测试策略

**单元测试**：
- ADB 命令封装（mock subprocess）
- 任务状态机转换

**集成测试**：
- Agent ↔ 中心 API 通信
- 设备断连模拟
- 日志上传流程

**故障注入测试**：
- ADB 超时
- Host 心跳中断
- 任务执行失败

---

## 关键文件

| 文件 | 操作 | 说明 |
|------|------|------|
| `backend/agent/adb_wrapper.py` | 新建 | ADB 命令封装，支持超时重试 |
| `backend/agent/task_executor.py` | 新建 | 任务执行器，封装各类测试工具 |
| `backend/agent/main.py` | 新建 | Agent 主程序 |
| `backend/api/routes/tasks.py` | 新建 | 任务 API 路由 |
| `backend/scheduler/dispatcher.py` | 新建 | 任务调度器 |
| `frontend/src/components/DeviceCard.tsx` | 新建 | 设备卡片组件 |
| `frontend/src/components/CreateTaskForm.tsx` | 新建 | 任务创建表单 |
| `frontend/src/components/LogViewer.tsx` | 新建 | 日志查看器 |

---

## 风险与缓解

| 风险 | 缓解措施 |
|------|----------|
| ADB 不稳定导致设备频繁断连 | Agent 本地重连机制、离线检测、自动重试与熔断 |
| 现有工具启动方式不统一（.bat 不兼容 Linux） | 为每类工具提供 Linux 兼容脚本与统一入口 |
| 日志体量巨大导致存储与检索成本过高 | 热日志索引、冷日志归档、可配置留存周期 |
| 安全问题（任意命令执行） | 命令白名单、参数校验、Agent 只暴露有限 API |
| 长任务中断无法定位 | 结构化状态机与关键节点心跳，记录可回放的运行轨迹 |

---

## SESSION_ID（供 /ccg:execute 使用）

- **CODEX_SESSION**: `019bdf4e-5455-7cd2-8ea0-f1d8464b1bc1`
- **GEMINI_SESSION**: `ced7640d-abcc-4986-942b-0ade8649d61a`

---

*本计划由多模型协作生成，综合了 Codex 的后端架构分析和 Gemini 的前端 UI/UX 设计。*
