"""CI workflow 结构探针（#2641）：定位**真的在执行**的步骤，而不是文本/注释。

背景：三个结构守卫（离线子集 / promtool 场景闸门 / lock-order PR 路径）此前的判据是
「step 名 + run 全文子串」。#2445 修过一次（锚点从全文子串改为按 step 名定位），但
**同形态的另一半**仍在：`run` 里的**注释行**、以及**已不再执行该命令的步骤**，照样
能满足判据。真实经过（#2641，tip `f1179f95`）：PR 路径的 pytest 调用被搬进**并行子
shell** 后，旧判据所在的 step 只剩 `test -f repo.rc` 与两条 `# --ignore=…` 注释，
而把真实调用整段删掉，三个守卫**仍然全绿**。

本模块把「真实执行者」的定义固定下来，供各守卫共用：

- :func:`code_lines` —— 剥掉空行与 ``#`` 注释行（只留真的会被 shell 执行的行）；
- :func:`steps_running` —— ``run`` 的**代码**里含指定 needle 的 ``(job, step)``；
- :func:`pr_reachable` —— job 是否会在 ``pull_request`` 事件上运行（无 ``if`` 视为会跑）。

判据因此从「有人**提到**这段文本」升级为「这段文本出现在某个**真会跑**的步骤的
**代码**里」。守卫自身用合成 workflow 做红绿自证（见各守卫文件内的用例）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"


def load_jobs(path: Path = CI_YML) -> dict[str, Any]:
    """ci.yml 的 ``jobs`` 映射（结构变化直接抛错，不静默空跑）。"""
    import yaml

    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    jobs = (data or {}).get("jobs")
    assert isinstance(jobs, dict) and jobs, f"{path.name} 结构变化：jobs 缺失或为空"
    return jobs


def is_pr_reachable(job: dict[str, Any]) -> bool:
    """job 是否在 pull_request 事件上运行。

    只认两种真值：无 ``if``（所有事件都跑）、``if`` 里出现 ``== 'pull_request'``；
    ``!= 'pull_request'`` 属夜间全量，不算。
    """
    cond = job.get("if")
    if cond is None:
        return True
    text = str(cond)
    return "pull_request" in text and "!=" not in text


def run_text(step: dict[str, Any]) -> str:
    run = step.get("run", "")
    return run if isinstance(run, str) else "\n".join(run)


def code_lines(run: str) -> list[str]:
    """``run`` 里真的会被执行的命令行（剥空行与 ``#`` 注释行）。"""
    out: list[str] = []
    for line in run.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        out.append(stripped)
    return out


def code_text(step: dict[str, Any]) -> str:
    """``step['run']`` 的代码部分（注释与空行已剥），便于子串判据。"""
    return "\n".join(code_lines(run_text(step)))


def steps_running(
    needle: str,
    *,
    only_pr: bool | None = None,
    jobs: dict[str, Any] | None = None,
) -> list[tuple[str, int, dict[str, Any]]]:
    """返回 ``run`` **代码**里含 ``needle`` 的 ``(job 名, step 序号, step)``。

    ``only_pr=True`` 只收 PR 可达 job，``False`` 只收夜间（非 PR）job，``None`` 不限。
    """
    table = jobs if jobs is not None else load_jobs()
    found: list[tuple[str, int, dict[str, Any]]] = []
    for job_name, job in table.items():
        if only_pr is not None and is_pr_reachable(job) is not only_pr:
            continue
        for index, step in enumerate(job.get("steps") or []):
            if needle in code_text(step):
                found.append((job_name, index, step))
    return found
