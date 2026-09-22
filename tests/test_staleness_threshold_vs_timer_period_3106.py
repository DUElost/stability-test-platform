"""#3106：`*_last_run` 类**陈旧阈值必须 ≥ 2× 生产周期**，且周期从生产者推导而非手写。

存在理由：`StabilitySkillUsageUntrusted` 的陈旧分支写着 `> 172800`（48h），而它的
生产者 `stp-skill-usage.timer` 是**周级**（`Mon *-*-* 09:30`）——阈值是 0.29× 周期，
于是该告警每周约 5 天持续 firing。一个恒响的告警会把同一表达式里另外两种真信号
（探针崩了 / 强信号源不在场）淹没：这是告警疲劳，不是漏报，但同样让规则失效。

判据的形状（#3106 建议 + 本仓「未知即红」惯例）：

1. 从**规则文件**里抽出所有 `time() - <metric> > <阈值>` 形态（陈旧分支）；
2. 对每个 metric **推导**它的生产周期，来源按生产者类型分家：
   - systemd timer 驱动的 `stp_<x>_y_last_run` → 单元名 `stp-x-y.timer`（**直接推导，
     不入表**），周期取该 timer 的 `OnUnitActiveSec=` 或 `OnCalendar=`；
   - cron 驱动的（现在只有第五道闸账本）周期取 settings 的默认值，用
     `tools/dev/env_inventory.scan_reads()` 读——同一事实不留两套清单；
3. 断言 `阈值 ≥ 2 × 周期`（连续两次没产出才算真停摆；一次抖动不 page）；
4. **推导不出来即红**：新增一条陈旧规则却没人说清它的生产周期时，本文件报错而不是
   静默跳过——静默跳过正是「判据退化后仍报绿」的入口。

`2×` 不是拍脑袋：本仓同族规则自己就这么写的（`stp-script-guard` 日级配 48h、
第五道闸日常设 sweep 配 48h 并注明「48h = 2× 日周期」）。本文件把这句话变成可执行判据。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.dev.env_inventory import scan_reads  # noqa: E402

RULE_FILES: tuple[Path, ...] = (
    ROOT / "deploy" / "prometheus" / "alerts-stability-platform.yml",
    ROOT / "deploy" / "prometheus" / "site-alerts.yml",
)
TIMER_DIR = ROOT / "deploy" / "control-plane" / "systemd"

#: 陈旧判据的最小倍率：连续两轮无产出才是真停摆。
MIN_RATIO = 2
DAY = 86400

_STALE = re.compile(r"time\(\)\s*-\s*(?P<metric>[A-Za-z_:][\w:]*)\s*>\s*(?P<threshold>\d+)")
_TIMER_METRIC = re.compile(r"^stp_(?P<stem>.+)_last_run$")
_DURATION = re.compile(r"^(?P<value>\d+)(?P<unit>s|sec|min|h|d)?$")

#: **只登记 cron 驱动的生产者**：metric → 承载其周期的环境变量名。
#: timer 驱动的生产者一律不入表（由 `stp_<x>_y_last_run` → `stp-x-y.timer` 推导），
#: 这样新增探针不需要改本文件，而「漏登记」由
#: `test_every_staleness_expression_has_a_derivable_period` 兜住。
_CRON_PRODUCERS: dict[str, str] = {
    "stability_script_presence_sweep_timestamp": "SCRIPT_PRESENCE_SWEEP_CRON",
}


def _iter_exprs(path: Path):
    """产出 (规则名 or '(unnamed)', 表达式文本)。"""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    for group in (data or {}).get("groups", []) or []:
        for rule in group.get("rules", []) or []:
            expr = rule.get("expr")
            if isinstance(expr, str):
                yield rule.get("alert") or rule.get("record") or "(unnamed)", expr


def staleness_pairs() -> list[tuple[str, str, str, int]]:
    """所有 (文件, 规则名, metric, 阈值秒)。"""
    out: list[tuple[str, str, str, int]] = []
    for path in RULE_FILES:
        for name, expr in _iter_exprs(path):
            for match in _STALE.finditer(expr):
                out.append((path.name, name, match.group("metric"), int(match.group("threshold"))))
    return out


def _parse_duration(raw: str) -> int:
    match = _DURATION.match(raw.strip())
    if not match:
        raise AssertionError(f"无法解析时长 {raw!r}（本判据不猜，未知即红）")
    value = int(match.group("value"))
    unit = match.group("unit") or "s"
    factor = {"s": 1, "sec": 1, "min": 60, "h": 3600, "d": DAY}[unit]
    return value * factor


def timer_period_seconds(path: Path) -> int:
    """从 timer 单元推导周期。认 OnUnitActiveSec/OnBootSec/OnActiveSec 与日历两种形态；
    看不懂就红——不返回「假设值」，否则一条读不懂的周期会静默变成绿灯。"""
    text = path.read_text(encoding="utf-8")
    for key in ("OnUnitActiveSec", "OnBootSec", "OnActiveSec"):
        found = re.search(rf"^{key}=(.+)$", text, re.MULTILINE)
        if found:
            return _parse_duration(found.group(1).strip())
    found = re.search(r"^OnCalendar=(.+)$", text, re.MULTILINE)
    if found:
        spec = found.group(1).strip()
        # `Mon *-*-* 09:30:00` 带星期字段 ⇒ 周级；`*-*-* 09:30:00` ⇒ 日级。
        weekday = any(spec.startswith(day) for day in
                      ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"))
        if weekday:
            return 7 * DAY
        if spec.startswith("*-*-*"):
            return DAY
        raise AssertionError(
            f"{path.name} 的 OnCalendar={spec!r} 不在本判据的解析面内（未知即红）："
            "请扩展本判据而不是跳过"
        )
    raise AssertionError(f"{path.name} 既无 OnUnitActiveSec/OnBootSec/OnActiveSec 也无 OnCalendar")


def cron_period_seconds(expr: str) -> int:
    fields = expr.split()
    if len(fields) != 5:
        raise AssertionError(f"cron {expr!r} 不是五段（未知即红）")
    minute, hour, dom, _month, dow = fields
    if dow != "*" and dom == "*":
        return 7 * DAY
    if hour != "*" and dom == "*":
        return DAY
    if minute.startswith("*/") and minute[2:].isdigit():
        return int(minute[2:]) * 60
    if minute.isdigit() and hour == "*" and dom == "*" and dow == "*":
        return 3600
    raise AssertionError(f"cron {expr!r} 的周期不在本判据的解析面内（未知即红）")


def period_seconds(metric: str) -> int:
    """推导 metric 的生产周期（秒）。推导不出来即断言失败。"""
    if cron_var := _CRON_PRODUCERS.get(metric):
        default = scan_reads(ROOT).get(cron_var, {}).get("default")
        assert isinstance(default, str) and default != "-", (
            f"{metric} 的周期来源 {cron_var} 取不到默认值（扫描器结果变了？未知即红）"
        )
        return cron_period_seconds(default)
    if match := _TIMER_METRIC.match(metric):
        unit = "stp-" + match.group("stem").replace("_", "-") + ".timer"
        path = TIMER_DIR / unit
        assert path.exists(), (
            f"{metric} 推导出的生产者 {unit} 不存在（{TIMER_DIR}）——"
            "要么补单元，要么在 _CRON_PRODUCERS 里登记它的真实周期来源"
        )
        return timer_period_seconds(path)
    raise AssertionError(
        f"{metric} 的周期无法推导：既不是 stp_*_last_run（timer 命名约定），"
        "也不在 _CRON_PRODUCERS 里。新增陈旧判据时必须说清它的生产周期"
    )


def _ratio_ok(threshold: int, period: int) -> bool:
    return threshold >= MIN_RATIO * period


# ── 判据本体 ────────────────────────────────────────────────────────────────

def test_staleness_threshold_is_at_least_two_periods() -> None:
    pairs = staleness_pairs()
    assert pairs, "一条陈旧判据都没扫到——正则或规则文件路径变了，判据已退化"
    offenders: list[str] = []
    for file_name, rule, metric, threshold in pairs:
        period = period_seconds(metric)
        if not _ratio_ok(threshold, period):
            offenders.append(
                f"{file_name} {rule}: {metric} 阈值 {threshold}s "
                f"< {MIN_RATIO}× 周期 {period}s（{threshold / period:.2f}×）"
                "——恒响形态：真停摆会被淹没"
            )
    assert not offenders, "陈旧阈值小于 2× 生产周期：\n  " + "\n  ".join(offenders)


def test_every_staleness_expression_has_a_derivable_period() -> None:
    """推导不出周期即红（防「静默跳过」把判据退化成恒绿）。"""
    for _file_name, _rule, metric, _threshold in staleness_pairs():
        period_seconds(metric)  # 内部断言即判据


def test_predicate_and_parsers_have_teeth() -> None:
    """变异自证：本判据必须能判红 #3106 的那个形状（172800 配周级），
    同时放行三条现行正确配置——否则「出口形同虚设」或「恒红被忽略」。"""
    week = 7 * DAY
    assert not _ratio_ok(172800, week), "0.29× 的 #3106 形状被放行了"
    assert _ratio_ok(1209600, week), "2× 周级被误杀"
    assert _ratio_ok(172800, DAY), "现行 stp-script-guard（日级配 48h）被误杀"
    assert _ratio_ok(3600, 300), "现行 stp-pg-guard（5min 配 1h）被误杀"
    assert not _ratio_ok(86400, DAY), "1× 周期被放行了（一次抖动即 page）"

    # 解析器对着真实单元取值，而不是对着本文件的假设。
    assert timer_period_seconds(TIMER_DIR / "stp-skill-usage.timer") == week
    assert timer_period_seconds(TIMER_DIR / "stp-script-guard.timer") == DAY
    assert timer_period_seconds(TIMER_DIR / "stp-pg-guard.timer") == 300
    assert cron_period_seconds("30 9 * * *") == DAY
    assert cron_period_seconds("*/15 * * * *") == 900
    assert cron_period_seconds("0 * * * *") == 3600
    assert cron_period_seconds("30 9 * * 1") == week
