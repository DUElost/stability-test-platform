#!/usr/bin/env python3
"""治理面结构检查（synthesis C-G1 的 L0 层）。

治理面 = CLAUDE.md / AGENTS.md / .cursor/rules / AI 门禁 workflow——所有 AI
会话行为的上游事实源。本脚本只做**确定性结构检查**，不用 LLM：

  S1  CLAUDE.md `@import` 必须独占一行且目标存在
      （事故：写在中文行内静默失效，只能人肉 /context 发现）
  S2  CLAUDE.md / AGENTS.md / docs/DOC-MAP.md 相对链接目标必须存在
      （实测发生过 DOC-MAP 断链）
  S3  .cursor/rules/*.mdc frontmatter 三字段齐全，alwaysApply!=true 时 globs 非空
      （坏 frontmatter = 规则静默不加载，与 S1 同故障类）
  S4  pr-agent.yml 防绕过机制锚点仍在（digest pin / fallback 空 /
      #421 disable-auto 步骤 / 门禁与命令 job 分离）
  S5  required checks 文档↔workflow 互检：ci.yml/pr-agent.yml 定义的 job id
      未在 AGENTS.md 记载，或反之缺 job
  S6  CLAUDE.md/AGENTS.md 行数信息行（仅输出，不判失败——分层加载是既定取舍）

用法:
    python tools/dev/check_governance_surface.py --check     # 门禁模式
    python tools/dev/check_governance_surface.py --self-test # 正反样例自证

verify-before-asserting: --self-test 对每条规则构造"已知坏样例必红 +
已知好样例必绿"，先证明检查器自身会失败，才允许它去拦别人。
"""
from __future__ import annotations

import os
import re
import sys
from urllib.parse import unquote

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── 检测器（纯函数，输入文本，输出问题列表；供 --check 与 --self-test 共用）──

# 类 import token：@ 起头 + 含 `/` 或以 .md 结尾（排除纯 @提及、@user 无斜杠）
_IMPORT_TOKEN = re.compile(r"@[\w][\w./-]*(?:/[\w./-]+|\.md)")


def check_imports(text: str, resolve) -> list[str]:
    """S1: CLAUDE.md 中 @import 必须独占一行（裸露形式）；目标须存在。

    反引号包裹的 `` `@path` `` 视为文档转义，放行——那是在解释语法而非使用它。
    跳过 ``` 围栏代码块。
    """
    issues: list[str] = []
    in_fence = False
    for lineno, line in enumerate(text.splitlines(), 1):
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        stripped = line.strip()
        tokens = _IMPORT_TOKEN.findall(stripped)
        if not tokens:
            continue
        if stripped.startswith("`"):
            continue  # 文档转义形式
        if stripped != tokens[0]:
            issues.append(
                f"S1 line {lineno}: @import 写在行内会静默失效（{stripped!r}），"
                f"必须独占一行"
            )
            continue
        target = resolve(tokens[0][1:])
        if not os.path.exists(target):
            issues.append(f"S1 line {lineno}: import 目标不存在: {tokens[0]}")
    return issues


_MD_LINK = re.compile(r"\]\(([^)\s]+)\)")


def check_links(text: str, basedir: str, label: str) -> list[str]:
    """S2: markdown 相对链接目标存在。跳过 http(s)/mailto/# 锚点。"""
    issues: list[str] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if line.strip().startswith("```"):
            continue  # 代码块内的示例路径不校验
        for m in _MD_LINK.finditer(line):
            raw = m.group(1)
            if raw.startswith(("http://", "https://", "mailto:", "#")):
                continue
            path_part = unquote(raw.split("#", 1)[0])
            if not path_part:
                continue  # 纯锚点 [x](#sec)
            target = os.path.normpath(os.path.join(basedir, path_part))
            if not os.path.exists(target):
                issues.append(f"S2 {label} line {lineno}: 断链 {raw}")
    return issues


def check_mdc_frontmatter(filename: str, text: str) -> list[str]:
    """S3: .mdc frontmatter 必含 description/globs/alwaysApply 且语义合法。"""
    issues: list[str] = []
    if not text.startswith("---"):
        issues.append(f"S3 {filename}: 缺 frontmatter")
        return issues
    end = text.find("\n---", 3)
    if end < 0:
        issues.append(f"S3 {filename}: frontmatter 未闭合")
        return issues
    block = text[4:end]

    def _clean(v: str) -> str:
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            return v[1:-1]
        return v

    fields: dict[str, str] = {}
    for m in re.finditer(r"^([\w-]+):\s*(.*)$", block, re.M):
        fields[m.group(1)] = _clean(m.group(2))
    if not fields.get("description"):
        issues.append(f"S3 {filename}: description 为空")
    aa = fields.get("alwaysApply", "").strip().lower()
    if aa not in ("true", "false"):
        issues.append(f"S3 {filename}: alwaysApply 必须是 true/false，得 {aa!r}")
    elif aa == "false" and not fields.get("globs"):
        issues.append(f"S3 {filename}: alwaysApply:false 但 globs 为空 → 规则永不激活")
    return issues


def check_pr_agent_anchors(text: str) -> list[str]:
    """S4: 防绕过机制锚点仍在。这些都是真实事故的转化物（#399/#421）。"""
    anchors = {
        "镜像 digest pin": "docker://pragent/pr-agent@sha256:",
        "fallback_models 置空": "config.fallback_models",
        "#421 gate 失败显式关 auto-merge 步骤": "Disable auto-merge on gate failure",
        "门禁/命令 job 分离(防 gate 被顶掉)": "pr-agent-comment:",
        "gate security 判定": "No security concerns",
    }
    return [
        f"S4 pr-agent.yml: 丢失锚点「{name}」——防绕过机制被改动，需人工确认是否有意"
        for name, needle in anchors.items()
        if needle not in text
    ]


def check_required_checks_doc(workflows: dict[str, str], agents_md: str) -> list[str]:
    """S5: ci.yml/pr-agent.yml 的 PR 门禁 job 与 AGENTS.md 六项记载互检。

    CodeQL 由 GitHub 默认设置提供（仓库无对应 workflow 文件），只查文档侧。
    """
    issues: list[str] = []
    for wf, job_ids in (("ci.yml", ["lint", "pr-typecheck", "pr-compileall", "pr-agent-tests"]),
                        ("pr-agent.yml", ["pr-agent-gate"])):
        text = workflows.get(wf, "")
        for jid in job_ids:
            if not re.search(rf"(?m)^\s{{2}}{re.escape(jid)}:\s*$", text):
                issues.append(f"S5 {wf}: 缺少 required job `{jid}`（防绕过清单不完整）")
            elif f"`{jid}`" not in agents_md and jid not in agents_md:
                issues.append(f"S5 AGENTS.md: 未记载 required check `{jid}`（文档漂移）")
    if "CodeQL" not in agents_md:
        issues.append("S5 AGENTS.md: 未记载 required check `CodeQL`（文档漂移）")
    return issues


# ── 门禁执行 ──

def run_check() -> int:
    issues: list[str] = []

    claude_md_path = os.path.join(ROOT, "CLAUDE.md")
    claude_md = open(claude_md_path, encoding="utf-8").read()
    resolve_from_root = lambda rel: os.path.join(ROOT, rel)  # noqa: E731
    issues += check_imports(claude_md, resolve_from_root)

    link_files = [
        ("CLAUDE.md", ROOT),
        ("AGENTS.md", ROOT),
        ("docs/DOC-MAP.md", os.path.join(ROOT, "docs")),
        # B1 迁移后三个描述型索引表住进 hub——同样纳入断链防护
        ("docs/README.md", os.path.join(ROOT, "docs")),
    ]
    for rel, basedir in link_files:
        text = open(os.path.join(ROOT, rel), encoding="utf-8").read()
        issues += check_links(text, basedir, rel)

    rules_dir = os.path.join(ROOT, ".cursor", "rules")
    if os.path.isdir(rules_dir):
        for fn in sorted(os.listdir(rules_dir)):
            if fn.endswith(".mdc"):
                text = open(os.path.join(rules_dir, fn), encoding="utf-8").read()
                issues += check_mdc_frontmatter(fn, text)
    else:
        issues.append("S3 .cursor/rules/ 目录不存在")

    pr_agent_path = os.path.join(ROOT, ".github", "workflows", "pr-agent.yml")
    if os.path.exists(pr_agent_path):
        issues += check_pr_agent_anchors(open(pr_agent_path, encoding="utf-8").read())

    ci_path = os.path.join(ROOT, ".github", "workflows", "ci.yml")
    workflows = {}
    if os.path.exists(ci_path):
        workflows["ci.yml"] = open(ci_path, encoding="utf-8").read()
    if os.path.exists(pr_agent_path):
        workflows["pr-agent.yml"] = open(pr_agent_path, encoding="utf-8").read()
    issues += check_required_checks_doc(
        workflows, open(os.path.join(ROOT, "AGENTS.md"), encoding="utf-8").read()
    )

    # S6: 仅信息输出
    for rel in ("CLAUDE.md", "AGENTS.md"):
        n = len(open(os.path.join(ROOT, rel), encoding="utf-8").read().splitlines())
        print(f"[info] S6 {rel}: {n} 行（体量趋势观测，不判失败）")

    for issue in issues:
        print(f"[BLOCK] {issue}")
    if issues:
        print(f"\n治理面结构检查失败：{len(issues)} 项", file=sys.stderr)
        return 1
    print("[OK] 治理面结构检查通过（S1–S5 阻塞项全绿）")
    return 0


# ── 自测：每条规则一坏一好两个样例 ──

def run_self_test() -> int:
    failures: list[str] = []

    def expect(rule: str, detector, should_flag: bool) -> None:
        got = bool(detector())
        if got != should_flag:
            failures.append(f"{rule}: 预期{'红' if should_flag else '绿'}，实际{'红' if got else '绿'}")

    good_doc = "# T\n\n见下。\n\n@AGENTS.md\n"
    bad_inline = "# T\n\n见下：@AGENTS.md 与后文\n"
    bad_missing = "# T\n\n@no/such-file.md\n"
    expect("S1 好样例", lambda: check_imports(good_doc, lambda r: os.path.join(ROOT, r)), False)
    expect("S1 中文行内 import", lambda: check_imports(bad_inline, lambda r: "/nonexistent"), True)
    expect("S1 目标缺失", lambda: check_imports(bad_missing, lambda r: "/nonexistent"), True)

    expect("S2 好 (目指本文件所在目录)", lambda: check_links("见 [本文件](check_governance_surface.py)", os.path.dirname(os.path.abspath(__file__)), "t"), False)
    expect("S2 断链", lambda: check_links("见 [无](no-such-file.md)", os.path.dirname(os.path.abspath(__file__)), "t"), True)

    good_mdc = "---\ndescription: d\nglobs: a/**\nalwaysApply: false\n---\nbody\n"
    empty_glob_mdc = "---\ndescription: d\nglobs: \"\"\nalwaysApply: false\n---\nb\n"
    no_aa_mdc = "---\ndescription: d\nglobs: a/**\n---\nb\n"
    expect("S3 合法 mdc", lambda: check_mdc_frontmatter("ok.mdc", good_mdc), False)
    expect("S3 globs 空", lambda: check_mdc_frontmatter("e.mdc", empty_glob_mdc), True)
    expect("S3 缺 alwaysApply", lambda: check_mdc_frontmatter("m.mdc", no_aa_mdc), True)

    full_pr_agent = (
        "jobs:\n  pr-agent-gate:\n"
        "      uses: docker://pragent/pr-agent@sha256:abc\n"
        "          config.fallback_models: '[]'\n"
        "      - name: Disable auto-merge on gate failure\n"
        "  pr-agent-comment:\n"
        "          - No security concerns\n"
    )
    broken = full_pr_agent.replace("Disable auto-merge on gate failure", "renamed-step")
    expect("S4 锚点齐全", lambda: check_pr_agent_anchors(full_pr_agent), False)
    expect("S4 disable-auto 步骤丢失", lambda: check_pr_agent_anchors(broken), True)

    ok_ci = "jobs:\n  lint:\n  pr-typecheck:\n  pr-compileall:\n  pr-agent-tests:\n"
    drop_ci = ok_ci.replace("  pr-typecheck:\n", "")
    wfs_ok = {"ci.yml": ok_ci, "pr-agent.yml": "jobs:\n  pr-agent-gate:\n"}
    wfs_bad = {"ci.yml": drop_ci, "pr-agent.yml": "jobs:\n  pr-agent-gate:\n"}
    doc_full = "required checks：lint / CodeQL / pr-typecheck / pr-compileall / pr-agent-tests / pr-agent-gate"
    doc_drift = doc_full.replace("pr-typecheck", "pr-typografie")
    expect("S5 一致", lambda: check_required_checks_doc(wfs_ok, doc_full), False)
    expect("S5 CI 删 job", lambda: check_required_checks_doc(wfs_bad, doc_full), True)
    expect("S5 文档漂移", lambda: check_required_checks_doc(wfs_ok, doc_drift), True)

    if failures:
        for f in failures:
            print(f"[SELFTEST-FAIL] {f}", file=sys.stderr)
        print(f"\n自测失败 {len(failures)} 项——检查器自身不可信，禁止用于拦截", file=sys.stderr)
        return 1
    print("[OK] self-test 通过：6 条规则各含红/绿样例双向验证")
    return 0


def main() -> int:
    if "--self-test" in sys.argv:
        return run_self_test()
    return run_check()


if __name__ == "__main__":
    sys.exit(main())
