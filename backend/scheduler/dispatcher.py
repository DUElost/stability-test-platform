import asyncio
import logging
import os
import threading
from datetime import datetime, timedelta
from typing import Optional, Set, Tuple

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..core.database import SessionLocal
from ..models.schemas import (
    Device,
    DeviceStatus,
    Host,
    HostStatus,
    RunStatus,
    Task,
    TaskRun,
    TaskStatus,
)

logger = logging.getLogger(__name__)

# 调度参数（可由环境变量覆盖）
SCAN_INTERVAL = float(os.getenv("DISPATCHER_SCAN_INTERVAL", "5"))
RETRY_DELAY = float(os.getenv("DISPATCHER_RETRY_DELAY", "10"))
WORKER_COUNT = int(os.getenv("DISPATCHER_WORKERS", "2"))
QUEUE_CAPACITY = int(os.getenv("DISPATCH_QUEUE_CAPACITY", "200"))
DEFAULT_MAX_CONCURRENT = int(os.getenv("MAX_CONCURRENT_TASKS", "1"))
DEVICE_LOCK_LEASE_SECONDS = int(os.getenv("DEVICE_LOCK_LEASE_SECONDS", "600"))


class TaskDispatcher:
    """基于 asyncio 的任务自动分发器"""

    def __init__(self) -> None:
        self.queue: Optional[asyncio.Queue[int]] = None
        self._seen: Set[int] = set()

    def start(self) -> threading.Thread:
        thread = threading.Thread(target=self._run_loop, name="task-dispatcher", daemon=True)
        thread.start()
        return thread

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self.queue = asyncio.Queue(maxsize=QUEUE_CAPACITY)
        loop.create_task(self._producer_loop())
        for idx in range(WORKER_COUNT):
            loop.create_task(self._worker_loop(idx))
        loop.run_forever()

    async def _producer_loop(self) -> None:
        while True:
            try:
                await self._enqueue_pending_tasks()
            except Exception:
                logger.exception("dispatch_producer_failed")
            await asyncio.sleep(SCAN_INTERVAL)

    async def _worker_loop(self, worker_id: int) -> None:
        while True:
            if self.queue is None:
                await asyncio.sleep(1)
                continue
            task_id = await self.queue.get()
            try:
                finished, retry = self._dispatch_once(task_id)
                if retry:
                    await asyncio.sleep(RETRY_DELAY)
                    await self._safe_requeue(task_id)
                else:
                    self._seen.discard(task_id)
            except Exception:
                logger.exception("dispatch_worker_failed", extra={"worker_id": worker_id, "task_id": task_id})
                self._seen.discard(task_id)
            finally:
                if self.queue:
                    self.queue.task_done()

    async def _enqueue_pending_tasks(self) -> None:
        """将数据库中 PENDING 任务放入内存队列"""
        if self.queue is None:
            return
        with SessionLocal() as db:
            pending = (
                db.query(Task)
                .filter(Task.status == TaskStatus.PENDING)
                .order_by(Task.priority.desc(), Task.id)
                .all()
            )
            for task in pending:
                if task.id in self._seen:
                    continue
                try:
                    self.queue.put_nowait(task.id)
                    self._seen.add(task.id)
                    logger.info("task_enqueued", extra={"task_id": task.id, "type": task.type})
                except asyncio.QueueFull:
                    logger.warning("dispatch_queue_full", extra={"task_id": task.id})
                    break

    async def _safe_requeue(self, task_id: int) -> None:
        """避免队列满导致丢失"""
        if self.queue is None:
            return
        try:
            self.queue.put_nowait(task_id)
        except asyncio.QueueFull:
            logger.warning("dispatch_requeue_dropped", extra={"task_id": task.id})

    def _dispatch_once(self, task_id: int) -> Tuple[bool, bool]:
        """
        返回 (finished, retry)
        finished=True 表示无需再次排队（成功或状态已变）
        retry=True  表示保持 PENDING 需后续重试
        """
        with SessionLocal() as db:
            task = db.get(Task, task_id)
            if not task or task.status != TaskStatus.PENDING:
                return True, False

            device, host = self._pick_device(db, task)
            if not device or not host:
                logger.info("no_available_device", extra={"task_id": task.id})
                return False, True

            capacity_ok, active, limit = self._host_capacity(db, host)
            if not capacity_ok:
                logger.info(
                    "host_at_capacity",
                    extra={"host_id": host.id, "active": active, "limit": limit, "task_id": task.id},
                )
                return False, True

            try:
                run_id = self._create_run_with_lock(db, task, device, host, limit)
                logger.info(
                    "task_dispatched",
                    extra={
                        "task_id": task.id,
                        "run_id": run_id,
                        "host_id": host.id,
                        "device_id": device.id,
                    },
                )
                return True, False
            except Exception as exc:
                logger.warning("dispatch_attempt_failed", extra={"task_id": task.id, "error": str(exc)})
                return False, True

    def _pick_device(self, db: Session, task: Task) -> Tuple[Optional[Device], Optional[Host]]:
        """选择可用设备与主机"""
        query = (
            db.query(Device)
            .join(Host, Device.host_id == Host.id)
            .filter(
                Host.status == HostStatus.ONLINE,
                Device.status == DeviceStatus.ONLINE,
                Device.lock_run_id.is_(None),
            )
        )
        if task.target_device_id:
            query = query.filter(Device.id == task.target_device_id)
        device: Optional[Device] = (
            query.order_by(Device.last_seen.desc().nullslast(), Device.id).first()
        )
        return device, device.host if device else None

    def _host_capacity(self, db: Session, host: Host) -> Tuple[bool, int, int]:
        """检查主机并发上限"""
        limit = DEFAULT_MAX_CONCURRENT
        if isinstance(host.extra, dict):
            limit = int(host.extra.get("max_concurrent_tasks", host.extra.get("maxConcurrentTasks", limit) or limit))
        active = (
            db.query(TaskRun)
            .filter(
                TaskRun.host_id == host.id,
                TaskRun.status.in_([RunStatus.QUEUED, RunStatus.DISPATCHED, RunStatus.RUNNING]),
            )
            .count()
        )
        return active < limit, active, limit

    def _create_run_with_lock(self, db: Session, task: Task, device: Device, host: Host, host_limit: int) -> int:
        """开启事务，更新任务状态、创建 run 并占用设备，内含主机并发保护"""
        now = datetime.utcnow()
        expires_at = now + timedelta(seconds=DEVICE_LOCK_LEASE_SECONDS)
        with db.begin():
            active = (
                db.query(TaskRun)
                .filter(
                    TaskRun.host_id == host.id,
                    TaskRun.status.in_([RunStatus.QUEUED, RunStatus.DISPATCHED, RunStatus.RUNNING]),
                )
                .with_for_update()
                .count()
            )
            if active >= host_limit:
                raise RuntimeError("host_capacity_exceeded")

            updated = db.execute(
                text("UPDATE tasks SET status = :queued WHERE id = :task_id AND status = :pending"),
                {
                    "task_id": task.id,
                    "queued": TaskStatus.QUEUED.value,
                    "pending": TaskStatus.PENDING.value,
                },
            )
            if updated.rowcount != 1:
                raise RuntimeError("task_status_conflict")

            run = TaskRun(
                task_id=task.id,
                host_id=host.id,
                device_id=device.id,
                status=RunStatus.QUEUED,
            )
            db.add(run)
            db.flush()

            locked = db.execute(
                text(
                    """
                    UPDATE devices
                    SET status = :busy_status,
                        lock_run_id = :run_id,
                        lock_expires_at = :expires_at
                    WHERE id = :device_id
                      AND status = :online_status
                      AND (lock_run_id IS NULL OR lock_expires_at IS NULL OR lock_expires_at < :now)
                    """
                ),
                {
                    "device_id": device.id,
                    "run_id": run.id,
                    "expires_at": expires_at,
                    "now": now,
                    "busy_status": DeviceStatus.BUSY.value,
                    "online_status": DeviceStatus.ONLINE.value,
                },
            )
            if locked.rowcount != 1:
                raise RuntimeError("device_lock_failed")
        return run.id


def start_dispatcher() -> threading.Thread:
    """供 FastAPI 启动阶段调用"""
    dispatcher = TaskDispatcher()
    return dispatcher.start()
