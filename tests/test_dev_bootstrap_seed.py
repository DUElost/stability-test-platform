"""#2381：dev bootstrap 的真值——空库必须拿到字典 seed，兜底必须自带标注。

`backend/scripts/init_dev_db.py` 曾用 ``Base.metadata.create_all()`` 建 dev 库：
38 张表、**0 行字典 seed**（``specialty`` 等静态字典的唯一事实源是 seed 迁移，
没有写端点），于是「新建 Plan 无专项可选」在每次 ``docker compose up`` 后复现，
且看起来像 schema 问题。容器实测（``tests/test_alembic_upgrade.py``）给出
before=38 表/0 specialty、after=39 表/6 specialty 的地面真值。

本文件钉住两条自己守不住的判据：

1. **脚本方式的 import 契约**——compose 以 ``python /app/backend/scripts/init_dev_db.py``
   调用，``sys.path[0]`` 是脚本目录而非仓库根；模块顶层 ``import backend.*`` 会在
   ``sys.path`` 注入之前就执行，直接 ModuleNotFoundError。``ruff check`` 抓不到这类
   问题（它不模拟运行时 sys.path），所以必须有运行时探针。
2. **兜底路径必须标注**——AGENTS.md：临时止血必须标注为过渡并写明终态出口。
   ``create_all`` 只作为「有表但无 ``alembic_version``」的 legacy dev 库兜底保留，
   输出里要写清代价（字典 seed 不会补齐）与出路（``alembic stamp``）。不标注的
   止血正是 #2381 藏了这么久的原因。
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "backend" / "scripts" / "init_dev_db.py"


def _source() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def _bootstrap_fn() -> ast.FunctionDef:
    fn = next(
        (n for n in ast.parse(_source()).body
         if isinstance(n, ast.FunctionDef) and n.name == "_bootstrap_schema"),
        None,
    )
    assert fn is not None, "_bootstrap_schema 消失：dev 库的 schema 路径必须收在这个函数里"
    return fn


def _bootstrap_src() -> str:
    """函数体源码（不含 docstring——docstring 里提到 create_all 会污染文本判据）。"""
    fn = _bootstrap_fn()
    lines = _source().splitlines()[fn.body[0].lineno - 1:fn.end_lineno]
    return "\n".join(lines)


def _call_lineno(fn: ast.FunctionDef, predicate) -> int:
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and predicate(node.func):
            return node.lineno
    raise AssertionError("未在 _bootstrap_schema 中找到目标调用")


def test_module_top_level_does_not_import_backend():
    """顶层不得 import ``backend.*``——必须在 ``sys.path`` 注入之后（函数体内）。

    这就是 #2381 修复过程中真实踩到的坑：把 ``resolve_database_url`` 提到模块顶层，
    compose 的脚本方式调用会直接崩，而 lint 全绿。
    """
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    offenders = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            mods = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            mods = [node.module or ""]
            if node.level:  # 相对 import
                mods = ["." * node.level + (node.module or "")]
        else:
            continue
        offenders += [m for m in mods if m.split(".")[0] == "backend"]

    assert not offenders, (
        f"init_dev_db.py 顶层 import 了 backend.*：{offenders}。脚本方式调用时 "
        f"sys.path[0] 是脚本目录，顶层 backend import 先于 sys.path 注入执行 → ModuleNotFoundError。"
    )


def test_script_mode_invocation_reaches_production_guard(tmp_path):
    """以 compose 的真实形态（脚本绝对路径 + 非仓库根 cwd）跑一遍模块体。

    用 ``ENV=production`` 的早退分支做探针：能打出拒绝信息，说明模块体在脚本方式下
    完整执行过（import 与 sys.path 注入顺序正确），且没有碰数据库。
    """
    env = {k: v for k, v in os.environ.items() if k != "ENV_SOURCE_FILE"}
    env.update({
        "TESTING": "1",
        "ENV": "production",
        "JWT_SECRET_KEY": "dev-bootstrap-probe",
        # 显式覆盖，绝不把本机 ambient 连接串带进子进程（本机可能是生产控制面）。
        "DATABASE_URL": "postgresql+psycopg://probe:probe@127.0.0.1:5432/stp_probe",
    })

    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0, "ENV=production 必须拒绝，否则 dev 脚本能打到生产库"
    assert "Refusing to initialize dev DB" in result.stderr + result.stdout, (
        f"没走到生产守卫，说明模块体在守卫之前就崩了：\n{result.stderr}"
    )


_DECISION_PROBE = (
    "import sys\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "import backend.scripts.init_dev_db as m\n"
    "cases = [\n"
    "    ('empty', set()),\n"
    "    ('on_chain', {'alembic_version', 'plan'}),\n"
    "    ('legacy', {'plan', 'users'}),\n"
    "]\n"
    "print('|'.join(f'{n}={m._choose_bootstrap_path(ts)}' for n, ts in cases))\n"
)


def test_path_decision_favour_alembic_for_every_chain_adoptable_db():
    """空库与已在链上的库都必须判为 alembic——这是字典 seed 的唯一来源。

    判据打在纯函数上（不是文本匹配）：把条件改成 ``if False`` 这类「形似保留、
    实质退回 create_all」的写法会直接红。顺带证明本模块可无副作用 import。
    """
    env = {k: v for k, v in os.environ.items() if k != "ENV_SOURCE_FILE"}
    env.update({
        "TESTING": "1",
        "JWT_SECRET_KEY": "dev-bootstrap-probe",
        "DATABASE_URL": "postgresql+psycopg://probe:probe@127.0.0.1:5432/stp_probe",
    })
    result = subprocess.run(
        [sys.executable, "-c", _DECISION_PROBE, str(ROOT)],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr

    got = dict(kv.split("=", 1) for kv in result.stdout.strip().splitlines()[-1].split("|"))
    assert got == {
        "empty": "alembic",
        "on_chain": "alembic",
        "legacy": "create_all_legacy",
    }, f"dev bootstrap 路径判定变了：{got}（空库判错就是 #2381 复发）"


def test_alembic_branch_runs_before_create_all():
    """``_bootstrap_schema`` 里 alembic 升级必须排在 ``create_all`` 之前。"""
    fn = _bootstrap_fn()
    upgrade_line = _call_lineno(fn, lambda f: isinstance(f, ast.Name) and f.id == "_run_upgrade")
    create_all_line = _call_lineno(
        fn, lambda f: isinstance(f, ast.Attribute) and f.attr == "create_all"
    )
    assert upgrade_line < create_all_line, (
        f"create_all（:{create_all_line}）排在 alembic 升级（:{upgrade_line}）之前——"
        "那就是 #2381 的原状：空库拿不到字典 seed"
    )
    imported = {
        (node.module, alias.name)
        for node in ast.walk(fn)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assert ("backend.scripts.check_schema_sync", "_run_upgrade") in imported, (
        "必须复用 check_schema_sync 的同一条升级通道（含 #934 ambient-DATABASE_URL 语义），"
        "不要在同一仓库里写第二份 alembic 调用"
    )


def test_create_all_fallback_is_labelled_with_cost_and_exit():
    """兜底保留可以，但必须在输出里写明代价与终态出口。"""
    body = _bootstrap_src()

    assert "Base.metadata.create_all" in body, "legacy dev 库兜底被删：收养无 alembic_version 的库仍需可用路径"
    assert "path=create_all_legacy" in body, "兜底路径没有显式标识，无法从输出判读走了哪条"
    for marker in ("dictionary_seeds_not_applied", "specialty", "alembic stamp"):
        assert marker in body, f"兜底告警缺少 {marker!r}：未标注的止血会沉淀成技术债（AGENTS.md）"


def test_bare_unlabelled_ready_line_is_gone():
    """旧的 ``print("dev_db_schema_ready")`` 不区分路径，是 #2381 的遮蔽来源。"""
    src = SCRIPT.read_text(encoding="utf-8")
    assert 'print("dev_db_schema_ready")' not in src, (
        "ready 输出必须带 path=，否则 create_all 兜底与 alembic 链在日志里同形"
    )


def test_production_guard_runs_before_schema_work():
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
    calls = [
        node.func.id
        for node in ast.walk(main)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ]
    assert calls.index("_refuse_production") < calls.index("_bootstrap_schema"), (
        "生产守卫必须在任何 schema 动作之前"
    )
