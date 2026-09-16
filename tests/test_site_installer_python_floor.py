"""#2268：安装器的 Python 下限必须与「支持矩阵」同源，且被机器守住。

问题的形状不是「有人用了 `typing.Self`」，而是**三层事实各说各话、没有任何判据把它们
绑在一起**：

- 支持矩阵（`tools/release/build_bundle.py` 的 `SUPPORTED_PLATFORMS`）说「Ubuntu 22.04
  支持」，其证据只有 Agent 侧的 compileall；
- 安装器 venv 与后端 venv 都建在系统 `/usr/bin/python3` 上
  （`deploy/lib/deploy-common.sh:83`、`tools/site_config/stages.py:603`），22.04 自带 3.10；
- `tools/site_config` 从 base 起就 import 3.11+ 的 `typing.Self`。

CI 五个 job 全固定 3.11 ⇒ 这三层的矛盾**在门禁上不可见**，而 `preflight` 只查命令是否
存在，于是操作员看到「前置检查全绿」之后，下一步就是 import 阶段 ImportError。

本文件的分工（谁兜什么，写清楚以免被当成全量证明）：

1. **真证在 CI**：`.github/workflows/ci.yml` 的 `pr-agent-tests` 用 3.10 解释器把整个
   `tools.site_config` import 闭包加载一遍（本文件的用例 4 钉住该步骤与版本一致）；
2. **离线替补**：用例 1/2 用 AST 扫「3.11+ 才有的名字/语法」，秒级、不依赖解释器，
   覆盖最可能被再次引入的那几类（表外形态由 CI 冒烟兜）；
3. **口径闭环**：用例 3 把矩阵平台 → 该平台系统解释器 → `preflight.MIN_PYTHON` 三者锁成
   一个不等式，新增平台不登记解释器即红（这正是 #2268 的复发路径）；
4. **报告契约**：用例 5 钉住版本不足时 preflight 给出 FAIL + 可执行 Fix，而不是沉默。

纯离线：只读源码与 CI 文本，不起容器、不联网。
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from tools.site_config import checks, preflight  # noqa: E402
from tools.release.build_bundle import SUPPORTED_PLATFORMS  # noqa: E402

INSTALLER_ROOT = REPO_ROOT / "tools" / "site_config"
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"
FLOOR_JOB = "pr-agent-tests"
FLOOR_STEP = "Import installer closure on the floor interpreter"

# 各平台的**系统** python3（发行版随附版本；新增平台必须在此登记，否则用例 3 红）。
# 来源：Debian 13 → 3.13，Ubuntu 22.04 → 3.10，Ubuntu 24.04 → 3.12。
PLATFORM_SYSTEM_PYTHON = {
    ("debian", "13"): (3, 13),
    ("ubuntu", "22.04"): (3, 10),
    ("ubuntu", "24.04"): (3, 12),
}

# 3.10 之后才存在的 `from <module> import <name>` / `<module>.<attr>` 形态。
# 这不是完整的版本兼容矩阵——那由 CI 的真实加载兜；本表收的是**已经在本仓出现过或最容易被
# 顺手写进来**的那几个（`typing.Self` 就是 #2268 的元凶）。
_POST_FLOOR_SYMBOLS: dict[str, dict[str, tuple[int, ...]]] = {
    "typing": {
        "Self": (3, 11), "Never": (3, 11), "LiteralString": (3, 11), "assert_never": (3, 11),
        "get_overloads": (3, 11), "reveal_type": (3, 11), "TypeVarTuple": (3, 11),
        "Unpack": (3, 11), "Buffer": (3, 12), "override": (3, 12),
    },
    "enum": {"StrEnum": (3, 11)},
    "datetime": {"UTC": (3, 11)},
    "contextlib": {"chdir": (3, 11)},
    "asyncio": {"TaskGroup": (3, 11), "timeout": (3, 11), "timeout_at": (3, 11)},
    "itertools": {"batched": (3, 12)},
    "sys": {"stdlib_module_names": (3, 10)},  # 恰好等于下限：用来验证「== 下限」不误报
}
_POST_FLOOR_MODULES = {"tomllib": (3, 11)}
_FLOOR = preflight.MIN_PYTHON


def _is_type_checking(test: ast.expr) -> bool:
    """`if TYPE_CHECKING:` / `if typing.TYPE_CHECKING:` 的谓词形态。"""
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    return isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"


def _guarded_lines(tree: ast.Module) -> set[int]:
    """TYPE_CHECKING 分支内的行号——那里的 import 运行期不执行，是合规的兼容写法。"""
    lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and _is_type_checking(node.test):
            lines.update(sub.lineno for sub in ast.walk(node) if hasattr(sub, "lineno"))
    return lines


def _has_future_annotations(tree: ast.Module) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            if any(alias.name == "annotations" for alias in node.names):
                return True
    return False


def _findings_for_source(source: str, name: str = "<synthetic>") -> list[str]:
    """扫一份源码，返回「在声明下限上不可用」的构造清单。"""
    try:
        tree = ast.parse(source, filename=name)
    except SyntaxError as exc:
        # 当前解释器能解析、但下限解释器不认识的语法（`except*`、PEP 695）在此暴露
        return [f"{name}: 无法解析（{exc.msg}）"]
    aliases: dict[str, str] = {}
    findings: list[str] = []
    guarded = _guarded_lines(tree)
    # TYPE_CHECKING 里的 import 不执行，但注解必须在运行期也不求值才安全——
    # 没有 `from __future__ import annotations` 的文件不能享受这个豁免。
    deferred = _has_future_annotations(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                module = alias.name.split(".")[0]
                aliases[alias.asname or module] = module
                since = _POST_FLOOR_MODULES.get(module)
                if since and since > _FLOOR and not (node.lineno in guarded and deferred):
                    findings.append(f"{name}:{node.lineno} import {alias.name}（{'.'.join(map(str, since))}+）")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            table = _POST_FLOOR_SYMBOLS.get(module, {})
            skip = node.lineno in guarded and (module == "typing")
            for alias in node.names:
                since = table.get(alias.name)
                if not since or since <= _FLOOR:
                    continue
                if skip and deferred:
                    continue
                if skip:
                    findings.append(
                        f"{name}:{node.lineno} TYPE_CHECKING 里 import {alias.name} 但缺"
                        f" `from __future__ import annotations`（注解仍会在运行期求值）"
                    )
                    continue
                findings.append(f"{name}:{node.lineno} from {module} import {alias.name}（{'.'.join(map(str, since))}+）")
        elif isinstance(node, ast.Attribute):
            root: ast.expr = node
            parts: list[str] = []
            while isinstance(root, ast.Attribute):
                parts.append(root.attr)
                root = root.value
            if not isinstance(root, ast.Name):
                continue
            module = aliases.get(root.id)
            if not module:
                continue
            since = _POST_FLOOR_SYMBOLS.get(module, {}).get(parts[0])
            if since and since > _FLOOR and node.lineno not in guarded:
                findings.append(f"{name}:{node.lineno} {root.id}.{parts[0]}（{'.'.join(map(str, since))}+）")
    for node in ast.walk(tree):
        if getattr(node, "lineno", None) in guarded:
            continue
        if getattr(ast, "TryStar", None) and isinstance(node, ast.TryStar):
            findings.append(f"{name}:{node.lineno} except* 语法（3.11+）")
        if getattr(ast, "TypeAlias", None) and isinstance(node, ast.TypeAlias):
            findings.append(f"{name}:{node.lineno} type 别名语句（3.12+）")
        if getattr(node, "type_params", None):
            findings.append(f"{name}:{getattr(node, 'lineno', 0)} PEP 695 泛型语法（3.12+）")
    return findings


def _installer_sources() -> list[Path]:
    return sorted(INSTALLER_ROOT.rglob("*.py"))


def test_floor_detector_is_discriminative():
    """判别器自证：每一类 3.11+ 形态都必须被抓到，干净源码不得被抓。"""
    positives = [
        "from typing import Annotated, Self",
        # 合规写法被拆掉 future import 后必须重新变红（豁免不是无条件的）
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from typing import Self\n"
        "def f(x) -> Self: return x",
        "import datetime\nx = datetime.UTC",
        "from enum import StrEnum",
        "import tomllib",
        "import asyncio\nasync def f():\n    async with asyncio.TaskGroup() as tg: pass",
        "import contextlib, os\nwith contextlib.chdir('/tmp'): pass",
        "from itertools import batched",
        "type Alias = int",
        "def f(x):\n    try:\n        pass\n    except* ValueError:\n        pass",
    ]
    missed = [src for src in positives if not _findings_for_source(src)]
    assert not missed, "以下 3.11+ 形态没被抓到，判别器已失效：\n" + "\n".join(missed)
    negatives = [
        "from __future__ import annotations\nfrom typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n    from typing import Self\ndef f(x) -> Self: return x",
        "from typing import Annotated, Literal, TYPE_CHECKING",
        "import sys\nx = sys.stdlib_module_names",
        "import datetime\nx = datetime.timezone.utc",
        "import dataclasses\n@dataclasses.dataclass\nclass C: a: int",
        "match x:\n    case 1: pass",
    ]
    flagged = [src for src in negatives if _findings_for_source(src)]
    assert not flagged, "以下下限内合法的写法被误报：\n" + "\n".join(flagged)


def test_installer_import_closure_is_floor_compatible():
    """`tools/site_config` 全量源码不得出现高于声明下限的构造（#2268 本体）。"""
    sources = _installer_sources()
    assert len(sources) >= 15, f"扫描面异常缩小（{len(sources)} 个文件）——本守卫会变成恒真"
    findings: list[str] = []
    for path in sources:
        findings += _findings_for_source(path.read_text(encoding="utf-8"), path.relative_to(REPO_ROOT).as_posix())
    floor = ".".join(str(part) for part in preflight.MIN_PYTHON)
    assert not findings, (
        f"安装器 import 链用了 {floor}+ 的构造，声明下限为 {floor}"
        f"（Ubuntu 22.04 系统解释器）——要么改用下限内写法，要么把矩阵里的 22.04 撤掉："
        "\n" + "\n".join(findings)
    )


def test_support_matrix_and_floor_are_one_statement():
    """矩阵里每个平台的系统解释器都必须登记，且不低于 `preflight.MIN_PYTHON`。"""
    declared = {
        (platform["distribution"], version)
        for platform in SUPPORTED_PLATFORMS
        for version in platform["versions"]
    }
    unregistered = sorted(declared - set(PLATFORM_SYSTEM_PYTHON))
    assert not unregistered, (
        f"支持矩阵新增平台但没登记其系统 python3：{unregistered}——"
        f"这正是 #2268 的复发路径（矩阵说支持、没人核对解释器够不够）"
    )
    obsolete = sorted(set(PLATFORM_SYSTEM_PYTHON) - declared)
    assert not obsolete, f"登记表里还有已退出矩阵的平台，请删除：{obsolete}"
    lowest = min(PLATFORM_SYSTEM_PYTHON[key] for key in declared)
    assert preflight.MIN_PYTHON <= lowest, (
        f"MIN_PYTHON={preflight.MIN_PYTHON} 高于矩阵最低平台自带的 {lowest}——"
        f"要么该平台不再可达（须从矩阵撤出），要么把安装链降回 {lowest}"
    )


def test_ci_loads_the_closure_on_the_declared_floor():
    """CI 必须真用声明下限那档解释器加载一次闭包，且排在 3.11 依赖安装之前。"""
    workflow = yaml.safe_load(CI_YML.read_text(encoding="utf-8"))
    steps = workflow["jobs"][FLOOR_JOB]["steps"]
    floor_text = ".".join(str(part) for part in preflight.MIN_PYTHON)
    py_versions = [
        (index, str((step.get("with") or {}).get("python-version", "")))
        for index, step in enumerate(steps)
        if (step.get("with") or {}).get("python-version")
    ]
    floor_hits = [index for index, version in py_versions if version == floor_text]
    assert floor_hits, (
        f"{FLOOR_JOB} 里没有 setup-python {floor_text} 的步骤——声明的下限从未被真实加载过，"
        f"矩阵里的 Ubuntu 22.04 只是文档承诺"
    )
    floor_index = floor_hits[0]
    assert any(step.get("name") == FLOOR_STEP for step in steps[floor_index:floor_index + 2]), (
        f"`{FLOOR_STEP}` 必须紧跟在 setup-python {floor_text} 之后，否则它跑的不是 3.10"
    )
    smoke = next(step for step in steps if step.get("name") == FLOOR_STEP)
    assert "pkgutil" in smoke["run"] and "tools.site_config" in smoke["run"], (
        "冒烟步骤必须加载**整个 import 闭包**（pkgutil 遍历），只 import 入口模块只能"
        "覆盖 preflight 一条路径——#2268 的六个子命令正是从其余路径崩的"
    )
    later = [version for index, version in py_versions if index > floor_index]
    assert later, (
        f"{floor_text} 之后必须再 setup 一次主版本，否则后续 `pip install --require-hashes`"
        f" 会装进 {floor_text} 环境（dev lock 与解释器错位）"
    )


def test_preflight_fails_closed_on_a_too_old_interpreter():
    """版本不足时 preflight 必须 FAIL 并给出可执行 Fix（不是让 install 崩 traceback）。"""
    failing = preflight._python_floor_check((preflight.MIN_PYTHON[0], preflight.MIN_PYTHON[1] - 1))
    assert failing.status == "FAIL"
    assert failing.code in checks.MESSAGES, "FAIL 必须带 remediation，否则操作员只看到一句红"
    assert "python3" in checks.MESSAGES[failing.code].lower()
    ok = preflight._python_floor_check(preflight.MIN_PYTHON)
    assert ok.status == "PASS"
    assert failing.message.strip() and str(preflight.MIN_PYTHON[1]) in failing.message

    from tests.test_site_preflight import checks_by_id, healthy_ops

    report = preflight.run_preflight(ops=healthy_ops())
    assert "preflight.python" in checks_by_id(report), "下限断言没进 preflight 报告=没接线"
