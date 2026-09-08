#!/usr/bin/env python3
"""harness_probe.py —— Harness 摄取矩阵探针（ADR-0034 P2 验收 / #855 方向 b 落地）。

第一性设计（见 docs/notes/feature/2026-09-07-harness-ingest-probe.md）：
- 检测对象 = 黑盒观测「各 Harness 对启动契约的加载结果」，不白盒推断加载机制；
- 双题探针是最小完备的：契约层只有两个语义对象（根启动契约 + scoped 真身）；
- 判卷确定性（唯一探针串 + 正则），零 LLM judge——语义层随模型漂移，结构层稳定；
- EXPECTED 显式编码：实际偏离预期即红（**行为漂移检测器**，即使偏离是「变好」——
  行为变化本身就是信号，如 #857 上游修复后对照行变绿提示退役 wrapper）；
- 不进常规 CI：每家一次真实非交互 LLM 会话（30-60s，外部依赖）——挂 check:gov
  手动/低频（ADR-0034 §2.7 P3 同款注意力预算纪律）。

用法:
    python tools/dev/harness_probe.py                 # 跑全部可自动化形态
    python tools/dev/harness_probe.py --only claude-with-root,codex
    python tools/dev/harness_probe.py --json out.json # 结果留档（时间序列）
    python tools/dev/harness_probe.py --self-test     # 纯函数红绿自证（离线）
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PROBE_CWD = os.path.join(ROOT, "backend", "agent")

# 探针目标词：scoped 真身标题（backend/agent/AGENTS.md）与根契约标题。
# 与 ADR-0034 附录 A / G2 验收协议同源——契约演进时同步更新。
SCOPED_MARK = "Agent 侧 scan / upload"
ROOT_MARKS = ("## 总原则", "## 提交前")

PROBE_PROMPT = (
    "不要读取任何文件，不要使用任何工具。仅凭你当前已加载的指令上下文回答，"
    "格式必须是 Q1=是/否 Q2=是/否 Q3=一次/两次/没有："
    f"Q1 你的指令中是否出现{ROOT_MARKS[0]}或{ROOT_MARKS[1]}这样的标题行？"
    f"Q2 你的指令中是否出现『{SCOPED_MARK}』这个标题？"
    f"Q3 该『{SCOPED_MARK}』的内容在你的上下文中出现了几次？"
)

# ── 判卷（纯函数）──

_Q1_RE = re.compile(r"Q1\s*[=:：]\s*(是|否)", re.IGNORECASE)
_Q2_RE = re.compile(r"Q2\s*[=:：]\s*(是|否)", re.IGNORECASE)


def grade(response: str) -> dict:
    """从回答提取 Q1/Q2；任一缺失 → graded=False（不可判卷，如异常/超时）。"""
    q1 = _Q1_RE.search(response)
    q2 = _Q2_RE.search(response)
    if not q1 or not q2:
        return {"graded": False, "q1": None, "q2": None}
    return {"graded": True, "q1": q1.group(1) == "是", "q2": q2.group(1) == "是"}


# ── 形态定义（每行 = 一个探测形态）──
#
# command 模板 {prompt} 为探针 prompt 占位；cwd 相对 ROOT。
# expected 来自 2026-09-06/07 实测（ADR-0034 附录 A）；偏离预期 = FAIL，
# 即使偏离「变好」——行为变化本身就是信号（如 #857 修复 → claude-subdir 变绿
# → 输出会提示 wrapper 可退役）。

FORMS = [
    {
        "id": "claude-with-root",
        "desc": "Claude Code 经 claude_with_root.sh（根供给 wrapper，P2b）",
        "command": "sh {root}/tools/dev/claude_with_root.sh {prompt}",
        "cwd": "backend/agent",
        "expected": {"q1": True, "q2": True},
        "note": "#857 根供给；对照行 claude-subdir-plain 变绿时本形态可退役",
    },
    {
        "id": "claude-subdir-plain",
        "desc": "Claude Code 子目录裸跑（#857 对照组——上游修复监测行）",
        "command": "claude -p {prompt}",
        "cwd": "backend/agent",
        "expected": {"q1": False, "q2": True},
        "note": "预期 Q1=否（@import 子目录不解析，上游 #79046/#87020）；"
                "变 Q1=是 = 上游修复，提示 claude_with_root.sh 退役",
    },
    {
        "id": "codex",
        "desc": "Codex CLI",
        "command": "codex exec --skip-git-repo-check {prompt}",
        "cwd": "backend/agent",
        "expected": {"q1": True, "q2": True},
        "note": "",
    },
    {
        "id": "cursor",
        "desc": "Cursor Agent CLI",
        "command": "cursor-agent -p --trust {prompt}",
        "cwd": "backend/agent",
        "expected": {"q1": True, "q2": True},
        "note": "非交互需 --trust",
    },
    {
        "id": "opencode",
        "desc": "OpenCode",
        "command": "opencode run {prompt}",
        "cwd": "backend/agent",
        "expected": {"q1": True, "q2": True},
        "note": "需本机 opencode.json 在启动目录树内",
    },
    {
        "id": "codebuddy",
        "desc": "CodeBuddy",
        "command": "codebuddy -p {prompt}",
        "cwd": "backend/agent",
        "expected": {"q1": True, "q2": True},
        "note": "零配置",
    },
    {
        "id": "zcode",
        "desc": "Zcode（GUI——不可自动化，人工执行）",
        "command": None,
        "cwd": "backend/agent",
        "expected": {"q1": False, "q2": True},
        "note": "人工：子目录打开 ZCode 窗口，AI 面板新对话粘贴 PROBE_PROMPT；"
                "workspace-only 注入形态（根不注入），结果人工核对 EXPECTED",
    },
]


def build_command(template: str, prompt: str, root: str) -> str:
    """模板填充：prompt 经 shell 引号包裹（#1046）——PROBE_PROMPT 含空格，
    裸插会被 shell 分词，argv 边界依赖各 CLI 对多余位置参数的宽容度，
    升级即静默变 UNGRADED。纯函数（自测共用）。"""
    return template.format(prompt=shlex.quote(prompt), root=root)


def run_form(form: dict, timeout_s: int = 180) -> dict:
    """执行一个形态的探针；返回 {graded, q1, q2, q3, error, seconds}。"""
    if not form["command"]:
        return {"graded": False, "error": "manual（GUI，无自动化通道）"}
    prompt = PROBE_PROMPT
    cmd = build_command(form["command"], prompt, ROOT)
    cwd = os.path.join(ROOT, form["cwd"])
    start = time.time()
    try:
        proc = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True,
                              text=True, timeout=timeout_s)
        response = (proc.stdout or "") + "\n" + (proc.stderr or "")
        error = None if proc.returncode == 0 else f"exit={proc.returncode}"
    except subprocess.TimeoutExpired:
        return {"graded": False, "error": "timeout", "seconds": timeout_s}
    except OSError as exc:
        return {"graded": False, "error": f"not-runnable: {exc}"}
    graded = grade(response)
    graded["error"] = error
    graded["seconds"] = round(time.time() - start, 1)
    return graded


# ── 纯函数：结果 vs 预期（行为漂移检测核心）──

def compare(actual: dict, expected: dict) -> list[str]:
    """返回偏离清单；空 = 与预期一致。graded=False 时返回「不可判卷」而非偏离。"""
    if not actual.get("graded"):
        return [f"不可判卷（{actual.get('error', 'no answer')}）"]
    drifts = []
    for key in ("q1", "q2"):
        if actual[key] != expected[key]:
            drifts.append(f"{key}={actual[key]}（预期 {expected[key]}）")
    return drifts


# ── 矩阵执行 ──

def run_matrix(only: str | None, timeout_s: int, json_out: str | None) -> int:
    probe_prompt_lines = PROBE_PROMPT
    results = []
    for form in FORMS:
        fid = form["id"]
        if only and fid not in only.split(","):
            continue
        if not form["command"]:
            results.append({"id": fid, "manual": True, **form["expected"]})
            print(f"\n== {fid} == [MANUAL] {form['desc']}")
            print(f"    人工：cd {PROBE_CWD} 起新会话（{form['note'][:40]}…），"
                  f"粘贴：\n    {probe_prompt_lines}")
            continue
        print(f"\n== {fid} == {form['desc']}（cwd={form['cwd']}，≤{timeout_s}s）",
              flush=True)
        actual = run_form(form, timeout_s)
        drifts = compare(actual, form["expected"])
        status = "PASS" if actual.get("graded") and not drifts else (
            "UNGRADED" if not actual.get("graded") else "DRIFT")
        results.append({"id": fid, "status": status, "drifts": drifts, **actual})
        if actual.get("graded"):
            print(f"    Q1={'是' if actual['q1'] else '否'} Q2={'是' if actual['q2'] else '否'} "
                  f"Q3={actual.get('q3', '?')} → {status}"
                  + (f"（漂移: {'; '.join(drifts)}）" if drifts else ""))
        else:
            print(f"    不可判卷: {actual.get('error')}")
        if actual.get("error"):
            print(f"    error: {actual['error']}")
    manual = [r for r in results if r.get("manual")]
    auto = [r for r in results if not r.get("manual")]
    ungraded = [r for r in auto if not r.get("graded")]
    drifted = [r for r in auto if r.get("status") == "DRIFT"]
    print(f"\n== 矩阵汇总：{len(auto)} 自动（PASS {len(auto)-len(ungraded)-len(drifted)} / "
          f"UNGRADED {len(ungraded)} / DRIFT {len(drifted)}）+ {len(manual)} 人工 =="
          , flush=True)
    if json_out:
        payload = {
            "date": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "root_version": _git_head(),
            "results": [{k: v for k, v in r.items() if k != "manual"} for r in results],
        }
        with open(json_out, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        print(f"[OK] 结果留档 → {json_out}")
    # 退出码：DRIFT（行为漂移）= 2；UNGRADED（环境不可用）= 0（advisory 语义，
    # 区分「行为变了」与「跑不了」——与 pr-migrate 的 SKIP 纪律一致）
    return 2 if drifted else 0


def _git_head() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True,
                              text=True, cwd=ROOT, check=True).stdout.strip()
    except Exception:
        return "unknown"


def run_self_test() -> int:
    failures: list[str] = []

    def expect(name, cond):
        if not cond:
            failures.append(name)

    # 判卷：正则提取（含全角冒号/空格容错）
    g = grade("Q1=是 Q2=否")
    expect("判卷基础", g["graded"] and g["q1"] is True and g["q2"] is False)
    g = grade("Q1：是\nQ2：否")
    expect("判卷全角冒号", g["graded"] and g["q1"] is True and g["q2"] is False)
    expect("不可判卷", grade("收到")["graded"] is False)

    # compare：偏离检测（含「变好也是漂移」）
    expect("一致无偏离", compare({"graded": True, "q1": True, "q2": True},
                                 {"q1": True, "q2": True}) == [])
    drifts = compare({"graded": True, "q1": False, "q2": True}, {"q1": True, "q2": True})
    expect("q1 偏离检出", drifts == ["q1=False（预期 True）"])
    drifts = compare({"graded": True, "q1": True, "q2": True}, {"q1": False, "q2": True})
    expect("变好也是漂移（#857 修复监测）", len(drifts) == 1)
    expect("不可判卷不算漂移", compare({"graded": False, "error": "timeout"},
                                       {"q1": True}) == ["不可判卷（timeout）"])

    # FORMS 完整性：每行有 command 或 manual 标注；expected 二键
    for form in FORMS:
        expect(f"FORMS {form['id']} expected 二键",
               set(form["expected"].keys()) == {"q1", "q2"})
        expect(f"FORMS {form['id']} command/manual 二选一",
               bool(form["command"]) or form["id"] == "zcode")

    # PROBE_PROMPT 含双题与禁令
    expect("prompt 禁工具", "不要读取任何文件" in PROBE_PROMPT)
    expect("prompt 双题", SCOPED_MARK in PROBE_PROMPT and ROOT_MARKS[0] in PROBE_PROMPT)

    # build_command：prompt 引号安全（#1046）——含空格 prompt 经 shell 分词后
    # 必须原样还原为单个 argv；各 FORMS 模板逐一验证
    for form in FORMS:
        if not form["command"]:
            continue
        filled = build_command(form["command"], PROBE_PROMPT, "/r")
        argv = shlex.split(filled)
        expect(f"build_command {form['id']} prompt 单 argv 还原",
               PROBE_PROMPT in argv)
    nasty = "a b 'c \"d $E `f`"
    argv = shlex.split(build_command("x -p {prompt} {root}", nasty, "/r"))
    expect("build_command 恶意字符还原", argv[2] == nasty and argv[3] == "/r")

    if failures:
        for f in failures:
            print(f"[SELFTEST-FAIL] {f}", file=sys.stderr)
        return 1
    print("[OK] harness_probe self-test 通过（判卷/偏离/FORMS 完整性 红绿双向）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Harness 摄取矩阵探针（ADR-0034 P2/#855-b）")
    ap.add_argument("--only", help="仅跑指定形态（逗号分隔 id）")
    ap.add_argument("--timeout", type=int, default=180, help="单形态超时秒数")
    ap.add_argument("--json", dest="json_out", help="结果 JSON 留档路径")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        return run_self_test()
    return run_matrix(args.only, args.timeout, args.json_out)


if __name__ == "__main__":
    sys.exit(main())
