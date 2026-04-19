"""Watcher 子系统异常定义。

分层：
    WatcherError
      ├─ WatcherStartError   — 启动阶段失败（探测/权限/NFS 配额）→ 导致 Job FAILED
      └─ WatcherRuntimeError — 运行期失败（adb 掉线/子进程崩溃）→ 降级，不杀 Job
"""

from typing import Optional


class WatcherError(Exception):
    """Watcher 子系统异常基类。"""

    def __init__(self, message: str, *, code: str = "", context: Optional[dict] = None):
        super().__init__(message)
        self.code = code or self.__class__.__name__
        self.context = context or {}


class WatcherStartError(WatcherError):
    """Watcher 启动失败（必须由 JobSession 捕获并决定 Job 生死）。

    典型 code:
        - probe_failed             : 所有能力探测失败
        - nfs_not_writable         : NFS 落地目录不可写
        - already_running          : 同 serial 已存在 watcher（进程内保护）
        - policy_required_missing  : required_categories 的路径全部不可访问
    """


class WatcherRuntimeError(WatcherError):
    """Watcher 运行期非致命异常。

    典型 code:
        - source_restart_exhausted : EventSource 连续重试耗尽
        - pull_quarantined         : 文件 pull 失败超阈值
        - queue_overflow           : 事件队列溢出（已丢弃事件）
    """
