"""#2989：ADR 索引不得把已落地决策标成「未落地」；M7 看板须覆盖主表 M7 行。"""
from __future__ import annotations

import re
from pathlib import Path

README = Path(__file__).resolve().parents[1] / "docs" / "adr" / "README.md"


def _main_table_rows() -> list[str]:
    text = README.read_text(encoding="utf-8")
    # 主清单表：从「当前 ADR 清单」到「里程碑看板」
    m = re.search(
        r"## 当前 ADR 清单\n\n\|.*?\n\|---.*?\n(.*?)(?:\n## |\Z)",
        text,
        re.S,
    )
    assert m, "找不到主 ADR 清单表"
    return [ln for ln in m.group(1).splitlines() if ln.startswith("| [ADR-")]


def test_adr_0045_notes_say_landed_not_pending() -> None:
    """Accepted + 实现已合入时，备注不得再写「未落地」（#2989）。"""
    rows = [r for r in _main_table_rows() if "ADR-0045" in r]
    assert len(rows) == 1, rows
    row = rows[0]
    assert "**Accepted**" in row or "| Accepted |" in row or "| **Accepted** |" in row
    assert "未落地" not in row, row
    assert "已落地" in row or "adf9c24a" in row


def test_m7_board_lists_proposed_and_covers_main_table_m7() -> None:
    """看板须显式点出 Proposed 未决项，且包含主表全部 M7 ADR 编号。"""
    text = README.read_text(encoding="utf-8")
    assert "## 里程碑看板" in text
    assert "Proposed（未决）" in text
    for num in ("0039", "0046", "0047"):
        assert f"ADR-{num}" in text.split("## 里程碑看板", 1)[1]

    main_m7 = set()
    for row in _main_table_rows():
        if "| M7 |" not in row and "| **M7** |" not in row:
            # 目标里程碑列通常是第 5 列
            cols = [c.strip() for c in row.strip("|").split("|")]
            if len(cols) >= 5 and cols[4] == "M7":
                m = re.search(r"ADR-(\d{4})", cols[0])
                if m:
                    main_m7.add(m.group(1))
        else:
            m = re.search(r"ADR-(\d{4})", row)
            if m:
                main_m7.add(m.group(1))

    board = text.split("## 里程碑看板", 1)[1].split("## 维护约定", 1)[0]
    missing = sorted(n for n in main_m7 if f"ADR-{n}" not in board and f"{n}" not in board)
    # 0045 等用缩写「0045」也算覆盖
    missing = [n for n in missing if n not in board]
    assert not missing, f"主表 M7 ADR 未进看板（#2989）：{missing}"
