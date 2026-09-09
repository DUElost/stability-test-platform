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
  S8  CLAUDE.md 双形态（#857）：指向 AGENTS.md 的 symlink（内容直读，子目录
      ancestor 加载即送达）或恰含 `@AGENTS.md` 单条 import——不得递归导入
  S9  根入口只允许固定的启动级章节；三级及以下（含 ####+ 深层）一律禁止
  S10 class 目录内 Agent Note 必须日期命名（yyyy-mm-dd-主题.md），且 2026-09-05 起
      新增 Note 的 Status/Class 头部与 class 目录一致
  S11 AGENTS.md 硬不变量锚点逐条在场（防整条删除/改写静默丢失——S4 同模式）
  S12 ADR 索引一致性：头部状态行 ↔ adr/README 主表/DOC-MAP/M7 看板（status 词级
      + 规范位版本），头部行 ↔ 版本记录块末项（#861/#867 五次复发后的确定性收口）

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
    """S8（经典形态）：常驻 CLAUDE import 只允许最小共享启动契约。"""
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
        return [
            f"S8 CLAUDE.md: @import 必须且只能是 {allowed!r}，实际 {imports!r}"
            "（或改为指向 AGENTS.md 的 symlink，见 #857 双形态）"
        ]
    return []


def check_claude_entry_form(text: str, is_symlink: bool, link_target: str = "") -> list[str]:
    """S8 双形态（#857 根契约绕过，G2 真身+薄壳上移到根）：

    - symlink 形态：CLAUDE.md → AGENTS.md，内容直读零 @import——子目录会话
      经 ancestor 加载即送达根契约（@import 仅 cwd 级生效的上游缺陷无法命中）；
    - 经典形态：恰含 `@AGENTS.md` 单条 import（根 cwd 启动时展开）。"""
    if is_symlink:
        if os.path.basename(link_target) != "AGENTS.md":
            return [
                f"S8 CLAUDE.md: symlink 形态必须指向 AGENTS.md（实际 {link_target!r}）"
            ]
        return []
    return check_resident_imports(text)


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
    # execution-contract.md 是执行语义**唯一权威源**（ADR-0034 P0a），预算随其
    # 版本化演进上调：v1.8（#906 决策实体唯一性）落地时 main 上已达
    # 19638/20000 bytes（98%），预算已从「防臃肿」变成「阻止契约演进」。
    # 2026-09-09 用户裁决：上调至 260 行/26KB（仅抬该文件，其余不变）。
    "docs/development/ai/execution-contract.md": (260, 26000),
    "backend/agent/AGENTS.md": (40, 5000),
    "backend/agent/aee/AGENTS.md": (100, 10000),
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


_HEADING = re.compile(r"^(#{2,})\s+(.*)$")


def check_root_headings(label: str, text: str) -> list[str]:
    """S9: 根入口只保留启动级固定章节；三级及以下一律禁止（含 ####+ 深层）。"""
    issues = []
    allowed = ROOT_HEADING_ALLOWLIST[label]
    for lineno, line in enumerate(text.splitlines(), 1):
        m = _HEADING.match(line)
        if not m:
            continue
        depth = len(m.group(1))
        if depth >= 3:
            issues.append(
                f"S9 {label} line {lineno}: 禁止{depth}级章节，细节应迁往按需文档"
            )
        else:
            heading = m.group(2).strip()
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


# S12: ADR 索引一致性。ADR 头部「状态」行是 status+版本的权威写法，adr/README
# 主表、DOC-MAP 行、M7 看板行是派生面——#861/#867 五次复发证明「PR 内记得同步」
# 不可靠（含文内形态：头部行停 v1.3 而版本记录块已到 v1.6）。规则取最小可靠面：
# status 词级同步到所有在场派生面；版本仅当头部行携带**规范位**版本（紧跟状态词的
# **Status（vX.Y）** 或 **Status**（vX.Y：…））时才约束派生面——0029/0030 等
# 注解散文里的 vX.Y（「历经 v1 评审 → v2.1 → …」）不视作头部版本，避免误报。
_ADR_STATUSES = {"Proposed", "Accepted", "Superseded", "Deprecated"}
_ADR_STATUS_LINE = re.compile(
    r"-\s*状态[：:]\s*\*{0,2}(Proposed|Accepted|Superseded|Deprecated)\*{0,2}(.*)$"
)
_ADR_VERSION_TOKEN = re.compile(r"v(\d+\.\d+)")
_ADR_README_LINK = re.compile(r"\((?:\./)?(ADR-\d{4}[^)]*\.md)\)")
_ADR_DOCMAP_LINK = re.compile(r"\((?:\./)?adr/(ADR-\d{4}[^)]*\.md)\)")
_ADR_M7_ENTRY = re.compile(
    r"ADR-(\d{4})（\*\*(Proposed|Accepted|Superseded|Deprecated)\*\*\s*v(\d+\.\d+)"
)


def parse_adr_status_line(line: str) -> tuple[str, str] | tuple[None, None]:
    """S12 辅助：ADR 头部「状态」行 → (status, 规范位版本)；无版本则 version=None。"""
    m = _ADR_STATUS_LINE.match(line.strip())
    if not m:
        return None, None
    rest = m.group(2).lstrip("*（ (")
    vm = _ADR_VERSION_TOKEN.match(rest)
    return m.group(1), vm.group(1) if vm else None


_RECORD_CONTINUATION = re.compile(r"^(?:-\s+)?\*{0,2}v(\d+\.\d+)")


def parse_adr_record_tip(text: str) -> str | None:
    """S12 辅助：「版本记录」块的最后一个版本 token（块止于下一个 `- ` 项）。

    结构口径（#1058）：标题行取「版本记录」标签后全部 token 的末项（兼容
    ADR-0024 式单行罗列形态）；块内续行只认**行首**版本 token（ADR-0034 式
    `**vX.Y 本版：…**` 形态）——续行行尾的引用 token（如「本版号 v1.8 已被
    占用，重编 v1.9」里另一处 vX.Y）不再充当末项。标题行行尾引用污染在单行
    罗列形态下仍无法机械区分，靠书写纪律。"""
    tip = None
    in_record = False
    for line in text.splitlines():
        s = line.strip()
        if not in_record:
            if s.startswith("- 版本记录") or s.startswith("- **版本记录**"):
                in_record = True
                found = _ADR_VERSION_TOKEN.findall(s)
                if found:
                    tip = found[-1]
        else:
            if s.startswith("- "):
                break
            m = _RECORD_CONTINUATION.match(s)
            if m:
                tip = m.group(1)
    return tip


def parse_adr_readme_row(line: str) -> tuple[str, str, str] | tuple[None, None, None]:
    """S12 辅助：adr/README 主表行 → (文件名, status, 摘要版本前缀)。

    status 取链接 cell 之后的第一个精确状态 cell（#1058）——不锚列位时，
    状态 cell 缺席而摘要先出现状态词会被取错比对对象。"""
    if "ADR-" not in line:
        return None, None, None
    link = _ADR_README_LINK.search(line)
    if not link:
        return None, None, None
    cells = [c.strip() for c in line.split("|") if c.strip()]
    link_idx = next((i for i, c in enumerate(cells) if "ADR-" in c), None)
    tail = cells[link_idx + 1:] if link_idx is not None else cells
    status = next((c for c in tail if c in _ADR_STATUSES), None)
    summary = cells[-1] if cells else ""
    vm = re.match(r"v(\d+\.\d+)：", summary)
    return link.group(1), status, vm.group(1) if vm else None


def parse_docmap_adr_row(line: str) -> tuple[str, list[str]] | tuple[None, None]:
    """S12 辅助：DOC-MAP 中指向 adr/ 的行 → (文件名, 行内全部 vX.Y token)。"""
    if "/adr/" not in line:
        return None, None
    link = _ADR_DOCMAP_LINK.search(line)
    if not link:
        return None, None
    return link.group(1), _ADR_VERSION_TOKEN.findall(line)


def check_adr_surface_sync(
    num: str,
    header_status: str | None,
    header_version: str | None,
    record_tip: str | None,
    row_status: str | None,
    row_version: str | None,
    docmap_versions: list[str] | None,
    m7_status: str | None,
    m7_version: str | None,
) -> list[str]:
    """S12: ADR 头部 ↔ 派生索引面一致性。不在场（None）的派生面不约束。"""
    issues: list[str] = []
    where = f"S12 ADR-{num}"
    if record_tip and header_version and record_tip != header_version:
        issues.append(
            f"{where}: 头部状态行 v{header_version} ≠ 版本记录块末项 v{record_tip}"
            "——bump 版本必须同步头部行（#867 文内漂移形态）"
        )
    if header_status and row_status and header_status != row_status:
        issues.append(
            f"{where}: adr/README 主表状态 {row_status} ≠ 头部 {header_status}"
        )
    if header_version:
        if row_version is None:
            issues.append(
                f"{where}: adr/README 主表行缺版本前缀（头部 v{header_version}）"
            )
        elif row_version != header_version:
            issues.append(
                f"{where}: adr/README 主表 v{row_version} ≠ 头部 v{header_version}"
            )
        if docmap_versions is not None and header_version:
            if not docmap_versions:
                issues.append(
                    f"{where}: DOC-MAP 行缺版本 token（头部 v{header_version}）"
                    "——改 ADR 必带 DOC-MAP 版本行（#1058，此前静默跳过）"
                )
            elif docmap_versions[-1] != header_version:
                issues.append(
                    f"{where}: DOC-MAP 行 v{docmap_versions[-1]} ≠ 头部 v{header_version}"
                )
        if m7_status and header_status and m7_status != header_status:
            issues.append(f"{where}: M7 看板行状态 {m7_status} ≠ 头部 {header_status}")
        if m7_version is not None and m7_version != header_version:
            issues.append(f"{where}: M7 看板行 v{m7_version} ≠ 头部 v{header_version}")
    return issues


NOTE_CLASSES = {"feature", "bug-fix", "simplification", "architecture", "process", "testing"}
NOTE_HEADER_CUTOFF = "2026-09-05"


def check_agent_note_header(label: str, text: str) -> list[str]:
    """S10: 新格式启用后的 Agent Note 头部必须与 class 目录一致；文件名必须日期命名。"""
    filename = os.path.basename(label)
    match = re.match(r"^(\d{4}-\d{2}-\d{2})-.+\.md$", filename)
    if not match:
        # class 目录内非日期命名的 .md 一律拒绝（README.md 已在调用方排除）——
        # 否则改名即可绕过 Status/Class 头校验（#854）
        return [f"S10 {label}: Agent Note 文件名必须形如 yyyy-mm-dd-<主题>.md"]
    if match.group(1) < NOTE_HEADER_CUTOFF:
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
    "ai-work": ("ci.yml", "Execution Registry 自测"),
    "pr-migrate": ("ci.yml", "Migrate empty PostgreSQL database"),
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
    "gov-skills": None,  # 数据源=本机 ~/.claude 转录，物理不在 runner 上
    "ai-drift": None,    # P3 advisory gate：CI runner 无本机 registry 数据可查；
                         # 夜间全量在本机跑 check:full 时留痕；转 required 须独立裁决
    "invariant-diff": ("ci.yml", "差异面不变量检查"),
    # #855 差异面不变量检查：2026-09-07 升格 BLOCK（全库枚举替代观察期验证
    # 精度），与 immutability 同模式接入 ci.yml lint job。
    "harness-ingest": None,  # #855-b 摄取矩阵（ADR-0034 P2 验收）：每家一次真实
                             # 非交互 LLM 会话（分钟级+外部依赖），CI 不跑；
                             # check:gov 手跑，行为漂移时人工介入。与
                             # invariant-diff（差异面）互补成两腿。
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
    claude_is_link = os.path.islink(claude_md_path)
    claude_link = os.readlink(claude_md_path) if claude_is_link else ""
    resolve_from_root = lambda rel: os.path.join(ROOT, rel)  # noqa: E731
    issues += check_imports(claude_md, resolve_from_root)
    issues += check_claude_entry_form(claude_md, claude_is_link, claude_link)

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
        # P0a/P0b：Execution Contract 唯一权威源（ADR-0034）纳入断链防护
        (
            "docs/development/ai/execution-contract.md",
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
        # G2：scoped 真身（symlink 薄壳 CLAUDE.md → AGENTS.md 不单列，同内容）
        ("backend/agent/AGENTS.md", os.path.join(ROOT, "backend", "agent")),
        (
            "backend/agent/aee/AGENTS.md",
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
        # #857 双形态：CLAUDE.md 为 symlink 时内容即 AGENTS.md，行数/字节与
        # 章节由 AGENTS.md 侧的同名检查覆盖，不按 CLAUDE.md 的更紧预算重复计
        if rel == "CLAUDE.md" and claude_is_link:
            continue
        text = open(os.path.join(ROOT, rel), encoding="utf-8").read()
        issues += check_resident_budget(rel, text)
    for rel in ROOT_HEADING_ALLOWLIST:
        if rel == "CLAUDE.md" and claude_is_link:
            continue  # 同上：symlink 形态的章节结构由 AGENTS.md 白名单约束
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

    adr_dir = os.path.join(ROOT, "docs", "adr")
    adr_readme_path = os.path.join(adr_dir, "README.md")
    docmap_path = os.path.join(ROOT, "docs", "DOC-MAP.md")
    if os.path.isdir(adr_dir) and os.path.exists(adr_readme_path) and os.path.exists(docmap_path):
        adr_readme = open(adr_readme_path, encoding="utf-8").read()
        docmap_text = open(docmap_path, encoding="utf-8").read()
        readme_rows: dict[str, tuple[str | None, str | None]] = {}
        for line in adr_readme.splitlines():
            fn, st, ver = parse_adr_readme_row(line)
            if fn:
                readme_rows[fn] = (st, ver)
        m7_line = next(
            (l for l in adr_readme.splitlines() if l.startswith("| M7")), ""
        )
        m7_entries = {
            em.group(1): (em.group(2), em.group(3))
            for em in _ADR_M7_ENTRY.finditer(m7_line)
        }
        docmap_rows: dict[str, list[str]] = {}
        for line in docmap_text.splitlines():
            fn, tokens = parse_docmap_adr_row(line)
            if fn:
                docmap_rows[fn] = tokens
        for fn in sorted(os.listdir(adr_dir)):
            if not (fn.startswith("ADR-") and fn.endswith(".md")):
                continue
            text = open(os.path.join(adr_dir, fn), encoding="utf-8").read()
            status_line = next(
                (l for l in text.splitlines() if l.strip().startswith("- 状态")),
                None,
            )
            header_status, header_version = (
                parse_adr_status_line(status_line) if status_line else (None, None)
            )
            num = fn[4:8]
            issues += check_adr_surface_sync(
                num,
                header_status,
                header_version,
                parse_adr_record_tip(text),
                *readme_rows.get(fn, (None, None)),
                docmap_rows.get(fn),
                *m7_entries.get(num, (None, None)),
            )

    for issue in issues:
        print(f"[BLOCK] {issue}")
    if issues:
        print(f"\n治理面结构检查失败：{len(issues)} 项", file=sys.stderr)
        return 1
    print("[OK] 治理面结构检查通过（阻塞项全绿：S1–S12、S5x）")
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
    expect(
        "S8 symlink 形态合法（#857）",
        lambda: check_claude_entry_form("# 契约内容直读\n", True, "AGENTS.md"),
        False,
    )
    expect(
        "S8 symlink 指错真身",
        lambda: check_claude_entry_form("# x\n", True, "docs/DOC-MAP.md"),
        True,
    )
    expect(
        "S8 双形态互斥（symlink 下不查 import）",
        lambda: check_claude_entry_form("无任何 import 行\n", True, "AGENTS.md"),
        False,
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
    expect(
        "S9 深层标题逃逸被拦（#854）",
        lambda: check_root_headings(
            "AGENTS.md", "# T\n\n## 总原则\n\n#### 领域细节逃逸\n"
        ),
        True,
    )
    expect(
        "S9 五级标题逃逸被拦",
        lambda: check_root_headings("AGENTS.md", "# T\n\n##### 更深\n"),
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
    expect(
        "S10 非日期文件名被拦（#854）",
        lambda: check_agent_note_header(
            "docs/notes/process/no-date-note.md", good_note
        ),
        True,
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

    # S12 辅助函数（#1058：record_tip 行首锚定 / readme 状态列锚定 / docmap 缺 token）
    record_single = "- 版本记录：v1.0（初版）/ v1.1（#909 例外契约化）\n- 优先级：P0\n"
    expect("S12 record_tip 单行罗列取末项",
           lambda: parse_adr_record_tip(record_single) != "1.1", False)
    record_cont = (
        "- 版本记录：v1.0 #858 / v1.1 #866（指针化）\n"
        "**v1.2 本版：判据修订（全文见契约 §9 v1.1）**\n"
        "**v1.3 本版：并发反转（本版号 v1.1 已被并行 PR 占用，重编 v1.3）**\n"
        "- 优先级：P1\n"
    )
    expect("S12 record_tip 续行行尾引用 token 不干扰",
           lambda: parse_adr_record_tip(record_cont) != "1.3", False)
    record_no_cont = "- 版本记录：v1.0（初版）\n- 优先级：P0\n"
    expect("S12 record_tip 无续行取标题行末项",
           lambda: parse_adr_record_tip(record_no_cont) != "1.0", False)
    expect("S12 record_tip 无版本记录块返回 None",
           lambda: parse_adr_record_tip("# T\n\n正文\n") is not None, False)

    readme_row = ("| [ADR-0001](./ADR-0001-x.md) | 描述（Accepted 曾出现在摘要里） "
                  "| Accepted | P0 | M1 | v1.2：最新摘要 |\n")
    _fn, _st, _ver = parse_adr_readme_row(readme_row)
    expect("S12 readme 行解析",
           lambda: (_fn, _st, _ver) != ("ADR-0001-x.md", "Accepted", "1.2"), False)
    _st2 = parse_adr_readme_row(
        "| [ADR-0001](./ADR-0001-x.md) | Accepted | Deprecated | P0 | v1.2：摘要 |\n")[1]
    expect("S12 readme 状态取链接后首个状态 cell", lambda: _st2 != "Accepted", False)
    expect("S12 readme 非 adr 链接行不解析",
           lambda: parse_adr_readme_row(
               "| M7 | ADR-0001（**Accepted** v1.9） |") != (None, None, None), False)

    expect("S12 docmap 行在缺版本 token 即拦（#1058）",
           lambda: not any("缺版本 token" in i for i in check_adr_surface_sync(
               "01", "Accepted", "1.2", None, "Accepted", "1.2", [], None, None)),
           False)
    expect("S12 docmap 行缺失（None）仍不约束",
           lambda: check_adr_surface_sync(
               "01", "Accepted", "1.2", None, "Accepted", "1.2", None, None, None) != [],
           False)
    expect("S12 docmap 末项一致不报",
           lambda: check_adr_surface_sync(
               "01", "Accepted", "1.2", None, "Accepted", "1.2", ["1.0", "1.2"],
               None, None) != [],
           False)

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

    # S12 夹具：解析器规范位/散文边界 + 五面一致性红绿
    expect(
        "S12 头部括号内版本",
        lambda: parse_adr_status_line("- 状态：**Accepted（v1.6）**")
        != ("Accepted", "1.6"),
        False,
    )
    expect(
        "S12 头部外挂括号版本",
        lambda: parse_adr_status_line("- 状态：**Accepted**（v0.7：P1 编码已合入）")[1]
        != "0.7",
        False,
    )
    expect(
        "S12 注解散文 token 不作头部版本",
        lambda: parse_adr_status_line(
            "- 状态：**Accepted**（2026-08-19 拍板；历经 v1 评审 → v2.1 → v2.5 重设计）"
        )[1]
        is not None,
        False,
    )
    adr_record = (
        "- 版本记录：v0.1 #858 / v1.0 #865 / v1.2 #877\n"
        "**v1.3 实测**\n**v1.6 本版：裁决**\n"
        "- 优先级：P1\n"
    )
    expect(
        "S12 版本记录末项",
        lambda: parse_adr_record_tip(adr_record) != "1.6",
        False,
    )
    row_ok = parse_adr_readme_row(
        "| [ADR-0034](./ADR-0034-x.md) | 标题 | Accepted | P1 | M7 | v1.6：决策 |"
    )
    expect(
        "S12 主表行解析",
        lambda: row_ok != ("ADR-0034-x.md", "Accepted", "1.6"),
        False,
    )
    expect(
        "S12 五面一致",
        lambda: check_adr_surface_sync(
            "0034", "Accepted", "1.6", "1.6", "Accepted", "1.6", ["1.6"],
            "Accepted", "1.6",
        ),
        False,
    )
    expect(
        "S12 README 状态漂移",
        lambda: check_adr_surface_sync(
            "0002", "Superseded", None, None, "Accepted", None, None, None, None
        ),
        True,
    )
    expect(
        "S12 README 版本漂移",
        lambda: check_adr_surface_sync(
            "0034", "Accepted", "1.6", "1.6", "Accepted", "1.0", None, None, None
        ),
        True,
    )
    expect(
        "S12 头部行落后版本记录",
        lambda: check_adr_surface_sync(
            "0034", "Accepted", "1.3", "1.6", "Accepted", "1.6", None, None, None
        ),
        True,
    )
    expect(
        "S12 DOC-MAP 版本漂移",
        lambda: check_adr_surface_sync(
            "0034", "Accepted", "1.6", "1.6", "Accepted", "1.6", ["1.1"], None, None
        ),
        True,
    )
    expect(
        "S12 M7 看板漂移",
        lambda: check_adr_surface_sync(
            "0034", "Accepted", "1.6", "1.6", "Accepted", "1.6", None, "Accepted", "1.0"
        ),
        True,
    )
    expect(
        "S12 头部无版本不约束派生面",
        lambda: check_adr_surface_sync(
            "0030", "Accepted", None, None, "Accepted", "1.9", ["1.9"],
            "Accepted", "1.9",
        ),
        False,
    )
    expect(
        "S12 行缺版本前缀被拦",
        lambda: check_adr_surface_sync(
            "0033", "Accepted", "1.1", None, "Accepted", None, None, None, None
        ),
        True,
    )

    if failures:
        for f in failures:
            print(f"[SELFTEST-FAIL] {f}", file=sys.stderr)
        print(f"\n自测失败 {len(failures)} 项——检查器自身不可信，禁止用于拦截", file=sys.stderr)
        return 1
    print("[OK] self-test 通过：13 条规则各含红/绿样例双向验证")
    return 0


def main() -> int:
    if "--self-test" in sys.argv:
        return run_self_test()
    return run_check()


if __name__ == "__main__":
    sys.exit(main())
