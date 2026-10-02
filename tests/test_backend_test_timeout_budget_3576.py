"""#3576：`backend-test` 的 job 级超时必须覆盖三个步骤之和，且留抖动余量。

实测墙钟（step 级，7 次全量 run 的 job steps 时间线）：

    Run backend tests      16~23 min（6/7 样本；单点离群 50m37s）
    Run agent tests        ~4.5 min（恒定）
    Run repo-level tests   10~15 min
    ─────────────────────────────────
    常态合计               33~39 min

`timeout-minutes` 是 **job 级**预算，覆盖全部步骤。60 遇到单步 3 倍抖动就被顶穿，
而顶穿的后果不只是这个 job 红——`Run repo-level tests` 会被 kill，那批测试当夜
**静默未跑完、没有任何结论**（09-26 run `36269603230` 实测：backend 单步 50m37s 后
job 在下一步被杀，只跑了 4m10s）。

把数字钉在这里而不是只写在注释里：注释不会红，超时被改回 60 时没有任何东西报警。
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[1]
_CI = _REPO_ROOT / ".github" / "workflows" / "ci.yml"

# 实测常态合计的上沿（33~39 min）+ 离群余量。取 90 而非更小：单步离群实测到 50m37s，
# 三步之和最坏约 70min，90 才有真正余量。
_MIN_SANE = 70


@pytest.fixture(scope="module")
def ci() -> dict:
    return yaml.safe_load(_CI.read_text(encoding="utf-8"))


def test_backend_test_timeout_covers_all_three_steps(ci: dict):
    """`backend-test` 的 job 预算必须 ≥ 三步之和的最坏上沿。"""
    t = ci["jobs"]["backend-test"].get("timeout-minutes")
    assert isinstance(t, int), f"backend-test 未设整数 timeout，实际 {t!r}"
    assert t >= _MIN_SANE, (
        f"backend-test timeout={t}min 低于三步之和的最坏上沿 {_MIN_SANE}min——"
        f"单步 3 倍抖动就会把 job 顶穿并让后续步骤静默不跑（#3576 实测）"
    )


def test_backend_test_still_declares_all_three_test_steps(ci: dict):
    """本判据的前提：job 真的串着三个测试步骤。少一个则超时基准需重算。"""
    names = {s.get("name") for s in ci["jobs"]["backend-test"]["steps"]}
    for step in ("Run backend tests", "Run agent tests", "Run repo-level tests"):
        assert step in names, f"backend-test 少了步骤 {step}，超时基准需重算"


def test_other_jobs_timeout_untouched(ci: dict):
    """只动 backend-test：另两个全量 job 的超时不该被顺带改。"""
    assert ci["jobs"]["frontend-check"].get("timeout-minutes") == 60
    assert ci["jobs"]["docker-build"].get("timeout-minutes") == 60


def test_backend_test_remains_excluded_from_pr_path(ci: dict):
    """本改动不改变门控：backend-test 仍只在全量跑（PR 侧由 #3572 的信息性 job 承担）。"""
    assert ci["jobs"]["backend-test"].get("if") == "github.event_name != 'pull_request'"
