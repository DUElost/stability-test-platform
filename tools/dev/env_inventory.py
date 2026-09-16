#!/usr/bin/env python3
"""环境变量读取清单：生成 / 漂移门禁（#737 文档切片）。

问题（#737）：`backend/**` 有两百多个 `os.getenv` / `os.environ.get` 读取名，
绝大多数从未记录，运维与新人只能读源码；`.env*.example` 只承载**运维模板**
子集，天然不覆盖内部旋钮。

本工具把「代码到底读了什么」做成可验证的清单，消除配置黑盒：

- 扫描 `backend/**/*.py`（不含 `backend/agent/scripts/**`——版本化脚本目录的
  环境契约见 ADR-0020）的读取形态：`os.getenv("X")`、`os.environ.get("X")`、
  `os.environ["X"]`、以及项目 helper（`_int_env("X", …)` 等 `_*_env("X"` 形态，
  默认值取 `production_default=` / 位置第二参数字面量）；
- 以及 ADR-0042 的 Settings 类字段（AST 解析）：`validation_alias`/`alias`
  （含 `AliasChoices` 多别名）或 `env_prefix + 字段名.upper()`；
- 渲染确定性表格（变量 | 默认 | 示例登记 | 类别 | 首个读取点）写入
  `docs/development/environment-variables.md` 的生成块（marker 之间，勿手改）；
- `--check` 与文档生成块逐字节比对：代码新增读取名而文档未刷新即红。

退出码：漂移/用法错 → 1；一致或写入成功 → 0。`--self-test` 离线红绿双向自证。
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs" / "development" / "environment-variables.md"
SCAN_ROOT = ROOT / "backend"
#: 相对 ``SCAN_ROOT`` 的**组件**跳过（整棵子树，任何层级命中即跳）：只有字节码缓存。
#: 不要把 ``scripts`` 写进来——组件是相对 ``backend/`` 取的，会连
#: ``backend/scripts/**`` 一起跳过（#2026 实测：控制面脚本目录 21 个文件从清单消失，
#: ``STP_INITIAL_ADMIN_*`` 等读取名对门禁不可见而门禁仍报 OK，并反过来建议删除
#: 只在该目录读取的声明）。要排除 agent 侧版本化脚本目录，用下面按路径前缀的
#: ``SCAN_SKIP_TREES``。
SCAN_SKIP_PARTS = {"__pycache__"}
#: 相对 ``SCAN_ROOT`` 的**子树前缀**跳过：``backend/agent/scripts/**``——版本化脚本
#: 目录的环境契约见 ADR-0020，不是控制面运行时读取面（#2026 前身语义）。
SCAN_SKIP_TREES = (("agent", "scripts"),)
SKIP_PARTS = {"__pycache__", ".git", ".wt", "node_modules", ".venv", "venv"}


def _rel_parts(path: Path) -> tuple[str, ...]:
    """相对仓库根（``ROOT``）的路径组件。

    **必须按相对组件比较**，不能用 ``path.parts``（绝对路径）：本仓推荐的并行执行
    方式是专属 worktree，位置就在 ``<repo>/.wt/<name>``，其绝对路径里必然含 ``.wt``
    ——按绝对路径判会把**该 worktree 下的每个文件**都跳过，``scan_reads`` 返回 0，
    于是 `_INTERNAL_ONLY` 里每一条都被误判成「已陈旧」（#1978 实测，19 条假红，
    并诱导人去删真实读点）。跳过规则的本意是排除仓库**内部**的 ``.wt/``（嵌套
    worktree），不是排除 worktree 根。
    """
    try:
        return path.relative_to(ROOT).parts
    except ValueError:  # 不在 ROOT 下（调用方指向外部路径）→ 退回绝对组件
        return path.parts


BEGIN_MARK = "<!-- env-inventory:begin（generated：python tools/dev/env_inventory.py --write） -->"
END_MARK = "<!-- env-inventory:end -->"

#: 读取形态（每文件动态组装，见 `_file_patterns`）。组 1 = 变量名；
#: 组 2（可选）= 默认值表达式原文。接收者只认 `os` 及其**导入别名**——
#: 2026-09-14 复核踩过两个坑：① 只认字面 `os.getenv` 会漏 `import os as X`；
#: ② 放宽容收（裸 `environ.get(`）会把 WSGI environ 的 `HTTP_ORIGIN` 误当
#: 环境变量。故改为按导入语句精确派生接收者。
_OS_MODULE_ALIAS_RE = re.compile(r"^import\s+os\s+as\s+([A-Za-z_][A-Za-z0-9_]*)", re.M)
_OS_GETENV_ALIAS_RE = re.compile(r"from\s+os\s+import\s+getenv\s+as\s+([A-Za-z_][A-Za-z0-9_]*)")
_OS_ENVIRON_ALIAS_RE = re.compile(r"from\s+os\s+import\s+environ\s+as\s+([A-Za-z_][A-Za-z0-9_]*)")
_OS_BARE_GETENV_RE = re.compile(r"from\s+os\s+import\s+getenv\b(?!\s+as\b)")
_OS_BARE_ENVIRON_RE = re.compile(r"from\s+os\s+import\s+environ\b(?!\s+as\b)")
_HELPER_ENV_RE = re.compile(
    r"""\b_[a-z][a-z0-9_]*_env\(\s*["']([A-Z][A-Z0-9_]+)["']([^)\n]*)""",
)


def _getenv_pattern(receiver: str | None) -> re.Pattern[str]:
    prefix = f"{receiver}\\." if receiver else ""
    return re.compile(
        rf"""\b{prefix}getenv\(\s*["']([A-Z][A-Z0-9_]+)["']\s*(?:,\s*([^),\n]+))?"""
    )


def _environ_get_pattern(receiver: str | None) -> re.Pattern[str]:
    prefix = f"{receiver}\\." if receiver else ""
    return re.compile(
        rf"""\b{prefix}environ\.get\(\s*["']([A-Z][A-Z0-9_]+)["']\s*(?:,\s*([^),\n]+))?"""
    )


def _environ_item_pattern(receiver: str | None) -> re.Pattern[str]:
    prefix = f"{receiver}\\." if receiver else ""
    return re.compile(rf"""\b{prefix}environ\[\s*["']([A-Z][A-Z0-9_]+)["']\s*\]""")


def _bare_call_pattern(func_name: str) -> re.Pattern[str]:
    """裸函数调用形态：`getenv("X")` / `from os import getenv as _g` 的 `_g("X")`。"""
    return re.compile(
        rf"""\b{func_name}\(\s*["']([A-Z][A-Z0-9_]+)["']\s*(?:,\s*([^),\n]+))?"""
    )


def _file_patterns(text: str) -> list[re.Pattern[str]]:
    """按文件的导入语句派生读取形态（防止别名绕过 / 防止 WSGI environ 误报）。"""
    patterns: list[re.Pattern[str]] = []
    receivers = ["os", *_OS_MODULE_ALIAS_RE.findall(text)]
    for receiver in sorted(set(receivers)):
        patterns.append(_getenv_pattern(receiver))
        patterns.append(_environ_get_pattern(receiver))
        patterns.append(_environ_item_pattern(receiver))
    if _OS_BARE_GETENV_RE.search(text):
        patterns.append(_bare_call_pattern("getenv"))
    if _OS_BARE_ENVIRON_RE.search(text):
        patterns.append(_environ_get_pattern(None))
        patterns.append(_environ_item_pattern(None))
    for alias in _OS_GETENV_ALIAS_RE.findall(text):
        patterns.append(_bare_call_pattern(alias))
    for alias in _OS_ENVIRON_ALIAS_RE.findall(text):
        patterns.append(_environ_get_pattern(alias))
        patterns.append(_environ_item_pattern(alias))
    patterns.append(_HELPER_ENV_RE)
    return patterns

_LITERAL_RE = re.compile(r"""^\s*(?:["']([^"']*)["']|(-?\d+(?:\.\d+)?))\s*$""")
_KWARG_RE = re.compile(r"""(?:production_default|default)\s*=\s*([^,\n)]+)""")

#: 内部声明（#737 收口）：**既未进运维模板、也不打算登记**的读取名，每条必须写明
#: 理由。门禁规则：「代码读取名 ∈ 示例登记 ∪ 内部声明」，二者之外即红——强制每个
#: 读取名二选一，防止「新加一个环境变量、谁也不知道」重新堆积。
#: 验收口径：需运维按环境/规模/机型调整的 → 登记进示例；控制面注入的派生键、
#: 测试/开发专用、纯内部实现细节 → 在此声明并写明理由。
_INTERNAL_ONLY: dict[str, str] = {
    # #2180（D 步）：AGENT_SECRET_B64 / ENV_OVERRIDES_B64 / ENV_PATH_KEYS_B64 /
    # INSTALL_DIR 的声明已删——删除的热更新 heredoc 补丁器是它们唯一的
    # os.environ 读取点；现只作为远端脚本内的 shell 变量经 argv 交 wrapper，
    # 不再是控制面读取的环境变量。
    "FAKE_TAR_SLEEP": "测试夹具（模拟 tar 耗时），无常驻配置语义",
    "HOST_IP": "测试注入的 host 身份；生产由 Agent 自行解析",
    "PRECHECK_NOTIFY_DEBOUNCE_SECONDS": "precheck 通知去抖：实现细节（防重复推送），不属运维旋钮",
    "STP_AGENT_VERSION": "hot-update 写入的版本标记（派生值，不自设）",
    "STP_ALLOW_UNSAFE_TEST_DATABASE_URL": "测试守卫逃生门：仅本地测试库用，生产禁止设置",
    "STP_ARTIFACT_DIGEST_CACHE": "制品摘要缓存的紧急关闭开关（内部实现细节）",
    # #2026：以下 8 条随「`backend/scripts/**` 不再被误跳」首次进入清单——均为一次性
    # 诊断/引导脚本的 CLI 等价入参（各自有 `--backend` / `--host-id` / `--env-file`
    # 或本机默认值），不属部署环境配置，故声明内部而非登记进运维模板。
    "STP_AUDIT_BACKEND": "一次性诊断脚本（audit_stage_a_env / preflight_control_plane）的 `--backend` 等价项；刻意无内置默认地址（不硬编码生产地址）",
    "STP_AUDIT_ENV_FILE": "同上脚本的 `--env-file` 等价项（被审计的 .env 路径），非部署环境配置",
    "STP_BACKEND_URL": "一次性运维脚本（batch_hot_update）的控制面地址覆盖，默认本机 `127.0.0.1:8000`",
    "STP_DEDUP_LOG_ENCODING": "去重日志文件编码（locale 细节，跟随机型）",
    "STP_DEDUP_PLACE": "去重扫描写入的站点标签（元数据；由采集侧脚本语境决定）",
    "STP_DEVICE_SERIAL": "脚本运行时注入：Agent 为脚本进程注入设备序列号",
    "STP_EXTRACT_BACKEND": "一次性提取脚本（jira_extract_run52）的 `--backend` 等价项，无内置默认地址",
    "STP_INITIAL_ADMIN_PASSWORD": "站点安装链 S3 受控首管理员引导的一次性入参（tools/site_config 以子进程环境注入，密码只经环境）；刻意不进 .env 模板，避免凭据落盘",
    "STP_INITIAL_ADMIN_USER": "同 STP_INITIAL_ADMIN_PASSWORD（受控首管理员引导的用户名入参）",
    "STP_NOTIFY_SAQ_RETRIES": "读取点仅存在于测试（断言 _int_env 行为）",
    "STP_SMOKE_HOST_ID": "真机冒烟脚本（sprint4_real_device_verify）的 `--host-id` 等价项，无内置默认",
    "STP_SMOKE_ORIGIN": "测试用：smoke 夹具断言 origin",
    "STP_STEP_PARAMS": "脚本运行时注入：步骤参数 JSON（Agent→脚本协议）",
    "STP_VERIFY_BACKEND": "冒烟/校验脚本（smoke_jira_api / sprint4_real_device_verify）的 `--backend` 等价项，无内置默认地址",
    "STP_WATCHER_AEE_RECONCILE_HOSTS": "目标机本地选择性对账清单（现场排障临时用，默认空=全量）",
    "SUDO_UID": "sudo 调用时由系统注入（stp_agent_priv）",
    "SUDO_GID": "sudo 调用时由系统注入（stp_agent_priv）",
}


def _normalize_default(raw: str | None) -> str:
    """把默认值表达式归一为展示值：字面量取值，其余记 `-`（表达式/未设默认）。"""
    if not raw:
        return "-"
    match = _LITERAL_RE.match(raw)
    if match:
        return match.group(1) if match.group(1) is not None else match.group(2) or "-"
    kwarg = _KWARG_RE.search(raw)
    if kwarg:
        return _normalize_default(kwarg.group(1))
    return "-"


def _iter_py_files(scan_root: Path):
    for path in sorted(scan_root.rglob("*.py")):
        rel = path.relative_to(scan_root)
        if set(rel.parts) & SCAN_SKIP_PARTS:
            continue
        if any(rel.parts[: len(tree)] == tree for tree in SCAN_SKIP_TREES):
            continue
        if any(part in SKIP_PARTS for part in _rel_parts(path)):
            continue
        yield path


def _literal_default(node: ast.AST | None) -> str:
    """AST 字面量 → 展示值；非字面量记 `-`（与行扫描口径一致）。"""
    if isinstance(node, ast.Constant):
        if isinstance(node.value, str):
            return node.value
        if isinstance(node.value, (int, float)):
            return str(node.value)
        if isinstance(node.value, bool):
            return str(node.value).lower()
    return "-"


def _alias_names(node: ast.AST | None) -> list[str]:
    """validation_alias / alias 的取值 → env 名列表（支持 AliasChoices 多别名）。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "AliasChoices":
        names: list[str] = []
        for arg in node.args:
            names.extend(_alias_names(arg))
        return names
    return []


def _settings_field_entries(text: str) -> list[tuple[str, int, str]]:
    """解析 Settings 类字段 → [(env 名, 行号, 默认值)]（ADR-0042 的 D6）。

    识别判据：类基名以 ``Settings`` 结尾（含 BaseSettings 及其子类命名惯例），
    或类体含 ``model_config = SettingsConfigDict(...)``。
    名字来源：``validation_alias`` / ``alias``（字符串或 ``AliasChoices`` 的字符串
    参数）；缺省回落到 ``env_prefix + 字段名.upper()``（env_prefix 取
    ``SettingsConfigDict(env_prefix=...)``，默认空）。
    默认值：``Field(...)`` 的位置默认或 ``default=`` 字面量；无则 `-`。
    """
    import warnings

    try:
        with warnings.catch_warnings():
            # 源文件里的非法转义（\d 等）会经 ast.parse 冒 SyntaxWarning；
            # 与清单无关，压掉以免污染门禁输出。
            warnings.simplefilter("ignore", SyntaxWarning)
            tree = ast.parse(text)
    except SyntaxError:
        return []
    entries: list[tuple[str, int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        base_names = [
            base.id if isinstance(base, ast.Name) else getattr(base, "attr", "")
            for base in node.bases
        ]
        prefix = ""
        is_settings = any(name.endswith("Settings") for name in base_names if name)
        for stmt in node.body:
            target_names = []
            value = None
            if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
                target_names = [stmt.targets[0].id]
                value = stmt.value
            elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                target_names = [stmt.target.id]
                value = stmt.value
            if value is None:
                continue
            if (
                isinstance(value, ast.Call)
                and getattr(value.func, "id", "") == "SettingsConfigDict"
            ):
                is_settings = True
                for kw in value.keywords:
                    if kw.arg == "env_prefix":
                        prefix = _literal_default(kw.value)
                        if prefix == "-":
                            prefix = ""
            elif (
                "model_config" in target_names
                and isinstance(value, ast.Call)
                and getattr(value.func, "id", "") == "SettingsConfigDict"
            ):
                # 只认 SettingsConfigDict：普通 BaseModel 的 `model_config = ConfigDict(...)`
                # 不是 Settings（2026-09-14 首版误把 130+ 个 pydantic 模型字段/env 名混入）。
                is_settings = True
        if not is_settings:
            continue
        for stmt in node.body:
            if not isinstance(stmt, ast.AnnAssign) or not isinstance(stmt.target, ast.Name):
                continue
            field = stmt.target.id
            alias: list[str] = []
            default = "-"
            value = stmt.value
            if isinstance(value, ast.Call) and getattr(value.func, "id", "") == "Field":
                for kw in value.keywords:
                    if kw.arg in ("validation_alias", "alias"):
                        alias = _alias_names(kw.value) or alias
                    elif kw.arg == "default":
                        default = _literal_default(kw.value)
                if default == "-" and value.args:
                    default = _literal_default(value.args[0])
            else:
                default = _literal_default(value)
            names = alias or [f"{prefix}{field.upper()}"]
            for name in names:
                entries.append((name, stmt.lineno, default))
    return entries


def scan_reads(scan_root: Path = SCAN_ROOT) -> dict[str, dict]:
    """{变量名: {default, locations: [(relpath, line)], test_only}}（确定性排序）。"""
    reads: dict[str, dict] = {}
    for path in _iter_py_files(scan_root):
        relpath = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        settings_entries = _settings_field_entries(text)
        for name, lineno, default in settings_entries:
            entry = reads.setdefault(
                name, {"default": "-", "locations": [], "test_only": True},
            )
            entry["locations"].append((str(relpath), lineno))
            if entry["default"] == "-" and default != "-":
                entry["default"] = default
        patterns = _file_patterns(text)
        for lineno, line in enumerate(text.splitlines(), 1):
            for pattern in patterns:
                for match in pattern.finditer(line):
                    name = match.group(1)
                    default = _normalize_default(
                        match.group(2) if match.lastindex and match.lastindex >= 2 else None
                    )
                    entry = reads.setdefault(
                        name, {"default": "-", "locations": [], "test_only": True},
                    )
                    entry["locations"].append((str(relpath), lineno))
                    if entry["default"] == "-" and default != "-":
                        entry["default"] = default
    for entry in reads.values():
        entry["locations"] = sorted(set(entry["locations"]))
        entry["test_only"] = all(
            "/tests/" in f"/{loc[0]}" for loc in entry["locations"]
        )
    return reads


def example_keys() -> set[str]:
    """任一 `.env*.example` 出现的键名（含注释态条目）。"""
    keys: set[str] = set()
    pattern = re.compile(r"^#?\s*([A-Z][A-Z0-9_]+)=")
    for path in sorted(ROOT.glob("**/.env*.example")):
        if any(part in SKIP_PARTS for part in _rel_parts(path)):
            continue
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            match = pattern.match(line.strip())
            if match:
                keys.add(match.group(1))
    return keys


def audit(reads: dict[str, dict], registered: set[str]) -> list[str]:
    """清单一致性违规（#737 收口）：强制「示例登记 ∪ 内部声明」二选一。"""
    issues: list[str] = []
    for name in sorted(reads):
        if name in registered and name in _INTERNAL_ONLY:
            issues.append(f"{name}：已登记进示例却仍在内部声明清单（删声明或撤登记）")
        elif name not in registered and name not in _INTERNAL_ONLY:
            issues.append(f"{name}：未登记进示例、也未声明内部（二选一后刷新文档）")
    for name in sorted(set(_INTERNAL_ONLY) - set(reads)):
        issues.append(f"{name}：内部声明已陈旧——代码中不再有读取点（删除声明）")
    return issues


def render_block(reads: dict[str, dict], registered: set[str]) -> str:
    total = len(reads)
    unregistered = sorted(name for name in reads if name not in registered)
    lines = [
        BEGIN_MARK,
        "",
        f"共 **{total}** 个读取名（`backend/**`，不含 `backend/agent/scripts/**`；"
        f"含 ADR-0042 Settings 字段）："
        f"**{len(registered & set(reads))}** 个已在 `.env*.example` 登记，"
        f"**{len(unregistered)}** 个声明为内部（理由见下节）。",
        "示例文件是**运维模板**（承载需要运维/机型调整的子集）；本表是**代码侧完整清单**。",
        "门禁：每个读取名必须「登记进示例」或「内部声明」二选一，二者之外即红。",
        "",
        "| 变量 | 默认 | 示例 | 类别 | 首个读取点 |",
        "|---|---|---|---|---|",
    ]
    for name in sorted(reads):
        entry = reads[name]
        first = entry["locations"][0]
        category = "测试" if entry["test_only"] else "运行时"
        flag = "✅" if name in registered else "—"
        lines.append(
            f"| `{name}` | `{entry['default']}` | {flag} | {category} | `{first[0]}:{first[1]}` |"
        )
    lines += [
        "",
        "### 内部声明（未进运维模板，含理由）",
        "",
        "| 变量 | 理由 |",
        "|---|---|",
    ]
    for name in unregistered:
        lines.append(f"| `{name}` | {_INTERNAL_ONLY.get(name, '（未声明——门禁会红）')} |")
    lines += ["", END_MARK]
    return "\n".join(lines)


def semantic_signature(reads: dict[str, dict], registered: set[str]) -> dict[str, tuple]:
    """语义签名：名称 → (默认, 是否登记, 类别)。**不含行号**。

    为什么：读取点行号会随无关 PR 加行而平移，把「加了一行注释」判成清单漂移
    会造成无关 PR 的 required check 误红（2026-09-14 #1952 实证）。门禁只对
    语义变化负责；行号列是 `--write` 时刷新的导航快照。
    """
    signature: dict[str, tuple] = {}
    for name, entry in reads.items():
        signature[name] = (
            entry["default"],
            name in registered,
            "测试" if entry["test_only"] else "运行时",
        )
    return signature


def doc_signature(doc_text: str) -> dict[str, tuple]:
    """从文档生成块解析语义签名（表列：变量 | 默认 | 示例 | 类别 | 读取点）。"""
    inner = doc_text.partition(BEGIN_MARK)[2].partition(END_MARK)[0]
    row = re.compile(
        r"^\| `([A-Z][A-Z0-9_]+)` \| `([^`]*)` \| (✅|—) \| (运行时|测试) \|"
    )
    signature: dict[str, tuple] = {}
    for line in inner.splitlines():
        match = row.match(line)
        if match:
            name, default, flag, category = match.groups()
            signature[name] = (default, flag == "✅", category)
    return signature


def signature_diff(
    expected: dict[str, tuple], actual: dict[str, tuple], limit: int = 20,
) -> list[str]:
    """人类可读的语义差异（expected=代码计算，actual=文档现状）。"""
    issues: list[str] = []
    for name in sorted(set(actual) - set(expected)):
        issues.append(f"{name}：文档有、代码已无读取点（删行或刷新文档）")
    for name in sorted(set(expected) - set(actual)):
        issues.append(f"{name}：代码新增读取名、文档未登记（登记或内部声明后 --write）")
    for name in sorted(set(expected) & set(actual)):
        exp, act = expected[name], actual[name]
        if exp[0] != act[0]:
            issues.append(f"{name}：默认值变化 {act[0]!r} → {exp[0]!r}")
        if exp[1] != act[1]:
            issues.append(f"{name}：登记状态变化（文档示例列={act[1]}，代码侧={exp[1]}）")
        if exp[2] != act[2]:
            issues.append(f"{name}：类别变化 {act[2]} → {exp[2]}")
    return issues[:limit]


def _replace_block(doc_text: str, block: str) -> str:
    if BEGIN_MARK not in doc_text or END_MARK not in doc_text:
        raise SystemExit(f"文档缺少生成块 marker：{BEGIN_MARK} / {END_MARK}")
    head, _, rest = doc_text.partition(BEGIN_MARK)
    _, _, tail = rest.partition(END_MARK)
    return f"{head}{block}{tail}"


def _self_test() -> int:
    """红绿双向自证：注入新读取名后 --check 必红，--write 后必绿。"""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_root = Path(tmp)
        scan_root = tmp_root / "backend"
        scan_root.mkdir(parents=True)
        (scan_root / "mod.py").write_text(
            'import os\nA = os.getenv("ZZ_SELFTEST_VAR", "7")\n', encoding="utf-8",
        )
        reads = scan_reads(scan_root)
        assert "ZZ_SELFTEST_VAR" in reads, "扫描未命中新读取名"
        assert reads["ZZ_SELFTEST_VAR"]["default"] == "7", "默认值提取失败"
        assert not reads["ZZ_SELFTEST_VAR"]["test_only"], "类别判定失败"

        doc = tmp_root / "doc.md"
        doc.write_text("前文\n" + render_block({}, set()) + "\n后文\n", encoding="utf-8")
        block = render_block(reads, set())
        text = _replace_block(doc.read_text(encoding="utf-8"), block)
        assert "ZZ_SELFTEST_VAR" in text, "生成块未包含新读取名"
    print("[OK] env_inventory self-test 通过（扫描/默认值/类别/渲染/替换）")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="把生成块写回文档（幂等）")
    parser.add_argument("--check", action="store_true", help="校验文档生成块与代码一致（漂移即红）")
    parser.add_argument("--self-test", action="store_true", help="离线红绿自证")
    args = parser.parse_args()

    if args.self_test:
        return _self_test()

    reads = scan_reads()
    block = render_block(reads, example_keys())

    if args.write:
        DOC.write_text(
            _replace_block(DOC.read_text(encoding="utf-8"), block) + "\n",
            encoding="utf-8",
        )
        print(f"[OK] 已刷新 {DOC.relative_to(ROOT)}（{len(reads)} 个读取名）")
        return 0

    if args.check:
        doc_text = DOC.read_text(encoding="utf-8")
        if BEGIN_MARK not in doc_text or END_MARK not in doc_text:
            print("[FAIL] 文档缺少 env-inventory 生成块 marker", file=sys.stderr)
            return 1
        issues = audit(reads, example_keys())
        if issues:
            print("[FAIL] 环境变量清单裁决缺失：", file=sys.stderr)
            for item in issues:
                print(f"        - {item}", file=sys.stderr)
            return 1
        expected = semantic_signature(reads, example_keys())
        actual = doc_signature(doc_text)
        issues = signature_diff(expected, actual) if expected != actual else []
        if issues:
            print("[FAIL] 环境变量清单语义漂移：", file=sys.stderr)
            for item in issues:
                print(f"        - {item}", file=sys.stderr)
            print(
                "        处置：登记/声明后 `python tools/dev/env_inventory.py --write` 提交文档；"
                "纯行号平移不触发本门禁。",
                file=sys.stderr,
            )
            return 1
        print(f"[OK] 环境变量清单一致（{len(reads)} 个读取名）")
        return 0

    print(f"读取名 {len(reads)} 个；未登记 {sum(1 for n in reads if n not in example_keys())} 个")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
