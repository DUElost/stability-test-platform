"""M7 看板行解析的失明收口回归测试（#3205）。

`check_governance_surface.py` 的 `_ADR_M7_ENTRY` 原只认完整形态
`ADR-0036（**Accepted** v1.0…`，而 M7 看板行实存的是紧凑形态
（`**0051**（v1.8；…` / `**ADR-0047**（v1.3：…`）——整行 ``finditer`` 0 命中，
``m7_entries`` 恒空，看板状态/版本比对对**全部** ADR 静默跳过（S12 的
「不在场不约束」纪律把失明变成了假绿）。收口 = 正则双形态 + 看板行 0 条目
即红的失明下限（门禁本体每次 `check:quick` 实跑，本测试钉解析面与真值一致性）。
"""

from __future__ import annotations

import re
from pathlib import Path

from tools.dev.check_governance_surface import _ADR_M7_ENTRY, parse_adr_status_line

REPO = Path(__file__).resolve().parents[1]

#: 修复前的旧正则——保留在此作判别力对照：对当前看板行必须仍是 0 命中，
#: 否则说明看板已回到完整形态、本收口的前提消失（应复核是否可简化）。
_OLD_REGEX = re.compile(
    r"ADR-(\d{4})（\*\*(Proposed|Accepted|Superseded|Deprecated)\*\*\s*v(\d+\.\d+)"
)


def _m7_line() -> str:
    readme = (REPO / "docs" / "adr" / "README.md").read_text(encoding="utf-8")
    line = next((l for l in readme.splitlines() if l.startswith("| M7")), "")
    assert line, "adr/README.md 缺 M7 看板行（S12 索引面被删？）"
    return line


def _adr_header_version(num: str) -> str:
    matches = list((REPO / "docs" / "adr").glob(f"ADR-{num}-*.md"))
    assert len(matches) == 1, f"ADR-{num} 文件定位失败：{[p.name for p in matches]}"
    text = matches[0].read_text(encoding="utf-8")
    status_line = next(
        (l for l in text.splitlines() if l.strip().startswith("- 状态")), ""
    )
    status, version = parse_adr_status_line(status_line)
    assert version, f"ADR-{num} 头部无规范位版本，无法比对"
    return version


def test_regex_matches_both_board_forms():
    # 紧凑形态（无 ADR- 前缀、无状态词）——修复前 0 命中的形态
    m = _ADR_M7_ENTRY.search("**0051**（v1.8；Phase 0/2a/2b/3 落地…）")
    assert m and m.group(1) == "0051" and m.group(2) is None and m.group(3) == "1.8"
    # 带前缀紧凑形态
    m = _ADR_M7_ENTRY.search("**ADR-0047**（v1.3：容量不变量进启动门禁…）")
    assert m and m.group(1) == "0047" and m.group(2) is None and m.group(3) == "1.3"
    # 完整形态（旧行为不回归）
    m = _ADR_M7_ENTRY.search("ADR-0036（**Accepted** v1.0：定稿）")
    assert m and m.group(1) == "0036" and m.group(2) == "Accepted" and m.group(3) == "1.0"


def test_m7_board_line_parses_nonempty():
    """失明下限的真值面：真看板行必须解析出条目（0 条目 = 门禁对全体 ADR 失明）。"""
    entries = {
        m.group(1): (m.group(2), m.group(3))
        for m in _ADR_M7_ENTRY.finditer(_m7_line())
    }
    assert len(entries) >= 5, f"M7 看板行只解析出 {len(entries)} 条，解析面退化"


def test_m7_board_versions_match_adr_headers():
    """看板上每个带版本号的条目与其 ADR 头部规范位版本一致（#3205 的原始病灶：
    看板 0051 钉 v1.5 而头部已 v1.8，因失明而无人报）。"""
    entries = {
        m.group(1): m.group(3) for m in _ADR_M7_ENTRY.finditer(_m7_line())
    }
    stale = []
    for num, board_version in sorted(entries.items()):
        header = _adr_header_version(num)
        if board_version != header:
            stale.append(f"ADR-{num}: 看板 v{board_version} ≠ 头部 v{header}")
    assert not stale, "M7 看板版本漂移：\n" + "\n".join(stale)


def test_old_regex_still_blind_on_current_board():
    """判别力对照：旧正则对当前看板行 0 命中——本收口修的正是它。"""
    hits = _OLD_REGEX.findall(_m7_line())
    assert hits == [], (
        "旧正则在当前看板行上出现命中——看板形态或已全部回到完整形态，"
        "复核双形态正则与失明下限是否仍承重"
    )
