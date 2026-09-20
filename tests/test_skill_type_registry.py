"""#2864：skill `type` 分型是判据源——取值合法性与 `event` 登记必须可机械核验。

背景：#2785 让 HOLLOW 判洞窗口按 `SKILL.md` frontmatter 的 `type` 分型
（`persistent` 14 天 / `event` 60 天），但 `type` 不在任何校验面内——改一行
frontmatter 即把窗口放大 4.3×，且无 allowlist、无登记、无审计痕迹。
本文件把「判据源不由被检方单独掌握」钉成两条：

1. **单一事实源**：门禁的合法取值集合必须与判洞窗口表（`HOLLOW_DAYS`）一致——
   任一侧新增/删改分型，另一侧不改就红；
2. **真实库存量通过**：仓库当前每个 skill 的 `type` 都合法，且 `event` 型都在
   门禁的登记表里（含批准依据与复查期）——用**冻结日期**断言，避免测试随
   复查期到期自然变红（到期由门禁 `--check` 负责报红，那是要人重新裁决的信号）。
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILLS_DIR = REPO_ROOT / ".claude" / "skills"


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_gov = _load("check_governance_surface", "tools/dev/check_governance_surface.py")
_report = _load("skill_usage_report", "tools/dev/skill_usage_report.py")


def _inventory_types() -> dict[str, str]:
    """仓库实际库存：slug → 声明分型（缺省 persistent，与判洞逻辑同义）。"""
    out: dict[str, str] = {}
    for d in sorted(p for p in SKILLS_DIR.iterdir() if p.is_dir()):
        sk = d / "SKILL.md"
        if not sk.is_file():
            continue
        text = sk.read_text(encoding="utf-8")
        out[d.name] = (
            _gov._skill_frontmatter_fields(text).get("type", "") or "persistent"
        )
    return out


def test_allowed_types_match_hollow_windows():
    """合法取值集合 ⇔ 判洞窗口表：任一侧新增分型而另一侧缺失即红（单一事实源）。"""
    assert _gov._SKILL_TYPES == set(_report.HOLLOW_DAYS), (
        f"门禁合法取值 {sorted(_gov._SKILL_TYPES)} 与判洞窗口表 "
        f"{sorted(_report.HOLLOW_DAYS)} 不一致——新增分型必须同时改这两处"
    )


def test_registry_reasons_and_review_dates_are_present():
    """登记条目必须自带批准依据与复查期（`YYYY-MM-DD`）——空壳登记等于没登记。"""
    assert _gov._SKILL_EVENT_TYPE_REGISTRY, "event 登记表为空（两个存量 event skill 应已登记）"
    for slug, (reason, review_by) in _gov._SKILL_EVENT_TYPE_REGISTRY.items():
        assert reason.strip(), f"{slug}: 登记缺少批准依据"
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", review_by), (
            f"{slug}: 复查期格式须为 YYYY-MM-DD，实际 {review_by!r}"
        )


def test_real_inventory_passes_skill_type_check():
    """真实库存量在（冻结）在期日期下必须全绿；同时保证夹具**覆盖到** event 项。"""
    seen = _inventory_types()
    assert "event" in seen.values(), "库存里没有 event 型 skill——夹具失去覆盖意义"
    issues = _gov.check_skill_type_registry(seen, today="2026-09-20")
    assert issues == [], "真实库存量的 type 校验未通过：\n  " + "\n  ".join(issues)


def test_unregistered_event_and_unknown_type_are_rejected():
    """反例（与门禁自检同判据，独立于其进程）：未登记 event / 未知取值必须红。"""
    base = {slug: "event" for slug in _gov._SKILL_EVENT_TYPE_REGISTRY}
    assert _gov.check_skill_type_registry(
        {**base, "rogue": "event"}, today="2026-09-20"
    ), "未登记的 event 分型未被判红"
    assert _gov.check_skill_type_registry(
        {**base, "rogue": "monthly"}, today="2026-09-20"
    ), "未知 type 取值未被判红"
