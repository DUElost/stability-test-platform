"""源扫描型否定断言的棘轮门禁（#2639 建议 3）。

**为什么要有这条**：`assert "<字面量>" not in src` 型守卫（读被测源码再判存在/不存在）
在锚点漂移时会**静默失效**——它还在跑、还是绿的，只是不再覆盖任何东西
（#2639 第 3 例：DLE 落库点随 #1520 搬走后，两条否定断言恒真了一个窗口）。

判据（AST，不是 grep）：函数体内把 `read_text()` / `getsource()` 的结果绑到变量（或直接内联调用），
且对该变量做过 `assert <x> not in <它>` → 该用例是**源扫描型否定断言**，
必须改用 `tools/dev/source_anchor.py`（先证锚点在，再判形态）。

用 AST 而非文本 grep 是承接 #2641/#2642 的教训：注释里的同形文本不得满足判据，
判据必须落在代码行上。

**棘轮而非一次性迁移**：全家族是 28 个文件 / 78 处（本单示范迁移 2 例后，基线仍剩
26 个文件、75 处，`backend/agent/tests/**` 也在内），
一轮改完既做不到、也会和在窗 Execution 撞同一批文件。所以：

- `BASELINE` 之外的新 offender → 红（不再增加存量）；
- `BASELINE` 里已消失的条目 → 红（基线只能缩短，改完必须回来下调）；
- 一个 offender 都扫不到 → 红（判据/路径失效——#2639 自己的「12 个文件」就是被
  `git grep -- tests/**/*.py` 这条**静默零命中**的 pathspec 少算了 81 个文件的实例）。
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCAN_DIRS = ("tests", "backend/tests", "backend/agent/tests")
#: 助手自身与判据自身不参与扫描（它们的字面量是**描述**该形态，不是使用该形态）。
EXEMPT = {
    "tools/dev/source_anchor.py",
    "tests/test_source_scan_anchor_ratchet.py",
}
#: 助手模块名——**按导入判定**已迁移，不按子串（#2641/#2642 教训：注释里的同形文本
#: 不得满足判据——实测把 `# source_anchor` 写进任意文件就能骗过子串标记）。
HELPER_MODULE = "tools.dev.source_anchor"
_SOURCE_READER_ATTRS = {"read_text", "getsource"}

#: 存量清单（只能缩短）。注释里的数字是**该文件内的否定断言处数**，仅供排优先级。
BASELINE = frozenset(
    {
        "backend/agent/tests/test_device_flash_scripts.py",  # 5
        "backend/agent/tests/test_flash_firmware_v1316.py",  # 1
        "backend/agent/tests/test_flash_preflight_v102.py",  # 1
        "backend/agent/tests/test_legacy_tool_cleanup.py",  # 16
        "backend/tests/services/test_agent_installer.py",  # 3
        "backend/tests/services/test_dedup_scan_merge.py",  # 1
        "backend/tests/services/test_job_log_signal.py",  # 1
        "backend/tests/test_ci_and_test_harness_files.py",  # 5
        "backend/tests/test_deployment_files.py",  # 6
        "backend/tests/test_schema_sync_guard.py",  # 2
        "backend/tests/test_ssh_security.py",  # 1
        "tests/test_agent_priv_boundary.py",  # 3
        "tests/test_agentctl_contract.py",  # 2
        "tests/test_ansible_config_channel_2218.py",  # 2
        "tests/test_deploy_scripts.py",  # 3
        "tests/test_dev_bootstrap_seed.py",  # 1
        "tests/test_install_agent_noninteractive.py",  # 3
        "tests/test_pg_restore_drill.py",  # 1
        "tests/test_prepare_env.py",  # 1
        "tests/test_script_seed_static_guards.py",  # 1
        "tests/test_seed_revision_version_guard.py",  # 1
        "tests/test_site_bootstrap.py",  # 2
        "tests/test_site_handover.py",  # 3
        "tests/test_site_install.py",  # 4
        "tests/test_site_preflight.py",  # 2
        "tests/test_update_agent_playbook.py",  # 4
    }
)


def imports_helper(tree: ast.AST) -> bool:
    """该文件是否真的从助手模块导入了东西（判「已迁移」的唯一口径）。"""
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "") == HELPER_MODULE:
            return True
        if isinstance(node, ast.Import) and any(a.name == HELPER_MODULE for a in node.names):
            return True
    return False


def _reader_call(node: ast.AST) -> bool:
    return isinstance(node, ast.Call) and getattr(node.func, "attr", "") in _SOURCE_READER_ATTRS


def _source_bound_names(fn: ast.AST) -> set[str]:
    """函数内被「源码文本读取」结果绑定的变量名。"""
    names: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign) and any(_reader_call(c) for c in ast.walk(node.value)):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
    return names


def _negations_on_source(fn: ast.AST, names: set[str]) -> list[int]:
    """`assert <x> not in <源码>`（变量形态与内联调用形态都算）。"""
    hits: list[int] = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Assert) or not isinstance(node.test, ast.Compare):
            continue
        if not any(isinstance(op, ast.NotIn) for op in node.test.ops):
            continue
        right = node.test.comparators[0]
        bound_name = isinstance(right, ast.Name) and right.id in names
        if bound_name or _reader_call(right):
            hits.append(node.lineno)
    return hits


def scan_offenders(roots: list[Path]) -> dict[str, list[tuple[str, list[int]]]]:
    """返回 `{repo 相对路径: [(函数名, [行号…])…]}`，只含未使用助手的 offender。"""
    offenders: dict[str, list[tuple[str, list[int]]]] = {}
    for base in roots:
        for path in sorted(base.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            try:
                rel = path.relative_to(REPO_ROOT).as_posix()
            except ValueError:
                rel = path.as_posix()  # 判别力自证的临时目录不在仓库内
            if rel in EXEMPT:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            if imports_helper(tree):
                continue  # 已迁移：先证锚点在，再判形态
            per_fn: list[tuple[str, list[int]]] = []
            for fn in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)):
                lines = _negations_on_source(fn, _source_bound_names(fn))
                if lines:
                    per_fn.append((fn.name, lines))
            if per_fn:
                offenders[rel] = per_fn
    return offenders


def _default_roots() -> list[Path]:
    roots = [REPO_ROOT / d for d in SCAN_DIRS]
    missing = [str(r.relative_to(REPO_ROOT)) for r in roots if not r.is_dir()]
    assert not missing, f"扫描目录消失（判据会静默零命中）：{missing}"
    return roots


def test_offenders_equal_baseline_no_growth_no_staleness() -> None:
    """新增 offender 即红；基线里已不存在的条目也红（棘轮必须双向收紧）。"""
    found = set(scan_offenders(_default_roots()))
    grown = sorted(found - BASELINE)
    stale = sorted(BASELINE - found)
    assert not grown, (
        "新的源扫描型否定断言未走公共锚点助手（锚点漂移时它会恒真）：\n"
        + "\n".join(grown)
        + "\n改法：SourceGuard.of_module(...).anchored(真源锚点) 后再 assert_absent(...)，"
        "见 tools/dev/source_anchor.py"
    )
    assert not stale, (
        "这些文件已不含该形态（迁移完成或用例被删），请把 BASELINE 下调：\n"
        + "\n".join(stale)
    )



def test_scan_is_load_bearing() -> None:
    """判据必须真的命中东西——静默零命中就是 #2639 少算 81 个文件的那类事故。"""
    offenders = scan_offenders(_default_roots())
    assert offenders, "全仓零命中：判据或扫描路径已失效"
    total = sum(len(lines) for per_fn in offenders.values() for _, lines in per_fn)
    assert total >= 50, f"命中处数骤降到 {total}（基线 75），请复核判据是否被削弱"
    # 已知实例必须在场（三例中仍在存量里的第 3 例宿主与被注释掉的同类）
    assert "backend/agent/tests/test_legacy_tool_cleanup.py" in offenders


def test_detector_discriminates(tmp_path: Path) -> None:
    """判别力自证：真 offender 命中、helper 版不命中、纯注释/普通文件不命中。"""
    pkg = tmp_path / "tests"
    pkg.mkdir()
    (pkg / "bad.py").write_text(
        "from pathlib import Path\n\n\ndef t():\n"
        '    src = Path("x").read_text(encoding="utf-8")\n'
        '    assert "row.state = ev.state" not in src\n',
        encoding="utf-8",
    )
    (pkg / "good.py").write_text(
        "from tools.dev.source_anchor import SourceGuard\n\n\ndef t():\n"
        "    guard = SourceGuard.of_repo_path(\'a.py\').anchored(\'def f():\')\n"
        '    guard.assert_absent("row.state = ev.state", why="t")\n',
        encoding="utf-8",
    )
    (pkg / "comment_only.py").write_text(
        "def t():\n    src = __import__(\'pathlib\').Path(\'x\').read_text()\n"
        '    # 旧写法：assert "x" not in src —— 已改\n    assert len(src) > 0\n',
        encoding="utf-8",
    )
    (pkg / "unrelated.py").write_text(
        "def t():\n    values = [1, 2]\n    assert 9 not in values\n", encoding="utf-8"
    )
    # 注释里夹带助手模块名**不算**已迁移（子串判据会被这一招绕过，故按 AST 判定）
    (pkg / "smuggled.py").write_text(
        "# 已迁移到 tools.dev.source_anchor（其实没有）\n\n\ndef t():\n"
        '    src = Path("x").read_text(encoding="utf-8")\n'
        '    assert "row.state = ev.state" not in src\n',
        encoding="utf-8",
    )
    # 仓库外路径会退化成绝对路径（见 scan_offenders 的 rel 计算），按文件名比对
    found = {Path(rel).name for rel in scan_offenders([pkg])}
    assert found == {"bad.py", "smuggled.py"}, found


def test_exempt_list_is_not_self_defeating() -> None:
    """EXEMPT 只能含助手与判据自身，且这两个文件必须在场（防「加排除项」绕过）。"""
    assert EXEMPT == {
        "tools/dev/source_anchor.py",
        "tests/test_source_scan_anchor_ratchet.py",
    }
    for rel in EXEMPT:
        assert (REPO_ROOT / rel).is_file(), rel
