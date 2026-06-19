"""LogArchiver — Agent 侧运行日志归档调度器（ADR-0025 Sprint 2 / D4）。

职责（ADR-0025 2026-06-18 修订）：
    - interval 后台线程周期扫描 `RUN_LOG_DIR/<job_id>/` 下 Job 日志目录
    - 已完成 Job：目录树直复制到 NFS/15.4（非 tar）+ 注册 JobArtifact + 标记
    - 活跃 Job（长跑）：patrol cycle 边界快照到 NFS snapshots/（不 prune、不注册）
    - 归档与 prune 解耦：复制成功后不立即 prune，本地达阈值才 prune（15.4 已有副本）
    - 监控本地磁盘使用量，达阈值后主动溢出旧 Job 日志（prune 已归档的）

完成判定（关键正确性，见 ADR-0025 Sprint 2 计划 §4）：
    - job_id **不在** local_db.get_active_jobs()（Agent 权威活跃集合）
    - **且** 目录 mtime 早于 grace_seconds（覆盖 teardown 收尾 + outbox flush 窗口）
    活跃 Job 不走归档，走 cycle 快照（不 prune）。溢出场景仍不碰活跃 job。

安全序（不丢数据）：
    写 NFS/15.4（可验证）→ 同步注册成功 → mark_job_archived → prune 由阈值触发
    任一步失败：保留本地，下一轮重试。NFS 是耐久副本，注册元数据可重试。
"""

from __future__ import annotations

import logging
import os
import shutil
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

ARTIFACT_TYPE_RUN_LOG_BUNDLE = "run_log_bundle"


class LogArchiver:
    """进程级单例；由 Agent main.py configure + start。"""

    _instance: Optional["LogArchiver"] = None
    _instance_lock = threading.Lock()

    def __init__(self) -> None:
        self._db = None
        self._host_id: str = ""
        self._nfs_base_dir: str = ""
        self._run_log_dir: Optional[Path] = None
        self._api_url: str = ""
        self._agent_secret: str = ""
        self._interval: float = 3600.0
        self._grace_seconds: float = 1800.0
        self._request_timeout: float = 30.0
        self._session: Optional[requests.Session] = None
        self._stop_evt = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._configured = False
        # 指标（累计自进程启动）
        self._archived_total = 0
        self._spilled_total = 0
        self._archive_failed = 0
        self._last_archive_at: Optional[str] = None
        # 待归档数缓存：仅在低频 scan/spill 末尾刷新，供 20s 心跳 O(1) 读取，
        # 避免每次心跳都 _iter_job_dirs() 遍历目录 + 逐 job 查 SQLite。
        self._pending_archive_cached = 0
        self._metrics_lock = threading.Lock()
        # per-job 归档在途集合：scan_once 线程与 spill_oldest 线程可能同时选中同一
        # job_id，用它保证同一 job 同一时刻只有一个归档在跑（不同 job 仍可并发）。
        self._inflight: set = set()
        self._inflight_lock = threading.Lock()

    # ------------------------------------------------------------------
    # 单例
    # ------------------------------------------------------------------

    @classmethod
    def instance(cls) -> "LogArchiver":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    @classmethod
    def _reset_for_tests(cls) -> None:
        with cls._instance_lock:
            inst = cls._instance
            cls._instance = None
        if inst is not None:
            try:
                inst.stop(timeout=0.5)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # 配置 / 启停
    # ------------------------------------------------------------------

    def configure(
        self,
        *,
        local_db,
        host_id: str,
        nfs_base_dir: str,
        run_log_dir: str,
        api_url: str,
        agent_secret: str = "",
        interval_seconds: float = 3600.0,
        grace_seconds: float = 1800.0,
        request_timeout: float = 30.0,
        session: Optional[requests.Session] = None,
    ) -> "LogArchiver":
        if self._thread is not None and self._thread.is_alive():
            raise RuntimeError("configure() after start() is not allowed")
        self._db = local_db
        self._host_id = host_id
        self._nfs_base_dir = (nfs_base_dir or "").strip()
        self._run_log_dir = Path(run_log_dir)
        self._api_url = api_url.rstrip("/")
        self._agent_secret = agent_secret or ""
        self._interval = max(60.0, float(interval_seconds))
        self._grace_seconds = max(0.0, float(grace_seconds))
        self._request_timeout = max(1.0, float(request_timeout))
        self._session = session or requests.Session()
        self._configured = True
        logger.info(
            "log_archiver_configured run_log_dir=%s nfs_base=%s interval=%.0fs grace=%.0fs",
            self._run_log_dir, self._nfs_base_dir or "<disabled>",
            self._interval, self._grace_seconds,
        )
        return self

    def is_configured(self) -> bool:
        return self._configured

    def start(self) -> None:
        if not self._configured:
            raise RuntimeError("LogArchiver not configured — call configure(...) first")
        if not self._nfs_base_dir:
            logger.warning("log_archiver_start_skipped: nfs_base_dir empty (归档禁用)")
            return
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_evt.clear()
        self._thread = threading.Thread(
            target=self._run, name="log-archiver", daemon=True,
        )
        self._thread.start()
        logger.info("log_archiver_started")

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_evt.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        logger.info("log_archiver_stopped metrics=%s", self.snapshot_metrics())

    # ------------------------------------------------------------------
    # 主循环 + 单次扫描（测试可直驱）
    # ------------------------------------------------------------------

    def _run(self) -> None:
        while not self._stop_evt.is_set():
            try:
                self.scan_once()
            except Exception:
                logger.exception("log_archiver_scan_unhandled")
            self._stop_evt.wait(self._interval)

    def scan_once(self, *, grace_seconds: float | None = None) -> int:
        """扫描并归档所有已完成且过 grace 的 Job 目录；活跃 Job 做 cycle 快照。

        ADR-0025 2026-06-18 修订：活跃 Job 不再跳过，改为调 snapshot_active_job
        做目录树快照（不 prune）。已完成 Job 归档后不立即 prune（归档与 prune 解耦）。

        grace_seconds=None 用配置默认(self._grace_seconds,默认 1800s);
        grace_seconds=0 完全旁路 grace(手动立即归档,由 archive_now control 指令触发)。
        """
        if not self._configured or self._db is None or not self._nfs_base_dir:
            return 0
        effective_grace = self._grace_seconds if grace_seconds is None else grace_seconds
        archived = 0
        now = self._now()
        active_ids = self._active_job_ids()
        for job_dir, job_id in self._iter_job_dirs():
            if job_id in active_ids:
                # 活跃 Job：cycle 快照（不 prune、不注册 JobArtifact）
                try:
                    self.snapshot_active_job(job_id, job_dir, cycle=0)
                except Exception:
                    logger.exception("log_archiver_snapshot_unhandled job_id=%d", job_id)
                continue
            if self._db.is_job_archived(job_id):
                # 已归档但本地残留（如上轮 prune 失败）→ 清理本地
                self._prune_local(job_dir, job_id)
                continue
            if not self._is_aged(job_dir, now, effective_grace):
                continue
            try:
                if self.archive_one(job_id, job_dir):
                    archived += 1
            except Exception:
                self._bump("_archive_failed")
                logger.exception("log_archiver_archive_failed job_id=%d", job_id)
        self._refresh_pending_cache()
        return archived

    def spill_oldest(self, *, max_jobs: int = 1) -> int:
        """磁盘压力下提前归档最旧的已完成 Job（放宽 grace，仍不碰活跃）。

        由 LocalDiskMonitor 调用。返回本次溢出归档的 Job 数。
        """
        if not self._configured or self._db is None or not self._nfs_base_dir:
            return 0
        active_ids = self._active_job_ids()
        candidates: List[tuple] = []
        for job_dir, job_id in self._iter_job_dirs():
            if job_id in active_ids or self._db.is_job_archived(job_id):
                continue
            try:
                mtime = job_dir.stat().st_mtime
            except OSError:
                continue
            candidates.append((mtime, job_id, job_dir))
        candidates.sort(key=lambda t: t[0])  # 最旧优先
        spilled = 0
        for _mtime, job_id, job_dir in candidates[: max(1, int(max_jobs))]:
            try:
                if self.archive_one(job_id, job_dir, spilled=True):
                    # ADR-0025 2026-06-18: 归档与 prune 解耦，但 spill 的目的是释放本地空间
                    # → spill 场景下归档成功后显式 prune
                    self._prune_local(job_dir, job_id)
                    spilled += 1
            except Exception:
                self._bump("_archive_failed")
                logger.exception("log_archiver_spill_failed job_id=%d", job_id)
        self._refresh_pending_cache()
        return spilled

    # ------------------------------------------------------------------
    # 单 Job 归档
    # ------------------------------------------------------------------

    def archive_one(self, job_id: int, job_dir: Path, *, spilled: bool = False) -> Optional[str]:
        """归档单个 Job（per-job 在途互斥入口）。

        scan_once 线程与 spill_oldest 线程可能同时选中同一 job：用在途集合 claim +
        claim 下二次确认 is_job_archived，保证同一 job 既不被并发归档、也不被
        顺序重复归档（他线程刚归档完即释放）。返回 storage_uri；在途冲突或已被
        他线程归档 → 返回 None（安静跳过，不计失败）。任一步失败抛异常。
        """
        with self._claim_archive(job_id) as acquired:
            if not acquired:
                logger.debug("log_archiver_archive_skipped_inflight job_id=%d", job_id)
                return None
            # claim 下二次确认：另一线程可能刚归档完同一 job（顺序竞态），避免对
            # 已 prune 的目录重打 tar 抛错并误计 archive_failed。
            if self._db is not None and self._db.is_job_archived(job_id):
                return None
            return self._do_archive(job_id, job_dir, spilled=spilled)

    @contextmanager
    def _claim_archive(self, job_id: int):
        """per-job 在途互斥：yield True=获得归档权；False=另一线程正在归档此 job。"""
        with self._inflight_lock:
            acquired = job_id not in self._inflight
            if acquired:
                self._inflight.add(job_id)
        try:
            yield acquired
        finally:
            if acquired:
                with self._inflight_lock:
                    self._inflight.discard(job_id)

    @staticmethod
    def _copytree_safe(src: str, dst: str) -> None:
        """copytree 但对目录 copystat 失败安全忽略（NFS/CIFS EPERM）。

        shutil.copytree 即使 copy_function=copyfile，对目录自身仍调 copystat，
        在 NFS/CIFS 挂载上会 PermissionError。改为手动遍历 + copyfile，
        不碰任何元数据（与原 copyfile 策略一致）。
        """
        src_path = Path(src)
        dst_path = Path(dst)
        dst_path.mkdir(parents=True, exist_ok=True)
        for entry in src_path.rglob("*"):
            rel = entry.relative_to(src_path)
            target = dst_path / rel
            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            elif entry.is_file():
                shutil.copyfile(str(entry), str(target))

    def _do_archive(self, job_id: int, job_dir: Path, *, spilled: bool = False) -> str:
        """归档单个 Job：目录树直复制到 NFS → 同步注册 → 标记（不立即 prune）。

        ADR-0025 2026-06-18 修订：tar → 目录树直复制（_copytree_safe），
        保持文件可独立访问/下载。归档与 prune 解耦——复制成功后不立即 prune，
        由 spill_oldest / 本地盘阈值触发 prune（15.4 已有副本，prune 仅释放本地空间）。

        返回 NFS storage_uri（目录路径）。任一步失败抛异常（保留本地，下轮重试）。
        由 archive_one 在持有 per-job claim 后调用。
        """
        # 1. 复制目录树到 NFS（_copytree_safe 避免 copystat EPERM）
        date = self._now().strftime("%Y-%m-%d")
        nfs_dir = Path(self._nfs_base_dir) / "archives" / date / str(job_id)
        if nfs_dir.exists():
            shutil.rmtree(str(nfs_dir), ignore_errors=True)
        self._copytree_safe(str(job_dir), str(nfs_dir))
        if not nfs_dir.exists():
            raise IOError(f"nfs copytree failed: {nfs_dir}")

        # 2. 写 manifest（目录级，无单文件 sha256；统计目录大小）
        size_bytes = self._dir_size(nfs_dir)
        self._write_manifest(nfs_dir / "manifest.json", job_id, "", size_bytes, spilled)
        storage_uri = str(nfs_dir)

        # 3. 同步注册 JobArtifact —— 确认成功才标记（不丢可下载性）
        if not self._register_artifact(job_id, storage_uri, size_bytes, ""):
            raise RuntimeError(f"artifact registration failed job_id={job_id}")

        # 4. 标记（不立即 prune —— ADR-0025 2026-06-18 归档与 prune 解耦）
        self._db.mark_job_archived(
            job_id, nfs_uri=storage_uri, sha256="",
            size_bytes=size_bytes, spilled=spilled,
        )

        self._bump("_archived_total")
        if spilled:
            self._bump("_spilled_total")
        with self._metrics_lock:
            self._last_archive_at = self._now().isoformat()
        logger.info(
            "log_archiver_archived job_id=%d uri=%s size=%d spilled=%s",
            job_id, storage_uri, size_bytes, spilled,
        )
        return storage_uri

    def snapshot_active_job(self, job_id: int, job_dir: Path, *, cycle: int = 0) -> Optional[str]:
        """活跃 Job cycle 边界快照（ADR-0025 2026-06-18 新增）。

        在 patrol cycle 末尾（写入静默窗口）复制目录树到 NFS snapshots/，
        **不 prune 本地**（Job 还在跑）、**不注册 JobArtifact**（避免表被快照刷爆）。
        前置：cycle 边界触发时文件静态（step 函数已返回、子进程已收尾）。

        返回 NFS storage_uri（快照目录路径）；失败返回 None。
        """
        if not self._configured or not self._nfs_base_dir:
            return None
        date = self._now().strftime("%Y-%m-%d")
        snapshot_dir = Path(self._nfs_base_dir) / "snapshots" / date / str(job_id) / f"cycle_{cycle}"
        if snapshot_dir.exists():
            shutil.rmtree(str(snapshot_dir), ignore_errors=True)
        try:
            self._copytree_safe(str(job_dir), str(snapshot_dir))
        except Exception:
            logger.exception("log_archiver_snapshot_failed job_id=%d cycle=%d", job_id, cycle)
            return None
        # 兜底：copy 后 sleep 200ms 再 stat 对比 size，不一致标 partial
        import time as _time
        _time.sleep(0.2)
        partial = False
        try:
            before = self._dir_size(snapshot_dir)
            _time.sleep(0.1)
            after = self._dir_size(snapshot_dir)
            partial = before != after
        except Exception:
            partial = True
        if partial:
            (snapshot_dir / "PARTIAL").write_text("snapshot may be incomplete", encoding="utf-8")
            logger.warning("log_archiver_snapshot_partial job_id=%d cycle=%d", job_id, cycle)
        logger.info("log_archiver_snapshot job_id=%d cycle=%d uri=%s", job_id, cycle, str(snapshot_dir))
        return str(snapshot_dir)

    def _iter_job_dirs(self):
        """yield (job_dir Path, job_id int)；只认目录名为整数的 job 目录。"""
        if self._run_log_dir is None or not self._run_log_dir.exists():
            return
        for entry in self._run_log_dir.iterdir():
            if not entry.is_dir():
                continue
            try:
                job_id = int(entry.name)
            except ValueError:
                continue
            yield entry, job_id

    def _active_job_ids(self) -> set:
        try:
            return {int(j["job_id"]) for j in self._db.get_active_jobs()}
        except Exception:
            logger.exception("log_archiver_active_jobs_failed — 保守跳过本轮")
            # 拿不到活跃集合时返回一个 sentinel：宁可不归档也不误删活跃 job
            raise

    @staticmethod
    def _is_aged(job_dir: Path, now: datetime, grace_seconds: float) -> bool:
        try:
            mtime = job_dir.stat().st_mtime
        except OSError:
            return False
        return (now.timestamp() - mtime) >= grace_seconds

    def _prune_local(self, job_dir: Path, job_id: int) -> None:
        """删除本地 job 目录（不再有 tar，ADR-0025 2026-06-18 改为目录树直复制）。"""
        shutil.rmtree(str(job_dir), ignore_errors=True)

    @staticmethod
    def _dir_size(path: Path) -> int:
        """递归计算目录总大小（bytes）。"""
        total = 0
        for entry in path.rglob("*"):
            if entry.is_file():
                try:
                    total += entry.stat().st_size
                except OSError:
                    pass
        return total

    def _write_manifest(
        self, manifest_path: Path, job_id: int, sha256: str,
        size_bytes: int, spilled: bool,
    ) -> None:
        import json
        manifest = {
            "job_id": job_id,
            "host_id": self._host_id,
            "sha256": sha256,
            "size_bytes": size_bytes,
            "spilled": spilled,
            "archived_at": self._now().isoformat(),
        }
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    def _prune_local(self, job_dir: Path, job_id: int) -> None:
        shutil.rmtree(str(job_dir), ignore_errors=True)
        local_tar = self._run_log_dir / f"{job_id}.tar.gz"
        try:
            local_tar.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            logger.warning("log_archiver_local_tar_unlink_failed job_id=%d", job_id)

    def _register_artifact(
        self, job_id: int, storage_uri: str, size_bytes: int, sha256: str,
    ) -> bool:
        """同步 POST /agent/jobs/{job_id}/artifacts；2xx 视为成功（含幂等命中）。"""
        if self._session is None:
            return False
        url = f"{self._api_url}/api/v1/agent/jobs/{job_id}/artifacts"
        payload: Dict[str, Any] = {
            "storage_uri": storage_uri,
            "artifact_type": ARTIFACT_TYPE_RUN_LOG_BUNDLE,
            "size_bytes": int(size_bytes),
            "checksum": sha256,
            "source_category": "run_log",
        }
        headers: Dict[str, str] = {}
        if self._agent_secret:
            headers["X-Agent-Secret"] = self._agent_secret
        try:
            resp = self._session.post(
                url, json=payload, headers=headers, timeout=self._request_timeout,
            )
        except Exception as exc:
            logger.warning(
                "log_archiver_register_http_exception job_id=%d err=%s", job_id, exc,
            )
            return False
        if 200 <= resp.status_code < 300:
            return True
        logger.warning(
            "log_archiver_register_http_error job_id=%d status=%d body=%s",
            job_id, resp.status_code, getattr(resp, "text", "")[:200],
        )
        return False

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    def _bump(self, key: str, delta: int = 1) -> None:
        with self._metrics_lock:
            setattr(self, key, getattr(self, key) + delta)

    def count_pending_archive(self) -> int:
        """未归档的已完成 Job 数（粗略：非活跃 + 未归档的 job 目录）。"""
        if not self._configured or self._db is None:
            return 0
        try:
            active_ids = {int(j["job_id"]) for j in self._db.get_active_jobs()}
        except Exception:
            return 0
        pending = 0
        for _job_dir, job_id in self._iter_job_dirs():
            if job_id in active_ids or self._db.is_job_archived(job_id):
                continue
            pending += 1
        return pending

    def _refresh_pending_cache(self) -> None:
        """重算待归档数并写入缓存。仅在低频 scan_once / spill_oldest 末尾调用，
        心跳走 snapshot_metrics 读缓存（O(1)），不触发目录遍历。"""
        n = self.count_pending_archive()
        with self._metrics_lock:
            self._pending_archive_cached = n

    def snapshot_metrics(self) -> Dict[str, Any]:
        with self._metrics_lock:
            return {
                "archived_total": self._archived_total,
                "spilled_total": self._spilled_total,
                "archive_failed": self._archive_failed,
                "last_archive_at": self._last_archive_at,
                # 读缓存（每 scan/spill 周期刷新）；避免 20s 心跳遍历 job 目录
                "pending_archive": self._pending_archive_cached,
            }


def collect_archive_heartbeat_metrics() -> Optional[Dict[str, Any]]:
    """ADR-0025 Sprint 2: 汇总归档可观测指标供心跳上报（→ Host.extra['archive']）。

    合并 LogArchiver（archived_total / spilled_total / archive_failed /
    last_archive_at / pending_archive）与 LocalDiskMonitor（local_disk_usage_pct /
    spill_cycles / spill_threshold_pct）两个单例的快照。归档子系统未配置
    （nfs_base_dir 为空 / watcher 未启用）时返回 None → 心跳不含 archive 段，
    archive-status 端点 agent_metrics 为 null。供 main.py 注入 HeartbeatThread。
    """
    archiver = LogArchiver.instance()
    if not archiver.is_configured():
        return None
    metrics: Dict[str, Any] = dict(archiver.snapshot_metrics())
    from .local_disk_monitor import LocalDiskMonitor

    monitor = LocalDiskMonitor.instance()
    if monitor.is_configured():
        metrics.update(monitor.snapshot_metrics())
    return metrics


__all__ = [
    "LogArchiver",
    "ARTIFACT_TYPE_RUN_LOG_BUNDLE",
    "collect_archive_heartbeat_metrics",
]
