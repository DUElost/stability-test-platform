"""零引用脚本版本的退役判据——**唯一事实源**（#735）。

背景：#735 的退役闭环此前只有两处口径——诊断工具 `check_unreferenced_script_versions`
报「active ∧ `plan_step` 零引用」，以及人工批量执行时临时加的收敛判据（见
`docs/notes/process/2026-09-16-script-retire-channel-735.md`）。两份口径不合一，
下一批就会漂。本模块把判据固化为纯函数：诊断工具的 `--guard` 与退役执行器
`tools/dev/retire_script_versions.py` 都必须经它，行为由
`backend/tests/test_script_retirement_guard.py` 锁定。

判据（按优先级，先命中先决定）：

1. `not is_active` → 已退役，不重复处理；
2. `refs > 0` → 仍被当前 Plan 配置引用，**不可退役**（与 API 409
   `SCRIPT_STILL_REFERENCED` 同构）；
3. 是该脚本族**最新的 active 版本** → 承接面豁免。冷却期要保护的是「刚发布、
   还没人把 Plan 迁过去」的最新版，不是已被同族更新版本取代的中间版。
   **退出判据**：同族一旦出现更新的 active 版本，本豁免自动让位，旧最新版随即进入
   4/5 判定——豁免不是永久身份；
4. 留存窗口内有执行事实：距今 `>= STALE_COOLDOWN_DAYS` 天 → 到期可退役，
   未到期 → 保留（追溯期）；
5. 其余（零引用 + 留存窗口内无执行事实）→ 退役候选。

刻意**不看** `script.created_at`：该列是**入库注册时间**，脚本扫描会把早已发布的历史
版本目录补登记（2026-09-13/14 一轮就把 `monkey_launch@5.0.1`、`gpu_check@1.0.7` 等补了
进来），按注册时间冷却会把最该退役的行留在场上。它只作展示与审计用。

已知边界：`last_used_on` 只覆盖 `plan_run` 保留期（`PLAN_RUN_RETENTION_DAYS`），因此
`None` 的含义是「留存窗口内零执行」，**不等于**「从未执行」。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable

#: 零引用版本在「无当前配置引用」之后，还需静默多少天无执行事实才可退役。
#: 与 `docs/development/script-versioning.md` §退役与删除 的表述由
#: `test_cooldown_constant_is_documented` 互锁——改数值必须同时改文档。
STALE_COOLDOWN_DAYS = 60

RETIRE = "RETIRE"
KEEP_INACTIVE = "KEEP_INACTIVE"
KEEP_REFERENCED = "KEEP_REFERENCED"
KEEP_LATEST_ACTIVE = "KEEP_LATEST_ACTIVE"
KEEP_RECENT_USE = "KEEP_RECENT_USE"


class InconsistentRetirementPlan(Exception):
    """判据被改坏、导致某脚本族将失去全部 active 版本时抛出（fail-loud）。"""


@dataclass(frozen=True)
class ScriptVersionFact:
    """一个脚本版本在判据所需维度上的投影（不依赖 ORM，便于纯函数测试）。"""

    name: str
    version: str
    is_active: bool
    refs: int
    last_used_on: date | None = None
    registered_on: date | None = None


@dataclass(frozen=True)
class Verdict:
    decision: str
    reason: str

    @property
    def retire(self) -> bool:
        return self.decision == RETIRE


def version_key(version: str) -> tuple:
    """把 `"1.3.14"` / `"v1.3.14"` 归一成可比较的排序键。

    数字段按数值比较（否则 `1.10.0 < 1.2.0` 会判错）；非数字段退化为字符串比较且
    恒排在数字段之后——版本目录名允许出现非数字后缀，判据不能因此抛异常。
    """
    parts = re.split(r"[.\-+_]", str(version).strip().lstrip("vV"))
    return tuple((0, int(p), "") if p.isdigit() else (1, 0, p) for p in parts if p != "")


def latest_active_versions(facts: Iterable[ScriptVersionFact]) -> dict[str, str]:
    """每个脚本族「最新且仍 active」的版本号——即第 3 条豁免的对象。

    只看 active 行：若最新版已被停用（草稿目录被删、或 #2048 那类回归版），承接面是
    次新的 active 版本，豁免必须落在它身上，否则该族唯一可用版本会被判为可退役。
    """
    out: dict[str, str] = {}
    for fact in facts:
        if not fact.is_active:
            continue
        current = out.get(fact.name)
        if current is None or version_key(fact.version) > version_key(current):
            out[fact.name] = fact.version
    return out


def classify(
    facts: Iterable[ScriptVersionFact],
    *,
    today: date | None = None,
    cooldown_days: int = STALE_COOLDOWN_DAYS,
) -> dict[tuple[str, str], Verdict]:
    """按模块文档的 1→5 优先级逐条判定，键为 `(name, version)`。"""
    facts = list(facts)
    today = today or date.today()
    latest = latest_active_versions(facts)
    verdicts: dict[tuple[str, str], Verdict] = {}

    for fact in facts:
        key = (fact.name, fact.version)
        if not fact.is_active:
            verdicts[key] = Verdict(KEEP_INACTIVE, "已退役（is_active=false）")
            continue
        if fact.refs > 0:
            verdicts[key] = Verdict(KEEP_REFERENCED, f"仍被 {fact.refs} 个 plan_step 引用")
            continue
        if latest.get(fact.name) == fact.version:
            verdicts[key] = Verdict(
                KEEP_LATEST_ACTIVE,
                "同族最新 active 版本（承接面）；出现更新的 active 版本后本豁免自动让位",
            )
            continue
        if fact.last_used_on is not None:
            idle_days = (today - fact.last_used_on).days
            if idle_days < cooldown_days:
                verdicts[key] = Verdict(
                    KEEP_RECENT_USE,
                    f"末次执行 {fact.last_used_on.isoformat()}（距今 {idle_days} 天 < "
                    f"{cooldown_days} 天冷却期）",
                )
                continue
            verdicts[key] = Verdict(
                RETIRE, f"零引用且末次执行距今 {idle_days} 天 ≥ {cooldown_days} 天"
            )
            continue
        verdicts[key] = Verdict(
            RETIRE, f"零引用且留存窗口内（{cooldown_days} 天口径）无执行事实"
        )

    assert_no_family_emptied(facts, verdicts)
    return verdicts


def retirement_candidates(
    facts: Iterable[ScriptVersionFact],
    *,
    today: date | None = None,
    cooldown_days: int = STALE_COOLDOWN_DAYS,
) -> list[ScriptVersionFact]:
    """可退役集合，按 `(name, version_key)` 稳定排序——可直接喂给执行器。"""
    facts = list(facts)
    verdicts = classify(facts, today=today, cooldown_days=cooldown_days)
    return [
        fact
        for fact in sorted(facts, key=lambda f: (f.name, version_key(f.version)))
        if verdicts[(fact.name, fact.version)].retire
    ]


def days_until_cooldown_expiry(
    fact: ScriptVersionFact,
    *,
    today: date | None = None,
    cooldown_days: int = STALE_COOLDOWN_DAYS,
) -> date | None:
    """「保留中」版本的到期复评日——给巡检留可执行日期，不留口头承诺。

    返回 `None` 表示无到期概念（已退役、仍被引用、或留存窗口内无执行事实）。
    """
    if not fact.is_active or fact.refs > 0 or fact.last_used_on is None:
        return None
    return fact.last_used_on + timedelta(days=cooldown_days)


def assert_no_family_emptied(
    facts: Iterable[ScriptVersionFact],
    verdicts: dict[tuple[str, str], Verdict],
) -> None:
    """判据后置不变量：任何脚本族都不得因本批退役失去全部 active 版本。

    现行判据下由第 3 条（同族最新 active 豁免）保证；本检查是**防未来改坏**的
    fail-loud 闸——豁免规则一旦被删或写反，这里当场抛，而不是让运维把某个脚本打成
    「无版本可选」。
    """
    remaining: dict[str, int] = {}
    for fact in facts:
        if not fact.is_active or verdicts[(fact.name, fact.version)].retire:
            continue
        remaining[fact.name] = remaining.get(fact.name, 0) + 1

    emptied = sorted({fact.name for fact in facts if fact.is_active} - set(remaining))
    if emptied:
        raise InconsistentRetirementPlan(
            "退役判据自相矛盾：以下脚本族将失去全部 active 版本 "
            f"（{', '.join(emptied)}）——检查是否漏了「同族最新 active 豁免」"
        )
