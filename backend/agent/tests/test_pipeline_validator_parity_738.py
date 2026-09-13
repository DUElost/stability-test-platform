"""#738：`pipeline_validator` 的双端副本必须保持**语义一致**。

背景：agent 侧保留了一份与 `backend/core/pipeline_validator.py` 逐字相同的副本（两份均
138 行，`diff` 为空），用途有二：

1. `job_runner._validate_pipeline_def` 的 **ImportError 回落**——独立部署的 agent 可能
   import 不到 `backend.core`；
2. `install_selfcheck` 在**安装期**校验 pipeline_def，那时控制面包还不一定在路径上。

所以「双端重复」是**有意为之**（不是可机械删除的死代码；消除它需要一次共享模块的设计
决策）。但**没有任何东西保证两份副本同步**：一旦漂移，同一个 pipeline_def 会在 agent 侧
与控制面侧得到**不同的合法性判定**——agent 放行控制面拒绝的定义（或反之）。

本用例把该不变量变成可回归断言：**两份实现对同一语料给出完全一致的结果**。
判据取「语义一致」而非「逐字相同」——后者会把一次合法的等价重构也判红，而真正要守的是
"两侧判定不得分歧"。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.agent.pipeline_validator import validate_pipeline_def as agent_validate
from backend.core.pipeline_validator import validate_pipeline_def as core_validate

_AGENT_COPY = Path(__file__).resolve().parents[1] / "pipeline_validator.py"
_CORE_COPY = Path(__file__).resolve().parents[2] / "core" / "pipeline_validator.py"


def _normalized(validate, pipeline_def: Any) -> Any:
    """把一次校验调用归一成可比较的值：正常返回 ``(ok, errors)``，抛错则记异常类型。

    把异常也纳入比对是刻意的：两侧对同一畸形输入「一边抛错、一边返回错误列表」同样是漂移。
    """
    try:
        is_valid, errors = validate(pipeline_def)
    except Exception as exc:  # noqa: BLE001 - 就要捕获任意异常做比对
        return ("raised", type(exc).__name__)
    return (is_valid, list(errors))


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
    """覆盖接受形态与各类拒绝形态，形状取自 `backend/tests/core/test_pipeline_validator.py`。"""
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


def test_both_copies_exist():
    """回落落点必须存在——`job_runner` 在 ImportError 时会 import agent 侧那份。"""
    assert _AGENT_COPY.is_file(), f"缺少 agent 侧回落副本：{_AGENT_COPY}"
    assert _CORE_COPY.is_file(), f"缺少 core 侧实现：{_CORE_COPY}"


def test_both_copies_agree_on_corpus():
    divergences = []
    for name, pipeline_def in _corpus():
        agent_result = _normalized(agent_validate, pipeline_def)
        core_result = _normalized(core_validate, pipeline_def)
        if agent_result != core_result:
            divergences.append(
                f"  - {name}: agent={agent_result!r} core={core_result!r}"
            )
    assert not divergences, (
        "两份 pipeline_validator 语义漂移（同一 pipeline_def 在两侧判定不同）：\n"
        + "\n".join(divergences)
        + "\n\n修法：让两份副本重新一致（历史上二者逐字相同）；"
        "若确需各自演进，请把本判据改为显式豁免并说明理由。"
    )
