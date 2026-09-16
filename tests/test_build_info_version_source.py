"""#2341 — 控制面 build_info 的版本真值来自部署清单，不再是源码字面量。

判据两层：

1. **结构**（不依赖运行实例）：`backend/main.py` 不再把平台版本写成字面量，且值确实
   取自 `release_manifest.resolve_build_info()`；`backend/__init__.py` 不再声明
   `__version__`（幽灵常量：自 2026-05-05 引入起从未更新，却让面板恒显示 2.0.0）。
2. **行为**：给定合成的 `release-manifest.json` → 值取自清单；缺失/损坏 → 显式回落
   `unknown` 且**不回落任何具体版本号**（回落成 "2.0.0" 之类会再造一个假信息）。

纯离线：只读源码 + 临时文件，不起容器、不连库。
"""
from __future__ import annotations

import ast
import json
import os
from pathlib import Path

# backend.core 的包级导入会解析 DATABASE_URL（root tests 无 conftest 注入）。
# 先例：tests/metrics_registry.py、tests/test_settings_scheduler.py；CI 已设 dummy URL，
# setdefault 不覆盖。
os.environ.setdefault("DATABASE_URL", "sqlite:///./test-build-info-version-source.db")

REPO_ROOT = Path(__file__).resolve().parents[1]
MAIN_PY = REPO_ROOT / "backend" / "main.py"
BACKEND_INIT = REPO_ROOT / "backend" / "__init__.py"


def _init_build_info_calls() -> list[ast.Call]:
    tree = ast.parse(MAIN_PY.read_text(encoding="utf-8"))
    return [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "init_build_info"
    ]


def test_main_py_takes_version_from_manifest_not_literals():
    """结构守卫：main.py 不得再把平台版本写成字面量。"""
    calls = _init_build_info_calls()
    assert calls, "main.py 里找不到 init_build_info 调用——判据失效，勿静默放行"

    for call in calls:
        for arg in [*call.args, *(kw.value for kw in call.keywords)]:
            assert not (isinstance(arg, ast.Constant) and isinstance(arg.value, str)), (
                f"init_build_info 在 main.py:{arg.lineno} 用了字符串字面量——"
                "平台版本必须来自 release_manifest.resolve_build_info()"
            )

    sources = [
        node.func.id
        for call in calls
        for node in ast.walk(call)
        if isinstance(node, ast.Call) and getattr(node.func, "id", None)
    ]
    assert "resolve_build_info" in sources, (
        "init_build_info 的值不来自 resolve_build_info()——真值必须是 release-manifest.json"
    )


def test_backend_package_has_no_ghost_version_constant():
    """结构守卫：包根不再声明 __version__（真值走清单，源码常量会漂移）。"""
    tree = ast.parse(BACKEND_INIT.read_text(encoding="utf-8"))
    declared = {
        target.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    assert "__version__" not in declared, (
        "backend/__init__.py 又声明了 __version__——它是会漂移的第二处真值；"
        "平台版本请从 release-manifest.json 读取（#2341 已裁决删除）"
    )


def _write_manifest(tmp_path: Path, payload) -> Path:
    path = tmp_path / "release-manifest.json"
    path.write_text(
        payload if isinstance(payload, str) else json.dumps(payload),
        encoding="utf-8",
    )
    return path


def test_resolve_build_info_reads_manifest(tmp_path):
    from backend.core.release_manifest import resolve_build_info

    manifest = _write_manifest(tmp_path, {
        "manifest_version": 1,
        "product": {"version": "local-20260916-a9f5f150"},
        "source": {"revision": "a9f5f1500000000000000000000000000000000"},
        "database": {"schema_target": "d4e5f6a7b8c9"},
    })

    assert resolve_build_info(manifest) == (
        "local-20260916-a9f5f150",
        "a9f5f1500000000000000000000000000000000",
    )


def test_resolve_build_info_falls_back_without_fake_version(tmp_path):
    """开发态（git checkout，无清单）→ 回落 unknown，且不含任何具体版本号。"""
    from backend.core.release_manifest import UNKNOWN, resolve_build_info

    version, commit = resolve_build_info(tmp_path / "does-not-exist.json")

    assert (version, commit) == (UNKNOWN, UNKNOWN)
    assert "2.0.0" not in version, "回落不得含具体版本号（那正是本单要消灭的假信息）"


def test_resolve_build_info_default_path_is_deploy_tree(tmp_path, monkeypatch):
    """部署态路径：默认参数读的就是部署树根下的 release-manifest.json。"""
    from backend.core import release_manifest

    _write_manifest(tmp_path, {"product": {"version": "v-deploy"}, "source": {"revision": "abc123"}})
    monkeypatch.setattr(release_manifest, "_REPO_ROOT", tmp_path)

    assert release_manifest.resolve_build_info() == ("v-deploy", "abc123")


def test_init_build_info_lands_in_exported_metric(tmp_path, monkeypatch):
    """行为（验收第 5 条）：合成清单 → **导出的指标值**取自清单；值可被 PromQL 查询到。

    注意 ``Info`` 的样本名带 ``_info`` 后缀（``stability_build_info``），声明名是
    ``stability_build``——#2286 的 ``_INFO_SUFFIXES`` 归一即为此。
    """
    from prometheus_client import REGISTRY

    from backend.core import metrics, release_manifest

    def _current_labels() -> tuple[str, str]:
        for metric in REGISTRY.collect():
            for sample in metric.samples:
                if sample.name == "stability_build_info":
                    return (
                        sample.labels.get("version", "unknown"),
                        sample.labels.get("commit", "unknown"),
                    )
        return "unknown", "unknown"

    original = _current_labels()
    _write_manifest(tmp_path, {
        "product": {"version": "local-20260916-a9f5f150"},
        "source": {"revision": "a9f5f150deadbeef"},
    })
    monkeypatch.setattr(release_manifest, "_REPO_ROOT", tmp_path)
    try:
        metrics.init_build_info(*release_manifest.resolve_build_info())
        assert REGISTRY.get_sample_value(
            "stability_build_info",
            {"version": "local-20260916-a9f5f150", "commit": "a9f5f150deadbeef"},
        ) == 1.0, "指标值没取自清单"
    finally:
        metrics.init_build_info(*original)


def test_resolve_build_info_tolerates_broken_manifest(tmp_path):
    """清单损坏/形状异常 → 同样显式回落，不抛（启动期不允许因此起不来）。"""
    from backend.core.release_manifest import UNKNOWN, resolve_build_info

    assert resolve_build_info(_write_manifest(tmp_path, "{not json")) == (UNKNOWN, UNKNOWN)
    assert resolve_build_info(_write_manifest(tmp_path, ["not", "a", "dict"])) == (
        UNKNOWN, UNKNOWN,
    )
    # 部分键缺失：能读到的那一半照常给出，读不到的回落
    partial = _write_manifest(tmp_path, {"source": {"revision": "deadbeef"}})
    assert resolve_build_info(partial) == (UNKNOWN, "deadbeef")
