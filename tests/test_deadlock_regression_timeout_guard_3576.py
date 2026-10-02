"""#3576 形态 B：并发回归测试不得有无界的 barrier/gather 编排。

实测代价（2026-09-16 run `35148237108`）：`backend-test` 在
`test_aggregator_deadlock_regression.py::test_no_key_update_still_serializes_writers`
通过后（21:56:23）**挂死 48 分钟**，直到 job 级 `timeout-minutes`（当时 60）把整个
job 掐掉。挂死窗口内日志仅 2 行、**0 条 DB 语句** ⇒ DB 全程空闲，可排除「真实 DB
死锁」，是 `asyncio.Barrier(2)` 饿死：一侧在到达 barrier 前抛异常，另一侧永久等待。

为什么这条要钉住：真死锁应该让测试**红**并给出锁序信息，而不是静默吃掉一整轮 CI，
还会连带把 backstop 归因拖到超时（#2441 的形态 B）。代价不对称，所以「快速失败」
必须是被守护的性质，而不是恰好没人触发。

本组测试**不跑那个真测试**（它需要 testcontainers 的一次性 PG），而是：
① 结构断言——该文件不得出现无界的 gather；
② 行为验证——用最小复现证明「一侧提前抛异常」在加界后**快速失败**而非挂死。
"""
from __future__ import annotations

import ast
import asyncio
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[1]
_TARGET = _ROOT / "backend" / "tests" / "services" / "test_aggregator_deadlock_regression.py"


def _gather_calls(tree: ast.AST) -> list[ast.Call]:
    return [
        n for n in ast.walk(tree) if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute) and n.func.attr == "gather"
    ]


def test_target_file_exists():
    assert _TARGET.is_file(), f"目标测试文件缺失：{_TARGET}"


def test_every_gather_is_bounded_by_wait_for():
    """每个 `asyncio.gather` 都必须被 `asyncio.wait_for` 包住，否则挂死无兜底。

    结构断言而非运行时断言：真跑一次要起 testcontainers 的 PG，而「有没有上界」
    是可以在不执行的情况下判定的。
    """
    tree = ast.parse(_TARGET.read_text(encoding="utf-8"))
    bare = []
    for call in _gather_calls(tree):
        # 父节点链里出现 wait_for 即视为有界
        bounded = False
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Attribute) or node.func.attr != "wait_for":
                continue
            if any(arg is call for arg in node.args):
                bounded = True
                break
        if not bounded:
            bare.append(call.lineno)
    assert not bare, f"这些 asyncio.gather 没有超时上界（行 {bare}）——会挂到 job 级上限"


def test_bounded_pattern_fails_fast_instead_of_hanging():
    """行为验证：复现「一侧在 barrier 前抛异常」，验证加界后快速失败。

    这是本组测试的核心——它把「会不会挂死」从推测变成实测。用极小 timeout（1s）
    以免拖慢套件；真实测试里用的是 60s。
    """
    async def scenario() -> str:
        barrier = asyncio.Barrier(2)

        async def reaches_barrier() -> str:
            await barrier.wait()
            return "ok"

        async def dies_before_barrier() -> str:
            raise RuntimeError("boom before barrier")

        try:
            await asyncio.wait_for(
                asyncio.gather(reaches_barrier(), dies_before_barrier(),
                               return_exceptions=True),
                timeout=1,
            )
        except (asyncio.TimeoutError, TimeoutError):
            return "fast-fail"
        return "no-timeout"

    # 不加界时该场景确实会挂死（用 wait_for 在外层兜住，避免本测试自己挂住）
    assert asyncio.run(scenario()) == "fast-fail", \
        "一侧提前抛异常时应被 wait_for 捕获并快速失败，而不是永久等待"


def test_repo_has_no_unbounded_asyncio_barrier():
    """全仓 `asyncio.Barrier` 只应出现在已加界的那个文件里。

    `threading.Barrier` 不在此列：它的 `wait()` 同样是阻塞的，但仓库内既有用法都把
    `barrier.wait()` 放在 worker 的**第一条语句**（前面不可能有异常），构造上安全。
    """
    hits = []
    for p in list((_ROOT / "backend").rglob("*.py")) + list((_ROOT / "tests").rglob("*.py")):
        if ".venv" in p.parts:
            continue
        if p.resolve() == Path(__file__).resolve():
            continue  # 本守卫的 docstring 里就写着这个字符串，别把自己算进去
        try:
            src = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if "asyncio.Barrier" in src:
            hits.append(p.relative_to(_ROOT))
    assert hits == [Path("backend/tests/services/test_aggregator_deadlock_regression.py")], (
        f"出现未预期的 asyncio.Barrier 用法：{hits}——同型无界等待需逐个核"
    )
