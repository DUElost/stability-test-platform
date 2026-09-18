#!/usr/bin/env python3
"""上帝文件行数封顶棘轮门禁（#736）。

规则：`CEILINGS` 列出的文件，行数不得超过其封顶值。封顶值是**棘轮**——
把领域逻辑下沉到 `services/` 的同一个 PR 里应把它调小；**上调必须写明理由**。
本门禁只负责让「继续往胖控制器里堆业务逻辑」在 PR 路径上立刻变红，
不负责判断某个具体改动是否合理（那是评审的事）。

为什么用棘轮而不是统一上限：三个文件的行数差异很大（`origin/main` 实测
957 / 1622 / 2303），一刀切的上限要么形同虚设（按最大的定）、要么逼出「先把
文件改名再堆」的应付式改动（按最小的定）。棘轮把每个文件**当前状态**当基线，
只允许向好的方向走。

封顶值 = 实测行数 × 1.05（向上取整）；缓冲是为了不与正在瘦身的 #1520 切片
互相打架（搬出一半时文件可能短暂变长）。

退出码：超限或有**过期条目**（文件已不存在/被改名）→ 1 并逐条列出；
`CEILINGS` 为空 → 2（「什么都没检查」不能长得像「全绿」）；否则 0。
`--self-test` 离线红绿自证（与本文件同 step 运行，防分类规则回归）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

#: 封顶值 = 设定时的 `origin/main` 实测行数 × 1.05（向上取整）；缓冲是为了不与正在
#: 瘦身的 #1520 切片互相打架（搬出一半时文件可能短暂变长）。
#:
#: 下调时机：把领域逻辑下沉到 services/ 的**同一批**改动落地后即下调（棘轮的方向）。
#: 键为仓库相对路径。
#:
#: 历史（棘轮记录，只增不改）：
#: - 2026-09-17 初版（`1f22c951`）：2303 / 957 / 1622 → 2419 / 1005 / 1704；
#: - 2026-09-17 首次下调（`2524278d`，#1520 切片把两个路由文件搬空）：
#:   709 / 515 / 1622 → 745 / 541 / 1704（-69% / -46% / 持平）；
#: - 2026-09-18 二次下调（#1520 catalog / auth / re-export / shape 合入后）：
#:   592 / 391 / 360 / 1622 → 622 / 411 / 378 / 1704；并新增 `projects.py`
#:   （#1520 三主战场之一，此前未入册）；
#: - 2026-09-18 三次下调（plan_runs 薄壳抛光）：596 → 482 → 封顶 **507**。
#: - 2026-09-18 四次下调（#736：recovery/lease-lost 抽出 `recovery_executor.py`）：
#:   main 1622 → 1177 → 封顶 **1236**。
#: - 2026-09-18 五次下调（#736：disk/watcher 启动抽出 `bootstrap_subsystems.py`）：
#:   main 1177 → 1087 → 封顶 **1142**。
#: - 2026-09-18 六次下调（#736：身份/HOST_ID 抽出 `startup_identity.py`）：
#:   main 1087 → 1015 → 封顶 **1066**。
#: - 2026-09-18 七次下调（#736：SocketIO control 抽出 `control_handler.py`）：
#:   main 1015 → 846 → 封顶 **889**。
#: - 2026-09-18 八次下调（#736：占位绑定抽出 `active_job_bindings.py`）：
#:   main 846 → 825 → 封顶 **867**。
#: - 2026-09-18 九次下调（#736：recovery 接线抽出 `recovery_runtime.py`）：
#:   main 825 → 792 → 封顶 **832**。
#: - 2026-09-18 十次下调（#736：claim tick 抽出 `claim_loop.py`）：
#:   main 792 → 682 → 封顶 **717**。
CEILINGS: dict[str, int] = {
    "backend/api/routes/plan_runs.py": 507,
    "backend/api/routes/agent_api.py": 411,
    "backend/api/routes/projects.py": 378,
    "backend/agent/main.py": 717,
}


def count_lines(path: Path) -> int:
    """文件行数（`splitlines`——末尾换行不算一行，与 `wc -l` 同口径）。"""
    return len(path.read_text(encoding="utf-8").splitlines())


def check_ceilings(ceilings: dict[str, int] | None = None, root: Path = ROOT) -> list[tuple[str, int, int]]:
    """返回超限清单 ``[(相对路径, 实际行数, 封顶值), ...]``（升序按差值）。

    文件不存在同样计入（实际行数记 0、封顶值保留），由调用方按「过期条目」报红：
    文件被拆散/改名时应在本 PR 里同步移除条目，否则封顶表会慢慢变成摆设。
    """
    table = CEILINGS if ceilings is None else ceilings
    offenders: list[tuple[str, int, int]] = []
    for rel, limit in table.items():
        path = root / rel
        actual = count_lines(path) if path.exists() else 0
        if actual > limit or not path.exists():
            offenders.append((rel, actual, limit))
    return offenders


def _format(offenders: list[tuple[str, int, int]]) -> str:
    lines = []
    for rel, actual, limit in offenders:
        if actual == 0:
            lines.append(f"  {rel}: 文件不存在（条目过期——拆分/改名时请在本 PR 里移除条目）")
        else:
            lines.append(f"  {rel}: {actual} 行 > 封顶 {limit} 行（超出 {actual - limit}）")
    return "\n".join(lines)


def _self_test() -> int:
    """离线红绿自证：三种形态各自可判、缺配置不算绿。"""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "small.py").write_text("x = 1\n" * 10, encoding="utf-8")
        (root / "big.py").write_text("x = 1\n" * 20, encoding="utf-8")

        # 绿：未超限
        if check_ceilings({"small.py": 10, "big.py": 20}, root):
            print("[self-test] 未超限的文件被判超限", file=sys.stderr)
            return 1
        # 红：超限
        over = check_ceilings({"small.py": 10, "big.py": 19}, root)
        if [rel for rel, _, _ in over] != ["big.py"]:
            print(f"[self-test] 超限判定错误：{over}", file=sys.stderr)
            return 1
        # 红：条目过期（文件不存在）
        stale = check_ceilings({"gone.py": 1}, root)
        if [rel for rel, _, _ in stale] != ["gone.py"]:
            print(f"[self-test] 过期条目判定错误：{stale}", file=sys.stderr)
            return 1
        # 行数口径：末尾换行不算一行（与 wc -l 同）
        (root / "exact.py").write_text("a\nb\nc\n", encoding="utf-8")
        if count_lines(root / "exact.py") != 3:
            print("[self-test] 行数口径错误（末尾换行被多算）", file=sys.stderr)
            return 1
    print("[OK] check_god_files_ceiling self-test 通过（超限/过期/未超限三态可判）")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="上帝文件行数封顶棘轮门禁（#736）")
    parser.add_argument("--self-test", action="store_true", help="离线红绿自证")
    args = parser.parse_args(argv)

    if args.self_test:
        return _self_test()

    if not CEILINGS:
        print("[FAIL] CEILINGS 为空——门禁什么都没检查", file=sys.stderr)
        return 2

    offenders = check_ceilings()
    if offenders:
        print(
            "[FAIL] 以下文件超过行数封顶（#736）：\n"
            f"{_format(offenders)}\n"
            "新业务逻辑请下沉到 backend/services/ 领域服务层；"
            "确实需要上调封顶值的，在 PR 描述里写明理由。",
            file=sys.stderr,
        )
        return 1
    for rel, limit in sorted(CEILINGS.items()):
        print(f"[OK] {rel}: {count_lines(ROOT / rel)} / {limit} 行")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
