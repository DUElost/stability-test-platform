"""指标注册表索引助手（告警规则 / Grafana 仪表板契约测试共用，#1257/#1258）。

放在独立模块避免两处重复维护同一段注册表私有结构适配；调用方（root tests）
通过 ``python -m pytest`` 从仓库根运行时可直接 ``from tests.metrics_registry``。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# backend.core.database 在导入期解析 DATABASE_URL（root tests 无 conftest 注入）。
# 先例：tests/test_leader_election.py；CI 已设 dummy URL，setdefault 不覆盖。
os.environ.setdefault("DATABASE_URL", "sqlite:///./test-metrics-registry.db")

ROOT = Path(__file__).resolve().parents[1]


def metric_registry_index() -> tuple[dict[str, set[str]], set[str]]:
    """(样本名→标签名集合, 不可查询的基础名集合)。

    来源 = backend 指标注册表（无 DB/网络副作用）。带标签的指标在未实例化
    子序列时 ``collect()`` 不产出样本，故用注册表的 collector→names 映射 +
    ``_labelnames``；Histogram/Summary 的**基础名**被 client 一并列出但不是
    可查询序列（裸用会静默永不触发），单独收集供显式拒绝。
    """
    sys.path.insert(0, str(ROOT))
    import backend.core.metrics  # noqa: F401  导入即注册
    from prometheus_client import REGISTRY

    collector_to_names = getattr(REGISTRY, "_collector_to_names", None)
    assert collector_to_names, "prometheus_client 注册表私有结构变化，请更新本助手"
    index: dict[str, set[str]] = {}
    for collector, names in collector_to_names.items():
        label_names = set(getattr(collector, "_labelnames", ()) or ())
        for name in names:
            # 直方图 _bucket 额外允许 le（client 不把它列进 _labelnames）
            extra = {"le"} if name.endswith("_bucket") else set()
            index.setdefault(name, set()).update(label_names | extra)

    non_queryable = {
        base
        for base in (getattr(c, "_name", "") for c in collector_to_names)
        if base
        and any(f"{base}{suffix}" in index for suffix in ("_bucket", "_sum", "_count"))
    }
    assert index, "指标注册表为空——backend.core.metrics 未注册任何指标"
    return index, non_queryable
