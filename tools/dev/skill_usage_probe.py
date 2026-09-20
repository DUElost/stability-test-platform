#!/usr/bin/env python3
"""每日 skill 用量巡检的执行者：跑判据、落 node-exporter 指标、按语义决定退出码。

存在理由（#2881）：#2785 把 skill 用量探针接上 timer 后，「有 HOLLOW」这件事的**唯一出口**
是 unit failed——而 node-exporter 没开 systemd collector、探针也不落任何指标，运维不轮询
failed unit 时它等于不存在（「探针跑了没人看」）。本文件补上指标出口，形态与
`tools/dev/script_guard_probe.py`（#735 的同形缺口）一致。

权限与源路径：textfile 生产者按本仓惯例以 root 跑（`stp-script-guard` / `stp-mem-top` 同款，
指标目录 root:root），而 skill 转录在**部署用户**家目录里（`stp-skill-usage.service` 此前
正是为此降权）。故本单元改为 root 跑 + 显式把两个源目录传给 report（走 **CLI 而非 env**：
新环境变量要进 `environment-variables.md` 与 env 门禁，unit 里直接写进 ExecStart 就够了）。

退出码契约（回答「这次任务失败了吗」，不是「有没有洞」）：

| report --json | 含义 | 本任务 | 指标 |
|---|---|---|---|
| 正常输出、强信号源在场 | 有洞数 N（N≥0） | exit 0（数据交给人读，不是任务失败） | hollow=N, unknown=0 |
| 强信号源缺源（#2851 语义） | 结论未知 | exit 0 | unknown=1（此时 hollow 读数无意义） |
| 没走到输出那一步 | 判据不可信 | **exit 1**（systemd failed ⇒ 既有告警面） | broken=1 |

`hollow>0` 与 `last_run` 陈旧由 `deploy/prometheus/alerts-stability-platform.yml` 的两条规则
消费；指标写不出去即当场失败——不做「跑成功了但没人知道」的静默停摆。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.dev.textfile_metrics import render_gauges, write_atomic  # noqa: E402

DEFAULT_METRICS_PATH = "/var/lib/prometheus/node-exporter/stp-skill-usage.prom"
REPORT_MODULE = "tools.dev.skill_usage_report"

_METRIC_HELP = {
    "stp_skill_usage_hollow": "达到分型观察窗且强信号零调用的 skill 数（洞态）",
    "stp_skill_usage_unknown": "1 = 强信号源不在场，结论未知（不得当成 0 个洞）",
    "stp_skill_usage_broken": "1 = 探针自身异常（不是「没有洞」）",
    "stp_skill_usage_last_run": "上次成功执行巡检的 unix 时间戳（发现探针静默停摆）",
}


def transcript_dir_for(home: Path, deploy_root: Path) -> Path:
    """Claude 转录目录的推导（与 `skill_usage_report.TRANSCRIPT_DIR` 的编码一致）。

    会话转录按**工作目录**分目录，编码规则是绝对路径的 `/` 换成 `-`：
    `/home/u/stp` → `~/.claude/projects/-home-u-stp`。root 跑时 `~` 是 /root，故必须由
    部署用户的家目录推导，否则找不到源（旧单元靠 `User=` 降权解决，本单改用显式路径）。
    """
    encoded = str(deploy_root).replace("/", "-")
    return home / ".claude" / "projects" / encoded


def run_report(
    python_exe: str, *, transcript_dir: Path | None, codex_dir: Path | None
) -> tuple[int, dict]:
    """跑 `--json`，返回 (退出码, payload)；payload 解析失败按 broken 处理。"""
    argv = [python_exe, "-m", REPORT_MODULE, "--json"]
    if transcript_dir is not None:
        argv += ["--transcript-dir", str(transcript_dir)]
    if codex_dir is not None:
        argv += ["--codex-dir", str(codex_dir)]
    # 解释器缺失/超时同属「工具坏了」：必须折成 broken 并照样写指标，不能冒 traceback
    # ——那样指标停在旧值（hollow=0），消费方读到的是「探针说干净」，而探针其实已经死了。
    try:
        proc = subprocess.run(
            argv, cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=600
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, {"_stderr_tail": f"{type(exc).__name__}: {exc}"}
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        payload = {
            "_stdout_tail": proc.stdout[-400:],
            "_stderr_tail": proc.stderr[-400:],
        }
    return proc.returncode, payload


def summarize(rc: int, payload: dict) -> tuple[dict[str, float], int]:
    """纯函数：把 (退出码, payload) 折成 (指标, 任务退出码)。

    判据以 **payload** 为准（它是判据端的结构化结论），rc 只用来识别「没走到输出」：
    两者矛盾时按更坏的读法处理——payload 缺失就是 broken，绝不粉饰成「没有洞」。
    """
    if not isinstance(payload, dict) or "hollow" not in payload:
        return {"hollow": 0.0, "unknown": 0.0, "broken": 1.0}, 1
    hollow = float(payload.get("hollow") or 0)
    if not payload.get("strong_source_present", True):
        # #2851：缺源 ⇒「零调用」与「没数据」不可分，hollow 读数无意义 ⇒ 折 unknown；
        # 同时把 hollow 原样带出（人工读时仍在），但告警只看 unknown。
        return {"hollow": hollow, "unknown": 1.0, "broken": 0.0}, 0
    return {"hollow": hollow, "unknown": 0.0, "broken": 0.0}, 0


def render_metrics(values: dict[str, float], *, ran_at: int) -> str:
    mapping = {
        "stp_skill_usage_hollow": f"{values['hollow']:g}",
        "stp_skill_usage_unknown": f"{values['unknown']:g}",
        "stp_skill_usage_broken": f"{values['broken']:g}",
        "stp_skill_usage_last_run": str(int(ran_at)),
    }
    return render_gauges(_METRIC_HELP, mapping)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--metrics-path", default=DEFAULT_METRICS_PATH)
    parser.add_argument("--python", default=sys.executable, help="跑判据的解释器（默认当前）")
    parser.add_argument(
        "--home",
        default=None,
        help="部署用户家目录（root 跑时用它推导转录路径；缺省则不传，由 report 按当前用户推导）",
    )
    parser.add_argument("--deploy-root", default=str(REPO_ROOT))
    args = parser.parse_args(argv)

    home = Path(args.home).expanduser() if args.home else None
    deploy_root = Path(args.deploy_root)
    transcript_dir = transcript_dir_for(home, deploy_root) if home else None
    codex_dir = (home / ".codex" / "sessions") if home else None

    rc, payload = run_report(
        args.python, transcript_dir=transcript_dir, codex_dir=codex_dir
    )
    values, exit_code = summarize(rc, payload)
    ran_at = int(time.time())
    try:
        write_atomic(Path(args.metrics_path), render_metrics(values, ran_at=ran_at))
    except OSError as exc:
        print(f"PROBE BROKEN: 指标写入失败 {args.metrics_path}: {exc}", file=sys.stderr)
        return 1

    if values["broken"]:
        tail = ""
        if isinstance(payload, dict):
            tail = (payload.get("_stderr_tail") or payload.get("_stdout_tail") or "")[-300:]
        print(f"PROBE BROKEN: 判据端异常（report rc={rc}）{tail}", file=sys.stderr)
    elif values["unknown"]:
        print(
            "PROBE UNKNOWN: 强信号源不在场——洞数结论未知，不降级为「零个洞」，需人工核查",
            file=sys.stderr,
        )
    elif values["hollow"]:
        print(
            f"PROBE HOLLOW: {values['hollow']:g} 个 skill 达到观察窗且强信号零调用——"
            "删除或改写 description 触发词（人工裁决，本任务不自动动手）"
        )
    else:
        print("PROBE OK: 无洞态 skill")
    print(f"metrics -> {args.metrics_path}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
