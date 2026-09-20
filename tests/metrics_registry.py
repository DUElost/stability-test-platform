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


import ast
import re

# 本仓**自己生产**的 node-exporter textfile 指标生产者。这些指标不来自 backend
# 注册表（独立进程），但也不能变成告警面的豁免口子——名字从生产者源码里静态
# 提取：生产者删掉一个名字，用它的告警立刻退化为「未知指标」而红。
_TEXTFILE_PRODUCERS = (
    "tools/dev/script_guard_probe.py",
    # #2632：PG 日志「猜 schema」指纹采集（控制面宿主 timer 跑，写 textfile 指标）
    "tools/dev/pg_error_guard.py",
    # #2881：skill 用量（HOLLOW）巡检（站点 timer 跑，写 textfile 指标）
    "tools/dev/skill_usage_probe.py",
)
_TEXTFILE_HELP_ATTR = "_METRIC_HELP"
_METRIC_NAME_RE = re.compile(r"[a-zA-Z_:][a-zA-Z0-9_:]*")


def textfile_metrics_for(rel: str, root: Path = ROOT) -> set[str]:
    """**单个** textfile 生产者源码里声明的指标名（#2788：供「站点装得到吗」按单元过滤）。

    与 ``textfile_metric_index`` 同一套结构校验（HELP dict 的 key 必须是合法指标名），
    只是把范围收敛到一个文件——按安装清单过滤时必须知道「哪个名字来自哪个生产者」。
    """
    path = root / rel
    if not path.is_file():
        return set()
    names: set[str] = set()
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == _TEXTFILE_HELP_ATTR:
                if not isinstance(node.value, ast.Dict):
                    continue
                for key in node.value.keys:
                    if not (isinstance(key, ast.Constant) and isinstance(key.value, str)):
                        continue
                    name = key.value
                    if not _METRIC_NAME_RE.fullmatch(name):
                        # 结构变了（HELP 文案被当成 key 之类）就明说，不把垃圾名
                        # 塞进 index——那会把「未知指标」伪装成「已知」而假绿。
                        raise AssertionError(
                            f"{rel}: {name!r} 不是合法指标名——检查 {_TEXTFILE_HELP_ATTR} 的结构")
                    names.add(name)
    return names


def textfile_metric_index(root: Path = ROOT) -> dict[str, set[str]]:
    """提取 textfile 生产者声明的指标名（标签集恒为空：本仓产物无标签）。"""
    index: dict[str, set[str]] = {}
    for rel in _TEXTFILE_PRODUCERS:
        for name in textfile_metrics_for(rel, root):
            index.setdefault(name, set())
    return index


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
    external = textfile_metric_index()
    assert external, (
        "textfile 生产者清单非空却没解析出任何指标名——"
        "生产者里 _METRIC_HELP 的结构变了，请更新 textfile_metric_index()")
    for name, labels in external.items():
        index.setdefault(name, set()).update(labels)
    return index, non_queryable
