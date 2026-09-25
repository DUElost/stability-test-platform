"""#3233：host-storage 磁盘水位规则的 promtool 场景层（与平台规则同口径：缺 promtool 时按 job 档位 fail/skip）。

主机资源规则文件此前没有场景层；本文件只覆盖 #3233 新增的 host-storage 组，内存组的判据由其文件头的
回测表背书。逐条判别力沿用 #2236：只抬这一条的阈值，场景层必须变红并点名该告警。
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from tests.test_prometheus_alerts_contract import _promtool_path, _raise_rule_threshold

PROM_DIR = Path(__file__).resolve().parents[1] / "deploy" / "prometheus"
RULES = PROM_DIR / "alerts-host-resources.yml"
SCENARIOS = PROM_DIR / "alerts-host-resources.test.yml"


def _storage_alerts() -> list[str]:
    data = yaml.safe_load(RULES.read_text(encoding="utf-8"))
    return [r["alert"] for g in data["groups"] if g["name"] == "host-storage" for r in g["rules"]]


def _promtool_test(cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [_promtool_path(), "test", "rules", SCENARIOS.name],
        cwd=cwd, capture_output=True, text=True, check=False,
    )


def test_host_storage_group_is_not_hollow():
    assert set(_storage_alerts()) == {
        "StabilityHostFilesystemFillingUp",
        "StabilityHostFilesystemLowSpace",
        "StabilityHostFilesystemCritical",
    }


def test_every_storage_rule_has_a_firing_scenario():
    """只写 ``exp_alerts: []`` 的规则等于零覆盖：每条都必须至少有一个「应当响」的断言。"""
    cases = [
        c for t in yaml.safe_load(SCENARIOS.read_text(encoding="utf-8"))["tests"]
        for c in t.get("alert_rule_test") or []
    ]
    firing = {c["alertname"] for c in cases if c.get("exp_alerts")}
    assert set(_storage_alerts()) <= firing, f"缺正向场景：{set(_storage_alerts()) - firing}"


def test_storage_scenarios_pass_with_promtool():
    res = _promtool_test(PROM_DIR)
    assert res.returncode == 0, res.stdout + res.stderr


@pytest.mark.parametrize("alert", _storage_alerts())
def test_per_rule_threshold_drift_turns_scenarios_red(tmp_path, alert):
    mutated, hits = _raise_rule_threshold(RULES.read_text(encoding="utf-8"), alert)
    assert hits, f"{alert}: 表达式里没有可变异的比较阈值"
    (tmp_path / RULES.name).write_text(mutated, encoding="utf-8")
    (tmp_path / SCENARIOS.name).write_text(SCENARIOS.read_text(encoding="utf-8"), encoding="utf-8")
    res = _promtool_test(tmp_path)
    out = res.stdout + res.stderr
    assert res.returncode != 0, f"{alert}: 阈值变异后场景层仍通过 → 该条场景没有判别力\n{out}"
    assert alert in out, f"{alert}: 场景层变红但未点名该告警\n{out}"
