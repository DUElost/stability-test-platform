"""#3322：角色规则文件里**每条**告警都必须有场景断言（含负例）。

为什么单独一条：平台文件有 `test_every_alert_rule_has_scenario_case` 兜着，但角色文件
`alerts-host-resources.yml` 不在它扫面内。09-23 落 G1 时我写了 4 条规则、**一条场景都没有**，
CI 全程静默——与 #3106 探针「HOLLOW 挂 7 天无人处置」、#2488「17 条规则一条没上线」同一失效族：
**建了没人判，等于没建**。#3233 补了磁盘三条的场景，内存四条仍是零覆盖，本条把口子焊上。

判据是"每条规则至少被引用一次"（正例或负例都算），并**自证解析非退化**：
取不到规则名或取不到场景断言名时直接红，而不是"空集合相等"式假绿。
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
RULES = REPO_ROOT / "deploy" / "prometheus" / "alerts-host-resources.yml"
SCENARIOS = REPO_ROOT / "deploy" / "prometheus" / "alerts-host-resources.test.yml"


def rule_names() -> list[str]:
    data = yaml.safe_load(RULES.read_text(encoding="utf-8"))
    return [r["alert"] for g in data.get("groups", []) for r in g.get("rules", []) if "alert" in r]


def asserted_names() -> set[str]:
    data = yaml.safe_load(SCENARIOS.read_text(encoding="utf-8"))
    out: set[str] = set()
    for block in data.get("tests", []):
        for case in block.get("alert_rule_test", []):
            if case.get("alertname"):
                out.add(str(case["alertname"]))
    return out


def test_scenario_surface_is_not_degenerate() -> None:
    rules, asserted = rule_names(), asserted_names()
    assert len(rules) >= 4, f"规则名解析退化：只取到 {len(rules)} 条（{rules}）"
    assert len(asserted) >= 4, f"场景断言解析退化：只取到 {len(asserted)} 条（{sorted(asserted)}）"


def test_every_host_rule_has_a_scenario_case() -> None:
    missing = sorted(set(rule_names()) - asserted_names())
    assert not missing, (
        "这些宿主规则没有任何 promtool 场景断言（正例或负例都没有）——它们在 CI 里等于不存在：\n"
        + "\n".join(f"  - {name}" for name in missing)
        + "\n写法参照同文件里 #3223 的四条：瞬时判据（塌陷型）与持续判据的 eval_time 必须分开，"
          "否则会「正确地不响」而什么都没测到。"
    )
