"""#736：上帝文件行数封顶棘轮门禁的语料与自证守卫。

门禁本体 `tools/dev/check_god_files_ceiling.py`：`CEILINGS` 列出的文件行数不得超过
封顶值；越过即红。本文件把三件事钉住：

1. 本仓当前状态确实在封顶值之内（否则门禁一上线就是红的）；
2. 封顶表覆盖那三个「上帝文件」且条目都存在——防止有人靠**悄悄删条目**让门禁变绿
   （#736 要的是棘轮，不是可选项）；
3. 超限、条目过期两态都能判红（`check_ceilings` 的红绿双向，与 `--self-test` 同源）。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location(
    "check_god_files_ceiling",
    REPO_ROOT / "tools" / "dev" / "check_god_files_ceiling.py",
)
_mod = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
sys.modules["check_god_files_ceiling"] = _mod
_spec.loader.exec_module(_mod)

#: 棘轮必须覆盖的三条基线（#736 点名；删条目 = 撤销门禁）。
_GOD_FILES = (
    "backend/api/routes/plan_runs.py",
    "backend/api/routes/agent_api.py",
    "backend/agent/main.py",
)


def test_repo_files_are_under_their_ceilings():
    """当前树必须在封顶值之内——否则门禁一上线就红（基线取值见该脚本抬头）。"""
    offenders = _mod.check_ceilings()
    assert offenders == [], (
        "以下文件超过行数封顶（#736）："
        + "; ".join(f"{rel} {actual}>{limit}" for rel, actual, limit in offenders)
    )


def test_ceiling_table_covers_the_named_god_files():
    """三条基线不得从封顶表里消失——删条目等于把棘轮拆掉。"""
    missing = [rel for rel in _GOD_FILES if rel not in _mod.CEILINGS]
    assert missing == [], f"封顶表缺少条目（不可靠删条目变绿）：{missing}"


def test_ceiling_entries_point_to_existing_files():
    """条目必须指向真实文件：文件被拆分/改名时要在同一 PR 里同步维护封顶表。"""
    missing = [rel for rel in _mod.CEILINGS if not (REPO_ROOT / rel).exists()]
    assert missing == [], f"封顶表条目过期（文件不存在）：{missing}"


def test_over_ceiling_file_is_reported(tmp_path):
    """红态：超限文件被报出（含实际行数与封顶值）；未超限的不报。"""
    (tmp_path / "big.py").write_text("x\n" * 30, encoding="utf-8")
    (tmp_path / "small.py").write_text("x\n" * 10, encoding="utf-8")

    offenders = _mod.check_ceilings({"big.py": 20, "small.py": 10}, tmp_path)

    assert [rel for rel, _, _ in offenders] == ["big.py"]
    assert offenders[0][1] == 30 and offenders[0][2] == 20


def test_missing_file_is_reported_as_stale_entry(tmp_path):
    """红态：条目指向不存在的文件（拆分/改名后忘记维护）。"""
    offenders = _mod.check_ceilings({"gone.py": 100}, tmp_path)
    assert [rel for rel, _, _ in offenders] == ["gone.py"]


def test_line_count_matches_wc_l(tmp_path):
    """行数口径与 `wc -l` 一致：末尾换行不算额外一行。"""
    path = tmp_path / "exact.py"
    path.write_text("a\nb\nc\n", encoding="utf-8")
    assert _mod.count_lines(path) == 3


def test_tool_self_test_passes():
    """门禁自带的离线红绿自证必须通过（CI 与本 gate 同 step 运行它）。"""
    assert _mod.main(["--self-test"]) == 0
