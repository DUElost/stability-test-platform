"""#4011：Agent 运行时依赖（backend/agent/requirements.txt）必须在 Python 3.10 下可解析。

问题背景（#3964 → #3978 → #4011）：
- Agent 运行时的解释器下限是 Python 3.10（`backend/agent/install_agent.sh:396` 与
  `backend/services/host_updater.py:316` 均以 `>= (3, 10)` 判定）；
- #3964 将 `pydantic-settings` 下限抬到 `>=2.16.0`（要求 Python >=3.11），但 CI 的
  `pr-agent-tests` 当时只用 3.10 加载站点安装器闭包（#2268），未在 3.10 上解析
  `backend/agent/requirements.txt`，导致 6 项 required check 全绿合入、17 台 3.10 主机热更新失败。

本守卫（纯离线，只读源码与 CI 配置）：
1. 钉住 `backend/agent/install_agent.sh` 与 `backend/services/host_updater.py` 的下限声明与
   CI 所用下限一致（同源性，防止一侧改了而另一侧静默脱钩）；
2. 钉住 required job `pr-agent-tests` 存在在 **Python 3.10** 解释器上解析
   `backend/agent/requirements.txt` 的真实可执行步骤（注释不算），且排在后续 3.11
   `setup-python`（及 `--require-hashes` 安装）之前；
3. 自证正反样例：删步骤 / 注释掉命令 / 改解释器版本 / 移到 3.11 之后 / 删 3.11 恢复步，
   守卫函数全部必红。
"""
from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any

import pytest

from tests import ci_workflow_probe as probe

REPO_ROOT = Path(__file__).resolve().parents[1]
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"
AGENT_REQUIREMENTS = REPO_ROOT / "backend" / "agent" / "requirements.txt"
INSTALL_AGENT_SH = REPO_ROOT / "backend" / "agent" / "install_agent.sh"
HOST_UPDATER_PY = REPO_ROOT / "backend" / "services" / "host_updater.py"

FLOOR_JOB = "pr-agent-tests"
AGENT_PYTHON_FLOOR = (3, 10)
AGENT_PYTHON_FLOOR_STR = "3.10"
MAIN_CI_PYTHON_STR = "3.11"
_REQ_REL = "backend/agent/requirements.txt"


def _assert_agent_requirements_floor_gate(jobs: dict[str, Any]) -> None:
    """断言 `pr-agent-tests` 在 Python 3.10 上解析 Agent 依赖，且早于 3.11 setup。"""
    assert FLOOR_JOB in jobs, f"ci.yml 缺少 required job {FLOOR_JOB!r}"
    job = jobs[FLOOR_JOB]
    assert probe.is_pr_reachable(job), f"{FLOOR_JOB!r} 必须在 pull_request 事件上运行"

    steps: list[dict[str, Any]] = job.get("steps") or []
    py_versions = [
        (index, str((step.get("with") or {}).get("python-version", "")))
        for index, step in enumerate(steps)
        if (step.get("with") or {}).get("python-version")
    ]

    matching_steps = [
        (index, step)
        for index, step in enumerate(steps)
        if "pip install" in probe.code_text(step) and _REQ_REL in probe.code_text(step)
    ]
    assert matching_steps, (
        f"{FLOOR_JOB} 缺少在可执行代码中用 pip install 解析 {_REQ_REL} 的步骤"
    )
    step_index, _step = matching_steps[0]

    preceding_py = [(idx, ver) for idx, ver in py_versions if idx < step_index]
    assert preceding_py, (
        f"步骤 #{step_index} 之前没有 setup-python——无法保证在 {AGENT_PYTHON_FLOOR_STR} 上运行"
    )
    active_py_index, active_py_ver = preceding_py[-1]
    assert active_py_ver == AGENT_PYTHON_FLOOR_STR, (
        f"Agent 依赖解析步骤前的 setup-python（步骤 #{active_py_index}）版本为 "
        f"{active_py_ver!r}，必须为 {AGENT_PYTHON_FLOOR_STR!r}"
    )

    later_py = [(idx, ver) for idx, ver in py_versions if idx > step_index]
    assert later_py, (
        f"Agent 依赖解析步骤（#{step_index}）之后必须重新 setup-python {MAIN_CI_PYTHON_STR}，"
        "否则后续 `--require-hashes` 安装会落入 3.10 环境"
    )
    restore_index, restore_ver = later_py[0]
    assert restore_ver == MAIN_CI_PYTHON_STR, (
        f"Agent 依赖解析步骤之后的 setup-python（步骤 #{restore_index}）版本为 "
        f"{restore_ver!r}，应恢复为 {MAIN_CI_PYTHON_STR!r}"
    )

    hash_install_steps = [
        idx for idx, s in enumerate(steps) if "--require-hashes" in probe.code_text(s)
    ]
    assert hash_install_steps, f"{FLOOR_JOB} 未找到 `--require-hashes` 依赖安装步骤"
    assert restore_index < hash_install_steps[0], (
        f"3.11 setup-python（#{restore_index}）必须排在 `--require-hashes`（#{hash_install_steps[0]}）之前"
    )


def test_agent_runtime_floor_sources_match_ci_floor():
    """Agent 安装/热更新脚本声明的下限与 CI 守卫口径必须一致。"""
    assert AGENT_REQUIREMENTS.is_file(), f"缺少 {_REQ_REL}"
    assert AGENT_REQUIREMENTS.read_text(encoding="utf-8").strip(), f"{_REQ_REL} 不能为空"

    expected_tuple = f"({AGENT_PYTHON_FLOOR[0]}, {AGENT_PYTHON_FLOOR[1]})"
    for path in (INSTALL_AGENT_SH, HOST_UPDATER_PY):
        text = path.read_text(encoding="utf-8")
        match = re.search(r"sys\.version_info\s*>=\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)", text)
        assert match, f"{path.relative_to(REPO_ROOT)} 未找到 sys.version_info >= (...) 判定"
        declared = (int(match.group(1)), int(match.group(2)))
        assert declared == AGENT_PYTHON_FLOOR, (
            f"{path.relative_to(REPO_ROOT)} 声明的下限是 {declared}，"
            f"与 CI 守卫的 {expected_tuple} 不一致"
        )


def test_ci_resolves_agent_requirements_on_floor_interpreter():
    """required job `pr-agent-tests` 必须在 3.10 上解析 Agent 依赖，且早于 3.11 setup。"""
    _assert_agent_requirements_floor_gate(probe.load_jobs(CI_YML))


class TestAgentRequirementsFloorGateSelfTest:
    """判别器红绿自证：删步骤、注释命令、改版本、颠倒顺序都必须触发失败。"""

    @staticmethod
    def _base_jobs() -> dict[str, Any]:
        return copy.deepcopy(probe.load_jobs(CI_YML))

    @staticmethod
    def _find_target_index(jobs: dict[str, Any]) -> int:
        steps = jobs[FLOOR_JOB]["steps"]
        return next(
            idx
            for idx, step in enumerate(steps)
            if "pip install" in probe.code_text(step) and _REQ_REL in probe.code_text(step)
        )

    def test_deleting_step_fails(self):
        jobs = self._base_jobs()
        idx = self._find_target_index(jobs)
        del jobs[FLOOR_JOB]["steps"][idx]
        with pytest.raises(AssertionError, match="缺少"):
            _assert_agent_requirements_floor_gate(jobs)

    def test_commenting_out_command_fails(self):
        jobs = self._base_jobs()
        idx = self._find_target_index(jobs)
        jobs[FLOOR_JOB]["steps"][idx]["run"] = (
            f"# python -m pip install --dry-run --ignore-installed -r {_REQ_REL}\n"
            "echo skipped\n"
        )
        with pytest.raises(AssertionError, match="缺少"):
            _assert_agent_requirements_floor_gate(jobs)

    @pytest.mark.parametrize("bad_version", ["3.11", "3.9"])
    def test_wrong_interpreter_version_fails(self, bad_version: str):
        jobs = self._base_jobs()
        idx = self._find_target_index(jobs)
        steps = jobs[FLOOR_JOB]["steps"]
        py_idx = max(
            i
            for i, step in enumerate(steps[:idx])
            if (step.get("with") or {}).get("python-version")
        )
        steps[py_idx]["with"]["python-version"] = bad_version
        with pytest.raises(AssertionError, match=f"{bad_version!r}"):
            _assert_agent_requirements_floor_gate(jobs)

    def test_moving_step_after_311_setup_fails(self):
        jobs = self._base_jobs()
        steps = jobs[FLOOR_JOB]["steps"]
        idx = self._find_target_index(jobs)
        target_step = steps.pop(idx)
        py311_idx = next(
            i
            for i, step in enumerate(steps)
            if str((step.get("with") or {}).get("python-version", "")) == MAIN_CI_PYTHON_STR
        )
        steps.insert(py311_idx + 1, target_step)
        with pytest.raises(AssertionError, match=MAIN_CI_PYTHON_STR):
            _assert_agent_requirements_floor_gate(jobs)

    def test_removing_311_restore_setup_fails(self):
        jobs = self._base_jobs()
        steps = jobs[FLOOR_JOB]["steps"]
        idx = self._find_target_index(jobs)
        py311_idx = next(
            i
            for i, step in enumerate(steps)
            if i > idx
            and str((step.get("with") or {}).get("python-version", "")) == MAIN_CI_PYTHON_STR
        )
        del steps[py311_idx]
        with pytest.raises(AssertionError, match="重新 setup-python"):
            _assert_agent_requirements_floor_gate(jobs)
