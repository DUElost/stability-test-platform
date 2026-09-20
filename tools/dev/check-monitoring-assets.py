#!/usr/bin/env python3
"""监控/告警资产漂移检测：站点上已装的副本，还和仓库这份事实源一致吗？

存在理由（#735 的第三格，接 #2488）：#2488 查出的事实是「生产 Prometheus 只加载 7/10 那次
手工拷贝的 10 条规则，仓库已有 19 条」，根因是规则文件既不在安装清单、模板也没有
`rule_files` 段。清单补齐后仍缺一环——**「改了仓库、没重跑安装」这类漂移没人发现**，
而下一次漂移几乎必然发生（规则文件是会被继续改的）。

**不新开 timer**：漂移只在部署时点有意义（伴随一次 pull / 重启），所以执行者复用
`tools/dev/check-deploy-source.sh`——它本来就在每次部署前跑、并已挂在 backend unit 的
`ExecStartPre=-` 上，语义正是「盘上现状 ≠ 仓库现状」。检测范围直接取
`monitoring_artifacts()`：**同一事实不留两套清单**。

判定与退出码（沿用 `--guard` 的分档思路：判定码与「无从判定」分开）：
    0 = 无漂移   1 = 有漂移   2 = 无从判定（清单为空 / **仓库源文件读不到** / 全部非判定）
每个资产的五态：
    match     逐字节一致（`<deploy-root>` 按本机仓库根替换后比对）
    drift     内容不一致
    absent    候选落点都不存在 ⇒ 本站可能未装监控栈，**单独报但不算漂移**
    skipped   源文件含无法由本机事实确定的占位符（`<site-id>`、`<prometheus-port>`…）
    source-missing  清单指向的仓库源文件不存在（清单与仓库脱节）⇒ **无从比对，计入 EXIT_UNKNOWN**
                    （#2800：此前记为 SKIPPED、全 SKIP 仍 exit 0，与本文件自己的退出码契约矛盾）
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.site_config.stages import monitoring_artifacts  # noqa: E402

#: 存量部署（installer 之前的机器）使用的发行版落点。本机就没有 `/etc/stp/`，
#: Prometheus 直接读 `/etc/prometheus/prometheus.yml`——只认 installer 路径会让这类
#: 机器永远「全部 absent」，检测退化成零信息。顺序：本站资产路径优先，发行版兜底。
LEGACY_FALLBACKS = {
    "etc/stp/prometheus/prometheus.yml": "etc/prometheus/prometheus.yml",
    "etc/stp/prometheus/rules/alerts-stability-platform.yml": (
        "etc/prometheus/rules/alerts-stability-platform.yml"),
}

#: 能由本机/仓库事实唯一确定的占位符；其余一律 skipped，不猜。
#: `<prometheus-retention>` 直接取 installer 的常量——同一事实源，不复制字面量。
from tools.site_config.stages import PROMETHEUS_RETENTION  # noqa: E402

_RESOLVABLE = {
    "<deploy-root>": None,                      # 运行时探测，见 resolve_deploy_root()
    "<prometheus-retention>": PROMETHEUS_RETENTION,
}
_UNKNOWN_PLACEHOLDER = re.compile(
    r"<(?!deploy-root>)[a-z][a-z0-9-]*>", re.IGNORECASE)

MATCH, DRIFT, ABSENT, SKIPPED, SOURCE_MISSING = "match", "drift", "absent", "skipped", "source-missing"
EXIT_OK, EXIT_DRIFT, EXIT_UNKNOWN = 0, 1, 2


def resolve_deploy_root(explicit: str | None) -> tuple[Path, str]:
    """`<deploy-root>` 的权威来源是**运行中的 backend unit**，不是脚本所在仓库根。

    默认值踩过一次坑：在 `.wt/<slug>` 里跑检测时脚本所在根是 worktree，而生产单元里渲染
    的是主检出路径 ⇒ 9 项里 7 项被判 DRIFT，全是假漂移。systemd 的 `WorkingDirectory=`
    才是"生产到底在哪棵树上"的事实。探测不到才退回脚本所在仓库根（并在输出里说明依据）。
    """
    if explicit:
        return Path(explicit), "--deploy-root 显式给定"
    probe = subprocess.run(["systemctl", "show", "stability-backend", "-p", "WorkingDirectory"],
                           capture_output=True, text=True)
    match = re.search(r"WorkingDirectory=(/\S+)", probe.stdout or "")
    if probe.returncode == 0 and match:
        return Path(match.group(1)), "systemd stability-backend.WorkingDirectory"
    return REPO_ROOT, "回退：脚本所在仓库根（未探测到运行中的 backend unit）"


def resolve_deploy_user(explicit: str | None) -> tuple[str, str]:
    """`<deploy-user>` 的权威来源是**运行中的 backend unit 的 `User=`**（与 `<deploy-root>`
    取 `WorkingDirectory=` 同法）。

    #2866：含该占位符的模板（`stp-skill-usage.service`）此前恒 `skipped` ⇒ 新增采样项 =
    新增盲区（改了 `User=`/`ExecStart=` 也不会判 DRIFT）。探测不到（开发机没有该 unit）
    时返回空串——**此时占位符保持原样、该条目继续 skipped**（不猜、也不假 DRIFT）。
    """
    if explicit:
        return explicit, "--deploy-user 显式给定"
    probe = subprocess.run(["systemctl", "show", "stability-backend", "-p", "User"],
                           capture_output=True, text=True)
    match = re.search(r"User=(\S+)", probe.stdout or "")
    if probe.returncode == 0 and match:
        return match.group(1), "systemd stability-backend.User"
    return "", "未探测到运行中的 backend unit（该占位符保持 skipped）"


def expected_text(source: Path, deploy_root: Path, deploy_user: str = "") -> str:
    """渲染期望内容：只做「本机可确定」的替换，与 installer 的 substitution 集同源子集。

    `deploy_user` 为空 ⇒ **不替换** `<deploy-user>`：残留占位符会让该条目落 `skipped`
    （与「探测不到」同义），而不是拿字面量去比对判出假 DRIFT。
    """
    text = source.read_text(encoding="utf-8")
    for token, value in _RESOLVABLE.items():
        if value is not None:
            text = text.replace(token, value)
    if deploy_user:
        text = text.replace("<deploy-user>", deploy_user)
    return text.replace("<deploy-root>", str(deploy_root))


def _git(repo_root: Path, *args: str) -> tuple[int, str]:
    rc = subprocess.run(["git", "-C", str(repo_root), *args],
                        capture_output=True, text=True)
    return rc.returncode, (rc.stdout or "").strip()


def describe_source_repo(repo_root: Path) -> str:
    """标注事实源是哪棵树、在哪个 revision 上——决定本次比对可不可信。

    实测骗过一次：`--repo-root` 指向主检出，而那棵工作树当时被别的 Execution 切在特性分支
    上 ⇒ 已装规则被比对该分支的**旧**源文件，判出一条假 DRIFT。runbook 路径上
    `check-deploy-source.sh` 已先校验「树在 main」，但直接跑本工具的人没有这道前置，
    所以至少要把它看见——提示不改退出码（保持 WARN 语义）。

    判据是「HEAD 内容是否等于 origin/main」，**不是分支名**——按名字判会同时犯两种错：
    `git worktree add --detach origin/main`（本次做出正确判定用的就是它）与刚开、还没提交的
    特性分支，内容都等于 main 却被警告；反过来「在 main 上但没 fetch」内容已经落后 main，
    按名字判却一片祥和。所以：`HEAD == origin/main` ⇒ 可信，否则提示（不改退出码）。
    """
    rc, sha_short = _git(repo_root, "log", "-1", "--format=%h")
    if rc != 0:
        return (f"{repo_root} @ (非 git 树)  ⚠ 无法确认事实源 revision："
                "本次比对可能不可信")
    _, sha = _git(repo_root, "rev-parse", "HEAD")
    rc_branch, name = _git(repo_root, "symbolic-ref", "--short", "HEAD")
    label = name if (rc_branch == 0 and name) else "(detached)"
    rc_main, main_sha = _git(repo_root, "rev-parse", "origin/main")
    if rc_main != 0 or not main_sha:
        # 没有 origin/main 引用可比（浅克隆、离线、无 remote）：不假装可信
        return (f"{repo_root} @ {label} {sha_short}"
                "  ⚠ 无 origin/main 引用可比：无法确认事实源就是 main")
    if sha and sha == main_sha:
        return f"{repo_root} @ {label} {sha_short}（内容 == origin/main）"
    ahead = f"{sha_short}≠{main_sha[:7]}"
    return (f"{repo_root} @ {label} {ahead}"
            "  ⚠ 事实源不是 origin/main 的内容：比对的是这棵树的当前版本，可能假漂移")


def candidate_paths(destination: str) -> list[str]:
    paths = [destination]
    legacy = LEGACY_FALLBACKS.get(destination)
    if legacy:
        paths.append(legacy)
    return paths


def inspect(
    system_root: Path, deploy_root: Path, repo_root: Path = REPO_ROOT, deploy_user: str = ""
) -> list[dict]:
    """逐资产比对。返回 [{source,destination,hit,state,detail}]，顺序与清单一致。"""
    results: list[dict] = []
    for source_rel, dest_rel, _mode in monitoring_artifacts():
        entry: dict = {"source": source_rel, "destination": dest_rel, "hit": None,
                       "state": ABSENT, "detail": ""}
        src = repo_root / source_rel
        if not src.is_file():
            # #2800：源缺失是「无从比对」，不是「跳过」——漂移检测最该响的形态之一
            # （改了仓库/移动了文件、清单没跟上），静默 exit 0 会让 check-deploy-source
            # 照常打「一致」。
            entry.update(state=SOURCE_MISSING,
                         detail="仓库源文件不存在（清单与仓库已脱节，无从比对）")
            results.append(entry)
            continue
        want = expected_text(src, deploy_root, deploy_user)
        # 残余占位符要在**替换之后**判：先判会把已可确定的（如 <prometheus-retention>
        # 取 installer 常量）也算成不可确定，资产被误记 SKIP、检测面静默变小（实测踩过）。
        residual = _UNKNOWN_PLACEHOLDER.findall(want)
        hit = next((system_root / rel for rel in candidate_paths(dest_rel)
                    if (system_root / rel).is_file()), None)
        if hit is None:
            entry.update(detail=f"落点不存在（候选：{', '.join(candidate_paths(dest_rel))}）")
        elif residual:
            entry.update(hit=str(hit.relative_to(system_root)), state=SKIPPED,
                         detail=f"源含不可由本机确定的占位符 {sorted(set(residual))}")
        else:
            got = hit.read_text(encoding="utf-8", errors="replace")
            entry.update(hit=str(hit.relative_to(system_root)),
                         state=MATCH if got == want else DRIFT,
                         detail="" if got == want else "内容与仓库渲染结果不一致")
        results.append(entry)
    return results


def summarize(results: list[dict]) -> tuple[int, dict[str, int]]:
    """判定码：0=无漂移；1=有漂移；2=无从判定（清单为空 / 源缺失 / 全部非判定）。

    #2800：源缺失（``SOURCE_MISSING``）必须让整体落 2——它意味着「本工具看不见仓库
    事实源」，与「比对后一致」不是一回事；全部条目都是非判定（source-missing/skipped）
    时同理。全 ``absent``（本站未装监控栈）**保持 0**：那是本站的确定事实，不是判不出。
    """
    counts = {s: 0 for s in (MATCH, DRIFT, ABSENT, SKIPPED, SOURCE_MISSING)}
    for item in results:
        counts[item["state"]] += 1
    if not results:
        return EXIT_UNKNOWN, counts
    if counts[DRIFT]:
        return EXIT_DRIFT, counts
    undecided = counts[SOURCE_MISSING] + counts[SKIPPED]
    if counts[SOURCE_MISSING] or undecided == len(results):
        return EXIT_UNKNOWN, counts
    return EXIT_OK, counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--system-root", default="/", help="比对的系统根（测试/多机用）")
    parser.add_argument("--deploy-user", default=None,
                        help="期望内容里 <deploy-user> 的取值；缺省探测运行中的 backend unit 的 User=")
    parser.add_argument("--deploy-root", default=None,
                        help="期望内容里 <deploy-root> 的取值；缺省探测运行中的 backend unit")
    parser.add_argument("--repo-root", default=str(REPO_ROOT),
                        help="事实源仓库根（默认脚本所在仓库；从 worktree 里查生产时用）")
    parser.add_argument("--json", action="store_true", help="机器可读输出")
    args = parser.parse_args(argv)

    system_root = Path(args.system_root)
    deploy_root, reason = resolve_deploy_root(args.deploy_root)
    deploy_user, user_reason = resolve_deploy_user(args.deploy_user)
    results = inspect(system_root, deploy_root, repo_root=Path(args.repo_root),
                      deploy_user=deploy_user)
    exit_code, counts = summarize(results)

    if args.json:
        print(json.dumps({"repo_root": args.repo_root,
                          "repo_source": describe_source_repo(Path(args.repo_root)), "counts": counts, "exit_code": exit_code,
                          "deploy_root": str(deploy_root), "deploy_root_source": reason,
                          "deploy_user": deploy_user, "deploy_user_source": user_reason,
                          "assets": results}, ensure_ascii=False, indent=2))
        return exit_code

    print("# 监控/告警资产漂移检测（事实源：monitoring_artifacts()）")
    print(f"# <deploy-root> = {deploy_root}（依据：{reason}）")
    print(f"# <deploy-user> = {deploy_user or '(未确定)'}（依据：{user_reason}）")
    print(f"# 事实源 = {describe_source_repo(Path(args.repo_root))}")
    for item in results:
        mark = {MATCH: "OK  ", DRIFT: "DRIFT", ABSENT: "ABSENT", SKIPPED: "SKIP ",
                SOURCE_MISSING: "MISS "}[item["state"]]
        where = item["hit"] or item["destination"]
        print(f"  [{mark:5s}] {where}" + (f"  ← {item['detail']}" if item["detail"] else ""))
        if item["state"] == DRIFT:
            print(f"          源文件：{item['source']}（改仓库 + 重跑安装，不要手改站点副本）")
    print(f"  统计：match {counts[MATCH]} · drift {counts[DRIFT]} · absent {counts[ABSENT]} "
          f"· skipped {counts[SKIPPED]} · source-missing {counts[SOURCE_MISSING]}")
    if counts[DRIFT]:
        print("  ⇒ 站点副本落后于/偏离仓库：跑站点安装（installer S2b/S4）或按 runbook 重新渲染。")
    elif counts[SOURCE_MISSING]:
        print("  ⇒ 无从判定：清单指向的仓库源文件缺失（清单与仓库脱节）——"
              "修清单或恢复源文件后重跑（本次不给出「一致」结论）。")
    elif counts[ABSENT]:
        print("  ⇒ 无漂移；ABSENT 项说明本站未装对应资产（可能本来就不启用监控栈）。")
    else:
        print("  ⇒ 无漂移。")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
