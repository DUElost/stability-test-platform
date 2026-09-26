"""#1707：PR 路径「离线子集」与容器依赖测试的对应关系守卫。

背景：#1569 把根 `tests/` 前移进 required check `pr-agent-tests`，但原注释断言
「除 `test_alembic_upgrade.py` 外全部纯离线」**与事实不符**——
`tests/test_script_seed_governance.py` 同样在模块级 fixture 里起
`PostgresContainer("postgres:16")`，且无 docker 探测/skip 兜底。当时漏判的原因：
只按镜像名 grep，而该文件用的是 `PostgresContainer(...)` 构造器（镜像名在参数里）。

后果（潜在，非当前故障）：required check 会依赖 docker 与镜像拉取；runner 无 docker
时该文件**硬失败**（实测 `DOCKER_HOST=tcp://127.0.0.1:1` → 3 errors、exit 1），
`pr-agent-tests` 整体红、阻塞全部合入。当前 GitHub runner 有 docker，故未暴露。

本文件把「纯离线 + 秒级」判据锚成结构断言，防止新增容器测试静默混入 PR 路径：

1. 扫描 `tests/*.py`，凡**源码里实际使用 testcontainers**的文件，必须出现在
   `ci.yml` 与 `scripts/run_gates.py` 的 `--ignore` 名单中；
2. 断言两份名单**互相一致**（本地 `repo-tests` 与 CI 同口径，避免判读分叉）。

判据取「源码实际使用」而非「import 了 testcontainers」——后者会误报仅**提及**
该词的正常文件（例如本守卫与 lock 测试在注释/参数里出现 "testcontainers"，
`test_ci_test_db_guard_wiring.py` 亦然）。
"""
from __future__ import annotations

import re
from pathlib import Path

from tests import ci_workflow_probe as probe

REPO_ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = REPO_ROOT / "tests"
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"
RUN_GATES = REPO_ROOT / "scripts" / "run_gates.py"
#: PR 路径「真跑根 tests/」的命令指纹——#2641：判据必须落在**代码行**上，
#: 不能靠 step 名或注释里的同形文本（旧形态正是被 `# --ignore=…` 注释满足的）。
_RUNNER_NEEDLE = "python -m pytest tests/"

# 真实起容器的构造器（模块级/函数级皆算）
_CONTAINER_CTOR = re.compile(r"\bPostgresContainer\s*\(")


# 真实起容器的构造器（模块级/函数级皆算）
#
# 排除本文件自身：其正则字面量本身含有 `PostgresContainer(`，会被自己的扫描命中
# （自指误报，实测于编写时暴露）。守卫不构造任何容器，理应不在名单内。
_SELF = "tests/test_offline_subset_guard.py"


def _files_using_testcontainers() -> set[str]:
    """返回 `tests/` 下**实际构造 testcontainer**的文件名（repo 相对路径）。"""
    found: set[str] = set()
    for path in sorted(TESTS_DIR.glob("test_*.py")):
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel == _SELF:
            continue
        if _CONTAINER_CTOR.search(path.read_text(encoding="utf-8")):
            found.add(rel)
    return found


def _ignore_args(text: str) -> set[str]:
    """从一段文本里提取 `--ignore=<tests/...>` 名单。"""
    return set(re.findall(r"--ignore=(tests/[\w./-]+\.py)", text))


def _ci_runner_steps() -> list[tuple[str, int, dict]]:
    """PR 路径上**真的执行根 tests/** 的步骤（run 的代码行含 pytest tests/）。"""
    return probe.steps_running(_RUNNER_NEEDLE, only_pr=True)


def _ci_ignored() -> set[str]:
    """ci.yml 里**真实执行者**的 `--ignore` 名单（只取代码行，注释不算）。"""
    found: set[str] = set()
    for _job, _index, step in _ci_runner_steps():
        found |= _ignore_args(probe.code_text(step))
    return found


def _run_gates_ignored() -> set[str]:
    """本地 repo-tests 门禁（run_gates.py）的 `--ignore` 名单。"""
    return _ignore_args(RUN_GATES.read_text(encoding="utf-8"))


class TestOfflineSubsetGuard:
    def test_detects_known_container_files(self):
        """自证：扫描器确实能认出已知的容器文件（防正则失效导致空集通过）。"""
        found = _files_using_testcontainers()
        assert "tests/test_alembic_upgrade.py" in found
        assert "tests/test_script_seed_governance.py" in found
        assert "tests/test_seed_default_params_face_3289.py" in found

    def test_no_container_file_misses_ci_ignore_list(self):
        """容器测试必须全部在 ci.yml 的 --ignore 名单里（#1707 主断言）。"""
        found = _files_using_testcontainers()
        ignored = _ci_ignored()
        missing = sorted(found - ignored)
        assert not missing, (
            f"以下测试真实起 testcontainer 但不在 ci.yml 的 --ignore 名单：{missing}。"
            "无 docker 的 runner 上它们会硬失败并阻塞全部合入——请加入 --ignore"
            "（归夜间 backend-test），或为该文件加 docker 不可用即 skip 的守卫。"
        )

    def test_run_gates_matches_ci_ignore_list(self):
        """本地 repo-tests 门禁与 CI 同口径（#1569 的既有约定）。"""
        ci = _ci_ignored()
        local = _run_gates_ignored()
        assert ci == local, (
            f"ci.yml 与 run_gates.py 的 --ignore 名单不一致："
            f"仅 CI={sorted(ci - local)}，仅本地={sorted(local - ci)}"
        )

    def test_ignore_lists_are_not_vacuous(self):
        """名单不得为空（防解析失效把断言变成永真）。"""
        assert _ci_runner_steps(), (
            f"PR 路径找不到真的执行 {_RUNNER_NEEDLE!r} 的步骤——判据失去落点"
        )
        assert _ci_ignored(), "ci.yml 的 --ignore 解析为空（真实执行者里没有参数）"
        assert _run_gates_ignored(), "run_gates.py 的 --ignore 解析为空"

    def test_mentioned_but_unused_files_are_not_flagged(self):
        """判据是「实际使用」而非「提及」——仅提到 testcontainers 的文件不应被计入。"""
        found = _files_using_testcontainers()
        assert "tests/test_ci_test_db_guard_wiring.py" not in found
        assert "tests/test_requirements_lock.py" not in found


class TestStructuralCriterion:
    """#2641：判据必须落在**会执行的代码行**上——注释里的同形文本不算。"""

    def test_comments_do_not_satisfy_the_criterion(self):
        synthetic = {
            "pr-echo": {
                "if": "github.event_name == 'pull_request'",
                "steps": [
                    {
                        "name": "Run repo-level tests",
                        "run": (
                            "test -f /tmp/repo.rc\n"
                            "# --ignore=tests/test_alembic_upgrade.py\n"
                            "# python -m pytest tests/ -q\n"
                        ),
                    }
                ],
            }
        }
        assert probe.steps_running(
            _RUNNER_NEEDLE, only_pr=True, jobs=synthetic
        ) == [], "注释里的同形文本不得算作真实执行者"

    def test_real_invocation_is_found(self):
        synthetic = {
            "pr-real": {
                "if": "github.event_name == 'pull_request'",
                "steps": [
                    {
                        "name": "Run agent tests",
                        "run": (
                            "python -m pytest tests/ -q \\\n"
                            "  --ignore=tests/test_alembic_upgrade.py\n"
                        ),
                    }
                ],
            }
        }
        hits = probe.steps_running(_RUNNER_NEEDLE, only_pr=True, jobs=synthetic)
        assert [job for job, _i, _s in hits] == ["pr-real"]
        assert _ignore_args(probe.code_text(hits[0][2])) == {"tests/test_alembic_upgrade.py"}
