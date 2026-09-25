"""#738 / ADR-0054：`pipeline_validator` 只有**一份实现**，两种包布局判定一致。

历史：本文件曾是「双端副本语义一致」的 parity 测试（agent 侧一份与
`backend/core/pipeline_validator.py` 逐字相同的拷贝，靠本文件防漂移）。ADR-0054
裁决把共享定义归入 `backend/agent/contracts/` 后，拷贝与 `except ImportError`
兜底都已删除——本文件随之改为**单实现测试**，文件名保留历史（#738）。

现在守三件事：
1. **单实现**：契约只有 `backend/agent/contracts/pipeline_validator.py` 一份，
   旧副本（core / agent 各一，含再导出壳）不存在（ADR-0054 D5）；
2. **两种布局的 import 与 schema 定位都成立**（ADR-0054 D3/D6）：真实仓库布局用
   进程内导入；主机安装布局（顶层包 ``agent`` + ``<install>/schemas/``）在临时
   目录里**真跑一遍**子进程导入 + 校验；
3. **同一语料两侧判定不分歧**：把历史 parity 语料分别喂给两种布局，结果必须逐字
   一致——不变量从「两份代码不得漂移」变成「一种代码不得挑布局」。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from backend.agent.contracts.pipeline_validator import (
    resolve_pipeline_schema_path,
    validate_pipeline_def,
)

# #739 第二批：本文件自 `backend/agent/tests/` 迁入（棘轮清零）；源码锚点按仓库根反查。
_REPO_ROOT = Path(__file__).resolve().parents[3]
_CONTRACT_MODULE = _REPO_ROOT / "backend" / "agent" / "contracts" / "pipeline_validator.py"
_LEGACY_COPIES = (
    _REPO_ROOT / "backend" / "core" / "pipeline_validator.py",
    _REPO_ROOT / "backend" / "agent" / "pipeline_validator.py",
)

#: 主机布局探针：以顶层包 ``agent`` 导入契约、打印 schema 定位与语料判定。
#: 在临时安装树（``<install>/agent/`` + ``<install>/schemas/``）里由子进程执行。
_HOST_LAYOUT_PROBE = r'''
import json
import sys

from agent.contracts.pipeline_validator import (
    resolve_pipeline_schema_path,
    validate_pipeline_def,
)


def run(pipeline_def):
    try:
        ok, errors = validate_pipeline_def(pipeline_def)
    except Exception as exc:  # noqa: BLE001 - 与仓库侧测试同样的「异常也是结果」
        return ["raised", type(exc).__name__]
    return [ok, list(errors)]


with open(sys.argv[1], encoding="utf-8") as f:
    corpus = json.load(f)

print(json.dumps({
    "schema": str(resolve_pipeline_schema_path()),
    "results": [run(case) for case in corpus],
}))
'''


def _normalized(validate, pipeline_def: Any) -> list:
    """把一次校验调用归一成可比对的值：正常返回 ``[ok, errors]``，抛错记异常类型。"""
    try:
        is_valid, errors = validate(pipeline_def)
    except Exception as exc:  # noqa: BLE001 - 就要捕获任意异常做比对
        return ["raised", type(exc).__name__]
    return [is_valid, list(errors)]


def _valid_lifecycle() -> dict:
    return {
        "lifecycle": {
            "init": [
                {
                    "step_id": "check_device",
                    "action": "script:check_device",
                    "version": "1.0.0",
                    "params": {},
                    "timeout_seconds": 30,
                    "retry": 0,
                }
            ],
            "patrol": {
                "interval_seconds": 60,
                "steps": [
                    {
                        "step_id": "watch_device",
                        "action": "script:watch_device",
                        "version": "1.0.0",
                        "params": {},
                        "timeout_seconds": 30,
                    }
                ],
            },
            "teardown": [],
        }
    }


def _corpus() -> list[tuple[str, Any]]:
    """历史 parity 语料：接受形态与各类拒绝形态，另含根类型畸形（None/非 dict）。"""
    with_version = {
        "step_id": "prepare",
        "action": "script:prepare",
        "version": "1.2.3",
        "params": {},
        "timeout_seconds": 30,
    }
    without_version = {k: v for k, v in with_version.items() if k != "version"}
    bad_prefix = {**with_version, "action": "action:prepare"}
    disabled = {**with_version, "enabled": False}

    nested_stages = {
        "lifecycle": {
            "init": [{"stage": "prepare", "steps": [with_version]}],
            "patrol": {"interval_seconds": 60, "steps": []},
            "teardown": [],
        }
    }
    return [
        ("valid_lifecycle", _valid_lifecycle()),
        ("valid_disabled_step", {**_valid_lifecycle(), "name": "with-disabled"}),
        ("top_level_stages", {"stages": {"prepare": [with_version]}}),
        ("legacy_phases", {"phases": {"prepare": [with_version]}}),
        ("lifecycle_phase_with_nested_stages", nested_stages),
        ("script_action_without_version", {"lifecycle": {"init": [without_version], "patrol": {"interval_seconds": 60, "steps": []}, "teardown": []}}),
        ("action_with_invalid_prefix", {"lifecycle": {"init": [bad_prefix], "patrol": {"interval_seconds": 60, "steps": []}, "teardown": []}}),
        ("disabled_step_alone", {"lifecycle": {"init": [disabled], "patrol": {"interval_seconds": 60, "steps": []}, "teardown": []}}),
        ("empty_dict", {}),
        ("none", None),
        ("not_a_dict", ["lifecycle"]),
    ]


def test_contract_has_single_implementation_without_legacy_copies():
    """ADR-0054 D5：只有 contracts/ 一份实现，旧副本（含再导出壳）已删。"""
    assert _CONTRACT_MODULE.is_file(), f"缺少契约实现：{_CONTRACT_MODULE}"
    for legacy in _LEGACY_COPIES:
        assert not legacy.exists(), (
            f"旧副本仍在：{legacy}——ADR-0054 D5 要求删除且不留再导出壳（壳会让 patch 目标分叉）"
        )


def test_repo_layout_schema_path_is_the_real_artifact():
    """D6 仓库布局：契约按 agent 包父目录解析到 ``backend/schemas/pipeline_schema.json``。"""
    expected = _REPO_ROOT / "backend" / "schemas" / "pipeline_schema.json"

    assert resolve_pipeline_schema_path() == expected
    assert expected.is_file()


def test_host_layout_imports_and_agrees_with_repo_layout(tmp_path):
    """D3/D6 主机布局：顶层包 ``agent`` + ``<install>/schemas/`` 真子进程跑通，判定一致。

    直接复刻安装后的目录形状（``install_agent.sh`` / DEPLOY.md）：契约文件若按
    ``__file__`` 裸深度定位 schema（搬迁前的 ``parent.parent``），在本布局必然解析错。
    """
    install = tmp_path / "stability-test-agent"
    for relative in (
        "agent/__init__.py",
        "agent/adb_wrapper.py",
        "agent/contracts/__init__.py",
        "agent/contracts/pipeline_validator.py",
    ):
        source = _REPO_ROOT / "backend" / relative
        target = install / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    schema_target = install / "schemas" / "pipeline_schema.json"
    schema_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(_REPO_ROOT / "backend" / "schemas" / "pipeline_schema.json", schema_target)

    corpus = [case for _, case in _corpus()]
    corpus_file = tmp_path / "corpus.json"
    corpus_file.write_text(json.dumps(corpus), encoding="utf-8")

    env = dict(os.environ)
    env["PYTHONPATH"] = str(install)   # 只给安装树：repo 布局不得参与
    proc = subprocess.run(
        [sys.executable, "-c", _HOST_LAYOUT_PROBE, str(corpus_file)],
        cwd=str(tmp_path), capture_output=True, text=True, env=env,
    )
    assert proc.returncode == 0, f"主机布局导入/校验失败：\n{proc.stderr}"

    payload = json.loads(proc.stdout)
    assert payload["schema"] == str(schema_target)
    assert payload["results"] == [_normalized(validate_pipeline_def, case) for case in corpus], (
        "同一契约在主机布局与仓库布局给出不同判定（ADR-0054：不变量是「一种代码不得挑布局」）"
    )
