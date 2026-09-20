#!/usr/bin/env python3
"""skill 用量报告：防「建而不用」的空洞（多 harness 版，#2785）。

用户裁决的前提是「建 skill 必须被用，不许造完就闲置」。本工具把这条变成
可观测事实。

数据源（两路均为本机会话转录，缺源自动跳过——他机/新站点不得确定性红灯，
与 #825 同一原则）：

  1. Claude Code 会话转录（强信号 = Skill 工具调用）
     ~/.claude/projects/<项目>/*.jsonl，流式匹配 `"Skill"` + `tool_use` + slug；
     一行同时命中 → 记一次调用，同行多次出现按出现次数计。
  2. Codex 会话转录（弱信号 = SKILL.md 被读取命令加载）
     ~/.codex/sessions/**/*.jsonl，匹配 cat/sed/head/tail/nl/bat/less 读
     SKILL.md 路径。⚠️ 读取≠触发：2026-09-19 逐会话核验，读取 SKILL.md 的
     会话全是认领轮/审查核验类审计（批量读取=审计指纹）。本列只作人工
     删留裁决的上下文，**不参与 HOLLOW 判定**。

HOLLOW 判据（只依赖强信号「是否为零」——零值可靠，正数为启发式上界），
按 SKILL.md frontmatter `type` 分型（#2785）：

    persistent（缺省）  存在 ≥14 天且 Claude 侧零调用
    event              存在 ≥60 天且 Claude 侧零调用
                       （紧急释放/扩容接入等低频事件场景，低频是场景属性
                       而非废弃证据；2026-09-19 裁决：现行两个 HOLLOW
                       skill 保留并转 event 型）

触发时二选一：删除，或改写 description 让触发词真实可命中——对应
design/2026-08-governance-surface-protection.md §8.1 观察台账。删前须看
Codex 列并抽查会话性质。

退出码契约（stp-skill-usage.timer 消费）：
    0 = 无洞 / 无 skill 登记 / 无任何数据源（不可观测 ≠ 违规，timer 恒绿）
    1 = 有洞且 --strict（unit failed → systemctl/journal 可见）
    工具自身异常 = 未捕获异常非零退出（unit 不得给 ExecStart 加 - 前缀掩盖）

用法:
    python tools/dev/skill_usage_report.py             # 报表
    python tools/dev/skill_usage_report.py --strict    # 有洞 → exit 1
    python tools/dev/skill_usage_report.py --self-test # 离线红绿自证
"""
from __future__ import annotations

import argparse
import json
import glob
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SKILLS_DIR = os.path.join(ROOT, ".claude", "skills")
TRANSCRIPT_DIR = os.environ.get(
    "STP_SKILL_TRANSCRIPT_DIR",
    os.path.expanduser(
        "~/.claude/projects/-home-debian13-stability-test-platform"
    ),
)
CODEX_DIR = os.environ.get(
    "STP_SKILL_CODEX_DIR",
    os.path.expanduser("~/.codex/sessions"),
)

# HOLLOW 观察窗按分型（#2785）：event 型场景数月一遇，14 天窗口会结构性误报。
HOLLOW_DAYS = {"persistent": 14, "event": 60}

# Codex 弱信号：只认「读取类命令 + SKILL.md 路径」，写入（apply_patch/heredoc）
# 与纯提及不计。间隔只排除 JSON 双引号（不跨字段），单引号须放行——sed 带引号
# 参数（sed -n '1,80p' path）是 2026-09-19 实测的主形态。
_CODEX_READ_RE = re.compile(
    r"\b(?:cat|sed|head|tail|nl|bat|less)\b[^\"]{0,80}"
    r"skills/([a-z0-9-]+)/SKILL\.md"
)
_CODEX_SESSION_DATE_RE = re.compile(r"rollout-(\d{4}-\d{2}-\d{2})T")


def inventory(skills_dir: str = SKILLS_DIR) -> list[dict]:
    """盘点仓内 skills：slug / description / 分型 / 出生时间（首次入库时间）。"""
    out = []
    for d in sorted(os.listdir(skills_dir)) if os.path.isdir(skills_dir) else []:
        sk = os.path.join(skills_dir, d, "SKILL.md")
        if not os.path.isfile(sk):
            continue
        text = open(sk, encoding="utf-8").read()
        desc = ""
        nm = d
        stype = "persistent"
        m = re.match(r"^---\n(.*?)\n---", text, re.S)
        if m:
            block = m.group(1)
            fm = dict(re.findall(r"^([\w-]+):\s*(.*)$", block, re.M))
            desc = fm.get("description", "").strip()
            nm = fm.get("name", d).strip()
            raw_type = fm.get("type", "").split("#", 1)[0].strip()
            if raw_type in HOLLOW_DAYS:
                stype = raw_type
            elif raw_type:
                # 未知分型按保守方向降级：persistent 窗口更短、更早亮灯。
                stype = "persistent"
        birth = None
        try:
            ts = subprocess.run(
                ["git", "log", "--diff-filter=A", "--format=%at", "-1", "--",
                 os.path.relpath(sk, ROOT)],
                capture_output=True, text=True, cwd=ROOT,
            ).stdout.strip()
            birth = int(ts) if ts else None
        except Exception:  # noqa: BLE001 —— 观测工具宽容降级
            pass
        out.append({"dir": d, "name": nm, "desc": desc[:60], "birth": birth,
                    "type": stype})
    return out


def scan_claude(claude_dir: str, slugs: list[str]) -> dict[str, tuple[int, int | None]]:
    """强信号：单遍扫描 Claude 转录 → {slug: (调用数, 最近调用 unix 时间|None)}。"""
    counts = {s: 0 for s in slugs}
    last: dict[str, int | None] = {s: None for s in slugs}
    ts_re = re.compile(r'"timestamp":"([^"]+)"')
    for path in glob.glob(os.path.join(claude_dir, "*.jsonl")):
        try:
            with open(path, encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    if '"Skill"' not in line or "tool_use" not in line:
                        continue
                    for s in slugs:
                        hits = line.count(f'"{s}"')
                        if not hits:
                            continue
                        counts[s] += hits
                        m = ts_re.search(line)
                        if m:
                            t = m.group(1).rstrip("Z")
                            for fmt in ("%Y-%m-%dT%H:%M:%S.%f",
                                        "%Y-%m-%dT%H:%M:%S"):
                                try:
                                    epoch = datetime.strptime(
                                        t, fmt).replace(
                                        tzinfo=timezone.utc).timestamp()
                                    last[s] = max(last[s] or 0, int(epoch))
                                    break
                                except ValueError:
                                    continue
        except OSError:
            continue
    return {s: (counts[s], last[s]) for s in slugs}


def scan_codex(codex_dir: str, slugs: list[str]) -> dict[str, tuple[int, str | None]]:
    """弱信号：单遍扫描 Codex 会话 → {slug: (读取会话数, 最近读取日期|None)}。

    按会话文件去重（同一会话读同一 SKILL.md 十遍仍记 1）；日期取 rollout
    文件名时间戳，缺则退回文件 mtime。
    """
    sessions: dict[str, set[str]] = {s: set() for s in slugs}
    last_date: dict[str, str | None] = {s: None for s in slugs}
    for path in glob.glob(os.path.join(codex_dir, "**", "*.jsonl"),
                          recursive=True):
        base = os.path.basename(path)
        md = _CODEX_SESSION_DATE_RE.search(base)
        day = md.group(1) if md else time.strftime(
            "%Y-%m-%d", time.localtime(os.path.getmtime(path)))
        try:
            with open(path, encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    if "/SKILL.md" not in line:
                        continue
                    for m in _CODEX_READ_RE.finditer(line):
                        s = m.group(1)
                        if s in sessions:
                            sessions[s].add(base)
                            if last_date[s] is None or day > last_date[s]:
                                last_date[s] = day
        except OSError:
            continue
    return {s: (len(sessions[s]), last_date[s]) for s in slugs}


def is_hollow(
    age_days: int | None, strong_calls: int, stype: str, *, strong_source_present: bool = True
) -> bool:
    """HOLLOW 判定：强信号为零 × 分型观察窗；出生时间未知不判（证据不足）。

    #2851：**强信号源不在场时不判洞**——`claude` 转录目录缺失而 codex 存在时，
    `claude={}` 会让每个 skill 的 `strong_calls` 都是 0，于是「没有数据」被静默当成
    「零调用」，超过观察窗的 skill 全部误判 HOLLOW（timer 恒红、表格还把未扫描的源
    印成「Claude 调用 0 次 最近 从未」——那是把观测缺口说成观测事实）。
    """
    if age_days is None:
        return False
    if not strong_source_present:
        return False
    return age_days >= HOLLOW_DAYS[stype] and strong_calls == 0


def _fmt_last(epoch: int | None) -> str:
    return (time.strftime("%Y-%m-%d %H:%M", time.localtime(epoch))
            if epoch else "从未")


def _self_test() -> int:
    """红绿自证：合成转录夹具验证两路扫描、分型降级与判洞表（不读真实转录）。"""
    with tempfile.TemporaryDirectory() as td:
        skills = os.path.join(td, "skills")
        claude = os.path.join(td, "claude")
        codex = os.path.join(td, "codex", "2026", "09", "10")
        for d in (skills, claude, codex):
            os.makedirs(d)
        for slug, stype in (("alpha-skill", "persistent"),
                            ("beta-skill", "event"),
                            ("gamma-skill", "weird-type")):
            os.makedirs(os.path.join(skills, slug))
            extra = f"\ntype: {stype}" if stype != "persistent" else ""
            with open(os.path.join(skills, slug, "SKILL.md"), "w",
                      encoding="utf-8") as fh:
                fh.write(f"---\nname: {slug}\n"
                         f"description: 夹具 {slug}{extra}\n---\n\n正文\n")
        # 行内注释须按 YAML 语义剥离后仍识别分型（SKILL.md 实物即此形态）。
        os.makedirs(os.path.join(skills, "delta-skill"))
        with open(os.path.join(skills, "delta-skill", "SKILL.md"), "w",
                  encoding="utf-8") as fh:
            fh.write("---\nname: delta-skill\ndescription: 夹具\n"
                     "type: event  # 低频事件场景\n---\n\n正文\n")

        # 强信号：一条真调用（alpha）；一条含 slug 但缺 tool_use 的干扰行（不得计）。
        with open(os.path.join(claude, "s1.jsonl"), "w", encoding="utf-8") as fh:
            fh.write('{"timestamp":"2026-09-10T10:00:00.000Z","name":"Skill",'
                     '"input":{"skill":"alpha-skill"},"tool_use":true}\n')
            fh.write('{"timestamp":"2026-09-11T10:00:00.000Z","name":"Skill",'
                     '"note":"beta-skill"}\n')
        # 弱信号：Codex 会话 sed 读 beta（计 1 会话）；apply_patch 写 gamma（不计）。
        with open(os.path.join(codex, "rollout-2026-09-12T08-00-00-x.jsonl"),
                  "w", encoding="utf-8") as fh:
            fh.write('{"cmd":"sed -n 1,80p .claude/skills/beta-skill/SKILL.md"}\n')
            fh.write('{"cmd":"apply_patch skills/gamma-skill/SKILL.md"}\n')

        inv = inventory(skills)
        by = {i["name"]: i for i in inv}
        assert by["delta-skill"]["type"] == "event", "带行内注释的 type 须识别"
        assert set(by) == {"alpha-skill", "beta-skill", "gamma-skill",
                           "delta-skill"}, by
        assert by["gamma-skill"]["type"] == "persistent", "未知 type 须降级 persistent"

        cl = scan_claude(claude, list(by))
        expect = int(datetime(2026, 9, 10, 10, 0,
                              tzinfo=timezone.utc).timestamp())
        assert cl["alpha-skill"] == (1, expect), cl["alpha-skill"]
        assert cl["beta-skill"] == (0, None), "缺 tool_use 的行不得计调用"

        cx = scan_codex(os.path.join(td, "codex"), list(by))
        assert cx["beta-skill"] == (1, "2026-09-12"), cx
        assert cx["gamma-skill"] == (0, None), "写入不得计弱信号读取"
        assert cx["alpha-skill"] == (0, None), cx

    # 判洞表：强信号为零 × 分型窗口；age 未知不判。
    assert is_hollow(20, 0, "persistent") is True
    assert is_hollow(20, 0, "event") is False, "event 型 20 天不判洞"
    assert is_hollow(61, 0, "event") is True
    assert is_hollow(20, 3, "persistent") is False, "有强信号调用不判洞"
    assert is_hollow(None, 0, "persistent") is False, "出生时间未知不判洞"

    # 弱信号正则红绿：读取命令集合外的 grep/写入不得命中；带引号 sed 参数须命中。
    hit = _CODEX_READ_RE.search("x cat .claude/skills/abc/SKILL.md y")
    assert hit and hit.group(1) == "abc"
    hit = _CODEX_READ_RE.search("sed -n '1,80p' .claude/skills/abc/SKILL.md")
    assert hit and hit.group(1) == "abc", "带引号 sed 参数是实测主形态"
    assert not _CODEX_READ_RE.search("grep -r skills/abc/SKILL.md .")
    assert not _CODEX_READ_RE.search("apply_patch skills/abc/SKILL.md")

    print("[self-test] OK")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--strict", action="store_true",
                    help="存在洞态 skill 时 exit 1（gate 与 stp-skill-usage.timer 消费）")
    ap.add_argument("--self-test", action="store_true",
                    help="离线红绿自证（不读真实转录）")
    # #2881：textfile 生产者按本仓惯例以 root 跑（stp-script-guard / stp-mem-top 同款），
    # 而转录在**部署用户**家目录里 ⇒ 由探针显式传入源路径（CLI 而非 env：新环境变量要进
    # environment-variables.md 与 env 门禁，unit 直接写进 ExecStart 就够）。
    ap.add_argument("--transcript-dir", default=None,
                    help="Claude 转录目录（缺省按当前用户 HOME 推导）")
    ap.add_argument("--codex-dir", default=None,
                    help="Codex 会话目录（缺省按当前用户 HOME 推导）")
    ap.add_argument("--json", action="store_true",
                    help="机器可读输出（探针消费）：hollow 数、源在场标志、逐 skill 事实")
    args = ap.parse_args()

    if args.self_test:
        return _self_test()

    transcript_dir = args.transcript_dir or TRANSCRIPT_DIR
    codex_dir = args.codex_dir or CODEX_DIR

    items = inventory()
    if not items:
        print("[info] .claude/skills/ 下没有已注册 skill")
        return 0

    slugs = [i["name"] for i in items]
    sources = []
    if os.path.isdir(transcript_dir):
        sources.append("claude")
    if os.path.isdir(codex_dir):
        sources.append("codex")
    if not sources:
        print(f"[skip] 无任何转录数据源（claude={transcript_dir} codex={codex_dir}）"
              "——本机不可观测 ≠ 违规，退出 0")
        if args.json:
            print(json.dumps({
                "hollow": 0, "skills": [], "scanned": [],
                "strong_source_present": False, "weak_source_present": False,
                "skipped": "no_transcript_source",
            }, ensure_ascii=False))
        return 0

    claude = scan_claude(transcript_dir, slugs) if "claude" in sources else {}
    codex = scan_codex(codex_dir, slugs) if "codex" in sources else {}

    now = time.time()

    if args.json:
        # #2881：机器可读输出**只**打 JSON（混着人类表格的 stdout 没法解析）。判据仍走同一个
        # is_hollow（不复制一份判断），缺源/零调用由「strong_source_present」与逐条 calls
        # 分开报，探针据此把缺源折成 unknown 而不是 hollow（#2851 同源语义）。
        strong_present = "claude" in sources
        rows = []
        json_hollow = 0
        for it in items:
            total, _last = claude.get(it["name"], (0, None))
            cx, _cx = codex.get(it["name"], (0, None))
            age_days = (int((now - it["birth"]) / 86400) if it["birth"] else None)
            is_hole = is_hollow(age_days, total, it["type"])
            json_hollow += 1 if is_hole else 0
            rows.append({
                "dir": it["dir"], "type": it["type"], "age_days": age_days,
                "claude_calls": total, "codex_sessions": cx, "hollow": is_hole,
            })
        print(json.dumps({
            "hollow": json_hollow,
            "strong_source_present": strong_present,
            "weak_source_present": "codex" in sources,
            "skills": rows,
        }, ensure_ascii=False))
        return 1 if (json_hollow and args.strict) else 0

    hollow = 0
    width = max(len(i["dir"]) for i in items)
    print(f"# skill 用量报告（强信号 claude: {'✓' if 'claude' in sources else '✗'}"
          f" / 弱信号 codex: {'✓' if 'codex' in sources else '✗'}）")
    print("# Claude 列=Skill 工具调用（判洞唯一依据）；Codex 列=SKILL.md 被读取的"
          "会话数（读取≠触发，审计噪声未甄别，仅供删留裁决参考）；"
          "判洞只依赖「是否为零」——零值可靠，正数仅代表有人知道它\n")
    strong_present = "claude" in sources
    for it in items:
        total, last = claude.get(it["name"], (0, None))
        cx, cx_last = codex.get(it["name"], (0, None))
        age_days = (int((now - it["birth"]) / 86400) if it["birth"] else None)
        age_s = f"{age_days}d" if age_days is not None else "?"
        flag = ""
        if is_hollow(age_days, total, it["type"], strong_source_present=strong_present):
            flag = f"  ⚠️ HOLLOW(≥{HOLLOW_DAYS[it['type']]}d/{it['type']})"
            hollow += 1
        codex_s = (f"{cx} 会话 最近 {cx_last or '—'}"
                   if "codex" in sources else "未扫")
        # #2851：缺源时列里写「未扫」而不是「0 次/从未」——不给观测缺口编造观测事实
        claude_s = (f"调用 {total:>3} 次 最近 {_fmt_last(last):<16}"
                    if strong_present else "未扫（源不在场，不判洞）      ")
        print(f"[{it['dir']:<{width}}] 出生 {age_s:>4} {it['type']:<10} "
              f"| Claude {claude_s} "
              f"| Codex 读 {codex_s}{flag}")
        if not it["desc"]:
            print(f"{'':<{width+4}}⚠️ description 为空（S7 应已拦；此处兜底提示）")

    if hollow:
        print(f"\n[HOLLOW] {hollow} 个 skill 达到分型观察窗且强信号零调用——"
              "删除或修 description 触发词，勿留空壳；删前核对 Codex 列会话性质",
              file=sys.stderr)
        return 1 if args.strict else 0
    print("\n[OK] 无洞态 skill")
    return 0


if __name__ == "__main__":
    sys.exit(main())
