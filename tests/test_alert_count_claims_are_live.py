"""计数型 / 版本型陈述必须与真值同口径（#2663）。

**本质问题**：「17/17 已全覆盖」「最新 active 版本 = v1.3.16」这类**把可派生量抄成文字**的
陈述，没有任何东西约束它等于真值——加一条告警规则、发一个小版本，文字就变成谎话。
#2663 登记的正是两次已发生的漂移（告警 12→21 条而三处仍写 17/17；`flash_firmware`
v1.3.17 已于 09-16 发布而 runbook 仍写 v1.3.16）。

**判据**：对「现状口径」文本（下表 LIVE_SURFACES）扫描四类计数断言与一类版本断言，
逐条与**当场派生的真值**对拍：

| 形态 | 真值来源 |
|---|---|
| `N 条告警` / `N 条告警规则` | `alerts-stability-platform.yml` 里的 distinct `alert` |
| `N 条(场景)断言` / `N 条场景用例` | 场景文件 distinct `alertname` 或 `alert_rule_test` 条目数；**裸 `N 条用例` 不认**（mtbf 文档里它指测试用例，实测误伤过一次） |
| `N/M 全覆盖` | 上述两者必须相等且都等于 N、M |
| `最新 active 版本 … vX.Y.Z` | `backend/agent/scripts/<同句点名的脚本>/` 最大版本目录 |

**为什么没有「带日期即豁免」**：runbook 那两行本来就写着「2026-09-15 复核 = v1.3.16」——
带日期并没有让它变得可用，运维今天照它核对仍会被误导。时点记录的正确落点是
`docs/notes/**`（按日期留档，不属现状口径），所以它整目录不在扫描面里，本判据也
就不需要任何绕过位。同理，扫描面里的历史叙述（`#2488` 那次「17 条告警一条也没上线」）
要么改成不写数字，要么移进 Note——写死数字就必须承担被派生量甩下的后果。

**已知假阴性**（判据边界，非漏洞）：只认上表五类字面形态。`N 条规则`（不带「告警」）、
`7/10 的手工拷贝`、`自测 7 条规则全绿`（治理面 S 系列，与告警无关）都不在形态内，
所以本文件不会误伤，也不会替它们担保。

不参与 `tools/dev/source_anchor.py` 的棘轮：本文件不做「读源码 + 否定断言」，它对文本
的是非判定是 `violations == []`（正向量），锚点漂移时它会以「扫不到文件」变红——
见 `test_scan_surface_is_not_hollow`。
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]

RULES_REL = "deploy/prometheus/alerts-stability-platform.yml"
SCENARIO_REL = "deploy/prometheus/alerts-stability-platform.test.yml"
SCRIPTS_DIR = "backend/agent/scripts"

#: 现状口径文本（时点记录在 docs/notes/** 与 docs/reviews/**，不在此列）。
LIVE_SURFACES = (
    "docs/operations",
    "docs/development",
    "docs/design",
    "docs/prd",
    "deploy",
    "scripts",
    "tests",
    "tools",
)
LIVE_SUFFIXES = {".md", ".py", ".sh", ".yml"}
#: 本文件的夹具里含有意写错的形态，不参与自身扫描。
SELF_EXEMPT = "tests/test_alert_count_claims_are_live.py"
#: 反空转之一：这些文件必须在扫描面里（改名/搬目录/从 LIVE_SURFACES 里摘掉某面，都会静默失效）。
#: 刻意**不设「扫描文件数 ≥ N」这类魔法下限**——N 可以被随手调小（实测把 200 改成 0 仍能全绿），
#: 而「每个面都必须存在且至少贡献 1 个文件 + 这 6 个已知文件必须在场」两条是可判真的结构约束。
MUST_SCAN = (
    "docs/operations/README.md",
    "docs/operations/2026-08-29-post-review-deploy-runbook.md",
    RULES_REL,
    SCENARIO_REL,
    "tests/test_alert_metric_producers.py",
    "tests/test_prometheus_alerts_contract.py",
)

PAT_RULES_CLAIM = re.compile(r"(\d+)\s*条\s*告警(?:规则)?")
PAT_CASE_CLAIM = re.compile(r"(\d+)\s*条\s*(?:场景断言|场景用例|断言)")
PAT_FULL_COVERAGE = re.compile(r"(\d+)\s*/\s*(\d+)[^。\n]{0,14}全覆盖")
PAT_LATEST_VERSION = re.compile(r"最新\s*active\s*版本[^\n|]*?v(\d+\.\d+\.\d+)")
PAT_BACKTICKED_NAME = re.compile(r"`([A-Za-z0-9_]+)`")
PAT_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def counts_from_text(rules_text: str, scenario_text: str) -> tuple[int, int, int]:
    """(规则条数, 场景覆盖的 distinct 告警数, 场景断言条目数)——纯函数，便于合成输入自证。"""
    rules = yaml.safe_load(rules_text)
    alerts = {
        item["alert"]
        for group in rules["groups"]
        for item in group.get("rules", [])
        if isinstance(item, dict) and "alert" in item
    }
    scenario = yaml.safe_load(scenario_text)
    cases = [
        entry
        for group in scenario.get("tests", [])
        for entry in (group.get("alert_rule_test") or [])
    ]
    covered = {entry.get("alertname") for entry in cases}
    assert alerts, f"{RULES_REL} 里没解析出任何 alert——真值口径已变，本判据在空转"
    assert covered, f"{SCENARIO_REL} 里没解析出任何 alertname——真值口径已变，本判据在空转"
    return len(alerts), len(covered), len(cases)


def counts_from_files(rules_path: Path, scenario_path: Path) -> tuple[int, int, int]:
    """从两个文件派生真值——判据里**不留**可以顺手硬编码返回值的中间层。"""
    return counts_from_text(
        rules_path.read_text(encoding="utf-8"),
        scenario_path.read_text(encoding="utf-8"),
    )


def live_counts() -> tuple[int, int, int]:
    return counts_from_files(REPO_ROOT / RULES_REL, REPO_ROOT / SCENARIO_REL)


def latest_script_version(script_name: str) -> str | None:
    """真值：`tool_manifest.json` 里该族最新**未退役**条目（ADR-0051 Phase 3：版本目录已退役）。"""
    import json

    doc = json.loads((REPO_ROOT / "tool_manifest.json").read_text(encoding="utf-8"))
    tool = doc.get("tools", {}).get(script_name)
    if not tool:
        return None
    versions = [
        str(e["version"]) for e in tool["versions"]
        if not e.get("retired") and re.fullmatch(r"\d+\.\d+\.\d+", str(e.get("version", "")))
    ]
    if not versions:
        return None
    return max(versions, key=lambda v: tuple(int(x) for x in v.split(".")))


def violations_in_line(line: str, counts: tuple[int, int, int]) -> list[str]:
    """单行文本 → 与真值不符的陈述列表（纯函数，供判据与夹具共用）。"""
    rules_total, covered_total, cases_total = counts
    found: list[str] = []

    for match in PAT_RULES_CLAIM.finditer(line):
        claimed = int(match.group(1))
        if claimed != rules_total:
            found.append(
                f"「{match.group(0)}」写死 {claimed} 条告警，真值 {rules_total} 条——"
                "要么删掉数字（指回派生源），要么当场改成真值"
            )

    for match in PAT_CASE_CLAIM.finditer(line):
        claimed = int(match.group(1))
        if claimed not in {covered_total, cases_total}:
            found.append(
                f"「{match.group(0)}」写死 {claimed}，真值是「覆盖 {covered_total} 条告警 / "
                f"{cases_total} 条断言条目」——删数字或改成真值"
            )

    for match in PAT_FULL_COVERAGE.finditer(line):
        num, den = int(match.group(1)), int(match.group(2))
        if not (num == den == rules_total == covered_total):
            found.append(
                f"「{match.group(0)}」声称 {num}/{den} 全覆盖，真值 {rules_total} 条规则 / "
                f"{covered_total} 条已覆盖——全覆盖是否成立只能派生，不能抄"
            )

    version_claim = PAT_LATEST_VERSION.search(line)
    if version_claim:
        names = PAT_BACKTICKED_NAME.findall(line)
        script = next((n for n in names if (REPO_ROOT / SCRIPTS_DIR / n).is_dir()), None)
        if script is None:
            found.append(
                f"「{version_claim.group(0)[:40]}…」写死了最新版本号，但同一句里点不出"
                f" `backend/agent/scripts/` 下的真实脚本名——请把脚本名用反引号写清楚"
            )
        else:
            truth = latest_script_version(script)
            claimed = version_claim.group(1)
            if claimed != truth:
                found.append(
                    f"{script} 的「最新 active 版本」文里写 v{claimed}，磁盘真值 v{truth}"
                    "——版本号不要抄进文档，指回 `GET /api/v1/scripts` 与脚本目录"
                )
    return found


def scan_live_surfaces(counts: tuple[int, int, int]) -> tuple[list[str], int]:
    """返回 (违规清单, 扫描文件数)。"""
    offenders: list[str] = []
    scanned = 0
    for surface in LIVE_SURFACES:
        root = REPO_ROOT / surface
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.suffix not in LIVE_SUFFIXES:
                continue
            rel = path.relative_to(REPO_ROOT).as_posix()
            if rel == SELF_EXEMPT:
                continue
            scanned += 1
            text = path.read_text(encoding="utf-8")
            for number, line in enumerate(text.splitlines(), 1):
                for problem in violations_in_line(line, counts):
                    offenders.append(f"{rel}:{number}: {problem}")
    return offenders, scanned


def test_live_counts_are_derivable() -> None:
    """真值本身可派生（派生不出来 = 上游格式变了，此时任何对拍都是空转）。"""
    rules_total, covered_total, cases_total = live_counts()
    assert rules_total >= 1 and covered_total >= 1
    assert cases_total >= covered_total


def test_live_surfaces_do_not_carry_stale_claims() -> None:
    offenders, scanned = scan_live_surfaces(live_counts())
    assert not offenders, (
        f"扫描 {scanned} 个现状口径文件，发现把可派生量抄成文字的陈述：\n"
        + "\n".join(offenders)
    )


def test_scan_surface_is_not_hollow() -> None:
    """反空转：每个面都必须真实在场并至少贡献 1 个文件，已知文件必须被扫到。"""
    present = set()
    for surface in LIVE_SURFACES:
        root = REPO_ROOT / surface
        assert root.is_dir(), f"扫描面 {surface} 已不存在（改名/删除）——判据在空转"
        hits = {
            p.relative_to(REPO_ROOT).as_posix()
            for p in root.rglob("*")
            if p.is_file() and p.suffix in LIVE_SUFFIXES
        }
        assert hits, f"扫描面 {surface} 里一个可扫文件都没有——判据在空转"
        present |= hits
    missing = sorted(f for f in MUST_SCAN if f not in present)
    assert not missing, "这些现状口径文件已不在扫描面里（改名/搬走了？）：" + ", ".join(missing)


def test_truth_follows_file_content_text_level() -> None:
    """文本级探针：往真值输入里加/减一条，输出必须跟着动（把派生改成常量即红）。"""
    base = live_counts()
    rules_text = (REPO_ROOT / RULES_REL).read_text(encoding="utf-8")
    scenario_text = (REPO_ROOT / SCENARIO_REL).read_text(encoding="utf-8")
    rules_doc = yaml.safe_load(rules_text)
    rules_doc["groups"][0]["rules"].append({"alert": "ProbeOnlyForCount", "expr": "up > 0"})
    assert counts_from_text(yaml.safe_dump(rules_doc), scenario_text) == (
        base[0] + 1, base[1], base[2])

    scenario_doc = yaml.safe_load(scenario_text)
    first = scenario_doc["tests"][0]["alert_rule_test"]
    assert len(first) >= 2, "场景文件的第一组只剩 1 条断言——本探针的前提变了，请换靶"
    scenario_doc["tests"][0]["alert_rule_test"] = first[1:]
    shrunk = counts_from_text(rules_text, yaml.safe_dump(scenario_doc))
    # 少一条断言条目必然跟着少 1；被删的那条若独占一个告警，覆盖数也会 -1（不独占则不变）。
    assert shrunk[0] == base[0] and shrunk[2] == base[2] - 1
    assert shrunk[1] in {base[1], base[1] - 1}
    assert shrunk != base


def test_truth_follows_file_content_file_level(tmp_path: Path) -> None:
    """文件级探针：真值必须来自**传进去的那两个文件**，不是来自仓库里那份。"""
    fake_rules = tmp_path / "rules.yml"
    fake_scenario = tmp_path / "scenario.yml"
    fake_rules.write_text(
        "groups:\n  - name: g\n    rules:\n"
        "      - alert: A\n        expr: up > 0\n      - alert: B\n        expr: up > 1\n",
        encoding="utf-8")
    fake_scenario.write_text(
        "tests:\n  - interval: 1m\n    alert_rule_test:\n"
        "      - alertname: A\n        eval_time: 5m\n"
        "      - alertname: A\n        eval_time: 10m\n"
        "      - alertname: B\n        eval_time: 5m\n",
        encoding="utf-8")
    assert counts_from_files(fake_rules, fake_scenario) == (2, 2, 3)
    # 「2 条告警」在这份假真值下是实话，在仓库真值下是谎话——判据跟着真值翻面。
    assert not violations_in_line("2 条告警", (2, 2, 3))
    assert violations_in_line("2 条告警", live_counts())


def test_detector_flags_stale_claims() -> None:
    """红样例：抄来的数字一过期就得判红（否则本文件自己就是它反对的那种守卫）。"""
    rules_total, covered_total, _ = live_counts()
    wrong_rules = rules_total + 4
    wrong_cases = covered_total + 7
    samples = [
        f"（新增告警必须带场景用例，恒跑；{wrong_rules}/{wrong_rules} 已全覆盖，",
        f"**{wrong_rules} 条告警规则 {wrong_cases} 条断言全覆盖**",
        f"{wrong_rules} 条规则现有 {wrong_cases} 条场景断言",
        f"最新 active 版本已注册（2026-09-15 复核 = **v{wrong_rules}.0.0**）；`flash_firmware`",
        "| `flash_firmware` | 最新 active 版本（复核 = v0.0.1）；若 Plan 引用 |",
        "| `no_such_script_xyz` | 最新 active 版本（复核 = v1.0.0） |",
    ]
    for sample in samples:
        assert violations_in_line(sample, live_counts()), f"样例未被抓住：{sample}"


def test_detector_accepts_live_claims() -> None:
    """绿样例：写「等于真值的数字」是允许的——判据反的是过期，不是数字本身。"""
    rules_total, covered_total, cases_total = live_counts()
    latest = latest_script_version("flash_firmware")
    assert latest is not None, "flash_firmware 版本目录解析不出来——真值口径已变"
    samples = [
        f"（恒跑；{rules_total}/{covered_total} 已全覆盖，",
        f"**{rules_total} 条告警规则 {covered_total} 条断言全覆盖**",
        f"{rules_total} 条规则现有 {cases_total} 条场景断言",
        f"| `flash_firmware` | 最新 active 版本 = v{latest}（{latest} 于磁盘真值） |",
    ]
    for sample in samples:
        assert not violations_in_line(sample, live_counts()), f"绿样例被误判：{sample}"


def test_detector_ignores_non_claims() -> None:
    """已知假阳性面（#2663 判据边界）：这些形态刻意不认，别把它们判红。"""
    counts = live_counts()
    samples = [
        "# 17 条告警一条也没上线，本机那份 7/10 的手工拷贝还停在 10 条。",  # 会被抓住（历史叙述需改写）
    ]
    assert violations_in_line(samples[0], counts), "历史叙述里的写死数字也应判红（需改写或移进 Note）"
    benign = [
        "净效果是「17 条规则一条也没上线」，与本机那份 7/10 手工拷贝停在 10 条是同一件事。",
        "| 2026-08-27 | S7 skill frontmatter 校验入 L0（自测 7 条规则全绿）|",
        "# 装了 promtool 的机器上偶发可见。这 10 条补齐后清单为空，任何新告警不补场景",
        "手工拷贝的 10 条规则，仓库已有 19 条",
        "- `flash_firmware` **最新 active 版本**已注册（`GET /api/v1/scripts?name=flash_firmware`",
        "存量：#2151 已逐条核对全部告警引用的指标都有真实生产者，故允许清单为空",
    ]
    for sample in benign:
        assert not violations_in_line(sample, counts), f"不该判红：{sample}"
