from enum import Enum


class JobStatus(str, Enum):
    PENDING      = "PENDING"
    RUNNING      = "RUNNING"
    COMPLETED    = "COMPLETED"
    FAILED       = "FAILED"
    ABORTED      = "ABORTED"
    UNKNOWN      = "UNKNOWN"


class PlanRunStatus(str, Enum):
    RUNNING         = "RUNNING"
    SUCCESS         = "SUCCESS"
    PARTIAL_SUCCESS = "PARTIAL_SUCCESS"
    FAILED          = "FAILED"
    # ADR-0026: schema-ready but NOT produced yet — they activate with the
    # admission-queue feature flag (P1 step 2+). Declared LAST to match the
    # migration's ALTER TYPE ADD VALUE append order (create_all/alembic parity).
    QUEUED          = "QUEUED"
    PRECHECK        = "PRECHECK"


#: 「通过」的 PlanRun 终态：**部分成功与完全成功同判**（跑到的设备都过了，只是有的
#: 没跑到）。这是本仓多处已经在用的语义——链触发
#: （`plan_chain_trigger.TRIGGERABLE_TERMINAL_STATUSES`）与种子验收
#: （`scripts/seed_and_smoke.PASSING_STATUSES`）都是这一组——故立为单一来源。
#:
#: 为什么需要它（#3101）：ADR-0048 v1.1 恢复 PARTIAL_SUCCESS 三态后，run 级「成功率」
#: 消费面只认 SUCCESS 而分母含 PARTIAL_SUCCESS，于是「设备有失败的 run」同时被算进
#: 分母、被排除在分子外 ⇒ 项目/脚本页的成功率无声下降，且与上面两处判据互相矛盾。
#: 这些消费面一律引用本常量，勿再写字面量 `'SUCCESS'`。
PASSING_PLAN_RUN_STATUSES: frozenset[str] = frozenset({
    PlanRunStatus.SUCCESS.value,
    PlanRunStatus.PARTIAL_SUCCESS.value,
})


class HostStatus(str, Enum):
    ONLINE   = "ONLINE"
    OFFLINE  = "OFFLINE"
    DEGRADED = "DEGRADED"


class DeviceStatus(str, Enum):
    ONLINE  = "ONLINE"
    OFFLINE = "OFFLINE"
    BUSY    = "BUSY"
    ERROR   = "ERROR"  # ADB 已发现设备但状态非 "device"（如 unauthorized），非物理离线


# ADR-0019: Device Lease enums

class LeaseType(str, Enum):
    JOB         = "JOB"
    SCRIPT      = "SCRIPT"
    MAINTENANCE = "MAINTENANCE"


class LeaseStatus(str, Enum):
    ACTIVE   = "ACTIVE"
    RELEASED = "RELEASED"
    EXPIRED  = "EXPIRED"


class EventState(str, Enum):
    """DeviceLogEvent lifecycle (ADR-0028 D1)."""

    DETECTED       = "DETECTED"
    PULL_FAILED    = "PULL_FAILED"
    LOCAL          = "LOCAL"
    UPLOAD_PENDING = "UPLOAD_PENDING"  # 方案 A：scan xls 引用后标记，等待 EventUploader 上送
    UPLOADING      = "UPLOADING"
    UPLOAD_FAILED  = "UPLOAD_FAILED"
    REMOTE         = "REMOTE"
    ARCHIVED       = "ARCHIVED"
    PRUNED         = "PRUNED"
