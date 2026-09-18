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
:data:`JOBS_FAMILY`。两者的分桶维度不同，混进同一份清单会让「按 run 对账」的读数失真。
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

#: 全部顶层族 = run 族 + job 族。测量/巡检的「族总量」口径用它，不要按 ``len(RUN_FAMILIES)+1`` 猜。
ALL_FAMILIES: tuple[str, ...] = RUN_FAMILIES + (JOBS_FAMILY,)

#: 承载 merge 报表的两族（E-1b 口径：同一份 ``merge/**`` 在 ``dedup/`` 与 ``jira/`` 各存
#: 一份，见提案 §1.1 事实 2）。这与 :data:`RUN_FAMILIES` 是**不同概念**——它是「同一份产物
#: 的双落点」，不是「run 生命周期下的全部族」；同样只此一份，读侧不得自带 ``("dedup", "jira")``。
MERGE_REPORT_FAMILIES: tuple[str, ...] = ("dedup", "jira")

#: 族下与主键同级、但**不是**主键的保留目录名（当前只有 ``devices/unassigned``，#2262）。
NON_KEY_ENTRIES: Mapping[str, frozenset[str]] = MappingProxyType(
    {"devices": frozenset({"unassigned"})}
)
