"""Agent disk / watcher subsystem bootstrap extracted from ``main`` (#736).

Owns the startup configure/start sequence for LogArchiver, scan/upload,
EventUploader, LocalDiskMonitor, and the optional Device Log Watcher stack.
``main`` keeps identity, control handlers, and the job loop; reload_config
re-applies a narrower force=True subset in place and is intentionally not
funneled through this module yet.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

from .aee.paths import get_aee_local_root, resolve_shared_storage_root
from .artifact_uploader import ArtifactUploader
from .config import BASE_DIR
from .event_uploader import EventUploader
from .local_disk_monitor import LocalDiskMonitor
from .log_archiver import LogArchiver
from .scan_runner import ScanRunner
from .settings import get_disk_archive_settings
from .unisoc_scan_runner import UnisocScanRunner
from .upload_manager import UploadManager
from .watcher import LogWatcherManager, OutboxDrainer
from .watcher.enable import watcher_subsystem_enabled

logger = logging.getLogger(__name__)


def start_disk_and_watcher_subsystems(
    *,
    local_db: Any,
    api_url: str,
    agent_secret: str,
    host_id: str,
    agent_instance_id: str,
    adb: Any,
    adb_path: str,
    sio_client: Any,
) -> Optional[OutboxDrainer]:
    """Configure and start disk + watcher background subsystems.

    Returns the started ``OutboxDrainer`` when the watcher subsystem is enabled;
    otherwise ``None`` (caller skips watcher shutdown).
    """
    # P2-3：scan/upload/归档/spill 不绑定 watcher 开关——各自按自身 env 门控
    #（EventUploader.start / LocalDiskMonitor 内部都有 enabled 判断）。
    hdd_root = str(get_aee_local_root())
    cifs_root = resolve_shared_storage_root()
    disk_settings = get_disk_archive_settings()
    LogArchiver.instance().configure(
        local_db=local_db,
        run_log_dir=str(BASE_DIR / "logs" / "runs"),
        interval_seconds=disk_settings.stp_log_archive_interval_seconds,
        grace_seconds=disk_settings.stp_log_archive_grace_seconds,
    ).start()
    logger.info("log_archiver=started")
    ScanRunner.instance().configure()
    UnisocScanRunner.instance().configure()
    UploadManager.instance().configure()
    # #2845：EventUploader 的上送目的地是 {cifs_root}/devices/…，无共享存储根时
    # 既无处落盘、configure 又会去求 get_aee_nfs_root() 抛 RuntimeError 打断整个
    # bootstrap。与下面的 spill monitor 同判据：根为空 = 组件不启用。
    if cifs_root:
        EventUploader.instance().configure(
            api_url=api_url,
            agent_secret=agent_secret,
            host_id=str(host_id),
            nfs_root=cifs_root,
        )
        EventUploader.instance().start()
        logger.info("event_uploader=started cifs=%s", cifs_root)
    else:
        logger.info("event_uploader_skipped cifs_root_empty")
    if cifs_root:
        LocalDiskMonitor.instance().configure(
            hdd_root=hdd_root,
            cifs_root=cifs_root,
            interval_seconds=disk_settings.stp_local_disk_monitor_interval_seconds,
            spill_threshold_pct=disk_settings.stp_local_disk_spill_threshold,
            target_pct=disk_settings.stp_local_disk_spill_target,
            api_url=api_url,
            agent_secret=agent_secret,
            host_id=str(host_id),
        ).start()
        logger.info("hdd_spill_monitor=started hdd=%s cifs=%s", hdd_root, cifs_root)
    else:
        logger.info("hdd_spill_monitor_skipped cifs_root_empty")

    # Device Log Watcher 子系统（全局或 Plan 默认开启时 configure）
    log_signal_drainer: Optional[OutboxDrainer] = None
    if watcher_subsystem_enabled():
        # 5B1 + D1：LogPuller 中心存储根（空串 = 禁用 puller，仅记元数据）
        nfs_base_dir = resolve_shared_storage_root()
        LogWatcherManager.instance().configure(
            adb=adb,
            adb_path=adb_path,  # InotifydSource.Popen 需要 adb 二进制路径
            local_db=local_db,
            sio_client=sio_client,
            api_url=api_url,
            agent_secret=agent_secret,
            agent_instance_id=agent_instance_id,
            nfs_base_dir=nfs_base_dir,
        )
        # log_signal_outbox 后台批量上送线程（watcher 写入 → drainer 推送到后端）
        log_signal_drainer = OutboxDrainer.instance().configure(
            local_db=local_db,
            api_url=api_url,
            agent_secret=agent_secret,
            interval_seconds=5.0,
            batch_size=50,
        )
        log_signal_drainer.start()
        # 5B2：artifact 上传单例（fire-and-forget；失败不影响 log_signal 主链路）
        ArtifactUploader.instance().configure(
            api_url=api_url,
            agent_secret=agent_secret,
            host_id=str(host_id),
            agent_instance_id=agent_instance_id,
            # #97: 登记前 promote 到共享根（控制面只认共享路径）。
            # 仅 STP_AEE_NFS_ROOT（及弃用别名）才启用；未配置则 LOCAL 直发。
            aee_shared_root=resolve_shared_storage_root(),
        )
        ArtifactUploader.instance().start()
        logger.info(
            "watcher_subsystem_enabled log_signal_drainer=started artifact_uploader=started"
        )
        # M4/T4-4: 清理上次进程残留的 active watcher_state(崩溃/重启脏记录)。
        # 必须在 configure(注入 local_db)之后调用。
        try:
            stale_cleaned = LogWatcherManager.instance().reconcile_on_startup()
            if stale_cleaned:
                logger.info("watcher_reconcile_on_startup cleaned_stale=%d", stale_cleaned)
        except Exception:
            logger.exception("watcher_reconcile_on_startup failed")
        # D2: AeeDbHistoryReconciler 启动期参数(读 env;是否真正启动按 capability + host 白名单门控)
        logger.info(
            "aee_reconciler_env enabled=%s interval_seconds=%s burst_interval_seconds=%s "
            "burst_rounds=%s hosts=%s",
            os.getenv("STP_WATCHER_AEE_RECONCILE_ENABLED", "true"),
            os.getenv("STP_WATCHER_AEE_RECONCILE_INTERVAL_SECONDS", "180"),
            os.getenv("STP_WATCHER_AEE_RECONCILE_BURST_INTERVAL_SECONDS", "60"),
            os.getenv("STP_WATCHER_AEE_RECONCILE_BURST_ROUNDS", "5"),
            os.getenv("STP_WATCHER_AEE_RECONCILE_HOSTS", "") or "(unset → 全 host 放行)",
        )
    else:
        logger.info(
            "watcher_subsystem_disabled (STP_WATCHER_ENABLED=false STP_WATCHER_PLAN_DEFAULT=false)"
        )
    return log_signal_drainer
