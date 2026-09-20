"""锁序家族（#2635/#2787/#2796/#2871/#2901）的**机读判据**：循环体内取行锁 ⇒ 集合内必须有全序。

家族原则（见 `docs/notes/bug-fix/2026-09-20-recovery-sync-job-lock-order-2871.md` 与
`2026-09-19-lock-order-two-halves-2787-2796.md`）：`SELECT … FOR UPDATE` 的**取锁顺序**
必须与对侧的全序（`ORDER BY job_instance.id`）一致——集合的「PG 返回序无保证」在代码里
看不见，所以这个家族已经出现四次；第四次（#2901）正是靠重扫全仓才发现的。本文件把它变成
**未知即红**：新增一处「循环里取锁、集合无全序」的写法，这里就会响。

认三种「有全序」的形态：

1. 迭代对象是 `sorted(...)` / `enumerate(sorted(...))`——集合在内存里定序；
2. 迭代对象是名字，且其**同一函数内**的赋值源文段含 `order_by(`（SQL 里定序）或 `sorted(`
   （内存里定序，`enumerate(sorted(...))` 这类先赋值再枚举的形态也归此条）；
3. 迭代对象本身是内联调用且源文段含 `order_by(`。

`_PENDING_TOTAL_ORDER` 登记尚未修、但有明确在队单的行锁循环；修复合入后该登记必须删
（陈旧登记同样红）——这是本仓对「已知欠账」的既有形态（如种子门禁的 legacy 清单）。
#2871（`agent_recovery.py` / `payload.active_jobs`）已由 PR #2903 合入，登记表当前为空。
"""
from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND = REPO_ROOT / "backend"

#: 尚未修、但有明确在队单的行锁循环：`(文件, 迭代对象源文段)` → 说明。
#: 修复合入后必须删登记——`test_pending_total_order_entries_are_not_stale` 会红。
_PENDING_TOTAL_ORDER: dict[tuple[str, str], str] = {}


def _has_lock(node: ast.AST) -> bool:
    return any(
        isinstance(sub, ast.Call)
        and isinstance(sub.func, ast.Attribute)
        and sub.func.attr == "with_for_update"
        for sub in ast.walk(node)
    )


def _ordered_segment(source: str, node: ast.AST) -> bool:
    """该源文段是否给出了全序：SQL 侧 `order_by(` 或内存侧 `sorted(`。"""
    segment = ast.get_source_segment(source, node) or ""
    return "order_by(" in segment or "sorted(" in segment


def _iterable_has_total_order(source: str, loop: ast.For | ast.AsyncFor, func: ast.AST) -> bool:
    it = loop.iter
    if isinstance(it, ast.Call) and getattr(it.func, "id", None) == "enumerate" and it.args:
        it = it.args[0]
    if isinstance(it, ast.Call) and getattr(it.func, "id", None) == "sorted":
        return True
    if _ordered_segment(source, it):
        return True
    if isinstance(it, ast.Name):
        for sub in ast.walk(func):
            if isinstance(sub, ast.Assign):
                names = [t for t in sub.targets if isinstance(t, ast.Name)]
            elif isinstance(sub, ast.AnnAssign) and isinstance(sub.target, ast.Name):
                names = [sub.target]
            else:
                continue
            if any(t.id == it.id for t in names) and _ordered_segment(source, sub):
                return True
    return False


def lock_loop_violations(source: str, filename: str = "<source>") -> list[str]:
    """循环**自身**体内取行锁、而迭代集合没有全序来源的地方（嵌套循环各判各的）。"""
    tree = ast.parse(source, filename=filename)
    out: list[str] = []
    funcs = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    for func in funcs:
        for loop in (n for n in ast.walk(func) if isinstance(n, (ast.For, ast.AsyncFor))):
            direct = [s for s in loop.body if not isinstance(s, (ast.For, ast.AsyncFor))]
            if not any(_has_lock(stmt) for stmt in direct):
                continue
            if _iterable_has_total_order(source, loop, func):
                continue
            expr = ast.get_source_segment(source, loop.iter) or "?"
            out.append(
                f"{filename}:{loop.lineno} 循环体内取行锁，但集合 `{expr}` 无全序来源"
                "（sorted() 或带 order_by() 的查询）——锁序家族（#2635/#2787/#2796/#2871/#2901）"
            )
    return out


def _scan_repo() -> list[str]:
    found: list[str] = []
    for path in sorted(BACKEND.rglob("*.py")):
        if "tests" in path.parts or path.name.startswith("test_"):
            continue
        rel = path.relative_to(REPO_ROOT).as_posix()
        found.extend(lock_loop_violations(path.read_text(encoding="utf-8"), rel))
    return found


# ── 判据自身（红绿双向）──────────────────────────────────────────────────────


def test_detector_flags_unordered_lock_loop():
    bad = """
async def f(db):
    rows = (await db.execute(select(Job).where(Job.status == "RUNNING"))).scalars().all()
    for row in rows:
        await db.execute(select(Job).where(Job.id == row.id).with_for_update())
"""
    violations = lock_loop_violations(bad)
    assert len(violations) == 1, violations
    assert "无全序来源" in violations[0]


def test_detector_accepts_the_three_ordered_shapes():
    good = """
async def f(db):
    a = sorted(rows, key=lambda r: r.id)
    for row in a:
        await db.execute(select(Job).where(Job.id == row.id).with_for_update())
    b = (await db.execute(select(Job).order_by(Job.id))).scalars().all()
    for row in b:
        await db.execute(select(Job).where(Job.id == row.id).with_for_update())
    for row in enumerate(sorted(rows, key=lambda r: r.id)):
        await db.execute(select(Job).where(Job.id == row[1].id).with_for_update())
"""
    assert lock_loop_violations(good) == []


def test_detector_ignores_loops_without_locks():
    """不取锁的循环不受本判据约束（否则会把无关集合判红）。"""
    src = """
def f(rows):
    for row in rows:
        print(row)
"""
    assert lock_loop_violations(src) == []


# ── 仓库面（未知即红 + 欠账登记不许陈旧）─────────────────────────────────────


def test_backend_has_no_unordered_lock_loops():
    unregistered = []
    for message in _scan_repo():
        rel = message.split(":", 1)[0]
        expr = message.split("`", 2)[1] if "`" in message else ""
        if (rel, expr) in _PENDING_TOTAL_ORDER:
            continue
        unregistered.append(message)
    assert not unregistered, (
        "新增了「循环体内取锁、集合无全序」的写法：\n  "
        + "\n  ".join(unregistered)
        + "\n（补 sorted()/order_by()，或按既有形态登记到 _PENDING_TOTAL_ORDER 并附在队单号）"
    )


def test_pending_total_order_entries_are_not_stale():
    """欠账修好了就必须删登记——否则登记表会变成永久豁免。"""
    scanned = {
        (m.split(":", 1)[0], (m.split("`", 2)[1] if "`" in m else "")) for m in _scan_repo()
    }
    stale = [key for key in _PENDING_TOTAL_ORDER if key not in scanned]
    assert not stale, f"这些登记已不再是违规（修复已合入？）：{stale}——请删除对应登记"


def test_session_watchdog_locks_host_and_job_rows_in_id_order():
    """`session_watchdog` 的两条行锁查询都必须有全序（#2901）。

    为什么单列一条：通用判据只认「循环体内 `with_for_update`」——job 行属于那一类（上面的
    仓库面判据看住），而 **host 行锁是 `host.status = OFFLINE` 的 ORM UPDATE**，不进那个
    分支，通用判据看不到它。这条直接钉住两条查询的 `order_by`（去掉任一即红）。
    """
    source = (REPO_ROOT / "backend/tasks/session_watchdog.py").read_text(encoding="utf-8")
    targets = {"dead_hosts", "running_jobs"}
    seen: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Assign):
            continue
        target = node.targets[0]
        if not (isinstance(target, ast.Name) and target.id in targets):
            continue
        seen.add(target.id)
        segment = ast.get_source_segment(source, node.value) or ""
        assert ".order_by(" in segment, (
            f"{target.id} 的查询缺 order_by：锁序家族的集合内全序要求（#2901）"
        )
    assert seen == targets, f"没找到这两条查询（改名了？）：{sorted(seen)}"
