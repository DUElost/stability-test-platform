"""#2151：promtool 场景层的 CI 接线契约（PR 路径守卫）。

背景：``tests/test_prometheus_alerts_contract.py::test_alert_scenarios_fire_with_promtool``
原先带 ``skipif promtool 不可用``，而 GitHub runner 不装 promtool → 该用例在 CI **恒 skip**。
于是阈值 / ``for:`` 时间窗 / 注解逐字匹配这三类语义漂移只在装了 promtool 的机器被拦
（= 取决于跑测机器）。#2030 为此补了恒跑的**结构层**，但结构层不覆盖「改了阈值而场景
文件没跟」。#2151 的裁决是：夜间全量 ``backend-test`` 装 **pinned** promtool，并用
``PROMTOOL_REQUIRED`` 让「缺失」从 skip 变 fail。

该方案自身的风险是**它没有测试**：删掉安装步骤、把 ``PROMTOOL_REQUIRED`` 改成假值、
把版本 pin 换成 ``latest``、把安装步骤挪到消费步骤之后——PR 路径全部放行，只能等下次
夜间全量「悄悄不跑」。本文件把接线锚成结构断言，与
``tests/test_ci_test_db_guard_wiring.py`` / ``tests/test_lock_order_pr_path_contract.py`` 同模式：

1. **夜间必备**：``backend-test`` 里有 pinned promtool 安装步骤（版本 + tarball sha256
   双 pin、装完做版本自证），且排在 ``Run repo-level tests`` **之前**（``GITHUB_PATH``
   只影响后续步骤，顺序反了等于没装）；消费步骤注入非假值的 ``PROMTOOL_REQUIRED``；
2. **PR 路径不装**：任何在 ``pull_request`` 上运行的 job 都不得出现 promtool 安装或
   ``PROMTOOL_REQUIRED``——issue 里否决的选项 2（与 ``tests/test_offline_subset_guard.py``
   的离线纯度冲突、且 PR 门禁要窄）需要机械守住，否则会被顺手加回去；
3. **分流语义本体**：``_promtool_path()`` 在「缺失 + required」下 fail、「缺失 + 非
   required」下 skip、「装了」下返回路径——用假 PATH/假可执行文件直接测，不需要真
   promtool，所以这一段也在 PR 路径上真跑（这正是 #2151 唯一能在合入前被验的部分）。

纯离线：只读文本 + 临时目录里的假可执行文件，不起容器、不联网、不调用 promtool。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"

NIGHTLY_JOB = "backend-test"
INSTALL_STEP = "Install pinned promtool"
CONSUMER_STEP = "Run repo-level tests"

_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
# 消费步骤里 promtool 判备假的假值集合——必须与
# tests/test_prometheus_alerts_contract.py::_promtool_required 保持一致（下方有用例对拍）
_FALSY = {"", "0", "false"}


def _jobs() -> dict:
    data = yaml.safe_load(CI_YML.read_text(encoding="utf-8"))
    jobs = data.get("jobs")
    assert isinstance(jobs, dict), "ci.yml 结构变化：jobs 缺失或不是映射"
    return jobs


def _job(name: str) -> dict:
    jobs = _jobs()
    assert name in jobs, f"ci.yml 缺少 job {name!r}（改名或删除会让本守卫静默空跑）"
    return jobs[name]


def _step(job: dict, name: str) -> tuple[int, dict]:
    steps = job.get("steps") or []
    for index, step in enumerate(steps):
        if str(step.get("name") or "").strip() == name:
            return index, step
    raise AssertionError(f"job 中找不到步骤 {name!r}（删除或改名即等于接线丢失）")


def _pr_job_names() -> set[str]:
    """在 pull_request 上会运行的 job 名集合。

    判据照抄 GitHub 语义的两类真值：无 ``if`` = 所有事件都跑；``if`` 里出现
    ``== 'pull_request'`` = PR 跑。``!= 'pull_request'`` 属夜间全量，不在此列。
    """
    names: set[str] = set()
    for name, job in _jobs().items():
        cond = job.get("if")
        if cond is None or ("pull_request" in str(cond) and "!=" not in str(cond)):
            names.add(name)
    return names


class TestNightlyWiring:
    """夜间全量 job 必须真跑场景层，且 pin 是可执行的 pin。"""

    def test_install_step_precedes_consumer(self):
        job = _job(NIGHTLY_JOB)
        install_at, _ = _step(job, INSTALL_STEP)
        consumer_at, _ = _step(job, CONSUMER_STEP)
        assert install_at < consumer_at, (
            f"{INSTALL_STEP} 必须在 {CONSUMER_STEP} 之前：GITHUB_PATH 只对后续步骤生效，"
            "顺序反了场景层又会退回静默 skip（#2151 原缺陷）"
        )

    def test_version_and_tarball_are_double_pinned(self):
        _, step = _step(_job(NIGHTLY_JOB), INSTALL_STEP)
        env = step.get("env") or {}
        version = str(env.get("PROMTOOL_VERSION", ""))
        digest = str(env.get("PROMTOOL_TARBALL_SHA256", ""))
        assert _VERSION_RE.match(version), (
            f"PROMTOOL_VERSION={version!r} 不是显式三段版本——pin 必须是字面量，"
            "latest/范围写法等于把场景层的判定权交回上游发布节奏"
        )
        assert _SHA_RE.match(digest), (
            f"PROMTOOL_TARBALL_SHA256={digest!r} 不是 64 位十六进制摘要——"
            "第三方二进制必须摘要校验（issue 选项 1 的代价项）"
        )

    def test_install_step_verifies_digest_and_self_proves_version(self):
        _, step = _step(_job(NIGHTLY_JOB), INSTALL_STEP)
        run = str(step.get("run", ""))
        assert "sha256sum --check" in run, "缺摘要校验：下载被替换/截断不会红"
        assert 'grep -q "version' in run, (
            "缺版本自证：pin 与实际安装不一致时无人发现，pin 退化成注释"
        )
        assert "latest" not in run, "安装路径不得引用 latest（pin 的判据就是无 latest）"

    def test_consumer_step_requires_promtool(self):
        _, step = _step(_job(NIGHTLY_JOB), CONSUMER_STEP)
        env = step.get("env") or {}
        assert "PROMTOOL_REQUIRED" in env, (
            f"{CONSUMER_STEP} 未注入 PROMTOOL_REQUIRED——promtool 缺失会重新变成静默 skip"
        )
        value = str(env["PROMTOOL_REQUIRED"])
        assert value.strip().lower() not in _FALSY, (
            f"PROMTOOL_REQUIRED={value!r} 是假值，等于没接线"
        )

    def test_consumer_step_runs_root_tests(self):
        _, step = _step(_job(NIGHTLY_JOB), CONSUMER_STEP)
        assert "tests/" in str(step.get("run", "")), (
            f"{CONSUMER_STEP} 不再收集 tests/，PROMTOOL_REQUIRED 就成了空断言"
        )


class TestPrPathStaysOptional:
    """PR 路径保持「可用时跑」——被否决的选项 2 不得复活。"""

    def test_pr_job_classifier_is_not_vacuous(self):
        names = _pr_job_names()
        assert {"lint", "pr-agent-tests"} <= names, f"PR job 判据失效，实际命中 {sorted(names)}"
        assert NIGHTLY_JOB not in names, "判据把夜间全量 job 误认成 PR job，下方断言会假绿"

    def test_no_pr_job_touches_promtool(self):
        problems: list[str] = []
        for name in sorted(_pr_job_names()):
            blob = yaml.safe_dump(_job(name), allow_unicode=True)
            if "promtool" in blob.lower():
                problems.append(f"{name}: 出现 promtool（安装或引用）")
            if "PROMTOOL_REQUIRED" in blob:
                problems.append(f"{name}: 注入 PROMTOOL_REQUIRED")
        assert not problems, (
            "PR 路径必须与 promtool 无关（离线纯度 + PR 门禁要窄，#2151 选项 2 已否决）：\n"
            + "\n".join(problems)
        )


class TestRequiredSemantics:
    """``_promtool_path`` 的 fail/skip 分流——不需要真 promtool，PR 路径即拦得住。"""

    @staticmethod
    def _sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, installed: bool, required):
        monkeypatch.delenv("PROMTOOL_REQUIRED", raising=False)
        if required is not None:
            monkeypatch.setenv("PROMTOOL_REQUIRED", required)
        if installed:
            exe = tmp_path / "promtool"
            exe.write_text("#!/bin/sh\necho fake promtool\n", encoding="utf-8")
            exe.chmod(0o755)
            monkeypatch.setenv("PATH", str(tmp_path))
        else:
            monkeypatch.setenv("PATH", str(tmp_path / "definitely-not-in-path"))

    @pytest.mark.parametrize("required", ["1", "true", "TRUE", "yes", "on"])
    def test_missing_promtool_fails_when_required(self, tmp_path, monkeypatch, required):
        from tests.test_prometheus_alerts_contract import _promtool_path

        self._sandbox(tmp_path, monkeypatch, installed=False, required=required)
        with pytest.raises(pytest.fail.Exception, match="Install pinned promtool"):
            _promtool_path()

    @pytest.mark.parametrize("required", [None, "", "0", "false", "FALSE"])
    def test_missing_promtool_skips_when_optional(self, tmp_path, monkeypatch, required):
        from tests.test_prometheus_alerts_contract import _promtool_path

        self._sandbox(tmp_path, monkeypatch, installed=False, required=required)
        with pytest.raises(pytest.skip.Exception, match="结构层"):
            _promtool_path()

    def test_installed_promtool_is_used_regardless_of_flag(self, tmp_path, monkeypatch):
        from tests.test_prometheus_alerts_contract import _promtool_path

        self._sandbox(tmp_path, monkeypatch, installed=True, required="1")
        assert Path(_promtool_path()).name == "promtool"

    def test_flag_semantics_match_this_guard(self, monkeypatch):
        """本守卫认定的假值集合必须与被测实现一致（否则「假值」断言是自证循环）。"""
        from tests.test_prometheus_alerts_contract import _promtool_required

        # 必须显式清场：夜间全量 job 的 step env 就带着 PROMTOOL_REQUIRED=1，
        # 依赖外部环境会让本用例在 backend-test 里恒红（实测于本地模拟 nightly）。
        monkeypatch.delenv("PROMTOOL_REQUIRED", raising=False)
        assert _promtool_required() is False, "未注入 PROMTOOL_REQUIRED 时默认须为可选"
        for falsy in sorted(_FALSY - {""}):
            monkeypatch.setenv("PROMTOOL_REQUIRED", falsy)
            assert _promtool_required() is False, f"{falsy!r} 两侧判定不一致"

    def test_nightly_env_value_is_accepted_by_implementation(self, monkeypatch):
        """接线值 ↔ 实现判据 的双向锁：ci.yml 写的值必须真能被认成「必备」。"""
        from tests.test_prometheus_alerts_contract import _promtool_path, _promtool_required

        _, consumer = _step(_job(NIGHTLY_JOB), CONSUMER_STEP)
        value = str((consumer.get("env") or {})["PROMTOOL_REQUIRED"])
        monkeypatch.setenv("PROMTOOL_REQUIRED", value)
        monkeypatch.setenv("PATH", "/definitely-not-in-path")
        assert _promtool_required(), f"ci.yml 的 PROMTOOL_REQUIRED={value!r} 未被实现认可"
        with pytest.raises(pytest.fail.Exception):
            _promtool_path()
