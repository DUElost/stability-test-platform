"""中心存储顶层目录族的单一来源（#2188）。

**为什么要收成一份**：同一个事实——「中心存储根下有哪些族、各族按什么主键分目录」——
原先散在三处，而且三份互不相同：

- retention purge 桶 ``("devices", "dedup", "jira", "_meta")``（``backend/scheduler/cron_scheduler.py``）；
- 只读基线采集 ``("devices", "dedup", "jira", "jobs")``（``backend/scripts/measure_center_storage.py``）；
- 路径契约表（``docs/design/2026-scan-upload-merge-contract.md``「中心存储路径」节，缺 ``_meta``）。

后果不是「数字不好看」，而是**安全网缺口**：``_meta/{run_id}/``（#2188 D 步的上传清单分片）
被 retention 清、却不在测量族里，该族的残留对 E-2 运维对账完全不可见——commit 自述的
「漏桶=E-2 必挂」因此不成立。反过来 ``jobs`` 在测量族里按 run 主键分解，把 ``jobs/{job_id}``
报成了 run，与「应已清理 run 清单」对账时会产出既非漏删也非干净的第三种读数。

**新增一族时只改这里**：run 主键的产物族进 :data:`RUN_FAMILIES`，job 主键的进
:data:`JOBS_FAMILY`，按整条链留存的进 :data:`CHAIN_FAMILIES`（B8 G6 的 ``evidence/``）。
四者的分桶维度不同：混进同一份清单会让「按 run 对账」的读数失真，链族与派生族还会被
按 run 的无条件清理器误删。
判别力由 ``tests/test_center_storage_families_single_source.py`` 守（消费方不得再自带字面量清单）。

**为什么在 ``backend/`` 包根而不是 ``backend/core/``**：``backend/core/__init__.py`` 会
eager ``from .database import engine``——缺 ``DATABASE_URL`` 时**整个包拒绝导入**。把这份清单
放进 ``backend.core`` 会立刻绑住 :mod:`backend.scripts.measure_center_storage`（只读基线采集，
文档承诺「不查库、可在只挂 NFS 的机器上跑」），让一个 ``--center-root`` 就够用的诊断工具
变成必须先有数据库配置。本模块只依赖标准库，不放任何业务逻辑。
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

#: run 主键族：``{root}/<family>/<plan_run_id>/``，与 run 同生命周期（retention 整批删）。
RUN_FAMILIES: tuple[str, ...] = ("devices", "dedup", "jira", "_meta")

#: job 主键族：``{root}/jobs/<job_id>/``；唯一索引是 StepTrace/JobArtifact 行（#2031），
#: 因此它**不进** :data:`RUN_FAMILIES`——按 run 分解时它是假 run。
JOBS_FAMILY: str = "jobs"

#: 链主键族：``{root}/evidence/<主PlanRun>_<采集目录>/<SN>/<事件目录>/``。
#: B8 G6（ADR-0062 D9 第 6 条、D11）：证据区按整条链留存，**不得**进入按 run
#: 清理的 :data:`RUN_FAMILIES`；清理前须单独取得 Owner 确认（D11 表格）。
EVIDENCE_FAMILY: str = "evidence"

#: 全部链主键族。与 RUN_FAMILIES / JOBS_FAMILY 并列，仅用于登记与测量口径。
CHAIN_FAMILIES: tuple[str, ...] = (EVIDENCE_FAMILY,)

#: 派生族：``{root}/compat_reruns/{plan_run_id}/{platform}/r{compat_rerun_id}/``。
#: B8 G8 受控兼容补跑（ADR-0062 D15 第 5–7 条）在日志根上产出的**独立**产物：目录名
#: 里带 run，但它不是 run 生命周期的一部分——一次补跑绑定「实际 PlanRun × 平台 × 一笔
#: 登记」，回收必须逐笔判定（终态、无未结重试、两侧都超期、无 I4 保护、无链引用），
#: 不能跟着 run 一起无条件删。
#:
#: **红线**：``compat_reruns`` **不得**加入 :data:`RUN_FAMILIES`——按 run 的无条件清理
#: 只遍历 ``RUN_FAMILIES``（``backend/scheduler/cron_scheduler.py``），加进去等于让
#: 一笔在跑或仍被引用的补跑产物随 run 消失。判别守卫见
#: ``backend/tests/scheduler/test_b8_4045_derived_family_survives_run_purge.py``。
#:
#: **当前只计量、不回收**：回收判据的到期锚点在 #4045 复核中判为待裁决（生产
#: ``PLAN_RUN_RETENTION_DAYS=36500`` 会让「以 run 级保留期为锚」的候选恒空；
#: ``compat_rerun.plan_run_id`` 无外键，run 行被清理后按 run 侧取证又会永不回收）。
#: 在 Owner 就 #4045 评论 6978340866 的 (a)/(b)/(c) 表态前，本族不得接入任何自动窗口。
COMPAT_RERUNS_FAMILY: str = "compat_reruns"

#: 全部派生族。与 run/job/链族并列，仅用于登记与测量口径；回收另按逐笔判据。
DERIVED_FAMILIES: tuple[str, ...] = (COMPAT_RERUNS_FAMILY,)

#: 全部顶层族 = run 族 + job 族 + 链族 + 派生族。测量/巡检的「族总量」口径用它，
#: 不要按 ``len(RUN_FAMILIES)+1`` 猜。
ALL_FAMILIES: tuple[str, ...] = (
    RUN_FAMILIES + (JOBS_FAMILY,) + CHAIN_FAMILIES + DERIVED_FAMILIES
)

#: 族到根的唯一归属表（B8 G0-a）。``evidence/`` 与 ``compat_reruns/`` 归日志根，
#: 与 ``devices/``、``jira/`` 并列（15.4 挂载面）。
FAMILY_STORAGE_ROOTS: Mapping[str, str] = MappingProxyType(
    {
        **dict.fromkeys(RUN_FAMILIES, "log"),
        JOBS_FAMILY: "control_plane",
        EVIDENCE_FAMILY: "log",
        COMPAT_RERUNS_FAMILY: "log",
    }
)


def storage_root_for_family(family: str, *, log_root: str, control_plane_root: str) -> str:
    """选择已解析的根；旧日志根不在写入/清理的候选中。未知族直接拒绝。"""
    return {"log": log_root, "control_plane": control_plane_root}[FAMILY_STORAGE_ROOTS[family]]

#: 承载 merge 报表的两族（E-1b 口径：同一份 ``merge/**`` 在 ``dedup/`` 与 ``jira/`` 各存
#: 一份，见提案 §1.1 事实 2）。这与 :data:`RUN_FAMILIES` 是**不同概念**——它是「同一份产物
#: 的双落点」，不是「run 生命周期下的全部族」；同样只此一份，读侧不得自带 ``("dedup", "jira")``。
MERGE_REPORT_FAMILIES: tuple[str, ...] = ("dedup", "jira")

#: 族下与主键同级、但**不是**主键的保留目录名（当前只有 ``devices/unassigned``，#2262）。
NON_KEY_ENTRIES: Mapping[str, frozenset[str]] = MappingProxyType(
    {"devices": frozenset({"unassigned"})}
)
