"""#2382：``PlanStep.timeout_seconds`` 的两套契约必须同源——「未配置」= **不写键**。

根因是同一个字段有两份互相矛盾的权威：

- 入口 ``PlanStepIn.timeout_seconds`` 是 ``Optional[int] = None``，DB 列 ``nullable=True``
  （``backend/models/plan.py``），引擎 ``_resolve_step_wall_clock`` 明确支持
  None → ``STP_STEP_WALL_CLOCK_SECONDS`` → 300s；
- 但 ``$defs.step`` 把 ``timeout_seconds`` 列为 ``required``，且组装器**无条件写键**，
  于是严格按 OpenAPI 省略该字段的调用方一律 422。

``stall_seconds`` 从一开始就走「未配置就不写键」，所以这个字段的修法被另一个字段遗忘了一次。
本文件守三条不变量：

1. schema 不要求 ``timeout_seconds``，且**不写 default**——有效默认随宿主 env 变，
   把数字烤进 schema 会掩盖 ``STP_STEP_WALL_CLOCK_SECONDS`` 的 fleet 覆盖能力；
2. 三处组装点（dispatcher 的 ORM 路径、run 快照路径、入口预校验）共用
   ``apply_step_timing_fields``，写的是「省略键」而不是「写 ``null``」——``null`` 会被
   ``{"type": "integer"}`` 拒掉，只有省略键才表达未配置；
3. ``timeout_seconds=0`` 缺 ``stall_seconds`` 的 422 必须可行动（#2016 同族：
   文案不指向修法＝没说）。

双端 ``pipeline_validator`` 的一致性**不在本文件重复守**——
``backend/agent/tests/test_pipeline_validator_parity_738.py`` 已按「语义一致而非逐字相同」
裁决过（#738）。本单把可行动文案写在**入口呈现层**而不是 validator 里，正是为了不碰那份裁决。

为什么用 subprocess 探针而不是顶层 ``import backend...``：``backend.core.database`` 在
**导入期**解析 ``DATABASE_URL``（根 ``tests/`` 没有 conftest 注入），而整个 ``tests/`` 目录
跑在 ``pr-agent-tests`` 这条 required check 里（#1569）。探针显式喂一份假连接串，既不把本机
（可能是生产）连接串带进子进程，也不把 env 固化到同进程其它用例——与
``tests/test_plan_run_abort_import_contract.py`` 同一手法。
"""

from __future__ import annotations

import ast
import functools
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from jsonschema import Draft7Validator

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "backend" / "schemas" / "pipeline_schema.json"
TYPES_TS = ROOT / "frontend" / "src" / "utils" / "api" / "types.ts"

# 三处「从 PlanStep 组装 step_def」的地方——本单把它们收成同一个判据。
ASSEMBLY_FILES = (
    "backend/services/plan_dispatcher_core.py",
    "backend/api/routes/plans.py",
)


# --------------------------------------------------------------------------
# 1) schema 层：纯离线，不需要 import backend
# --------------------------------------------------------------------------

def _schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _step(**overrides) -> dict:
    step = {"step_id": "s1", "action": "script:demo", "version": "v1"}
    step.update(overrides)
    return step


def _doc(step: dict) -> dict:
    return {"lifecycle": {"init": [step], "teardown": []}}


def _errors(doc: dict) -> list[str]:
    """与 ``backend/core/pipeline_validator.py`` 同格式的错误串。"""
    out = []
    for err in Draft7Validator(_schema()).iter_errors(doc):
        path = ".".join(str(p) for p in err.absolute_path) or "(root)"
        out.append(f"{path}: {err.message}")
    return out


def test_step_props_are_actually_present():
    """扫描面非空：删属性会让下面的「可选」断言恒真，这里先钉住字段存在。"""
    props = _schema()["$defs"]["step"]["properties"]
    for key in ("timeout_seconds", "stall_seconds"):
        assert key in props, f"$defs.step.properties 缺 {key}——本文件判据需随之更新"
        assert props[key]["type"] == "integer"


def test_timeout_seconds_is_optional_in_schema():
    """本单主判据：按 OpenAPI 省略 timeout_seconds 的计划必须通过 schema。"""
    assert _errors(_doc(_step())) == []


def test_timeout_seconds_is_not_required_and_has_no_baked_default():
    step = _schema()["$defs"]["step"]
    assert "timeout_seconds" not in step["required"]
    # 有效默认 = 宿主 env → 300s，烤进 schema 会掩盖覆盖能力（见模块 docstring）
    assert "default" not in step["properties"]["timeout_seconds"]


def test_explicit_null_is_still_rejected():
    """「未配置」≠「null」——这正是组装器必须**省键**而不是写 None 的理由。"""
    errors = _errors(_doc(_step(timeout_seconds=None)))
    assert any("is not of type" in e for e in errors), errors


def test_timeout_value_range_is_still_owned_by_schema():
    """负数仍由 schema 拒（本单刻意不在入口加 ``Field(ge=0)``，见 Note 的 Alternatives）。"""
    errors = _errors(_doc(_step(timeout_seconds=-5)))
    assert any("less than the minimum" in e for e in errors), errors


def test_zero_timeout_is_still_gated_on_stall_seconds():
    """放宽 required 不能把 0（不限墙钟）的停滞钟门禁一起放宽掉。"""
    errors = _errors(_doc(_step(timeout_seconds=0)))
    assert any("'stall_seconds' is a required property" in e for e in errors), errors
    assert _errors(_doc(_step(timeout_seconds=0, stall_seconds=60))) == []
    # then.minimum 是 1：stall=0 等于没有停滞钟，不满足「不限墙钟必须有兜底」
    assert _errors(_doc(_step(timeout_seconds=0, stall_seconds=0))) != []
    # 有墙钟时 stall 仍是可选
    assert _errors(_doc(_step(timeout_seconds=300))) == []


# --------------------------------------------------------------------------
# 2) 接线层：AST/文本，保证「三处共用同一判据」不被悄悄改回硬写
# --------------------------------------------------------------------------

def _tree(rel_path: str) -> ast.Module:
    return ast.parse((ROOT / rel_path).read_text(encoding="utf-8"))


def _step_def_literals(tree: ast.Module) -> list[ast.Dict]:
    """step_def 字面量 = 同时含 ``step_id`` 与 ``action`` 键的 dict。

    ``build_plan_snapshot`` 的快照 step 用的是 ``step_key``，不在扫描面内——快照按设计
    存原值（含 None），省略发生在**组装 lifecycle** 时。
    """
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            keys = {k.value for k in node.keys if isinstance(k, ast.Constant)}
            if {"step_id", "action"} <= keys:
                found.append(node)
    return found


def test_no_step_literal_writes_timeout_seconds():
    """三处组装点都不允许再无条件写 timeout_seconds / stall_seconds。"""
    offenders = []
    scanned = 0
    for rel in ASSEMBLY_FILES:
        for literal in _step_def_literals(_tree(rel)):
            scanned += 1
            keys = {k.value for k in literal.keys if isinstance(k, ast.Constant)}
            bad = keys & {"timeout_seconds", "stall_seconds"}
            if bad:
                offenders.append(f"{rel}:{literal.lineno} 写回了 {sorted(bad)}")
    assert scanned == 3, f"预期 3 处 step_def 字面量，实扫到 {scanned}——判据需随之更新"
    assert not offenders, offenders


def test_all_three_sites_call_the_shared_helper():
    """helper 调用点数固定为 3：少一处就是「一处修法被另一个字段遗忘」的重演。"""
    per_file = {}
    for rel in ASSEMBLY_FILES:
        tree = _tree(rel)
        per_file[rel] = sum(
            1
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and getattr(node.func, "id", getattr(node.func, "attr", None))
            == "apply_step_timing_fields"
        )
    assert per_file["backend/services/plan_dispatcher_core.py"] == 2, per_file
    assert per_file["backend/api/routes/plans.py"] == 1, per_file


def test_entry_route_imports_the_helper_from_dispatcher_core():
    """入口判据必须来自 dispatcher（同源），不接受复制一份实现。"""
    tree = _tree("backend/api/routes/plans.py")
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
        if (node.module or "").endswith("plan_dispatcher_core")
    }
    assert "apply_step_timing_fields" in imported, imported


def test_frontend_type_is_optional_in_sync_with_schema():
    """硬不变量「前端 API 类型与后端 schema 同步」在本字段上的具体化。

    tsc 抓不到这里的回归（展示层 ``stepTiming.ts`` 早已区分 0/缺省/n>0，改成必填只是
    把类型说窄），所以用结构断言兜。
    """
    text = TYPES_TS.read_text(encoding="utf-8")
    match = re.search(r"export interface PipelineStep \{(.*?)\n\}", text, re.S)
    assert match, "未找到 PipelineStep 接口——解析器需随 types.ts 结构更新"
    optional = set(re.findall(r"^\s{2}([a-z_]+)\?:", match.group(1), re.M))
    required = set(re.findall(r"^\s{2}([a-z_]+):", match.group(1), re.M))
    assert "timeout_seconds" in optional | required, "PipelineStep 不再声明 timeout_seconds"
    schema_requires = "timeout_seconds" in _schema()["$defs"]["step"]["required"]
    assert (
        "timeout_seconds" not in required
    ), "后端 schema 可省、前端却声明为必填——两侧契约又分叉了"
    assert schema_requires is False


# --------------------------------------------------------------------------
# 3) 行为层：子进程探针（需要 DATABASE_URL / JWT_SECRET_KEY 才能 import backend）
# --------------------------------------------------------------------------

_PROBE_SCENARIOS = {
    "core": {
        "helper_omits_unset",
        "helper_keeps_zero",
        "orm_path_omits_unset_and_validates",
        "orm_path_zero_timeout_without_stall_is_rejected",
        "snapshot_path_omits_unset_and_validates",
        "snapshot_legacy_row_without_the_key",
    },
    "entry": {
        "openapi_field_is_optional",
        "entry_accepts_omitted_timeout",
        "patrol_path_omits_unset_and_validates",
        "zero_timeout_message_is_actionable",
        "unrelated_error_passes_through_verbatim",
    },
}

_CORE_PROBE = r'''
import json
from types import SimpleNamespace

from backend.core.pipeline_validator import validate_pipeline_def
from backend.services.plan_dispatcher_core import (
    apply_step_timing_fields,
    build_lifecycle_from_snapshot,
    build_lifecycle_from_steps,
)

results = {}


def check(name, fn):
    try:
        fn()
    except Exception as exc:
        results[name] = "FAIL %s: %s" % (type(exc).__name__, exc)
    else:
        results[name] = "ok"


def _step(**kw):
    base = dict(
        step_key="s1", script_name="demo", script_version="v1", stage="init",
        sort_order=0, retry=0, enabled=True, params=None,
        timeout_seconds=None, stall_seconds=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _plan(**kw):
    base = dict(
        patrol_interval_seconds=None, timeout_seconds=None,
        barrier_timeout_seconds=None, barrier_max_wait_seconds=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _orm(steps, **plan_kw):
    return build_lifecycle_from_steps(
        _plan(**plan_kw), steps, {("demo", "v1"): {}}
    )


def _ok_build(lifecycle):
    # 「不写键」的产物必须直接通过统一判据——这才是入口 422 的那条路径
    assert validate_pipeline_def({"lifecycle": lifecycle})[0] is True, lifecycle


def _helper_omits_unset():
    got = apply_step_timing_fields({}, timeout_seconds=None, stall_seconds=None)
    assert got == {}, "未配置就不该写键: %r" % (got,)


def _helper_keeps_zero():
    got = apply_step_timing_fields({}, timeout_seconds=0, stall_seconds=60)
    assert got == {"timeout_seconds": 0, "stall_seconds": 60}, "0 与显式值必须原样保留: %r" % (got,)


check("helper_omits_unset", _helper_omits_unset)
check("helper_keeps_zero", _helper_keeps_zero)


def _orm_path_omits_unset():
    lifecycle = _orm([_step()])
    step_def = lifecycle["init"][0]
    assert "timeout_seconds" not in step_def, step_def
    assert "stall_seconds" not in step_def, step_def
    assert step_def["retry"] == 0, step_def
    _ok_build(lifecycle)


check("orm_path_omits_unset_and_validates", _orm_path_omits_unset)


def _orm_zero_without_stall():
    lifecycle = _orm([_step(timeout_seconds=0)])
    assert lifecycle["init"][0]["timeout_seconds"] == 0, lifecycle
    is_valid, errors = validate_pipeline_def({"lifecycle": lifecycle})
    assert is_valid is False, "0 缺停滞钟仍必须被门禁拒"
    assert any("'stall_seconds' is a required property" in e for e in errors), errors


check("orm_path_zero_timeout_without_stall_is_rejected", _orm_zero_without_stall)


def _snapshot_path_omits_unset():
    lifecycle = build_lifecycle_from_snapshot({
        "plan": {},
        "steps": [{
            "step_key": "s1", "script_name": "demo", "script_version": "v1",
            "stage": "init", "sort_order": 0, "retry": 0, "enabled": True,
            "timeout_seconds": None, "stall_seconds": None,
        }],
    })
    step_def = lifecycle["init"][0]
    assert "timeout_seconds" not in step_def, step_def
    _ok_build(lifecycle)


check("snapshot_path_omits_unset_and_validates", _snapshot_path_omits_unset)


def _snapshot_row_without_key():
    # 历史快照连键都没有：同样不许写出 null
    lifecycle = build_lifecycle_from_snapshot({
        "plan": {},
        "steps": [
            {
                "step_key": "old", "script_name": "demo", "script_version": "v1",
                "stage": "teardown", "sort_order": 0,
            },
            # 语义层要求 init 至少一步，这里配一个同样无时间键的历史行
            {
                "step_key": "legacy-init", "script_name": "demo",
                "script_version": "v1", "stage": "init", "sort_order": 0,
            },
        ],
    })
    assert "timeout_seconds" not in lifecycle["teardown"][0], lifecycle
    assert "timeout_seconds" not in lifecycle["init"][0], lifecycle
    _ok_build(lifecycle)


check("snapshot_legacy_row_without_the_key", _snapshot_row_without_key)

print(json.dumps(results, ensure_ascii=False))
'''

_ENTRY_PROBE = r'''
import json

from fastapi import HTTPException

import backend.api.routes.plans as plans

results = {}


def check(name, fn):
    try:
        fn()
    except Exception as exc:
        results[name] = "FAIL %s: %s" % (type(exc).__name__, exc)
    else:
        results[name] = "ok"


def _in(**kw):
    base = dict(
        step_key="s1", script_name="demo", script_version="v1", stage="init",
    )
    base.update(kw)
    return plans.PlanStepIn(**base)




def _openapi_field_is_optional():
    assert plans.PlanStepIn.model_fields["timeout_seconds"].is_required() is False, (
        "入口字段变成必填——与 schema 的口径又分叉了"
    )


check("openapi_field_is_optional", _openapi_field_is_optional)


def _entry_accepts_omitted_timeout():
    # 本单的复现形状：客户端严格按 OpenAPI 只给必填字段，过去一律 422
    plans._validate_assembled_lifecycle([_in()], None, None)
    lifecycle = plans._assemble_lifecycle_for_validation([_in()], None, None)
    assert "timeout_seconds" not in lifecycle["init"][0], lifecycle


check("entry_accepts_omitted_timeout", _entry_accepts_omitted_timeout)


def _patrol_path():
    steps = [_in(), _in(step_key="p1", stage="patrol", sort_order=1)]
    plans._validate_assembled_lifecycle(steps, 60, None)
    lifecycle = plans._assemble_lifecycle_for_validation(steps, 60, None)
    for step_def in lifecycle["patrol"]["steps"]:
        assert "timeout_seconds" not in step_def, step_def
        assert "stall_seconds" not in step_def, step_def


check("patrol_path_omits_unset_and_validates", _patrol_path)


def _zero_timeout_message():
    steps = [_in(timeout_seconds=0)]
    try:
        plans._validate_assembled_lifecycle(steps, None, None)
    except HTTPException as exc:
        detail = exc.detail
        assert exc.status_code == 422, exc.status_code
        assert detail["code"] == "INVALID_LIFECYCLE", detail
        assert len(detail["errors"]) == 1, detail
        message = detail["errors"][0]
        assert message.startswith("lifecycle.init.0: "), message
        assert "timeout_seconds=0" in message, message
        assert "stall_seconds >= 1" in message, message
        assert "300" in message, message
        assert "is a required property" not in message, message
    else:
        raise AssertionError("0 缺停滞钟必须仍被拒")


check("zero_timeout_message_is_actionable", _zero_timeout_message)


def _unrelated_error_passthrough():
    # version 为空 → 语义层的无关报错，必须原样透传（翻译面只有一条，不许吞别的）
    try:
        plans._validate_assembled_lifecycle([_in(script_version="")], None, None)
    except HTTPException as exc:
        errors = exc.detail["errors"]
        assert any("version is required for script action" in e for e in errors), errors
        assert all("timeout_seconds=0" not in e for e in errors), errors
    else:
        raise AssertionError("空 version 必须仍被拒")


check("unrelated_error_passes_through_verbatim", _unrelated_error_passthrough)

print(json.dumps(results, ensure_ascii=False))
'''


def _env_for_probe() -> dict:
    """显式覆盖（不是 setdefault）：不把本机/生产连接串带进子进程，也不依赖运行者设了什么。"""
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("DATABASE_URL", "JWT_SECRET_KEY", "TESTING")
    }
    env["DATABASE_URL"] = "sqlite:///" + str(
        Path(tempfile.gettempdir()) / "stp-2382-probe-not-a-database.db"
    )
    env["JWT_SECRET_KEY"] = "stp-2382-probe-only"
    return env


@functools.lru_cache(maxsize=None)
def _probe(which: str) -> dict:
    source = _CORE_PROBE if which == "core" else _ENTRY_PROBE
    proc = subprocess.run(
        [sys.executable, "-c", source],
        cwd=ROOT,
        env=_env_for_probe(),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, (
        f"{which} 探针子进程退出 {proc.returncode}\n"
        f"stdout: {proc.stdout[-2000:]}\nstderr: {proc.stderr[-4000:]}"
    )
    return json.loads(proc.stdout)


@pytest.mark.parametrize("which", sorted(_PROBE_SCENARIOS))
def test_probe_covers_every_scenario(which):
    """恒真防护：场景若在探针里被删掉，这里立刻红，而不是下面的参数化静默变少。"""
    assert set(_probe(which)) == _PROBE_SCENARIOS[which], sorted(_probe(which))


@pytest.mark.parametrize(
    "which,name",
    sorted((w, n) for w, names in _PROBE_SCENARIOS.items() for n in names),
)
def test_probe_scenario(which, name):
    assert _probe(which)[name] == "ok"
