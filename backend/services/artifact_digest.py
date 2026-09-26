"""Deployment artifact digest（ADR-0040 D1/D3）——控制面侧。

身份 = 现行整包载荷的规范化内容摘要，形式 ``sha256:<hex>``；输入集 = 部署
流程实际拥有并覆盖的文件集（``host_updater._iter_payload_files``——tarball 与
digest 共享同一枚举，契约漂移在共享点消除），条目为规范化序列
``(relpath, 可执行位, content sha256)``。

**算法不是本模块的实现**：``digest_entries`` / kind 词表 import 自契约包
``backend/agent/contracts/artifact_digest.py``（ADR-0054 D1，第 3 步起唯一实现）；
本模块只保留控制面自己的输入集枚举（``_iter_payload_files``，按 ADR-0054 §5
第 3 步留在 services）+ 进程缓存 + 收敛判定。

信任模型（ADR-0040 D2）：远端 current digest 由部署流程受控写入
``$INSTALL_DIR/agent/ARTIFACT_DIGEST``、经心跳上报；控制面 desired digest
现算 + 进程缓存（缓存键 = 输入集状态指纹），不落 host 表。

收敛判定只有 ``agent-code`` 一层（ADR-0040 D8 R1）：``host-resources`` 层已退役——
其内容（flashtool / AIMonkey）改由 ADR-0051 D7 工具包承接、消费时按包 sha 实测核验，
控制面不再判定、不再下发该层；R4 起 Agent 不再上报 ``agent_resources_digest``、
控制面也不再写该列（列保留一个版本窗口，删列另起迁移）。
"""

from __future__ import annotations

from dataclasses import dataclass

import hashlib
import logging
import os
import threading

from backend.agent.contracts.artifact_digest import (
    ARTIFACT_KIND_CODE,
    digest_entries,
)
from backend.services.host_updater import _iter_payload_files

logger = logging.getLogger(__name__)

_cache_lock = threading.Lock()
# 缓存按 kind 分桶（ADR-0040 D8 R4 起只剩 code 一桶；分桶结构保留，缓存形状不随面数变化）
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


def collect_artifact_entries(kind: str = ARTIFACT_KIND_CODE) -> list[tuple[str, bool, str]]:
    """规范化序列 ``(relpath, 可执行位, content sha256)``，按 relpath 排序。

    只有 ``code`` 一个分区（ADR-0040 D8 R4 退役 ``full`` / ``resources``），与契约
    ``collect_artifact_entries`` 同口径——枚举对拍由契约测试守护。
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


def _input_fingerprint(kind: str = ARTIFACT_KIND_CODE) -> str:
    """输入集状态指纹（stat-only，不读内容）——进程缓存的缓存键。"""
    parts = []
    for full_path, arcname in _iter_payload_files(kind):
        st = os.stat(full_path)
        parts.append((arcname.replace(os.sep, "/"), st.st_size, st.st_mtime_ns, st.st_mode & 0o111))
    parts.sort()
    return hashlib.sha256(repr(parts).encode("utf-8")).hexdigest()


def compute_desired_artifact_digest(kind: str = ARTIFACT_KIND_CODE) -> str:
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


@dataclass(frozen=True)
class ConvergencePlan:
    """D3 收敛判定结果——单层 ``agent-code``（ADR-0040 D8 R1 起）。

    ``code_drift``：code 身份 desired != 心跳上报的 current（含 current 缺失）。
    ``converged``：无事可做；``no_op_result`` 与 execute_hot_update 返回同构，
    调用方按同一记录语义留痕（D5）。
    """

    code_digest: str
    code_drift: bool
    converged: bool
    no_op_result: dict | None


def plan_convergence(host, *, force: bool = False) -> ConvergencePlan:
    """D3 no-op gate：desired == current 即跳过，否则标 drift。

    ``current`` 来自心跳上报的显式列——非空才参与判定（新协议下从未部署过
    的主机 current 为空 → 全量部署并由远端脚本写入 digest，一次迁移后进入
    no-op 稳态；#1943 修复后 current 随心跳及时刷新）。``force=True`` 显式
    跳过判定、强制 ``agent-code`` 全量（审计留痕由调用方走同一 finalize 通道）。

    ADR-0040 D8 R1：``host-resources`` 层不再参与——主机的 ``agent_resources_digest``
    与控制面树的 ``resources/`` 分区都不读，``force`` 也只作用于 ``agent-code``。
    """
    code_desired = compute_desired_artifact_digest(kind=ARTIFACT_KIND_CODE)

    code_current = (getattr(host, "agent_artifact_digest", None) or "").strip()
    code_drift = force or (not code_current) or code_current != code_desired

    converged = not code_drift
    no_op_result: dict | None = None
    if converged:
        no_op_result = {
            "ok": True,
            "converged": True,
            "reason": "digest-matched",
            "message": f"converged: artifact digest matched ({code_desired})",
            "duration_ms": 0,
            "deps_refreshed": False,
            "env_keys_synced": [],
            "env_paths_missing": {},
            "env_keys_retired": [],
            "code_version": "",
            "priv_mode": "unknown",
            "artifact_digest": code_desired,
            "phases": {"digest": 0},
        }
        logger.info(
            "hot_update_no_op host=%s code=%s", getattr(host, "id", "?"), code_desired,
        )
    return ConvergencePlan(
        code_digest=code_desired,
        code_drift=code_drift,
        converged=converged,
        no_op_result=no_op_result,
    )
