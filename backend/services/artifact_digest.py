"""Deployment artifact digest（ADR-0040 D1/D3）——控制面侧。

身份 = 现行整包载荷的规范化内容摘要，形式 ``sha256:<hex>``；输入集 = 部署
流程实际拥有并覆盖的文件集（``host_updater._iter_payload_files``——tarball 与
digest 共享同一枚举，契约漂移在共享点消除），条目为规范化序列
``(relpath, 可执行位, content sha256)``。

与 ``backend/agent/artifact_digest.py`` 为双侧镜像实现，字节级等价由
``backend/tests/services/test_artifact_digest.py`` 对照测试锁定（先例：
``script_catalog_version`` 双侧实现 + parity test）。

信任模型（ADR-0040 D2）：远端 current digest 由部署流程受控写入
``$INSTALL_DIR/agent/ARTIFACT_DIGEST``、经心跳上报；控制面 desired digest
现算 + 进程缓存（缓存键 = 输入集状态指纹），不落 host 表。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading

from backend.services.host_updater import _iter_payload_files

logger = logging.getLogger(__name__)

DIGEST_PREFIX = "sha256:"

_cache_lock = threading.Lock()
_cached_fingerprint: str | None = None
_cached_digest: str | None = None


def _cache_disabled() -> bool:
    # 与 script_catalog_version 同款约定：pytest 下默认关缓存，防跨测试污染。
    if os.getenv("TESTING") == "1" and "STP_ARTIFACT_DIGEST_CACHE" not in os.environ:
        return True
    return os.getenv("STP_ARTIFACT_DIGEST_CACHE", "").strip() == "0"


def invalidate_artifact_digest_cache() -> None:
    """Drop the in-process desired-digest cache."""
    global _cached_fingerprint, _cached_digest
    with _cache_lock:
        _cached_fingerprint = None
        _cached_digest = None


def collect_artifact_entries() -> list[tuple[str, bool, str]]:
    """规范化序列 ``(relpath, 可执行位, content sha256)``，按 relpath 排序。"""
    entries: list[tuple[str, bool, str]] = []
    for full_path, arcname in _iter_payload_files():
        st = os.stat(full_path)
        h = hashlib.sha256()
        with open(full_path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        entries.append((arcname.replace(os.sep, "/"), bool(st.st_mode & 0o111), h.hexdigest()))
    entries.sort()
    return entries


def digest_entries(entries: list[tuple[str, bool, str]]) -> str:
    """Digest a normalized ``(relpath, exec, sha256)`` sequence → ``sha256:<hex>``.

    纯函数；与 Agent 侧镜像实现必须字节级等价（parity test 锁定）。
    """
    payload = json.dumps(
        [[relpath, is_exec, sha] for relpath, is_exec, sha in entries],
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return DIGEST_PREFIX + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _input_fingerprint() -> str:
    """输入集状态指纹（stat-only，不读内容）——进程缓存的缓存键。"""
    parts = []
    for full_path, arcname in _iter_payload_files():
        st = os.stat(full_path)
        parts.append((arcname.replace(os.sep, "/"), st.st_size, st.st_mtime_ns, st.st_mode & 0o111))
    parts.sort()
    return hashlib.sha256(repr(parts).encode("utf-8")).hexdigest()


def compute_desired_artifact_digest() -> str:
    """Desired digest：现算 + 进程缓存（缓存键 = 输入集状态指纹）。"""
    global _cached_fingerprint, _cached_digest
    if _cache_disabled():
        return digest_entries(collect_artifact_entries())
    fingerprint = _input_fingerprint()
    with _cache_lock:
        if _cached_digest is not None and _cached_fingerprint == fingerprint:
            return _cached_digest
    digest = digest_entries(collect_artifact_entries())
    with _cache_lock:
        _cached_fingerprint = fingerprint
        _cached_digest = digest
    return digest


def evaluate_convergence(host, *, force: bool = False) -> tuple[str, dict | None]:
    """D3 no-op gate：desired == current → 返回 no-op 结果；否则返回 None。

    返回 ``(desired_digest, converged_result_or_None)``。``converged_result``
    与 ``execute_hot_update`` 的返回同构（``ok=True, converged=True,
    reason="digest-matched"``），调用方按同一记录语义留痕（D5）。
    ``current`` 来自心跳上报的 ``host.agent_artifact_digest``——该列非空才参与
    判定（新协议下从未部署过的主机 current 为空 → 全量部署并由远端脚本写入
    digest，一次迁移后进入 no-op 稳态）。``force=True`` 显式跳过判定（审计
    留痕由调用方走同一 finalize 通道，outcome=forced）。
    """
    desired = compute_desired_artifact_digest()
    if force:
        return desired, None
    current = (getattr(host, "agent_artifact_digest", None) or "").strip()
    if current and current == desired:
        logger.info(
            "hot_update_no_op host=%s digest=%s", getattr(host, "id", "?"), desired,
        )
        return desired, {
            "ok": True,
            "converged": True,
            "reason": "digest-matched",
            "message": f"converged: artifact digest matched ({desired})",
            "duration_ms": 0,
            "deps_refreshed": False,
            "env_keys_synced": [],
            "env_paths_missing": {},
            "code_version": "",
            "priv_mode": "unknown",
            "artifact_digest": desired,
            "phases": {"digest": 0},
        }
    return desired, None
