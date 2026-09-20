"""源扫描型否定断言的棘轮门禁（#2639 建议 3）。

**为什么要有这条**：`assert "<字面量>" not in src` 型守卫（读被测源码再判存在/不存在）
在锚点漂移时会**静默失效**——它还在跑、还是绿的，只是不再覆盖任何东西
（#2639 第 3 例：DLE 落库点随 #1520 搬走后，两条否定断言恒真了一个窗口）。

判据（AST，不是 grep）：函数体内把 `read_text()` / `getsource()` 的结果绑到变量（或直接内联调用），
且对该变量做过 `assert <x> not in <它>` → 该处是**源扫描型否定断言**，
必须改用 `tools/dev/source_anchor.py`（先证锚点在，再判形态）。

**判据的最小单位是断言，不是文件**：不做「该文件导入了助手就整体豁免」——否则一个已迁移
文件把某条 `assert_absent` 退回裸 `assert ... not in text` 时反而**变成免检**（导入越彻底，
覆盖越低，方向正好反了）。助手用的是 `guard.assert_absent(...)` 方法调用而非 `assert` 语句，
所以正确迁移的函数天然零命中，不需要文件级豁免；可豁免的只有 `EXEMPT` 里两个「描述该形态」
的元文件，且被 `test_exempt_list_is_not_self_defeating` 钉死不扩大。

**断言对象必须真的是「那篇源文本」**：读进来的文本若被 `json.loads` / `yaml.safe_load` /
`ast.parse` 解析成结构、被渲染函数产出成页面、或与序列化产物混进同一条表达式，
那么 `assert x not in 结果` 判的是**行为**（解析器/渲染器对不对），不是源扫描——它没有真源
锚点可编，硬要迁移只会造出假锚点。这类形态不算 offender（#2639 落地后复盘：存量里有 7 处
根本不是源扫描，把它们计入债务清单会让「还剩多少活」失真）。

**但默认方向是反的**：源文本进了**未登记**的函数时，按「仍然是文本」处理、照旧判红。
判据少抓可以下一轮补，**静默放过正是本棘轮要消灭的那件事**。放过路径**只有「显式登记的产物消费者」一条**，其余全按源文本判红——
夹具 `mystery.py`（未知消费者仍须命中）与 `washed_text.py`（洗过的文本仍须命中）钉住这个默认值，
`parsed.py`/`rendered.py`/`mixed.py` 钉住登记形态，另有
`test_tightening_only_subtracts_never_adds` 把「本收紧只做减法」变成逐位点比对，
`test_product_consumers_are_load_bearing` 禁止登记不再在场的产物消费者（死条目＝未来的黑洞）。

用 AST 而非文本 grep 是承接 #2641/#2642 的教训：注释里的同形文本不得满足判据，
判据必须落在代码行上。

**棘轮而非一次性迁移**：一轮改完既做不到、也会和在窗 Execution 撞同一批文件。所以：

- `BASELINE` 之外的新 offender → 红（不再增加存量）；
- `BASELINE` 里已消失的条目 → 红（基线只能缩短，改完必须回来下调）；
- 一个 offender 都扫不到 → 红（判据/路径失效——#2639 自己的「12 个文件」就是被
  `git grep -- tests/**/*.py` 这条**静默零命中**的 pathspec 少算了 81 个文件的实例）。

**存量规模不在这里写死**（承接 #2663：可派生量抄成文字就没人约束它）：现状口径只有两处——
`BASELINE` 的成员，以及 `test_scan_is_load_bearing` 当场数出的命中数。历史数字看
`git log --oneline -- <本文件>` 与 `docs/notes/testing/`。
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
_SOURCE_READER_ATTRS = {"read_text", "getsource"}

#: 结果**不再是文本**的具名消费者：解析成结构、或渲染/序列化成产物。
#: 对产物判「某词不存在」是**行为断言**（渲染器/解析器对不对），不是源扫描——它没有
#: 「真源锚点」可编，硬迁移只会造出假锚点。每条必须带理由，且不得留死条目
#: （见 `test_product_consumers_are_load_bearing`）。
_PRODUCT_CONSUMERS = {
    "json.loads": "文本已被解析成 Python 结构，断言对象是 dict/list 不是源文本",
    "json.dumps": "序列化产物，不是任何真源文件",
    "yaml.safe_load": "同 json.loads（YAML 侧）",
    "ast.parse": "解析成 AST，后续判的是节点不是文本",
    "render_navigation_page": "站点导航页**渲染产物**（tests/test_site_handover.py 三例）",
}

_SRC = "src"  # 是那篇源文本（含保文本变换）
_PRODUCT = "prod"  # 已被解析/渲染成别的东西——不属于本判据
_UNKNOWN = "unknown"  # 判不出来：**按源文本处理**（宁可多判，不可漏判）


def _callee_key(node: ast.Call) -> str:
    """调用者的可判定名字：`json.loads(x)` → "json.loads"；`a.read_text().replace()` → "replace"。"""
    func = node.func
    parts: list[str] = []
    while isinstance(func, ast.Attribute):
        parts.append(func.attr)
        func = func.value
    if isinstance(func, ast.Name):
        parts.append(func.id)
        return ".".join(reversed(parts))
    return parts[0] if parts else ""


def _arg_expr(node: ast.expr) -> ast.expr:
    """`f(*src)` 里的 `Starred` 拆一层；其余实参本身就是表达式。"""
    return node.value if isinstance(node, ast.Starred) else node


def _contains_reader(node: ast.AST) -> bool:
    return any(_reader_call(child) for child in ast.walk(node))


def _pick(parts: list[str]) -> str:
    """混合表达式：产物一旦混进来就不再是纯源文本；否则见源文本即源文本。"""
    if _PRODUCT in parts:
        return _PRODUCT
    if _SRC in parts:
        return _SRC
    return _UNKNOWN


def _provenance(node: ast.AST) -> str:
    """该表达式的值是「源文本」还是「解析/渲染产物」。不做数据流，判不出即 `_UNKNOWN`。"""
    if isinstance(node, ast.Call):
        if _reader_call(node):
            return _SRC
        key = _callee_key(node)
        if key in _PRODUCT_CONSUMERS:
            return _PRODUCT
        arg_provs = [_provenance(_arg_expr(a)) for a in node.args]
        arg_provs += [_provenance(kw.value) for kw in node.keywords]
        arg_provs = [p for p in arg_provs if p != _UNKNOWN]
        if _SRC in arg_provs:
            return _UNKNOWN  # 源文本进了**未知**函数：算不算文本判不出来 → 保守按文本
        return _PRODUCT if _PRODUCT in arg_provs else _UNKNOWN
    if isinstance(node, ast.Attribute | ast.Subscript):
        return _provenance(node.value)
    if isinstance(node, ast.BinOp):
        return _pick([_provenance(node.left), _provenance(node.right)])
    if isinstance(node, ast.JoinedStr):
        return _pick([_provenance(v) for v in node.values])
    if isinstance(node, ast.IfExp):
        return _pick([_provenance(node.body), _provenance(node.orelse)])
    if isinstance(node, ast.BoolOp):
        return _pick([_provenance(v) for v in node.values])
    return _UNKNOWN


def _is_source_like(value: ast.AST) -> bool:
    """绑成「源码文本变量」的口径：**判得出是产物才放过**，未知一律按源文本算。

    与旧版（子树里出现过 `read_text()` 就算）相比只**减去**可证的解析/渲染形态，
    不新增任何放过路径——假阳性可以慢慢收，**假阴性是静默的**（#2639 的立单理由）。
    """
    return _provenance(value) != _PRODUCT and _contains_reader(value)
#: 命中处数**下限**（不是现状计数）：判据被削弱时兜底，存量迁移不会撞红它。
#: 现状 45 处/21 文件（#2639 第四批迁移 playbook+agent_priv 共 7 处后由 50 下调）。
SITE_FLOOR = 40

#: 〔产物路径轴 n/m〕＝该文件 m 处里有 n 处断言的对象在 `tmp_path` 下（安装/渲染产物，不是仓库源），
#: 迁移它们只能编出**假锚点**。这些位点等「口径轴二」（判据识别 tmp 目录派生读取）释放，勿先动手。
#: 实测与理由见 `docs/notes/testing/2026-09-20-source-scan-batch4-playbook-priv-2639.md`。
#: 存量清单（只能缩短）。注释里的数字是**该文件内的否定断言处数**，仅供排优先级。
BASELINE = frozenset(
    {
        "backend/agent/tests/test_device_flash_scripts.py",  # 5
        "backend/agent/tests/test_flash_firmware_v1316.py",  # 1
        "backend/agent/tests/test_flash_preflight_v102.py",  # 1
        "backend/tests/services/test_agent_installer.py",  # 3
        "backend/tests/services/test_dedup_scan_merge.py",  # 1
        "backend/tests/services/test_job_log_signal.py",  # 1
        "backend/tests/test_ci_and_test_harness_files.py",  # 5
        "backend/tests/test_deployment_files.py",  # 6
        "backend/tests/test_ssh_security.py",  # 1  # 〔产物路径轴 1/1〕
        "tests/test_agentctl_contract.py",  # 2
        "tests/test_ansible_config_channel_2218.py",  # 1
        "tests/test_deploy_scripts.py",  # 3
        "tests/test_dev_bootstrap_seed.py",  # 1
        "tests/test_install_agent_noninteractive.py",  # 3  # 〔产物路径轴 1/3〕
        "tests/test_pg_restore_drill.py",  # 1
        "tests/test_prepare_env.py",  # 1  # 〔产物路径轴 1/1〕
        "tests/test_script_seed_static_guards.py",  # 1
        "tests/test_seed_revision_version_guard.py",  # 1
        "tests/test_site_bootstrap.py",  # 2  # 〔产物路径轴 2/2〕
        "tests/test_site_install.py",  # 3  # 〔产物路径轴 3/3〕
        "tests/test_site_preflight.py",  # 2
    }
)


def _reader_call(node: ast.AST) -> bool:
    return isinstance(node, ast.Call) and getattr(node.func, "attr", "") in _SOURCE_READER_ATTRS


def _source_bound_names(fn: ast.AST) -> set[str]:
    """函数内被「源码文本读取」结果绑定的变量名（解析/渲染产物不算）。"""
    names: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign) and _is_source_like(node.value):
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
        if bound_name or _is_source_like(right):
            hits.append(node.lineno)
    return hits


def iter_candidates(roots: list[Path]) -> list[Path]:
    """扫描根下真正被读到的候选文件。

    与 `scan_offenders` 分开，是为了把**走到了目录**与**判据命中**这两件事分开证：
    前者与存量无关、不会因某目录迁移完而失效，正是 #2639 那次 pathspec 静默零命中
    唯一能长期防住的形态。
    """
    found: list[Path] = []
    for base in roots:
        found.extend(p for p in sorted(base.rglob("*.py")) if "__pycache__" not in p.parts)
    return found


def scan_offenders(roots: list[Path]) -> dict[str, list[tuple[str, list[int]]]]:
    """返回 `{repo 相对路径: [(函数名, [行号…])…]}`，只含未使用助手的 offender。"""
    offenders: dict[str, list[tuple[str, list[int]]]] = {}
    for path in iter_candidates(roots):
        try:
            rel = path.relative_to(REPO_ROOT).as_posix()
        except ValueError:
            rel = path.as_posix()  # 判别力自证的临时目录不在仓库内
        if rel in EXEMPT:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
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
    assert total >= SITE_FLOOR, f"命中处数骤降到 {total}（下限 {SITE_FLOOR}），请复核判据是否被削弱"
    # 每个扫描根都必须真的被走到。**不**按「某个具体 offender 必须在场」钉：
    # 那等于把判据钉在存量上，该文件迁移完成后它要么逼一次无谓改动、要么变成恒真——
    # 正是本助手要消灭的那类空守（第二批迁移即 #2639 时钉的这条被迁移撞红的实例）。
    visited = iter_candidates(_default_roots())
    assert visited, "候选文件集合为空：扫描根或 pathspec 已失效"
    for root in SCAN_DIRS:
        depth = len(Path(root).parts)
        reached = [p for p in visited if p.relative_to(REPO_ROOT).parts[:depth] == Path(root).parts]
        assert reached, f"扫描根 {root}/ 零命中：该目录被静默漏掉（#2639 的原始事故形态）"


def test_scan_dirs_is_not_narrowed() -> None:
    """SCAN_DIRS 只能扩大不能缩小——`test_scan_is_load_bearing` 按它自查，删一项就少一项检查。

    与 `test_exempt_list_is_not_self_defeating` 同一类反自毁钉子：被约束的对象不能自己给自己
    发通行证。（#2639 的原始事故正是「扫不到就算通过」，而当时没人钉住扫描面本身。）
    """
    assert set(SCAN_DIRS) == {
        "tests",
        "backend/tests",
        "backend/agent/tests",
    }, "扫描面被改动：缩小即漏防，扩大需同步下调对应存量"


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
    # **解析成结构**后判键存在性：断言对象不是源文本，放过（#2639 假阳性主因之一）
    (pkg / "parsed.py").write_text(
        "import json\nfrom pathlib import Path\n\n\ndef t():\n"
        '    data = json.loads(Path("x").read_text(encoding="utf-8"))\n'
        '    assert "remove_table|ghost" not in data\n',
        encoding="utf-8",
    )
    # 渲染**产物**：同上，判的是渲染器的输出，不是任何真源文件
    (pkg / "rendered.py").write_text(
        "from pathlib import Path\n\n\ndef t(ctx):\n"
        '    page = render_navigation_page(Path("x").read_text(encoding="utf-8"), ctx)\n'
        '    assert "<deploy-root>" not in page\n',
        encoding="utf-8",
    )
    # 产物与文本**混在一起**再判：整表达式已不是纯源文本，放过
    (pkg / "mixed.py").write_text(
        "import json\nfrom pathlib import Path\n\n\ndef t(report):\n"
        '    blob = json.dumps(report) + Path("x").read_text(encoding="utf-8")\n'
        '    assert "PRIVATE" not in blob\n',
        encoding="utf-8",
    )
    # **洗一遍文本**再判（去注释/换行）：仍是源扫描，必须命中——把这类判成产物，
    # 就是本判据最贵的一类错误（静默漏防），故单独钉一条。
    (pkg / "washed_text.py").write_text(
        "from pathlib import Path\n\n\ndef t():\n"
        '    code = "\\n".join(\n'
        '        line\n'
        '        for line in Path("x").read_text(encoding="utf-8").splitlines()\n'
        '        if not line.startswith("#")\n'
        "    )\n"
        '    assert "become: yes" not in code\n',
        encoding="utf-8",
    )
    # 源文本进了**未知**函数：算不算文本判不出来 → 保守按源文本命中
    (pkg / "mystery.py").write_text(
        "from pathlib import Path\n\n\ndef t():\n"
        '    out = some_unregistered_helper(Path("x").read_text(encoding="utf-8"))\n'
        '    assert "SECRET" not in out\n',
        encoding="utf-8",
    )
    # 已导入助手的文件里**残留**一条裸否定断言：整文件豁免会把它放走，故必须命中
    (pkg / "regressed.py").write_text(
        "from pathlib import Path\n"
        "from tools.dev.source_anchor import SourceGuard\n\n\ndef t():\n"
        "    SourceGuard.of_repo_path('a.py').anchored('def f():')\n"
        '    src = Path("x").read_text(encoding="utf-8")\n'
        '    assert "row.state = ev.state" not in src\n',
        encoding="utf-8",
    )
    # 仓库外路径会退化成绝对路径（见 scan_offenders 的 rel 计算），按文件名比对
    found = {Path(rel).name for rel in scan_offenders([pkg])}
    assert found == {"bad.py", "smuggled.py", "regressed.py", "washed_text.py", "mystery.py"}, found


def _callee_keys_in(roots: list[Path]) -> set[str]:
    """语料里真实出现过的调用者名字（含末段方法名，便于按 `json.loads` 这类点号名比对）。"""
    keys: set[str] = set()
    for path in iter_candidates(roots):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for call in (n for n in ast.walk(tree) if isinstance(n, ast.Call)):
            key = _callee_key(call)
            keys.add(key)
            keys.add(key.rsplit(".", 1)[-1])
    return keys


def test_product_consumers_are_load_bearing() -> None:
    """`_PRODUCT_CONSUMERS` 每条都要**有理由**且**在语料里真的出现**。

    登记一个不再被任何代码使用的「产物消费者」，等于给未来留一个静默放过位：
    以后同名函数读源码再判禁词会被直接判成产物而不再受检。故此处强制「要么在场，要么删掉」。
    """
    keys = _callee_keys_in(_default_roots())
    dead = sorted(k for k in _PRODUCT_CONSUMERS if k not in keys)
    assert not dead, f"这些产物消费者已不在扫描面里，请删除（留着就是未来的漏判位）：{dead}"
    no_reason = sorted(k for k, v in _PRODUCT_CONSUMERS.items() if not v.strip())
    assert not no_reason, f"这些条目没写理由，写不清它为什么不是源文本就不该登记：{no_reason}"


def test_tightening_only_subtracts_never_adds() -> None:
    """新口径必须是旧口径的**子集**：只允许减少命中，绝不允许新增放过路径之外的命中。

    这条是「假阳性可以慢慢收，假阴性是静默的」的可执行化：哪天有人把默认值从
    「未知按源文本」翻成「未知按产物」，命中数会**下降**但方向错了——这里用逐位点
    比对把它拦下来（而不是只看总数）。
    """
    def legacy_bound_names(fn: ast.AST) -> set[str]:
        names: set[str] = set()
        for node in ast.walk(fn):
            if isinstance(node, ast.Assign) and any(_reader_call(c) for c in ast.walk(node.value)):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        names.add(target.id)
        return names

    lost: list[str] = []
    for path in iter_candidates(_default_roots()):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for fn in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)):
            old, new = legacy_bound_names(fn), _source_bound_names(fn)
            if not new <= old:
                rel = path.relative_to(REPO_ROOT).as_posix() if path.is_relative_to(REPO_ROOT) else path.name
                lost.append(f"{rel}:{fn.name} 新增绑定 {sorted(new - old)}")
    assert not lost, "判据出现了旧口径没有的绑定（本收紧只做减法）：\n" + "\n".join(lost)


def test_exempt_list_is_not_self_defeating() -> None:
    """EXEMPT 只能含助手与判据自身，且这两个文件必须在场（防「加排除项」绕过）。"""
    assert EXEMPT == {
        "tools/dev/source_anchor.py",
        "tests/test_source_scan_anchor_ratchet.py",
    }
    for rel in EXEMPT:
        assert (REPO_ROOT / rel).is_file(), rel
