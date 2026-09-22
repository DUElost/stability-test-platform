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

取锁判据（#2974 扩面）：

- `with_for_update()`（原判据）；
- **同模块 helper 内的锁**——传递解析调用图（防环），因为最典型的漏判正是「循环体里
  只调一个 `_lock_and_recheck(...)`」：#2974 的 `device_lease_reconciler.py` 候选项
  逐个调 `_abort_reaper_recheck_job`，锁在那里面，原判据整段跳过；
- **逐行 `update(Model).where(等值)` CAS**——UPDATE 同样取行锁，而 #2974 的
  `recycler.py` patrol-stall 批正是用它，原判据看不见。

已知射程（**不覆盖**，改判据前先读这段）：

- 跨模块 helper 与对象方法（`self.x()` / `obj.y()`）：只解析**同模块**的模块级函数名，
  不做跨模块调用图（成本与误报未评估，见 #2974 的证据边界）；
- 批量 `update(...).where(Model.id.in_(ids))`：一条语句的锁集合由执行器决定，不是
  「程序按某个顺序逐把取」——本判据管的是后者；
- 无显式锁的隐式取锁（如 `INSERT … ON CONFLICT`、外键检查）。

`_PENDING_TOTAL_ORDER` 登记尚未修、但有明确在队单的行锁循环；修复合入后该登记必须删
（陈旧登记同样红）。`_ADJUDICATED_SAFE` 登记**已裁定无锁序风险**的形态（键含迭代对象
源文段 ⇒ 代码一变登记即失配、判据重新变红，逼一次重裁定），每条必须写清裁定理由。
"""
from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND = REPO_ROOT / "backend"

#: 尚未修、但有明确在队单的行锁循环：`(文件, 迭代对象源文段)` → 说明。
#: 修复合入后必须删登记——`test_pending_total_order_entries_are_not_stale` 会红。
_PENDING_TOTAL_ORDER: dict[tuple[str, str], str] = {}

#: 已裁定**无锁序风险**的取锁循环（键同 `_PENDING_TOTAL_ORDER`）：每条必须写清裁定理由，
#: 理由不成立时删登记，而不是让判据继续绕开它。
_ADJUDICATED_SAFE: dict[tuple[str, str], str] = {
    ("backend/services/admission_pump.py", "claimed"): (
        "单行锁事务，不成环：循环体调用的 `requeue_plan_run` 只锁 1 行"
        "（`admission_pump.py:301-303`）且**当场 commit**（`:329`）——一次迭代最多持 1 把锁，"
        "等锁时手里没有锁，与任何取锁顺序都不成环。"
        "（#2974 裁定，2026-09-21；若该 helper 改成锁多行后提交，本登记即失效，必须重裁）"
    ),
}


def _chain_root(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> ast.AST:
    """把 `update(X).where(…)` 这类链式表达式的根取出来——`update(X)` 自身的源文段
    只覆盖到 `update(X)`，`.where(...)`/`.values(...)` 在它的父节点上。"""
    current = node
    while True:
        parent = parents.get(current)
        if isinstance(parent, (ast.Call, ast.Attribute)):
            current = parent
        else:
            return current


def _locksites_in(node: ast.AST, source: str, parents: dict[ast.AST, ast.AST]) -> bool:
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call):
            continue
        func = sub.func
        if isinstance(func, ast.Attribute) and func.attr == "with_for_update":
            return True
        if isinstance(func, ast.Name) and func.id == "update":
            # 批量 `….where(Model.id.in_(ids))` 不算：一条语句的锁集合由执行器决定，
            # 不是「程序按某个顺序逐把取」——本判据管的是后者（#2974 的射程界定）。
            root = _chain_root(sub, parents)
            if ".in_(" not in (ast.get_source_segment(source, root) or ""):
                return True
    return False


def _body_locks(
    node: ast.AST,
    source: str,
    funcs: dict[str, ast.AST],
    parents: dict[ast.AST, ast.AST],
    seen: frozenset[str],
) -> bool:
    """本语句是否取行锁——含它调用的**同模块**模块级函数（传递解析，`seen` 防环）。"""
    if _locksites_in(node, source, parents):
        return True
    for sub in ast.walk(node):
        if not (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name)):
            continue
        name = sub.func.id
        if name in funcs and name not in seen:
            if _body_locks(funcs[name], source, funcs, parents, seen | {name}):
                return True
    return False


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
            # 定序必须发生在**取锁之前**（#2974 残余）：`.sort()` 落在循环之后时，
            # 锁早已按未定序的顺序取过——判据此前只问「函数里有没有排序」，不看位置，
            # 于是把重排写在取锁之后也能过（假阴性口，实测 `rows.sort()` 挪到循环后仍返 []）。
            if getattr(sub, "lineno", 0) >= loop.lineno:
                continue
            if isinstance(sub, ast.Assign):
                names = [t for t in sub.targets if isinstance(t, ast.Name)]
            elif isinstance(sub, ast.AnnAssign) and isinstance(sub.target, ast.Name):
                names = [sub.target]
            else:
                names = []
            if any(t.id == it.id for t in names) and _ordered_segment(source, sub):
                return True
            # 就地排序（`rows.sort(key=…)`）与 `sorted()` 同义，同样算「内存里定序」：
            # #2974 的两处修复都把重排写在消费点（循环之前），判据要认得这个形态。
            if (
                isinstance(sub, ast.Call)
                and isinstance(sub.func, ast.Attribute)
                and sub.func.attr == "sort"
                and isinstance(sub.func.value, ast.Name)
                and sub.func.value.id == it.id
            ):
                return True
    return False


def lock_loop_violations(source: str, filename: str = "<source>") -> list[str]:
    """循环**自身**体内取行锁、而迭代集合没有全序来源的地方（嵌套循环各判各的）。"""
    tree = ast.parse(source, filename=filename)
    funcs = {
        n.name: n
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    out: list[str] = []
    for func in funcs.values():
        for loop in (n for n in ast.walk(func) if isinstance(n, (ast.For, ast.AsyncFor))):
            direct = [s for s in loop.body if not isinstance(s, (ast.For, ast.AsyncFor))]
            if not any(
                _body_locks(stmt, source, funcs, parents, frozenset()) for stmt in direct
            ):
                continue
            if _iterable_has_total_order(source, loop, func):
                continue
            expr = ast.get_source_segment(source, loop.iter) or "?"
            out.append(
                f"{filename}:{loop.lineno} 循环体内取行锁，但集合 `{expr}` 无全序来源"
                "（sorted() 或带 order_by() 的查询）——锁序家族（#2635/#2787/#2796/#2871/#2901/#2974）"
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


def test_detector_requires_ordering_to_precede_the_locks():
    """#2974 残余：定序必须发生在**取锁之前**，否则「函数里出现过排序」就会被误当全序。

    此前判据只问「这个函数里有没有 `sorted()`/`.sort()`/`order_by()`」，不看位置 ——
    把 `rows.sort()` 写在取锁循环**之后**（或写在另一条只对部分行生效的分支里）同样返回
    「有全序」，于是判据对「先取锁、后排」这一真实竞态隐形。绿侧则必须放行 #2974 的两处
    修复形态（排序语句在循环之前）。"""
    sort_after = """
async def f(db):
    rows = (await db.execute(select(Job))).scalars().all()
    for row in rows:
        await db.execute(select(Job).where(Job.id == row.id).with_for_update())
    rows.sort(key=lambda r: r.id)
"""
    violations = lock_loop_violations(sort_after)
    assert len(violations) == 1, f"取锁之后才排序被放行了（假阴性口）：{violations}"

    sort_before = """
async def f(db):
    rows = (await db.execute(select(Job))).scalars().all()
    rows.sort(key=lambda r: r.id)
    for row in rows:
        await db.execute(select(Job).where(Job.id == row.id).with_for_update())
"""
    assert lock_loop_violations(sort_before) == [], "取锁前定序（#2974 两处修复的形态）被误杀"


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


def test_detector_sees_lock_inside_same_module_helper():
    """锁在同模块 helper 里也必须被认出来——#2974 的漏判形态（`device_lease_reconciler`）。"""
    src = """
async def _lock_and_recheck(db, jid):
    return (await db.execute(
        select(Job).where(Job.id == jid).with_for_update()
    )).scalars().first()

async def f(db, rows):
    for row in rows:
        changed = await _lock_and_recheck(db, row.id)
"""
    violations = lock_loop_violations(src)
    assert len(violations) == 1, violations


def test_detector_sees_row_update_cas_but_not_batched_update():
    """逐行 `update(Model).where(等值)` 取锁；批量 `in_(...)` 一条语句锁多行，不属本判据。"""
    row_cas = """
def f(db, jobs):
    for job in jobs:
        db.execute(update(Job).where(Job.id == job.id).values(status="X"))
"""
    assert len(lock_loop_violations(row_cas)) == 1, lock_loop_violations(row_cas)

    batched = """
def f(db, groups):
    for state, ids in groups.items():
        db.execute(update(Job).where(Job.id.in_(ids)).values(state=state))
"""
    assert lock_loop_violations(batched) == [], lock_loop_violations(batched)


def test_detector_follows_helper_recursion_safely():
    """互相调用的 helper 不得把判据拖进死循环（`seen` 防环）。"""
    src = """
def _a(db, rows):
    _b(db, rows)

def _b(db, rows):
    _a(db, rows)

def f(db, rows):
    for row in rows:
        _a(db, [row])
"""
    assert lock_loop_violations(src) == []


# ── 仓库面（未知即红 + 欠账登记不许陈旧）─────────────────────────────────────


def _key_of(message: str) -> tuple[str, str]:
    return (message.split(":", 1)[0], (message.split("`", 2)[1] if "`" in message else ""))


def test_backend_has_no_unordered_lock_loops():
    unregistered = []
    for message in _scan_repo():
        if _key_of(message) in _PENDING_TOTAL_ORDER or _key_of(message) in _ADJUDICATED_SAFE:
            continue
        unregistered.append(message)
    assert not unregistered, (
        "新增了「循环体内取锁、集合无全序」的写法：\n  "
        + "\n  ".join(unregistered)
        + "\n（补 sorted()/order_by()，或按既有形态登记到 _PENDING_TOTAL_ORDER 并附在队单号；"
        "确无风险则登记 _ADJUDICATED_SAFE 并写明裁定理由）"
    )


def test_pending_total_order_entries_are_not_stale():
    """欠账修好了就必须删登记——否则登记表会变成永久豁免。"""
    scanned = {_key_of(m) for m in _scan_repo()}
    stale = [key for key in _PENDING_TOTAL_ORDER if key not in scanned]
    assert not stale, f"这些登记已不再是违规（修复已合入？）：{stale}——请删除对应登记"


def test_adjudicated_safe_entries_are_not_stale():
    """裁定名单同样会腐烂：形态变了（迭代对象改了名/换了集合）就必须重裁，不留永久豁免。"""
    scanned = {_key_of(m) for m in _scan_repo()}
    stale = [key for key in _ADJUDICATED_SAFE if key not in scanned]
    assert not stale, (
        f"这些「已裁定无风险」的登记已不再匹配任何违规形态：{stale}——"
        "代码形状变了，请重新裁定并更新登记（或删除）"
    )


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


# ── #2974 两处实修（通用判据已覆盖其形态，这两条把**理由与对侧**也钉住）────────


def _function_segment(rel: str, func_name: str) -> str:
    source = (REPO_ROOT / rel).read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
            return ast.get_source_segment(source, node) or ""
    raise AssertionError(f"{rel} 里没有 {func_name}（改名了？）")


def test_abort_reaper_locks_candidates_in_job_id_order():
    """#2974 其一：abort reaper 的候选项按 job id 升序取锁，对侧也是 id 升序。

    通用判据只能看到「集合有没有全序来源」（`reap_rows.sort`）；这条额外钉住候选**查询**
    本身也带 `order_by(JobInstance.id)` —— 读侧确定性是 `reap_rows` 保序的前提，
    少了它，重排就只剩下消费点那一行在兜底。
    """
    segment = _function_segment(
        "backend/scheduler/device_lease_reconciler.py", "_reconcile_aborted_running_jobs"
    )
    assert "order_by(JobInstance.id)" in segment, (
        "abort reaper 的候选查询必须 order_by(JobInstance.id)：一个 tick 在提交前持有整批 "
        "job 行锁（SAVEPOINT 不释放行锁），取锁顺序即返回序（#2974）"
    )
    assert "reap_rows.sort(" in segment, (
        "候选列表在取锁前必须显式按 job id 重排——对侧 agent_lease_extend / "
        "agent_coordinator_heartbeat 都按 id 升序取（#2974）"
    )


def test_patrol_stall_locks_batch_in_job_id_order():
    """#2974 其二：patrol-stall 批按 job id 升序取锁，批次**选择**仍按 overdue DESC。"""
    source = (REPO_ROOT / "backend/scheduler/recycler.py").read_text(encoding="utf-8")
    ordered_loops = 0
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, (ast.For, ast.AsyncFor)):
            continue
        it = node.iter
        if not (isinstance(it, ast.Call) and getattr(it.func, "id", None) == "sorted" and it.args):
            continue
        inner = it.args[0]
        if not (isinstance(inner, ast.Call)
                and getattr(inner.func, "id", None) == "_collect_patrol_stall_candidates"):
            continue
        assert any(kw.arg == "key" for kw in it.keywords), (
            "patrol-stall 批次的 sorted() 必须给出 key（按 job id）——循环体逐行 "
            "`update(JobInstance)` 且同事务持有到 commit，取锁顺序即迭代顺序（#2974）"
        )
        ordered_loops += 1
    assert ordered_loops == 1, (
        "必须恰有一处 `for … in sorted(_collect_patrol_stall_candidates(...), key=…)`："
        "循环体逐行取 job 行锁且同事务持有到 commit，而对侧 lease renew 按 id 升序取；"
        "两侧不同源即成环（#2974）"
    )
    assert "overdue_expr.desc()" in source, (
        "批次**选择**口径（overdue DESC）不能因为取锁重排而丢掉：先杀最该杀的、"
        "但按 id 取锁，两件事不要混（#2974）"
    )
