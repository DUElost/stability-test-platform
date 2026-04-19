"""WatcherPolicy — Device Log Watcher 运行策略。

策略来源优先级（高 → 低）：
    1. WorkflowDefinition.watcher_policy (DB, 运维可配)
    2. Agent 环境变量 (WATCHER_*)
    3. DEFAULT_POLICY (代码内兜底)

使用：
    policy = WatcherPolicy.from_job(job_payload)
    manager.start(serial, job_id, log_dir, policy)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, List, Optional


class OnUnavailableAction(str, Enum):
    """Watcher 能力不可用时的 Job 处理策略。"""

    FAIL = "fail"          # Job 直接 FAILED（稳态后再考虑启用）
    DEGRADED = "degraded"  # 继续执行但标记 watcher_capability=unavailable（首发默认）
    SKIP = "skip"          # 完全跳过 watcher（仅测试/特殊场景允许）


# 日志分类 → 设备路径列表（Agent 侧默认）
DEFAULT_PATHS: Dict[str, List[str]] = {
    "ANR":        ["/data/anr"],
    "AEE":        ["/data/aee_exp"],
    "VENDOR_AEE": ["/data/vendor/aee_exp"],
    "MOBILELOG":  ["/data/debuglogger/mobilelog"],
}

# 需要 adb root 才能访问的分类（探测阶段使用）
ROOT_REQUIRED_CATEGORIES: set[str] = {"ANR", "AEE", "VENDOR_AEE"}


@dataclass
class WatcherPolicy:
    """Watcher 运行策略（不可变配置）。"""

    # --- 能力与分类 ---
    # Watcher 职责边界（不要外扩）：手机侧"报错文件探测器"
    # 采集对象限定在 4 个目录下新产生的 crash/trace 文件；
    # logcat / console 实时输出 不是 watcher 的职责。
    paths: Dict[str, List[str]] = field(default_factory=lambda: dict(DEFAULT_PATHS))
    required_categories: List[str] = field(
        default_factory=lambda: ["ANR", "AEE"]  # 最低保证
    )
    # 首发默认 DEGRADED：第一次上线时，能力探测失败不应导致 Job 直接 FAILED，
    # 而是标记 watcher_capability=unavailable 让 Job 继续执行，便于运维观测真实覆盖率。
    # 稳态后（Watcher 在 90% 以上机型可用、误报率 < 1%）再考虑切到 FAIL。
    on_unavailable: OnUnavailableAction = OnUnavailableAction.DEGRADED

    # --- 批处理参数 ---
    batch_interval_seconds: float = 5.0
    batch_max_events: int = 20
    event_queue_maxsize: int = 1000          # per-device 队列上限
    pull_max_file_mb: int = 500              # 单文件上限（超过只记元数据）
    nfs_quota_mb: int = 2048                 # 单 Job NFS 写入配额

    # --- 源策略 ---
    inotifyd_reconnect_delay: float = 5.0
    polling_interval_seconds: float = 2.0
    probe_timeout_seconds: float = 5.0
    # 注意：此处故意不提供 logcat 源相关默认。
    # Watcher 只做"文件级报错信号"；logcat 作为 Pipeline action 独立消费。

    # --- 退出协议 ---
    # JobSession.__exit__ 同步等待 watcher drain 的最长时间。
    # 超时后锁立即释放，outbox 剩余条目由 OutboxDrainer 异步补发。
    exit_drain_timeout_seconds: float = 5.0

    # --- 可观测性 ---
    emit_via_socketio: bool = True
    emit_via_http_outbox: bool = True
    log_level: str = "INFO"

    # ------------------------------------------------------------------
    # 构造
    # ------------------------------------------------------------------

    @classmethod
    def from_job(cls, job_payload: Dict[str, Any]) -> "WatcherPolicy":
        """从 Agent 拉取的 job 数据构造 policy。

        期望 job_payload 结构:
            {
              "id": 123,
              "watcher_policy": {                  # 可选, 来自 WorkflowDefinition
                  "on_unavailable": "degraded",
                  "required_categories": ["ANR"],
                  "paths": {"ANR": ["/data/anr"]},
                  "nfs_quota_mb": 4096
              },
              ...
            }

        若 watcher_policy 缺失则走 env / DEFAULT。
        未知字段忽略（前向兼容）。
        """
        override: Dict[str, Any] = job_payload.get("watcher_policy") or {}
        env_override = _load_env_overrides()

        policy = cls(**_load_default_from_env())
        # 合并顺序: DEFAULT(env) → override
        for key, value in {**env_override, **override}.items():
            if not hasattr(policy, key):
                continue
            if key == "on_unavailable" and isinstance(value, str):
                value = OnUnavailableAction(value)
            if key == "paths" and isinstance(value, dict):
                # 用户只配了一部分分类时, 其他分类保留默认
                merged = dict(policy.paths)
                merged.update(value)
                value = merged
            setattr(policy, key, value)
        return policy

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["on_unavailable"] = self.on_unavailable.value
        return d


# ----------------------------------------------------------------------
# 内部辅助
# ----------------------------------------------------------------------

def _load_default_from_env() -> Dict[str, Any]:
    """从 Agent 环境变量加载默认值。返回可直接传给 WatcherPolicy() 的 kwargs。"""
    kwargs: Dict[str, Any] = {}

    if v := os.getenv("WATCHER_ON_UNAVAILABLE"):
        try:
            kwargs["on_unavailable"] = OnUnavailableAction(v)
        except ValueError:
            pass  # 非法值忽略, 走 DEFAULT

    if v := os.getenv("WATCHER_BATCH_INTERVAL_SECONDS"):
        try:
            kwargs["batch_interval_seconds"] = float(v)
        except ValueError:
            pass

    if v := os.getenv("WATCHER_NFS_QUOTA_MB"):
        try:
            kwargs["nfs_quota_mb"] = int(v)
        except ValueError:
            pass

    if v := os.getenv("WATCHER_LOG_LEVEL"):
        kwargs["log_level"] = v

    if v := os.getenv("WATCHER_EXIT_DRAIN_TIMEOUT_SECONDS"):
        try:
            kwargs["exit_drain_timeout_seconds"] = float(v)
        except ValueError:
            pass

    return kwargs


def _load_env_overrides() -> Dict[str, Any]:
    """保留接口：未来支持更多 env 覆盖（目前与 default 合并）。"""
    return {}
