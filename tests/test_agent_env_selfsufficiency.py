"""#2428：Agent 测试套件的 env 边界归套件自己——不由调用方代供。

`backend/core/security.py` 在**导入期**硬校验 `JWT_SECRET_KEY`。CI 的 `pr-agent-tests`
job 与本地 `run_gates.py` 的 `agent-tests` gate 都往环境里注了这个值，于是文档推荐的
入口 `python -m pytest backend/agent/tests` 在干净 shell 下**恒红 23 例**
（test_saq_scan_pipeline 16 / test_p3_3_multi_instance 4 / test_legacy_tool_cleanup 2 /
test_cron_scheduler 1），而 CI 全绿——改动者分不清「我改坏了」还是「环境本红」，只能
stash 复跑自证（#2414 现场踩中）。issue 把它记成「缺 saq/redis 本地依赖」，实测根因
只有一个 ambient 变量，与 saq/redis 无关。

修法与 #1295 对 `DATABASE_URL` 的处理同形：conftest 在导入任何 `backend.agent.*` 之前
`setdefault`。本文件钉四件事，前两件是**行为判据**（文本匹配骗不过顺序与覆盖语义）：

1. ambient **没有** `JWT_SECRET_KEY` 时，执行 conftest 后该值必须存在（缺 setdefault、
   或把它挪到 `backend.agent` 导入之后 → 执行期直接 RuntimeError，本判据红）；
2. ambient **有**该值时，必须原样保留（改成无条件赋值 = 覆盖调用方注入 → 红）；
3. `ci.yml` 的「Run agent tests」step 必须 `env -i` 剥环境真跑；
4. `run_gates.py` 的 `agent-tests` gate 必须同口径（#825 gate-parity）。

3/4 是「去遮蔽」判据：只修 conftest 而 CI 仍代供，缺口下次会长回来且没人看得见。
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFTEST_REL = "backend/agent/tests/conftest.py"
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"
RUN_GATES = REPO_ROOT / "scripts" / "run_gates.py"

_PROBE = (
    "import os, importlib.util as u;"
    f"s=u.spec_from_file_location('agent_conftest', {CONFTEST_REL!r});"
    "m=u.module_from_spec(s); s.loader.exec_module(m);"
    "print(os.environ.get('JWT_SECRET_KEY', '<ABSENT>'))"
)


def _exec_conftest(**extra_env: str) -> tuple[str, str]:
    """在只带 PATH/PYTHONPATH 的环境里执行 conftest，返回 (打印出的值, stderr)。"""
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONPATH": "."}
    env.update(extra_env)
    r = subprocess.run([sys.executable, "-c", _PROBE], cwd=REPO_ROOT, env=env,
                       capture_output=True, text=True)
    return r.stdout.strip(), r.stderr


def test_conftest_supplies_the_secret_when_ambient_is_clean():
    out, err = _exec_conftest()
    assert out not in ("", "<ABSENT>"), (
        "agent conftest 没有给出 JWT_SECRET_KEY——干净 shell 下整个套件恒红 "
        f"23 例（#2428）。stderr 尾部：\n{err[-400:]}"
    )


def test_conftest_does_not_clobber_caller_supplied_secret():
    out, _err = _exec_conftest(JWT_SECRET_KEY="caller-wins-value")
    assert out == "caller-wins-value", (
        f"调用方显式注入的 JWT_SECRET_KEY 被 conftest 覆盖为 {out!r}："
        "必须用 setdefault 而不是赋值（CI 侧 job env / 排障时的显式覆盖要仍然生效）"
    )


def test_ci_runs_agent_tests_with_ambient_env_stripped():
    """CI 的 agent 真跑必须 `env -i`，否则它替套件把缺口遮掉。"""
    text = CI_YML.read_text(encoding="utf-8")
    match = re.search(
        r"- name: Run agent tests\n\s+run:\s*(.+)", text
    )
    assert match, "未找到 ci.yml 的「Run agent tests」step（改了名字请同步本判据）"
    command = match.group(1).strip()
    assert command.startswith("env -i"), (
        f"CI 的 agent 真跑没有剥环境（{command}）：job 级 JWT_SECRET_KEY 会再次遮蔽"
        "套件自身的 env 缺口（#2428 的成因机制）"
    )
    assert "backend/agent/tests" in command, "判据取错 step"


def test_local_gate_matches_ci_agent_step():
    """本地 `agent-tests` gate 与 CI 同口径（#825：不得「本地绿、CI 红」或反之）。"""
    text = RUN_GATES.read_text(encoding="utf-8")
    # 捕获组只取引号本身（连 `f` 一起捕会让反向引用永远配不上）
    match = re.search(
        r'"agent-tests": \(\s*f(["\'])(.+?)\1,\s*\w+,\s*([^,\n]+),', text, re.S
    )
    assert match, "未解析到 run_gates.py 的 agent-tests gate（结构变了请同步本判据）"
    command, env_arg = match.group(2), match.group(3).strip()
    assert "env -i" in command and "backend/agent/tests" in command, (
        f"本地 agent-tests gate 口径已变（{command}）：必须与 ci.yml 同为 env -i 真跑"
    )
    assert env_arg == "None", (
        f"agent-tests gate 又恢复注入 env（{env_arg}）——那正是被 #2428 移除的遮蔽层："
        "gate 代供 JWT_SECRET_KEY 会让套件的 env 缺口只在本地暴露、CI 全绿"
    )
