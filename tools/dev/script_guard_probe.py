#!/usr/bin/env python3
"""每日 `--guard` 巡检的执行者：跑判据、落 node-exporter 指标、按语义决定退出码。

存在理由（2026-09-17 审计 A）：#735 把退役判据固化成 `--guard` 后，全仓**没有任何执行者**
——文档却写着「由运维或定时任务跑」。判据、退出码、工具三层齐备而零执行，等于没有守卫。
本文件补齐「最后一跳」，同时守住一条边界：**只读**，绝不代替人工授权去退役任何东西。

退出码契约（与 `--guard` 的 0/1/2/3 不同层，这里回答的是「这次任务失败了吗」）：

| `--guard` | 含义 | 本任务 | 指标 |
|---|---|---|---|
| 0 | 无到期项 | exit 0 | due=0 |
| 1 | 有应退役而未退役的版本 | **exit 0**（有活要干 ≠ 任务失败） | due=N |
| 2 | 使用事实不可得 | exit 0（巡检成功执行，结论未知要显式暴露） | unknown=1 |
| 3 | 工具自身异常 | **exit 1**（systemd failed ⇒ 进既有告警面） | broken=1 |

⚠ 码 1 有第二种来源（#2797）：判据在 **import 期**就炸（module 级 `resolve_database_url()`
/ `create_engine`，发生在 `main()` 的 try 之前）时，解释器的默认退出码恰好也是 1。故
`summarize` 以「payload 是否带 `guard` 块」区分真判定与进程早死：不带 ⇒ broken（见其实现）。

1/2 不 fail 的理由：让 timer 因「存在待授权退役项」天天 failed，会把真正的工具故障淹死在
告警疲劳里；到期数量与「未知」是**数据**，交给指标与人消费，而不是任务状态。
`due=0` 必须能与「从没跑过」区分 ⇒ 另出 `last_run` 指标；指标写不出去就当场失败，不做
「跑成功了但没人知道」的静默停摆。

实际退役动作只有一条路径：人工复核 `tools/dev/retire_script_versions.py plan` 产出的
manifest 之后跑 `execute --yes`。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DEFAULT_METRICS_PATH = "/var/lib/prometheus/node-exporter/stp-script-guard.prom"
MODULE = "backend.scripts.check_unreferenced_script_versions"

# 判定码与 backend/scripts/check_unreferenced_script_versions.py 的契约一致。这里刻意
# 不 import 那个模块：连「import 它都失败」的形态也必须能被执行者识别成 broken，
# 而不是跟着一起炸成不可解释的状态。
GUARD_OK = 0
GUARD_DUE = 1
GUARD_UNKNOWN = 2
GUARD_ERROR = 3

_METRIC_HELP = {
    "stp_script_guard_due": "超期零引用仍活跃、待人工授权退役的脚本版本数",
    "stp_script_guard_unknown": "1 = 执行事实维度不可得（结论未知，不得当成 0）",
    "stp_script_guard_broken": "1 = 巡检工具自身异常（不是「没有到期项」）",
    "stp_script_guard_last_run": "上次成功执行巡检的 unix 时间戳（发现守卫静默停摆）",
}


def _git(repo_root: Path, *args: str) -> tuple[int, str]:
    rc = subprocess.run(["git", "-C", str(repo_root), *args],
                        capture_output=True, text=True)
    return rc.returncode, (rc.stdout or "").strip()


def describe_guard_source(repo_root: Path = REPO_ROOT) -> tuple[str, bool]:
    """(判据来源描述, 是否等于 origin/main 的内容)——只用于日志归因。

    为什么需要：unit 的 `WorkingDirectory` 与 `run_guard` 的 `cwd=REPO_ROOT` 都指向**主检出**，
    所以每日巡检用的判据代码是那棵工作树的当前内容，而不是 main。别家会话把检出切到自己分支
    并改动 `script_retirement.py` 之后，巡检会静默采用它、给出一个看起来正常的 due 数——这正是
    ADR-0046 说的「部署源 vs 开发工作区」冲突蔓延到只读巡检上。

    这里只解决**事后能归因**（journal 留一行），不改判定码、不加指标、不加告警：实时防护仍靠
    `due/unknown/broken/last_run` 四值与「生产按 runbook 保持在 main」。与
    `tools/dev/check-monitoring-assets.py::describe_source_repo` 是同一逻辑的第二处使用——
    第三处出现时抽公共 helper（已在 note 的 Revisit 记名），不在这里提前抽象。
    """
    rc, sha_short = _git(repo_root, "log", "-1", "--format=%h")
    if rc != 0:
        return f"{repo_root} @ (非 git 树)", False
    _, sha = _git(repo_root, "rev-parse", "HEAD")
    rc_branch, name = _git(repo_root, "symbolic-ref", "--short", "HEAD")
    label = name if (rc_branch == 0 and name) else "(detached)"
    rc_main, main_sha = _git(repo_root, "rev-parse", "origin/main")
    if rc_main != 0 or not main_sha:
        return f"{repo_root} @ {label} {sha_short}（无 origin/main 引用可比）", False
    if sha and sha == main_sha:
        return f"{repo_root} @ {label} {sha_short} == origin/main", True
    return (f"{repo_root} @ {label} {sha_short} ≠ origin/main {main_sha[:7]}", False)


def run_guard(python_exe: str, today: str | None) -> tuple[int, dict]:
    """跑 `--guard --json`，返回 (退出码, payload)；payload 解析失败按 broken 处理。"""
    argv = [python_exe, "-m", MODULE, "--guard", "--json"]
    if today:
        argv += ["--today", today]
    # 解释器缺失 / 不可执行 / 超时都属「工具坏了」：必须**折成 broken 并照样写指标**，
    # 不能让它冒成 traceback——那样指标停在旧值（`due=0`），消费方读到的是"守卫说干净"，
    # 而守卫其实已经死了。这是本文件对自己定的契约：宁可报 broken，不可报干净。
    try:
        proc = subprocess.run(argv, cwd=str(REPO_ROOT), capture_output=True,
                              text=True, timeout=600)
    except (OSError, subprocess.SubprocessError) as exc:
        return GUARD_ERROR, {"_stderr_tail": f"{type(exc).__name__}: {exc}"}
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        payload = {"_stdout_tail": proc.stdout[-400:], "_stderr_tail": proc.stderr[-400:]}
    return proc.returncode, payload


def summarize(rc: int, payload: dict) -> tuple[dict[str, float], int]:
    """纯函数：把 (退出码, payload) 折成 (指标, 任务退出码)。

    以 `--guard` 的**退出码**为准（那才是契约），payload 只用于取数量；两者矛盾时按
    更坏的读法处理——码说 broken 就是 broken，绝不粉饰。

    #2797：`GUARD_DUE=1` 与「判据 import 期就炸」的**解释器默认退出码同码**（module 级
    `resolve_database_url()` / `create_engine` 都发生在 `main()` 的 try 之前）。判据 = payload
    是否带本工具约定的 `guard` 块：带 ⇒ 真判定（1=有活要干，任务成功）；不带 ⇒ 进程没走到
    输出那一步，折成 broken——否则守卫的死讯会被读成「有活要干」，broken 永不置位。
    """
    guard = payload.get("guard") if isinstance(payload, dict) else None
    violations = float((guard or {}).get("violations", 0) or 0)
    if rc == GUARD_OK:
        return {"due": violations, "unknown": 0.0, "broken": 0.0}, 0
    if rc == GUARD_DUE:
        if guard is None:
            return {"due": 0.0, "unknown": 0.0, "broken": 1.0}, 1
        # 码说「有到期项」而 payload 给不出数量：显示 1 而不是 0——把脏读成干净更糟。
        return {"due": max(violations, 1.0), "unknown": 0.0, "broken": 0.0}, 0
    if rc == GUARD_UNKNOWN:
        return {"due": 0.0, "unknown": 1.0, "broken": 0.0}, 0
    return {"due": 0.0, "unknown": 0.0, "broken": 1.0}, 1


def render_metrics(values: dict[str, float], *, ran_at: int) -> str:
    # 时间戳必须是整值字面量：`%g` 会把 1789600000 渲染成 `1.7896e+09`，
    # node_exporter 的 textfile 解析器对指数形式并不友好（单测实测抓到）。
    mapping = {
        "stp_script_guard_due": f"{values['due']:g}",
        "stp_script_guard_unknown": f"{values['unknown']:g}",
        "stp_script_guard_broken": f"{values['broken']:g}",
        "stp_script_guard_last_run": str(int(ran_at)),
    }
    lines: list[str] = []
    for name, val in mapping.items():
        lines.append(f"# HELP {name} {_METRIC_HELP[name]}")
        lines.append(f"# TYPE {name} gauge")
        lines.append(f"{name} {val}")
    return "\n".join(lines) + "\n"


def write_metrics(path: Path, text: str) -> None:
    """原子替换：node_exporter 可能正在读，半截文件会被解析成脏数据。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    # 只留 CLI 参数、不读环境变量：新环境变量要进 environment-variables.md 清单与
    # env 门禁，而 unit 文件里直接把路径写进 ExecStart 就够了——少一个变量少一处治理面。
    parser.add_argument("--metrics-path", default=DEFAULT_METRICS_PATH)
    parser.add_argument("--today", help="巡检基准日 YYYY-MM-DD（复算/测试用）")
    parser.add_argument("--python", default=sys.executable, help="跑判据的解释器（默认当前）")
    args = parser.parse_args(argv)

    # run_guard 已把 OSError / 超时全部折成 GUARD_ERROR，这里不再需要兜异常。
    rc, payload = run_guard(args.python, args.today)

    values, exit_code = summarize(rc, payload)
    ran_at = int(time.time())
    try:
        write_metrics(Path(args.metrics_path), render_metrics(values, ran_at=ran_at))
    except OSError as exc:
        print(f"GUARD BROKEN: 指标文件写入失败 {args.metrics_path}: {exc}", file=sys.stderr)
        return 1

    if values["broken"]:
        tail = ""
        if isinstance(payload, dict):
            tail = (payload.get("_stderr_tail") or payload.get("_stdout_tail") or "")[-300:]
        print(f"GUARD BROKEN: 巡检工具自身异常（guard rc={rc}）{tail}", file=sys.stderr)
    elif values["unknown"]:
        print("GUARD UNKNOWN: 执行事实维度不可得——不降级为「零使用」，需人工核查",
              file=sys.stderr)
    elif values["due"]:
        print(f"GUARD DUE: {values['due']:g} 条超期零引用仍活跃，"
              "需人工复核后 execute --yes（本任务不自动写）")
    else:
        print("GUARD OK: 无到期项")
    source, on_main = describe_guard_source()
    print(f"guard source: {source}"
          + ("" if on_main else "  ⚠ 判据取自主检出的当前版本，不是 origin/main"
             "：结论只适用于那棵树"))
    print(f"metrics -> {args.metrics_path}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
