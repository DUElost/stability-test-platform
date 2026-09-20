"""种子迁移的**静态**契约守卫（#942/#2055；#2551 从容器文件拆出）。

为什么单独一个文件：这组判据只读迁移源码（AST + 文本），**不需要 Postgres**——
原先与「带数据行为测试」同住 ``tests/test_script_seed_governance.py``，而那个文件起
testcontainers，因此在 PR 路径被 ``--ignore`` 整文件排除（离线准入判据见
``tests/test_offline_subset_guard.py``）。后果是实测过的：``#2527`` 这类**假阳性**
在 main 上常红整整一天，PR 上零信号（#2551 的事实表）。

拆开后：本文件无容器依赖 → ``pr-agent-tests`` 的 "Run repo-level tests" 自动覆盖它
（root ``tests/`` 全量跑，只 ignore 两个容器文件），**契约级红当天可见**。

守卫：``test_static_guards_are_not_in_container_file`` 钉住这次拆分——把静态判据挪回
容器文件（或给本文件引入 testcontainers）都会红。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from tools.dev.source_anchor import SourceGuard

SEED_VERSIONS_DIR = Path(__file__).resolve().parents[1] / "backend" / "alembic" / "versions"

#: 「真正的停用」= `UPDATE … SET … is_active = false`（按序、有界窗口）。#2527：
#: 只要 `is_active` 与 `false` 相邻就命中，会把 **INSERT 一行非活跃版本**与
#: docstring/注释里的同串一并算上（假阳性），同时又**漏掉** ORM 写法（假阴性）。
#: #2526 补强：排除模块/函数/类 docstring 的常量节点；f-string 取字面量片段拼接，
#: 避免 ``text(f"...")`` 形态整条漏掉。
_UPDATE_DEACTIVATE_RE = re.compile(
    r"\bupdate\b[\s\S]{0,200}?\bset\b[\s\S]{0,200}?\bis_active\s*=\s*false\b",
    re.IGNORECASE,
)


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """模块 / 函数 / 类 docstring 的 ``Constant`` 节点 id（它们**不是** SQL）。"""
    ids: set[int] = set()
    holders = (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
    for node in ast.walk(tree):
        if isinstance(node, holders):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                ids.add(id(body[0].value))
    return ids


def deactivation_sites(source: str, *, filename: str = "<migration>") -> list[str]:
    """返回该迁移源码里**逐处**真实的「停用既有版本」位置（#2527 + #2526）。

    只认两种形态：

    1. SQL：``UPDATE … SET … is_active = false …``（字符串常量 / f-string 字面量片段，
       按序且有界窗口；**排除**模块/函数/类 docstring）；
    2. ORM：属性赋值 ``某对象.is_active = False``。

    **不判**：docstring/注释提到该串、以及 ``INSERT … VALUES (…, false, …)``——
    后者是「登记一个非活跃版本」，不是停用既有版本（#2399 的 ``e5f6a7b8c9d0`` 即此形态，
    旧判据把它误判成违约）。
    """
    tree = ast.parse(source)
    docstrings = _docstring_nodes(tree)
    sites: list[str] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Constant)
            and node.value.value is False
        ):
            for target in node.targets:
                if isinstance(target, ast.Attribute) and target.attr == "is_active":
                    sites.append(f"{filename}:{node.lineno} ORM is_active=False")
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        ):
            if _UPDATE_DEACTIVATE_RE.search(node.value):
                sites.append(f"{filename}:{node.lineno} SQL UPDATE … is_active=false")
        elif isinstance(node, ast.JoinedStr):
            literal = "".join(
                part.value
                for part in node.values
                if isinstance(part, ast.Constant) and isinstance(part.value, str)
            )
            if _UPDATE_DEACTIVATE_RE.search(literal):
                sites.append(f"{filename}:{node.lineno} SQL UPDATE … is_active=false")
    return sites


def _has_deactivation(path: Path) -> bool:
    """该迁移是否**执行**过「停用既有版本」（#2526 语料守卫入口；实现委托 #2527 AST）。"""
    return bool(
        deactivation_sites(path.read_text(encoding="utf-8"), filename=path.name)
    )


def _seed_files_with_deactivation() -> list[Path]:
    """真正停用既有版本的迁移文件（#2527：按 AST 判形态，不按整文件子串）。"""
    return sorted(
        p for p in SEED_VERSIONS_DIR.glob("*.py")
        if deactivation_sites(p.read_text(encoding="utf-8"), filename=p.name)
    )


#: 存量豁免（#2055）：2026-09-12 及之前新增、且已在生产应用的 seed——#942 治理模板
#: 自 2026-09-13 起才随新 seed 生效（对照：`a3b2c1d0e9f8` 起都带检查）。追溯改造这些
#: 已应用迁移会改变「全新安装」的行为（引用存在时直接中止部署），故本轮只做**前向**守卫。
#: 终态出口：随零引用版本退役（#735）自然收敛；新增文件一律不得进入本表。
#:
#: #2527/#2526：判据由「整文件子串 `is_active = false`」改为 AST 识别真实停用形态后，
#: 本表**新增一条 `i9j0k1l2m3n4`（2026-08-31，legacy 窗口内）**——它写的是
#: ``SET is_active=false``（等号两侧**无空格**），旧子串判据完全看不见它（0 命中）。
#: 这是同一处判据的**假阴性**侧：修好之后浮出来的存量按既定 legacy 口径登记，
#: 不做追溯改造（理由同上：会改变全新安装的行为）。
_LEGACY_SEEDS_WITHOUT_REF_CHECK = {
    "a7b8c9d0e1f2", "b7c8d9e0f1a2", "b8c9d0e1f2a3", "c0d1e2f3a4b5", "c9d0e1f2a3b4",
    "d3e4f5a6b7c8", "e1f2a3b4c5d6", "e7f8a9b0c1d2", "f0a1b2c3d4e5", "g1h2i3j4k5l6",
    "g5a6b7c8d9e0", "h2i3j4k5l6m7", "h8i9j0k1l2m3", "i5j6k7l8m9n0", "i9j0k1l2m3n4",
    "j0k1l2m3n4o5", "k1l2m3n4o5p6", "o9p8q7r6s5t4", "p8q7r6s5t4u3", "p9q8r7s6t5u4",
    "q3r4s5t6u7v8", "q7r6s5t4u3v2", "r6s5t4u3v2w1", "s2t3u4v5w6x7", "s5t4u3v2w1x0",
    "t2u3v4w5x6y7", "u3v4w5x6y7z8", "u6v5w4x3y2z1", "u7v8w9x0y1z2", "v5w4x3y2z1a0",
    "w4x3y2z1a0b9",
}


def _revision_of(path: Path) -> str:
    m = re.search(
        r'^revision(?:\s*:\s*[^=]+)?\s*=\s*["\']([^"\']+)',
        path.read_text(encoding="utf-8"),
        re.M,
    )
    return m.group(1) if m else path.stem[:12]


class TestDeactivationDetector:
    """#2527：判据只认真实停用形态——旧判据（整文件子串）两头都错：

    - 假阳性：``INSERT … VALUES (…, false, …)`` 与 docstring 提及被当成违约
      （#2399 的 ``e5f6a7b8c9d0`` 因此常红）；
    - 假阴性：``SET is_active=false``（无空格）与 ORM ``obj.is_active = False`` 完全看不见。
    """

    def test_insert_inactive_row_and_docstring_are_not_deactivation(self):
        source = "\n".join([
            '"""补行：新登记一个非活跃版本（is_active = false 见 docstring）。"""',
            "def upgrade():",
            "    op.get_bind().execute(text(",
            '        "INSERT INTO script (name, version, is_active) "',
            '        "VALUES (:name, :ver, false)"',
            "    ))",
        ])
        assert deactivation_sites(source, filename="x.py") == []

    def test_sql_update_with_spaces_is_deactivation(self):
        source = 'def upgrade():\n    op.get_bind().execute(text("UPDATE script SET is_active = false WHERE name=:n"))\n'
        assert deactivation_sites(source, filename="x.py"), "有空格写法必须可见"

    def test_sql_update_without_spaces_is_deactivation(self):
        # i9j0k1l2m3n4（2026-08-31）即此形态：旧子串判据 0 命中 → 假阴性
        source = 'def upgrade():\n    op.get_bind().execute(text("UPDATE script SET is_active=false, updated_at=:now WHERE name=:n"))\n'
        assert deactivation_sites(source, filename="x.py"), "无空格写法必须可见"

    def test_orm_assignment_is_deactivation(self):
        source = "def upgrade():\n    row.is_active = False\n"
        assert deactivation_sites(source, filename="x.py"), "ORM 写法必须可见"

    def test_orm_true_assignment_is_not_deactivation(self):
        source = "def upgrade():\n    row.is_active = True\n"
        assert deactivation_sites(source, filename="x.py") == []


def test_new_seed_migrations_deactivating_versions_check_references():
    """#2055：**新增** seed 凡停用既有版本，必须先做 plan_step 引用检查（#942 裁决 A）。

    服务层的治理单测覆盖不到迁移文件本身——实测 2026-09-12 前有 30 个 legacy seed 缺这步
    （见 `_LEGACY_SEEDS_WITHOUT_REF_CHECK` 与 Agent Note 的 Revisit）。本守卫只对新文件生效。
    """
    offenders: list[str] = []
    for path in _seed_files_with_deactivation():
        if _revision_of(path) in _LEGACY_SEEDS_WITHOUT_REF_CHECK:
            continue
        text = path.read_text(encoding="utf-8")
        defines = "def _raise_if_any_version_referenced(" in text
        calls = "_raise_if_any_version_referenced(" in text.replace(
            "def _raise_if_any_version_referenced(", ""
        )
        if not (defines and calls):
            offenders.append(path.name)
    assert not offenders, (
        "以下**新增**迁移会停用版本但没有引用检查（#942/#2055）："
        f"{offenders}——停用仍被 plan_step 引用的版本应在迁移期失败并给出重指指引"
    )


def test_legacy_allowlist_has_no_stale_entries():
    """豁免表不得留下「其实已带检查」或已删除的 revision（防豁免面悄悄扩大）。"""
    present = {_revision_of(p) for p in _seed_files_with_deactivation()}
    stale = sorted(_LEGACY_SEEDS_WITHOUT_REF_CHECK - present)
    assert not stale, f"豁免表存在失效条目（请移除）：{stale}"


def test_seed_migrations_do_not_delete_script_rows_on_downgrade():
    """#2055：downgrade 用 DELETE 会删掉并非本迁移创建的行——应翻转 is_active。"""
    offenders = [
        p.name
        for p in sorted(SEED_VERSIONS_DIR.glob("*.py"))
        if "DELETE FROM script " in p.read_text(encoding="utf-8")
    ]
    assert not offenders, f"以下迁移的 downgrade 用 DELETE 而非 is_active 翻转：{offenders}"


# ── 判据自身的守卫（文本匹配的两类假信号都在这里钉住；#2526 语料入口）────────


def test_deactivation_detector_reads_sql_not_prose(tmp_path):
    """停用判据只看 SQL 字面量：注释/文档字符串不算，INSERT 新行也不算。

    旧判据是 ``"is_active = false" in text``（整文件文本匹配），两个方向都出错：
    只在注释里提到该串的迁移被误判为停用；而 INSERT 一条 is_active=false 的新行
    （如 #2399 的自愈迁移）根本没有停用任何既有版本。
    """
    prose_only = tmp_path / "a_prose_only.py"
    prose_only.write_text(
        '"""docstring: ``is_active = false`` 只是说明文字。"""\n'
        "# 注释里的 is_active = false 也不算\n"
        "def upgrade():\n"
        "    pass\n",
        encoding="utf-8",
    )
    insert_only = tmp_path / "b_insert_only.py"
    insert_only.write_text(
        "def upgrade():\n"
        "    conn.execute(text(\n"
        '        "INSERT INTO script (name, is_active) VALUES (:n, false)"\n'
        "    ))\n",
        encoding="utf-8",
    )
    real = tmp_path / "c_real.py"
    real.write_text(
        "def upgrade():\n"
        "    conn.execute(text(\n"
        '        "UPDATE script SET is_active=false, updated_at=:now "\n'
        '        "WHERE name=\'gpu_setup\' AND version=\'1.0.2\'"\n'
        "    ))\n",
        encoding="utf-8",
    )

    assert not _has_deactivation(prose_only), "注释/docstring 里的提及不是停用"
    assert not _has_deactivation(insert_only), "INSERT 新行不是停用既有版本"
    assert _has_deactivation(real), "真停用（含无空格写法）必须被看见"


def test_deactivation_detector_corpus_delta():
    """对本仓全部迁移的判据落点：真停用必在，误报与漏报各自钉住一条实例。"""
    names = {p.name for p in _seed_files_with_deactivation()}
    # 真停用（`is_active=false` 无空格形态）——旧判据因精确匹配整份漏掉
    assert "i9j0k1l2m3n4_seed_gpu_setup_v104_stable_install.py" in names
    # 误报（两处提及全在注释/docstring 里，SQL 只 INSERT）——不得再被算作停用
    assert "e5f6a7b8c9d0_repair_flash_firmware_seed_identity_2399.py" not in names


# ── 拆分守卫（#2551）────────────────────────────────────────────────────────

_THIS = Path(__file__).resolve()
_CONTAINER_FILE = _THIS.parent / "test_script_seed_governance.py"
_CI = _THIS.parents[1] / ".github" / "workflows" / "ci.yml"
_RUN_GATES = _THIS.parents[1] / "scripts" / "run_gates.py"
_REPO_ROOT = _THIS.parents[1]
#: 两个接线文件**共同**的替代物：容器文件的合法 ignore 行。有共同替代物 ⇒
#: 循环不需要 per-file 锚点表（#2639 第七批判据）。
_CONTAINER_IGNORE_LINE = "--ignore=tests/test_script_seed_governance.py"

_STATIC_NAMES = (
    "test_new_seed_migrations_deactivating_versions_check_references",
    "test_legacy_allowlist_has_no_stale_entries",
    "test_seed_migrations_do_not_delete_script_rows_on_downgrade",
    "test_deactivation_detector_reads_sql_not_prose",
    "test_deactivation_detector_corpus_delta",
)


def test_this_file_has_no_container_dependency():
    """本文件必须保持「离线可跑」——它是契约级守卫的 PR 路径载体。

    判**导入行**而不是子串：本文件与守卫自身的说明里都会出现那个词，
    子串判据会被自己命中（`test_offline_subset_guard.py` 踩过同一个坑）。
    """
    src = _THIS.read_text(encoding="utf-8")
    assert not re.search(r"^\s*(from|import)\s+testcontainers\b", src, re.M)


def test_static_guards_are_not_back_in_the_container_file():
    """拆分守卫：静态判据不得再挪回容器文件（那会让它们退回「只在夜间跑」）。"""
    container_src = _CONTAINER_FILE.read_text(encoding="utf-8")
    back = [name for name in _STATIC_NAMES if f"def {name}(" in container_src]
    assert not back, (
        f"这些静态判据又回到了容器文件里（PR 路径会整文件 ignore）：{back}——"
        "它们只读迁移源码，应留在 test_script_seed_static_guards.py"
    )


def test_this_file_is_on_the_pr_path():
    """接线：本文件不得出现在 CI / run_gates 的 `--ignore` 名单里（否则等于白拆）。"""
    name = _THIS.name
    for path in (_CI, _RUN_GATES):
        guard = SourceGuard.of_repo_path(path.relative_to(_REPO_ROOT)).anchored(
            _CONTAINER_IGNORE_LINE, expect=1
        )
        guard.assert_absent(
            f"tests/{name}",
            why=f"{path.name} 把本文件忽略了——PR 路径将不跑它（静态判据等于白拆）",
        )
    # 反向：容器文件必须仍在 ignore 名单里（否则 PR 路径会尝试起容器）
    assert "tests/test_script_seed_governance.py" in _CI.read_text(encoding="utf-8")
