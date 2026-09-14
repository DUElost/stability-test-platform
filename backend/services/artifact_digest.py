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

from dataclasses import dataclass

import hashlib
import json
import logging
import os
import threading

from backend.services.host_updater import _iter_payload_files

logger = logging.getLogger(__name__)

DIGEST_PREFIX = "sha256:"

ARTIFACT_KIND_FULL = "full"        # P1 全集身份（evaluate_convergence 判定用）
ARTIFACT_KIND_CODE = "code"        # P2：全集 − resources/**
ARTIFACT_KIND_RESOURCES = "resources"  # P2：全集 ∩ resources/**（除 mtbf/）

# kind 分区（#1963）：code ∪ resources == full、互斥——契约测试守护。
_RESOURCES_PREFIX = "resources/"

_cache_lock = threading.Lock()
# 缓存按 kind 分桶（full / code / resources 各自独立指纹）
_cache: dict[str, tuple[str | None, str | None]] = {}


def _cache_disabled() -> bool:
    # 与 script_catalog_version 同款约定：pytest 下默认关缓存，防跨测试污染。
    if os.getenv("TESTING") == "1" and "STP_ARTIFACT_DIGEST_CACHE" not in os.environ:
        return True
    return os.getenv("STP_ARTIFACT_DIGEST_CACHE", "").strip() == "0"


def invalidate_artifact_digest_cache() -> None:
    """Drop the in-process desired-digest cache（全部 kind）。"""
    with _cache_lock:
        _cache.clear()


def collect_artifact_entries(kind: str = ARTIFACT_KIND_FULL) -> list[tuple[str, bool, str]]:
    """规范化序列 ``(relpath, 可执行位, content sha256)``，按 relpath 排序。

    kind 分区（#1963，ADR-0040 §5-3 P2）：``full`` = 共享载荷枚举全集（P1
    语义不变）；``code`` = 全集 − ``resources/**``；``resources`` = 全集 ∩
    ``resources/**``（除 ``resources/mtbf/``，枚举层已排除）。code ∪
    resources == full且互斥，契约测试守护。
    """
    entries: list[tuple[str, bool, str]] = []
    for full_path, arcname in _iter_payload_files(kind):
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


def _input_fingerprint(kind: str = ARTIFACT_KIND_FULL) -> str:
    """输入集状态指纹（stat-only，不读内容）——进程缓存的缓存键。"""
    parts = []
    for full_path, arcname in _iter_payload_files(kind):
        st = os.stat(full_path)
        parts.append((arcname.replace(os.sep, "/"), st.st_size, st.st_mtime_ns, st.st_mode & 0o111))
    parts.sort()
    return hashlib.sha256(repr(parts).encode("utf-8")).hexdigest()


def compute_desired_artifact_digest(kind: str = ARTIFACT_KIND_FULL) -> str:
    """Desired digest：现算 + 进程缓存（缓存键 = 输入集状态指纹，按 kind 分桶）。"""
    if _cache_disabled():
        return digest_entries(collect_artifact_entries(kind))
    fingerprint = _input_fingerprint(kind)
    with _cache_lock:
        hit = _cache.get(kind)
        if hit is not None and hit[0] == fingerprint:
            return hit[1]
    digest = digest_entries(collect_artifact_entries(kind))
    with _cache_lock:
        _cache[kind] = (fingerprint, digest)
    return digest


def compute_desired_resources_digest() -> str:
    """P2（#1963）：host-resources 身份（resources/ 除 mtbf/）——分层流载体。"""
    return compute_desired_artifact_digest(ARTIFACT_KIND_RESOURCES)


@dataclass(frozen=True)
class ConvergencePlan:
    """D3 两层收敛判定结果（P2-B，#1975）。

    ``code_drift``：code 身份 desired != 心跳上报的 current（含 current 缺失）。
    ``resources_drift``：同判定于 host-resources 身份；**空集守卫**——控制面
    resources 分区为空（大件不入 git，CI/新树恒空）时恒 False 且
    ``resources_skipped_empty=True``：绝不下发空载荷把主机收敛到空。
    ``converged``：两层均无事可做；``no_op_result`` 与 execute_hot_update
    返回同构，调用方按同一记录语义留痕（D5）。
    """

    code_digest: str
    code_drift: bool
    resources_digest: str
    resources_drift: bool
    resources_skipped_empty: bool
    converged: bool
    no_op_result: dict | None


def plan_convergence(host, *, force: bool = False) -> ConvergencePlan:
    """D3 no-op gate（两层）：desired == current 的层跳过，否则标 drift。

    ``current`` 来自心跳上报的显式列——非空才参与判定（新协议下从未部署过
    的主机 current 为空 → 全量部署并由远端脚本写入 digest，一次迁移后进入
    no-op 稳态；#1943 修复后 current 随心跳及时刷新）。``force=True`` 显式
    跳过判定（审计留痕由调用方走同一 finalize 通道，outcome=forced）。
    """
    code_desired = compute_desired_artifact_digest(kind=ARTIFACT_KIND_CODE)
    resources_desired = compute_desired_artifact_digest(kind=ARTIFACT_KIND_RESOURCES)
    # 空集守卫：空输入集的 digest 恒定，但「期望态为空」不代表主机应被清空
    # ——resources 大件带外布放（gitignore），控制面不可见即不下发。
    resources_skipped_empty = resources_desired == digest_entries([])

    code_current = (getattr(host, "agent_artifact_digest", None) or "").strip()
    code_drift = force or (not code_current) or code_current != code_desired

    resources_current = (getattr(host, "agent_resources_digest", None) or "").strip()
    resources_drift = (
        force
        or (not resources_skipped_empty)
        and ((not resources_current) or resources_current != resources_desired)
    )

    converged = not code_drift and not resources_drift
    no_op_result: dict | None = None
    if converged:
        no_op_result = {
            "ok": True,
            "converged": True,
            "reason": "digest-matched",
            "message": (
                f"converged: artifact digests matched ({code_desired} / "
                f"{resources_desired})"
            ),
            "duration_ms": 0,
            "deps_refreshed": False,
            "env_keys_synced": [],
            "env_paths_missing": {},
            "code_version": "",
            "priv_mode": "unknown",
            "artifact_digest": code_desired,
            "resources_digest": resources_desired,
            "phases": {"digest": 0},
        }
        logger.info(
            "hot_update_no_op host=%s code=%s resources=%s",
            getattr(host, "id", "?"), code_desired, resources_desired,
        )
    return ConvergencePlan(
        code_digest=code_desired,
        code_drift=code_drift,
        resources_digest=resources_desired,
        resources_drift=resources_drift,
        resources_skipped_empty=resources_skipped_empty,
        converged=converged,
        no_op_result=no_op_result,
    )
