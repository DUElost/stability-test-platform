"""站点抓取面与「装到站点的告警规则」必须有生产者（#2643 建议 3）。

**问题**：#2488 补上了「规则装了但不加载」那层缺口，但站点 Prometheus 模板只有
node-exporter 一个抓取 job，而安装清单把**整份**仓库规则文件渲到站点。于是绝大多数
规则在站点上恒无样本——既不触发也不报错，而 installer 只看「服务起来 + `/-/ready`
通」就报 `monitoring_ready`。**「监控就绪」与「规则结构性不可触发」同时成立。**

**终态（#2643 方向 1 已落地，owner 裁决 2026-09-18）**：站点只装「站点可见面」规则
（`deploy/prometheus/site-alerts.yml`），平台文件里其余规则引用的都是控制面进程指标——
站点那个唯一的 node-exporter job 结构上抽不到，装了也恒不触发。判据保留三个方向的可判性：
往站点文件里加控制面域规则 → 红；站点子集与平台文件定义分叉 → 红（新增的对拍）；
**引用「仓库有生产者、但站点安装清单里没有该单元」的 textfile 指标 → 红**（#2788）。

**#2788（判据修正）**：旧的「站点可见 textfile 面」取的是**仓库全部** textfile 生产者
（`tests/metrics_registry._TEXTFILE_PRODUCERS`），于是 `stp_pg_guard_*`（生产者
`stp-pg-guard.{service,timer}` 是**控制面宿主**单元，`MONITORING_SAMPLER` 里没有）被误判成
「站点可见」——`StabilityPgSchemaGuessing` 装到站点后 `absent(stp_pg_guard_last_run)` 恒真、
**每站点永久 firing**。现在「站点可见 textfile 面」按**安装清单**过滤（见
`site_textfile_metric_index`），与「装了但恒不触发」是同一判据的两半。

真值全部**派生**，不用 grep 计数：

| 轴 | 来源 |
|---|---|
| 站点装的是哪些规则文件 | AST 读 `tools/site_config/stages.py` 的 `MONITORING_RULES` 字面量 |
| 站点能抓什么 | `deploy/prometheus/prometheus.yml` 的 `scrape_configs`（本仓唯一现状：单 job → node-exporter） |
| 规则引用了哪些指标 | yaml 解析 `expr`（含 `>-` 折叠多行）后与「指标全集」求交 |
| 指标有没有生产者 | `tests/metrics_registry.py`：控制面注册表 ∪ 本仓自管 textfile 产物 |

**为什么必须派生而不是 grep**：#2643 自述「18/21 条引用 `stability_*`」是
`grep 'expr:' | grep -c 'stability_'` 得来的，而 `StabilityPatrolStall` 的 `expr: >-`
是折叠标量——指标名在续行上，行级 grep 看不见，真值是 **19**（另：它那三个分类
`18 + 2 + 1 = 21` 里的 `absent(` 与 `stp_script_guard` 命中同一条规则，属重复计数）。
这与 #2639 那次 `git grep 'tests/**/*.py'` 静默少算 81 个文件是同一类错误。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import yaml

from tests.metrics_registry import (
    _TEXTFILE_PRODUCERS,
    metric_registry_index,
    textfile_metric_index,
    textfile_metrics_for,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
STAGES_REL = "tools/site_config/stages.py"
SITE_PROMETHEUS_REL = "deploy/prometheus/prometheus.yml"
RULES_REL = "deploy/prometheus/alerts-stability-platform.yml"
#: 站点实际安装的规则文件（#2643 方向 1：站点只装站点可见面）。
SITE_RULES_REL = "deploy/prometheus/site-alerts.yml"

#: Prometheus 内置序列——站点那个唯一 job 也能给出的东西。**不列 `node_*` 命名空间**：
#: 结构层（`test_prometheus_alerts_contract.py`）只认「注册表 ∪ textfile 产物」，`node_*`
#: 现在进不了规则文件；真要用它，得先改那一层，本判据再跟着加（不留没人走的分支）。
PROMETHEUS_BUILTIN = {"up", "scrape_samples_scraped", "instance"}
#: 站点现状：唯一抓取目标就是本机 node-exporter。前提变了必须重估 #2643（见测试）。
EXPECTED_SITE_JOBS = {"file-server": {"127.0.0.1:9100"}}

_METRIC_TOKEN = re.compile(r"[a-zA-Z_:][a-zA-Z0-9_:]*")

#: 存量清单（**已清零**）：#2643 方向 1（站点只装站点可见面）落地后，站点侧不再有
#: 「装了但结构性无生产者」的规则。判据保留「新增即红」的方向——往站点文件里加一条
#: 控制面域规则会在这里被抓住；清单重新变成非空即说明分层被破坏。
_SITE_INERT_RULES: frozenset[str] = frozenset()


def site_installed_rule_files() -> list[str]:
    """安装清单里会被渲到站点的仓库规则文件（AST 取字面量，不 import stages）。"""
    tree = ast.parse((REPO_ROOT / STAGES_REL).read_text(encoding="utf-8"), filename=STAGES_REL)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == "MONITORING_RULES" for t in node.targets):
            continue
        pairs: list[str] = []
        for elt in _iter_tuple_elements(node.value):
            items = list(_iter_tuple_elements(elt))
            if len(items) >= 2 and isinstance(items[0], ast.Constant):
                pairs.append(str(items[0].value))
        assert pairs, f"{STAGES_REL} 的 MONITORING_RULES 结构变了，取不到源文件"
        return pairs
    raise AssertionError(f"{STAGES_REL} 里找不到 MONITORING_RULES——安装清单改名了？")


def _iter_tuple_elements(node: ast.AST):
    if isinstance(node, ast.Tuple):
        for elt in node.elts:
            if isinstance(elt, ast.Starred):
                continue
            yield elt
    elif isinstance(node, ast.Constant) and isinstance(node.value, str):
        yield node


def site_scrape_jobs() -> dict[str, set[str]]:
    """站点 Prometheus 的抓取面：job 名 → 目标集合。"""
    doc = yaml.safe_load((REPO_ROOT / SITE_PROMETHEUS_REL).read_text(encoding="utf-8"))
    jobs: dict[str, set[str]] = {}
    for cfg in doc.get("scrape_configs") or []:
        targets: set[str] = set()
        for sc in cfg.get("static_configs") or []:
            targets |= set(sc.get("targets") or [])
        jobs[str(cfg.get("job_name"))] = targets
    assert jobs, f"{SITE_PROMETHEUS_REL} 没有任何 scrape_configs——抓取面口径已变"
    return jobs


#: textfile 生产者 → 站点安装清单里对应的单元名（#2788）。``None`` = 站点不装该单元。
#: 新增 textfile 生产者**必须**在这里登记（未登记即红）——否则「仓库有生产者」会被
#: 误判成「站点可见」，正是 #2788 的成因。
_PRODUCER_SITE_UNIT: dict[str, str | None] = {
    "tools/dev/script_guard_probe.py": "stp-script-guard",
    # 控制面宿主单元：读 PG 服务日志，而 PG 只在控制面宿主上（站点装了也扫不到）
    "tools/dev/pg_error_guard.py": None,
    # #2881：站点安装清单含 stp-skill-usage.{service,timer}（stages.py MONITORING_SAMPLER）
    "tools/dev/skill_usage_probe.py": "stp-skill-usage",
}


def site_installed_sampler_units() -> set[str]:
    """站点安装清单 ``MONITORING_SAMPLER`` 里落地的 systemd 单元名（AST 取字面量）。"""
    tree = ast.parse((REPO_ROOT / STAGES_REL).read_text(encoding="utf-8"), filename=STAGES_REL)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == "MONITORING_SAMPLER"
                   for t in node.targets):
            continue
        units: set[str] = set()
        for elt in _iter_tuple_elements(node.value):
            items = list(_iter_tuple_elements(elt))
            if len(items) >= 2 and isinstance(items[1], ast.Constant):
                name = str(items[1].value).rsplit("/", 1)[-1]
                for suffix in (".service", ".timer"):
                    if name.endswith(suffix):
                        units.add(name[: -len(suffix)])
                        break
        assert units, f"{STAGES_REL} 的 MONITORING_SAMPLER 结构变了，取不到单元名"
        return units
    raise AssertionError(f"{STAGES_REL} 里找不到 MONITORING_SAMPLER——安装清单改名了？")


def site_textfile_metric_index() -> dict[str, set[str]]:
    """**站点装得到**的 textfile 指标（#2788：按安装清单过滤，而非「仓库有没有生产者」）。

    生产者文件 → 单元名 → 单元是否在 ``MONITORING_SAMPLER``，三级全部显式；任何一级
    缺登记都红（未登记的 textfile 生产者不得悄悄获得「站点可见」身份）。
    """
    units = site_installed_sampler_units()
    index: dict[str, set[str]] = {}
    for rel in _TEXTFILE_PRODUCERS:
        assert rel in _PRODUCER_SITE_UNIT, (
            f"textfile 生产者 {rel} 未登记站点单元（_PRODUCER_SITE_UNIT）——新增即红："
            "先回答「它的单元在不在 MONITORING_SAMPLER 里」")
        if _PRODUCER_SITE_UNIT[rel] in units:
            for name in textfile_metrics_for(rel):
                index.setdefault(name, set())
    assert index, (
        "站点 textfile 面为空——要么 MONITORING_SAMPLER 结构变了，要么登记全错；"
        "空判据会让下面的 inert 对拍静默变绿")
    return index


def rule_expressions(rules_path: Path) -> list[tuple[str, str]]:
    """[(告警名, expr 全文)]——yaml 解析，折叠标量（`>-`）的多行 expr 也拿得到。"""
    doc = yaml.safe_load(rules_path.read_text(encoding="utf-8"))
    out: list[tuple[str, str]] = []
    for group in doc.get("groups") or []:
        for item in group.get("rules") or []:
            if isinstance(item, dict) and "alert" in item and "expr" in item:
                out.append((str(item["alert"]), str(item["expr"])))
    return out


def referenced_metrics(expr: str, universe: set[str]) -> set[str]:
    """expr 里真正引用的指标名 = 词法候选 ∩ 指标全集（标签名/函数名自然落在全集外）。"""
    return set(_METRIC_TOKEN.findall(expr)) & universe


def inert_rules_at_site() -> tuple[set[str], dict[str, set[str]]]:
    """返回（站点结构性不可触发的告警名, 告警名 → 站点装不到的指标）。

    「站点装不到」= 引用了站点可见面之外的指标。#2788 后站点可见面按**安装清单**过滤，
    故「仓库有 textfile 生产者、但站点不装那个单元」（如控制面宿主的 `stp_pg_guard_*`）
    也会被判进来，而不是像旧判据那样被误判成站点可见。
    """
    control_plane, _unqueryable = metric_registry_index()
    site_visible = set(site_textfile_metric_index()) | PROMETHEUS_BUILTIN
    # 全集 = 注册表 ∪ 站点可见面 ∪ 仓库全部 textfile：后者的唯一用途是让「已知但站点装不到」
    # 能被表达出来；不纳入全集的话这些名字会走「未知指标」分支，报错指向拼写而误导。
    known = set(control_plane) | site_visible | set(textfile_metric_index())
    inert: set[str] = set()
    detail: dict[str, set[str]] = {}
    installed = site_installed_rule_files()
    for rel in installed:
        path = REPO_ROOT / rel
        if not path.is_file():
            continue
        for name, expr in rule_expressions(path):
            refs = referenced_metrics(expr, known)
            assert refs, (
                f"{rel} 的 {name} 没引用到任何已知指标——要么指标名拼错（结构层该同时红），"
                "要么抓取面出现了新命名空间（那要先把该名字纳入站点可见面，别绕过对拍）"
            )
            not_site = refs - site_visible
            if not_site:
                inert.add(name)
                detail[name] = not_site
    return inert, detail


def test_site_scrape_surface_premise_is_single_node_exporter_job() -> None:
    """前提钉子：站点现状只有本机 node-exporter 这一个抓取面。"""
    jobs = site_scrape_jobs()
    assert jobs == EXPECTED_SITE_JOBS, (
        f"站点抓取面已变成 {jobs}，与 #2643 立单时的前提（仅 file-server → 127.0.0.1:9100）"
        "不同：若已补控制面 `/metrics` 抓取（建议 2），下面的债务清单必须清零并删掉本钉子"
    )


def test_site_installed_files_are_the_site_subset() -> None:
    """#2643 方向 1：站点装的是**站点可见面子集**（site-alerts.yml），不是整份平台规则。"""
    installed = site_installed_rule_files()
    assert installed == [SITE_RULES_REL], (
        f"站点装的规则文件是 {installed}，与方向 1 的终态（仅 {SITE_RULES_REL}）不一致——"
        "若改回整份平台文件或补抓取（方向 2），请连同下面的判据与债务清单一起裁决"
    )


def test_installed_rules_without_site_producers_stay_registered() -> None:
    """双向棘轮：新增「站点恒无样本」的规则即红；已修掉却还挂在清单里也红。"""
    inert, detail = inert_rules_at_site()
    grown = sorted(inert - _SITE_INERT_RULES)
    stale = sorted(_SITE_INERT_RULES - inert)
    assert not grown, (
        "这些规则装到站点却只有控制面才有生产者，站点侧结构性不可触发：\n"
        + "\n".join(f"  {name} ← {sorted(detail[name])}" for name in grown)
        + "\n处置（#2643）：要么让站点不装它（建议 1 分层），要么给站点补抓取（建议 2），"
          "要么它本来就该有 textfile/node 面生产者（那就修生产者）。"
    )
    assert not stale, (
        "债务清单里这些条目已不再是「站点不可触发」，请把 _SITE_INERT_RULES 一起缩短：\n"
        f"  {stale}"
    )


def test_debt_register_is_not_hollow() -> None:
    """反空转：清单必须 ⊆ 真实告警名，且派生真值本身非退化（否则对拍在空转）。"""
    all_names = {name for rel in site_installed_rule_files() for name, _ in rule_expressions(REPO_ROOT / rel)}
    assert _SITE_INERT_RULES <= all_names, (
        f"清单里有不存在的告警名：{sorted(_SITE_INERT_RULES - all_names)}"
    )
    control_plane, _ = metric_registry_index()
    assert len(control_plane) >= 100, "控制面注册表指标数异常偏少——全集解析退化了"
    assert set(textfile_metric_index()), "textfile 生产者索引为空——站点可见面的判据基础没了"
    assert set(site_textfile_metric_index()), "站点 textfile 面为空——按清单过滤的判据退化了"
    inert, _detail = inert_rules_at_site()
    assert inert == set(), (
        f"站点装入的规则里仍有结构性不可触发的：{sorted(inert)}——"
        "方向 1 的终态是**零条**；往站点文件里加控制面域规则会在这里红"
    )


def test_producer_unit_must_be_in_the_site_install_manifest() -> None:
    """#2788：textfile 生产者「在仓库里」不等于「站点装得到」。

    反例形态（本单来源）：把平台的 `StabilityPgSchemaGuessing`（生产者 `stp-pg-guard`
    是**控制面宿主**单元）放进站点子集——旧判据只看仓库 textfile 生产者索引，会放行；
    新判据按 `MONITORING_SAMPLER` 过滤后把它判进 inert（站点上 `absent()` 恒真 ⇒ 永久 firing）。
    """
    site_visible = set(site_textfile_metric_index()) | PROMETHEUS_BUILTIN
    known = set(textfile_metric_index()) | site_visible
    refs = referenced_metrics(
        "stp_pg_schema_error_events >= 5 or absent(stp_pg_guard_last_run)", known)
    assert refs, "指标本身必须是已知的（否则退化成拼写判据，报文会误导）"
    assert refs - site_visible, (
        "控制面宿主的 textfile 生产者必须判为「站点装不到」——否则站点装了它的规则会"
        "结构性不可触发（#2788 的假绿成因）"
    )
    # 反向：脚本守卫的生产者单元在清单里，必须仍是站点可见
    assert "stp_script_guard_last_run" in site_visible
    assert "stp-script-guard" in site_installed_sampler_units()


def test_folding_multiline_expr_is_still_read() -> None:
    """判别力：`>-` 折叠多行 expr 的续行指标必须被认出（不许退回行级 grep 那种判据）。"""
    universe = {"stability_probe_metric_total", "stp_script_guard_due"}
    # 唯一的目标指标在**续行**上——只读 expr 的第一行就会漏掉它（变异 V7 实测逼出这条形状）
    folded = (
        "sum(increase(probe_other[15m]))\n"
        "  - sum(rate(stability_probe_metric_total{le=\"1\"}[5m])) > 0"
    )
    assert referenced_metrics(folded, universe) == {"stability_probe_metric_total"}
    assert referenced_metrics(folded.splitlines()[0], universe) == set()
    assert referenced_metrics("stp_script_guard_due > 0", universe) == {"stp_script_guard_due"}
    assert referenced_metrics("sum(rate(probe_other{le=\"1\"}[5m]))", universe) == set()


def test_grep_shape_undercounts_the_real_value() -> None:
    """现场证据：#2643 的「18 条」少算了一条，靶就是 `StabilityPatrolStall`。"""
    lines = (REPO_ROOT / RULES_REL).read_text(encoding="utf-8").splitlines()
    at = next(i for i, ln in enumerate(lines) if ln.strip() == "- alert: StabilityPatrolStall")
    expr_line = next(ln for ln in lines[at:at + 8] if ln.strip().startswith("expr:"))
    assert expr_line.strip() == "expr: >-", f"该规则的 expr 形态变了（{expr_line!r}），本用例需换靶"
    assert "stability_patrol" not in expr_line
    parsed = dict(rule_expressions(REPO_ROOT / RULES_REL))
    assert "stability_patrol_failure_streak_observed_count" in parsed["StabilityPatrolStall"]


def _rules_by_name(path: Path) -> dict[str, dict]:
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    return {
        str(item["alert"]): item
        for group in doc.get("groups") or []
        for item in group.get("rules") or []
        if isinstance(item, dict) and "alert" in item
    }


def test_site_subset_is_verbatim_from_the_platform_file() -> None:
    """#2643 方向 1 的漂移守卫：站点子集必须是平台文件里的**同一份定义**。

    分层把「装什么」拆成两份文件的代价是「改一处忘一处」——这条对拍把代价关掉：
    站点文件里每条规则（除 `alert` 外）的全部字段必须与平台文件同名规则逐字段相等；
    也不得出现平台文件里没有的规则（那等于绕开权威定义面单独增删）。
    """
    site_rules = _rules_by_name(REPO_ROOT / SITE_RULES_REL)
    platform_rules = _rules_by_name(REPO_ROOT / RULES_REL)

    unknown = sorted(set(site_rules) - set(platform_rules))
    assert not unknown, (
        f"站点子集里有平台文件不存在的规则：{unknown}——新增规则请先落平台文件"
        "（权威定义面），再决定是否进站点可见面"
    )
    drift = sorted(name for name, rule in site_rules.items() if rule != platform_rules[name])
    assert not drift, (
        f"以下规则在站点子集与平台文件里定义不一致：{drift}——"
        "改规则时改平台文件、再同步子集，别让两份定义分叉"
    )
