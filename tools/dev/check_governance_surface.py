#!/usr/bin/env python3
"""治理面结构检查（synthesis C-G1 的 L0 层）。

治理面 = CLAUDE.md / AGENTS.md / Harness 适配说明 / .cursor/rules /
AI 门禁 workflow——所有 AI 会话行为的上游事实源。本脚本只做**确定性结构检查**，
不用 LLM：

  S1  CLAUDE.md `@import` 必须独占一行且目标存在
      （事故：写在中文行内静默失效，只能人肉 /context 发现）
  S2  根治理文档、文档地图与 Harness 适配说明的相对链接目标必须存在
      （实测发生过 DOC-MAP 断链）
  S3  .cursor/rules/*.mdc frontmatter 三字段齐全，alwaysApply!=true 时 globs 非空
      （坏 frontmatter = 规则静默不加载，与 S1 同故障类）
  S4  pr-agent.yml 防绕过机制锚点仍在（digest pin / fallback 空 /
      门禁与命令 job 分离）
  S5  required checks 文档↔workflow 互检：ci.yml/pr-agent.yml 定义的 job id
      未在 AGENTS.md 记载，或反之缺 job
  S6  常驻入口行数/字节预算，防止按需细节重新膨胀进启动上下文
  S7  .claude/skills/*/SKILL.md frontmatter：name 与目录一致、description 非空
      （写坏 = skill 对 agent 静默不存在，与 S1/S3 同故障类）
  S8  CLAUDE.md 只允许 @import 最小 AGENTS.md，不得递归导入文档地图或领域文档
  S9  根入口只允许固定的启动级章节，领域细节不能新增为二/三级章节
  S10 2026-09-05 起新增 Agent Note 的 Status/Class 头部与 class 目录一致
  S11 AGENTS.md 硬不变量锚点逐条在场（防整条删除/改写静默丢失——S4 同模式）

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


def check_resident_imports(text: str) -> list[str]:
    """S8: 常驻 CLAUDE import 只允许最小共享启动契约。"""
    imports = []
    in_fence = False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence and line.strip().startswith("@"):
            imports.append(line.strip()[1:])
    allowed = ["AGENTS.md"]
    if imports != allowed:
        return [f"S8 CLAUDE.md: @import 必须且只能是 {allowed!r}，实际 {imports!r}"]
    return []


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


RESIDENT_BUDGETS = {
    "AGENTS.md": (80, 8000),
    "CLAUDE.md": (60, 6000),
    ".cursor/rules/00-project-context.mdc": (30, 3000),
    ".cursor/rules/backend-python.mdc": (30, 3000),
    ".cursor/rules/frontend-typescript.mdc": (30, 3000),
    ".cursor/rules/agent-runtime.mdc": (30, 3000),
    ".cursor/rules/agent-scripts.mdc": (30, 3000),
    "docs/development/ai/harness-adapters.md": (100, 10000),
    "backend/agent/CLAUDE.md": (40, 5000),
    "backend/agent/aee/CLAUDE.md": (100, 10000),
}


def check_resident_budget(label: str, text: str) -> list[str]:
    """S6: 根入口或 Harness 适配超过预算即阻塞。"""
    max_lines, max_bytes = RESIDENT_BUDGETS[label]
    lines = len(text.splitlines())
    size = len(text.encode("utf-8"))
    issues = []
    if lines > max_lines:
        issues.append(f"S6 {label}: {lines} 行超过预算 {max_lines}")
    if size > max_bytes:
        issues.append(f"S6 {label}: {size} bytes 超过预算 {max_bytes}")
    return issues


ROOT_HEADING_ALLOWLIST = {
    "AGENTS.md": {"总原则", "硬不变量", "开始任务时", "按需入口", "提交前"},
    "CLAUDE.md": {"按需读取"},
}


def check_root_headings(label: str, text: str) -> list[str]:
    """S9: 根入口只保留启动级固定章节。"""
    issues = []
    allowed = ROOT_HEADING_ALLOWLIST[label]
    for lineno, line in enumerate(text.splitlines(), 1):
        if line.startswith("### "):
            issues.append(f"S9 {label} line {lineno}: 禁止三级章节，细节应迁往按需文档")
        elif line.startswith("## "):
            heading = line[3:].strip()
            if heading not in allowed:
                issues.append(f"S9 {label} line {lineno}: 非启动章节 {heading!r}")
    return issues


# S11: 硬不变量锚串刻意取 AGENTS.md 原文——改写措辞必须连锚一起改，
# 让「不变量静默消失/被改写」这件事本身过不了门禁（S9 只查章节名、S6 只查体量）。
HARD_INVARIANT_ANCHORS = [
    ("ASGI 入口", r"socketio\.ASGIApp\(sio_server, fastapi_app\)"),
    ("Pipeline 顶层只接受 lifecycle", r"Pipeline 顶层只接受 `lifecycle`"),
    ("action 唯一格式", r"action 唯一格式是 `script:<name>`"),
    ("Plan 不存 lifecycle", r"Plan 不存 lifecycle"),
    ("dispatcher 组装 lifecycle", r"pipeline_def\.lifecycle"),
    ("Redis 边界", r"Redis 只承载队列与瞬时跨进程通信"),
    ("生产 cookie/CSRF guard", r"secure cookie、受限 SameSite 和 CSRF guard"),
    ("Pydantic v2 only", r"Pydantic 只使用 v2 API"),
    ("业务表名单数", r"数据库业务表名使用单数"),
    ("default_params 不可原地修改", r"`default_params` 不可原地修改"),
    ("前端类型入口", r"frontend/src/utils/api/types\.ts"),
]


def check_hard_invariant_anchors(text: str) -> list[str]:
    """S11: 硬不变量锚点逐条在场。"""
    return [
        f"S11 AGENTS.md: 硬不变量锚点缺失「{label}」（{pattern}）——"
        f"确认是否被删除/改写；有意改写须同步更新锚点表"
        for label, pattern in HARD_INVARIANT_ANCHORS
        if not re.search(pattern, text)
    ]


NOTE_CLASSES = {"feature", "bug-fix", "simplification", "architecture", "process", "testing"}
NOTE_HEADER_CUTOFF = "2026-09-05"


def check_agent_note_header(label: str, text: str) -> list[str]:
    """S10: 新格式启用后的 Agent Note 头部必须与 class 目录一致。"""
    filename = os.path.basename(label)
    match = re.match(r"^(\d{4}-\d{2}-\d{2})-.+\.md$", filename)
    if not match or match.group(1) < NOTE_HEADER_CUTOFF:
        return []
    class_name = os.path.basename(os.path.dirname(label))
    issues = []
    lines = text.splitlines()
    if len(lines) < 4 or not lines[0].startswith("# ") or lines[1] != "":
        issues.append(f"S10 {label}: 头两行必须是标题和空行")
        return issues
    if lines[2] not in {"Status: proposed", "Status: implemented", "Status: rejected"}:
        issues.append(f"S10 {label}: 非法或缺失 Status 头")
    if class_name not in NOTE_CLASSES or lines[3] != f"Class: {class_name}":
        issues.append(f"S10 {label}: Class 必须与目录 {class_name!r} 一致")
    return issues


def check_pr_agent_anchors(text: str) -> list[str]:
    """S4: 防绕过机制锚点仍在。这些都是真实事故的转化物（#399 等）。"""
    anchors = {
        "镜像 digest pin": "docker://pragent/pr-agent@sha256:",
        "fallback_models 置空": "config.fallback_models",
        "自动 review/命令 job 分离(防产出被顶掉)": "pr-agent-comment:",
        "security 判定": "No security concerns",
        # 顾问模式下 security concerns 的唯一送达路径：check 颜色不再承载
        # 该信号，issue 步一旦被删就等于「发现静默丢失」。
        "security concerns 开 issue 兜底": "Open follow-up issue on security concerns",
    }
    return [
        f"S4 pr-agent.yml: 丢失锚点「{name}」——防绕过机制被改动，需人工确认是否有意"
        for name, needle in anchors.items()
        if needle not in text
    ]


def check_required_checks_doc(workflows: dict[str, str], agents_md: str) -> list[str]:
    """S5: ci.yml 的 PR 门禁 job 与 AGENTS.md 记载互检。

    CodeQL 由 GitHub 默认设置提供（仓库无对应 workflow 文件），只查文档侧。
    pr-agent.yml 不再贡献 required check（顾问模式），故不在此表。
    """
    issues: list[str] = []
    for wf, job_ids in (("ci.yml", ["lint", "pr-typecheck", "pr-compileall", "pr-agent-tests", "pr-migrate-empty-db"]),):
        text = workflows.get(wf, "")
        for jid in job_ids:
            if not re.search(rf"(?m)^\s{{2}}{re.escape(jid)}:\s*$", text):
                issues.append(f"S5 {wf}: 缺少 required job `{jid}`（防绕过清单不完整）")
            elif f"`{jid}`" not in agents_md and jid not in agents_md:
                issues.append(f"S5 AGENTS.md: 未记载 required check `{jid}`（文档漂移）")
    if "CodeQL" not in agents_md:
        issues.append("S5 AGENTS.md: 未记载 required check `CodeQL`（文档漂移）")
    return issues


# S5x: 本地门禁(GATES key) → CI 锚点 的显式映射。刻意不做自动推断——两侧
# 命名多对多，靠表强制「新增门禁必须回答 CI 对应物在哪」。None = 有意仅本地。
GATE_TO_CI_ANCHOR = {
    "ruff": ("ci.yml", "Ruff"),
    "eslint": ("ci.yml", "ESLint"),
    "tsc": ("ci.yml", "TypeScript check"),
    "knip": ("ci.yml", "knip 死代码检查"),
    "compileall": ("ci.yml", "Compile check"),
    "pollution": ("ci.yml", "空行注入污染检查"),
    "immutability": ("ci.yml", "脚本版本不可变检查"),
    "gov-surface": ("ci.yml", "治理面结构检查"),
    # public 仓库内网主机地址扫描（#538 收尾）——锚点即 ci.yml 中该 step 的 name
    "ip-leak": ("ci.yml", "内网主机地址检查"),
    "agent-tests": ("ci.yml", "Run agent tests"),
    # check:full 级——CI 对应物在 backend-test / frontend-check / docker-build job
    "backend-tests": ("ci.yml", "Run backend tests"),
    "integration": ("ci.yml", "Run backend tests"),
    "repo-tests": ("ci.yml", "Run repo-level tests"),
    "vitest": ("ci.yml", "Run vitest"),
    "frontend-build": ("ci.yml", "npm run build"),
    "docker-build": ("ci.yml", "Build backend image"),
    # 有意仅本地的例外——登记理由防止未来审计误判为缺口：
    "gov-evals": None,   # 按需诊断（裁决降级）：LLM 成本/flaky 不进阻塞路径
    "gov-skills": None,  # 数据源=本机 ~/.claude 转录，物理不在 runner 上
}


def check_gate_ci_mapping(gates_src: str, workflows: dict[str, str]) -> list[str]:
    """S5x: 双向断言 GATES 与 CI 步骤的配对关系（漂移当场红灯）。

    ① 本地每个 gate 必须在映射表登记（防「只加本地不接 CI」）；
    ② 已登记且非 None 的条目，锚点字符串必须在对应 workflow 出现
       （防「CI 改名/删步骤」与「映射表过期」）。
    """
    issues: list[str] = []
    for m in re.finditer(r'(?m)^\s{4}"([\w-]+)": \(', gates_src):
        gate = m.group(1)
        if gate not in GATE_TO_CI_ANCHOR:
            issues.append(
                f"S5x run_gates 门禁 {gate!r} 未在 GATE_TO_CI_ANCHOR 登记配对——"
                f"新增门禁必须先声明其 CI 对应物（或显式 None 并附理由）"
            )
    for gate, spec in GATE_TO_CI_ANCHOR.items():
        if spec is None:
            continue
        wf, anchor = spec
        if anchor not in workflows.get(wf, ""):
            issues.append(f"S5x {gate!r} 的 CI 锚点 {anchor!r} 在 {wf} 中消失")
    return issues


def check_skill_frontmatter(dirname: str, text: str) -> list[str]:
    """S7: skill 目录的 SKILL.md frontmatter 必须合法，且 name 与目录名一致。

    name 错配 / description 空 → Claude Code 不把该 skill 呈现给会话，
    属「文件在、能力亡」的静默失效。
    """
    issues: list[str] = []
    if not text.startswith("---"):
        return [f"S7 {dirname}: SKILL.md 缺 frontmatter"]
    end = text.find("\n---", 3)
    if end < 0:
        return [f"S7 {dirname}: frontmatter 未闭合"]

    def _clean(v: str) -> str:
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            return v[1:-1]
        return v

    fields: dict[str, str] = {}
    for m in re.finditer(r"^([\w-]+):\s*(.*)$", text[4:end], re.M):
        fields[m.group(1)] = _clean(m.group(2))
    nm = fields.get("name", "")
    if nm != dirname:
        issues.append(f"S7 {dirname}: frontmatter name={nm!r} 与目录名不一致")
    if not fields.get("description"):
        issues.append(f"S7 {dirname}: description 为空（agent 靠它决定是否加载）")
    return issues


# ── 门禁执行 ──

def run_check() -> int:
    issues: list[str] = []

    claude_md_path = os.path.join(ROOT, "CLAUDE.md")
    claude_md = open(claude_md_path, encoding="utf-8").read()
    resolve_from_root = lambda rel: os.path.join(ROOT, rel)  # noqa: E731
    issues += check_imports(claude_md, resolve_from_root)
    issues += check_resident_imports(claude_md)

    link_files = [
        ("CLAUDE.md", ROOT),
        ("AGENTS.md", ROOT),
        ("docs/DOC-MAP.md", os.path.join(ROOT, "docs")),
        # B1 迁移后三个描述型索引表住进 hub——同样纳入断链防护
        ("docs/README.md", os.path.join(ROOT, "docs")),
        (
            "docs/development/cursor-rules.md",
            os.path.join(ROOT, "docs", "development"),
        ),
        (
            "docs/development/ai/harness-adapters.md",
            os.path.join(ROOT, "docs", "development", "ai"),
        ),
        (
            "docs/development/dependencies-and-quality.md",
            os.path.join(ROOT, "docs", "development"),
        ),
        (
            "docs/development/repository-workflow.md",
            os.path.join(ROOT, "docs", "development"),
        ),
        (
            "docs/development/script-versioning.md",
            os.path.join(ROOT, "docs", "development"),
        ),
        (
            "docs/design/2026-scan-upload-merge-contract.md",
            os.path.join(ROOT, "docs", "design"),
        ),
        (
            "docs/operations/production-diagnostics.md",
            os.path.join(ROOT, "docs", "operations"),
        ),
        (
            "docs/operations/device-lease-emergency-release.md",
            os.path.join(ROOT, "docs", "operations"),
        ),
        ("backend/agent/CLAUDE.md", os.path.join(ROOT, "backend", "agent")),
        (
            "backend/agent/aee/CLAUDE.md",
            os.path.join(ROOT, "backend", "agent", "aee"),
        ),
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

    skills_dir = os.path.join(ROOT, ".claude", "skills")
    if os.path.isdir(skills_dir):
        for d in sorted(os.listdir(skills_dir)):
            sk_path = os.path.join(skills_dir, d, "SKILL.md")
            if os.path.isfile(sk_path):
                issues += check_skill_frontmatter(
                    d, open(sk_path, encoding="utf-8").read()
                )
            else:
                issues.append(f"S7 .claude/skills/{d}/: 缺 SKILL.md")

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

    gates_src_path = os.path.join(ROOT, "scripts", "run_gates.py")
    if os.path.exists(gates_src_path):
        gates_src = open(gates_src_path, encoding="utf-8").read()
        issues += check_gate_ci_mapping(gates_src, workflows)

    for rel in RESIDENT_BUDGETS:
        text = open(os.path.join(ROOT, rel), encoding="utf-8").read()
        issues += check_resident_budget(rel, text)
    for rel in ROOT_HEADING_ALLOWLIST:
        text = open(os.path.join(ROOT, rel), encoding="utf-8").read()
        issues += check_root_headings(rel, text)
    agents_text = open(os.path.join(ROOT, "AGENTS.md"), encoding="utf-8").read()
    issues += check_hard_invariant_anchors(agents_text)

    notes_root = os.path.join(ROOT, "docs", "notes")
    for class_name in sorted(NOTE_CLASSES):
        class_dir = os.path.join(notes_root, class_name)
        for filename in sorted(os.listdir(class_dir)):
            if filename == "README.md" or not filename.endswith(".md"):
                continue
            path = os.path.join(class_dir, filename)
            label = os.path.relpath(path, ROOT)
            issues += check_agent_note_header(
                label, open(path, encoding="utf-8").read()
            )

    for issue in issues:
        print(f"[BLOCK] {issue}")
    if issues:
        print(f"\n治理面结构检查失败：{len(issues)} 项", file=sys.stderr)
        return 1
    print("[OK] 治理面结构检查通过（阻塞项全绿：S1–S11、S5x）")
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
    expect("S8 最小 import", lambda: check_resident_imports("# T\n\n@AGENTS.md\n"), False)
    expect(
        "S8 递归导入文档地图",
        lambda: check_resident_imports("# T\n\n@AGENTS.md\n\n@docs/DOC-MAP.md\n"),
        True,
    )

    expect("S2 好 (目指本文件所在目录)", lambda: check_links("见 [本文件](check_governance_surface.py)", os.path.dirname(os.path.abspath(__file__)), "t"), False)
    expect("S2 断链", lambda: check_links("见 [无](no-such-file.md)", os.path.dirname(os.path.abspath(__file__)), "t"), True)

    good_mdc = "---\ndescription: d\nglobs: a/**\nalwaysApply: false\n---\nbody\n"
    empty_glob_mdc = "---\ndescription: d\nglobs: \"\"\nalwaysApply: false\n---\nb\n"
    no_aa_mdc = "---\ndescription: d\nglobs: a/**\n---\nb\n"
    expect("S3 合法 mdc", lambda: check_mdc_frontmatter("ok.mdc", good_mdc), False)
    expect("S3 globs 空", lambda: check_mdc_frontmatter("e.mdc", empty_glob_mdc), True)
    expect("S3 缺 alwaysApply", lambda: check_mdc_frontmatter("m.mdc", no_aa_mdc), True)

    expect(
        "S6 预算内",
        lambda: check_resident_budget("AGENTS.md", "# T\n"),
        False,
    )
    expect(
        "S6 超行数",
        lambda: check_resident_budget("AGENTS.md", "x\n" * 81),
        True,
    )
    expect(
        "S9 根章节白名单",
        lambda: check_root_headings("AGENTS.md", "# T\n\n## 总原则\n"),
        False,
    )
    expect(
        "S9 领域章节",
        lambda: check_root_headings("AGENTS.md", "# T\n\n## 数据库迁移\n"),
        True,
    )
    good_note = "# T\n\nStatus: implemented\nClass: process\n"
    bad_note = "# T\n\nStatus: accepted\nClass: feature\n"
    expect(
        "S10 新 note 头部合法",
        lambda: check_agent_note_header(
            "docs/notes/process/2026-09-05-example.md", good_note
        ),
        False,
    )
    expect(
        "S10 Status/Class 错配",
        lambda: check_agent_note_header(
            "docs/notes/process/2026-09-05-example.md", bad_note
        ),
        True,
    )
    expect(
        "S10 legacy 不追溯",
        lambda: check_agent_note_header(
            "docs/notes/process/2026-09-04-example.md", bad_note
        ),
        False,
    )

    invariants_full = (
        "- ASGI 入口是 `socketio.ASGIApp(sio_server, fastapi_app)`\n"
        "- Pipeline 顶层只接受 `lifecycle`，action 唯一格式是 `script:<name>`。\n"
        "- Plan 不存 lifecycle；组装 `pipeline_def.lifecycle`。\n"
        "- Redis 只承载队列与瞬时跨进程通信，不作为业务事实存储。\n"
        "- 生产环境必须满足 secure cookie、受限 SameSite 和 CSRF guard。\n"
        "- Pydantic 只使用 v2 API；数据库业务表名使用单数。\n"
        "- 已存在脚本版本的 `default_params` 不可原地修改。\n"
        "- 前端 API 类型以 `frontend/src/utils/api/types.ts` 为入口。"
    )
    expect(
        "S11 锚点齐全",
        lambda: check_hard_invariant_anchors(invariants_full),
        False,
    )
    expect(
        "S11 锚点缺失",
        lambda: check_hard_invariant_anchors("## 硬不变量\n\n（本节已清空）\n"),
        True,
    )
    expect(
        "S11 单条改写逃逸被拦",
        lambda: check_hard_invariant_anchors(
            invariants_full.replace("Pydantic 只使用 v2 API；", "用新版 Pydantic；")
        ),
        True,
    )

    full_pr_agent = (
        "jobs:\n  pr-agent-review:\n"
        "      uses: docker://pragent/pr-agent@sha256:abc\n"
        "          config.fallback_models: '[]'\n"
        "      - name: Open follow-up issue on security concerns\n"
        "  pr-agent-comment:\n"
        "          - No security concerns\n"
    )
    broken = full_pr_agent.replace("No security concerns", "Renamed verdict")
    no_issue = full_pr_agent.replace("Open follow-up issue on security concerns", "x")
    expect("S4 锚点齐全", lambda: check_pr_agent_anchors(full_pr_agent), False)
    expect("S4 security 判定丢失", lambda: check_pr_agent_anchors(broken), True)
    expect("S4 issue 兜底丢失", lambda: check_pr_agent_anchors(no_issue), True)

    ok_ci = "jobs:\n  lint:\n  pr-typecheck:\n  pr-compileall:\n  pr-agent-tests:\n  pr-migrate-empty-db:\n"
    drop_ci = ok_ci.replace("  pr-typecheck:\n", "")
    wfs_ok = {"ci.yml": ok_ci}
    wfs_bad = {"ci.yml": drop_ci}
    doc_full = "required checks：lint / CodeQL / pr-typecheck / pr-compileall / pr-agent-tests / pr-migrate-empty-db"
    doc_drift = doc_full.replace("pr-typecheck", "pr-typografie")
    expect("S5 一致", lambda: check_required_checks_doc(wfs_ok, doc_full), False)
    expect("S5 CI 删 job", lambda: check_required_checks_doc(wfs_bad, doc_full), True)
    expect("S5 文档漂移", lambda: check_required_checks_doc(wfs_ok, doc_drift), True)

    good_skill = "---\nname: foo\ndescription: 触发词 d\n---\n步骤"
    bad_name = "---\nname: bar\ndescription: d\n---\nb"
    bad_desc = "---\nname: foo\ndescription: \"\"\n---\nb"
    no_fm = "直接正文没有 frontmatter"
    expect("S7 合法 skill", lambda: check_skill_frontmatter("foo", good_skill), False)
    expect("S7 name 错配目录", lambda: check_skill_frontmatter("foo", bad_name), True)
    expect("S7 description 空", lambda: check_skill_frontmatter("foo", bad_desc), True)
    expect("S7 缺 frontmatter", lambda: check_skill_frontmatter("foo", no_fm), True)

    # S5x 夹具：映射表按全局常量走，workflows 必须含全部非 None 锚点才算「齐」
    # 注意：`for x in it if cond` 是先解包后过滤，None 会在 filter 前炸——
    # 必须用 filter() 预过滤再解包。
    wf_full = " ".join(
        f"- {anchor}\n"
        for (wf, anchor) in filter(None, GATE_TO_CI_ANCHOR.values())
    )
    src_with_gate = '    "ruff": (\n'
    src_mystery = '    "mystery-gate": (\n'
    wf_have = {"ci.yml": wf_full}
    wf_missing = {"ci.yml": wf_full.replace("- Ruff\n", "")}
    expect("S5x 配对齐", lambda: check_gate_ci_mapping(src_with_gate, wf_have), False)
    expect(
        "S5x CI 锚点消失",
        lambda: check_gate_ci_mapping(src_with_gate, wf_missing),
        True,
    )
    expect(
        "S5x 未登记新门禁",
        lambda: bool([i for i in check_gate_ci_mapping(src_mystery, wf_have) if "mystery" in i]),
        True,
    )

    if failures:
        for f in failures:
            print(f"[SELFTEST-FAIL] {f}", file=sys.stderr)
        print(f"\n自测失败 {len(failures)} 项——检查器自身不可信，禁止用于拦截", file=sys.stderr)
        return 1
    print("[OK] self-test 通过：12 条规则各含红/绿样例双向验证")
    return 0


def main() -> int:
    if "--self-test" in sys.argv:
        return run_self_test()
    return run_check()


if __name__ == "__main__":
    sys.exit(main())
