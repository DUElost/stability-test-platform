"""#2494：对外风险词表四面同源（ADR-0045 D2/D3/D4）——离线结构门禁。

形状与 #2418 完全同类：**判定链已经唯一（log_observation 出 S/A/B），但展示层的翻译表
还留在后端**，于是同一个「job 风险级别」在四个面有四种形状：报告 DTO 出 `S/A/B`、
结果页列表出 `HIGH/MEDIUM/LOW`、分布字段叫 `high/medium/low/unknown`、趋势又用第四态
`NONE`。前端徽标按另一套键查表，**缺键就落到 FALLBACK「未知」**——于是报告页同一张卡上
徽标说「未知」、旁边的 S/A/B 计数说「B:1」（tip `dc0254c1` 实测）。

本文件钉四条（行为侧的"同一 job 四面同级别"在 `backend/tests/api/test_results.py`，
它要 PG，不放进这个离线门禁）：

- 对外值域 `S/A/B/UNKNOWN` 每个值都必须在前端 `RISK` 表有键（缺一个=那种风险恒显未知）；
- 后端不再存在「级别 → HIGH/MEDIUM/LOW」这类展示层映射（D2：翻译只发生在前端一处）；
- `RiskDistribution` 的字段就是级别本身（D2）；
- 趋势第四态是 `UNKNOWN` 而不是 `NONE`（D2），且 UNKNOWN 与 B 是两个桶（D4：零事件
  不算"低风险"）。

与 `tests/test_status_vocabulary_drift.py` 同一范式，并同样带**解析器自守**断言
（解析退化会让上面的断言恒真，那比没有门禁更糟）。
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BADGE_TS = REPO_ROOT / "frontend/src/components/ui/status-badge.tsx"
RESULTS_PY = REPO_ROOT / "backend/api/routes/results.py"

# ADR-0045 D2：收敛后的对外值域。告警 severity（HIGH/MEDIUM/LOW）是另一条轴（D5），
# 因此这里只钉"风险级别"这一组值必须能被徽标查到。
OUTWARD_RISK_LEVELS = {"S", "A", "B", "UNKNOWN"}


def _frontend_risk_keys() -> set[str]:
    text = BADGE_TS.read_text(encoding="utf-8")
    match = re.search(
        r"^const RISK: Record<string, StatusEntry> = \{(.*?)^\}", text, re.S | re.M
    )
    assert match, "未解析到前端 RISK 表（status-badge.tsx 结构变了？请同步本解析器）"
    keys = set(re.findall(r'^\s{2}"?([A-Z_]+)"?:', match.group(1), re.M))
    assert keys, "RISK 表解析为空——恒真断言，判据失效"
    return keys


def _results_text() -> str:
    return RESULTS_PY.read_text(encoding="utf-8")


def test_every_outward_risk_level_has_a_badge_key():
    """对外值域的每个级别都要有徽标键，否则那种风险的徽标恒显「未知」。"""
    missing = OUTWARD_RISK_LEVELS - _frontend_risk_keys()
    assert not missing, (
        f"前端 RISK 表缺键 {sorted(missing)}：这些级别的徽标会落到 FALLBACK「未知」，"
        "而同屏的 S/A/B 计数照常显示——正是 #2494 描述的同卡自相矛盾。"
    )


def _module_names(text: str) -> set[str]:
    """模块级**定义的**名字（AST，不是子串）。

    用 AST 而不是 `"X" not in text`：解释性注释必然要写"以前这里有什么"，子串判据会
    把说明本身判成违规——同一个坑在 #2432/#2402 的守卫上踩过两次，这次一次到位。
    """
    tree = ast.parse(text)
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def _class_fields(text: str, cls: str) -> set[str]:
    """某个 Pydantic 模型的注解字段名（AST，不用正则刮源码块）。

    上一版用正则匹配字段块，结果刮到了同名前缀的**另一个**类（`RiskTrendOut`），
    断言于是变成"字段不全"的假红——结构门禁自己也得有判别力，否则同样是噪声源。
    """
    tree = ast.parse(text)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == cls:
            return {
                st.target.id
                for st in node.body
                if isinstance(st, ast.AnnAssign) and isinstance(st.target, ast.Name)
            }
    raise AssertionError(f"未解析到类 {cls}（结构变了请同步本解析器）")


def _code_string_constants(text: str) -> set[str]:
    """代码里出现的字符串字面量，**排除 docstring**（Expr(Constant(str)) 那类）。"""
    found: set[str] = set()

    def walk(node: ast.AST) -> None:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str):
            return  # 文档串/裸字符串说明，不是行为
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.Constant) and isinstance(child.value, str):
                found.add(child.value)
            else:
                walk(child)

    walk(ast.parse(text))
    return found


def test_backend_no_longer_translates_levels_for_display():
    """D2：后端不得再出现「级别 → HIGH/MEDIUM/LOW」这类字面量映射（第二套对外词表）。"""
    text = _results_text()
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        pairs = {
            k.value: v.value
            for k, v in zip(node.keys, node.values, strict=True)
            if isinstance(k, ast.Constant) and isinstance(v, ast.Constant)
        }
        assert not ({"S", "A", "B"} & set(pairs) and {"HIGH", "MEDIUM", "LOW"} & set(pairs.values())), (
            f"检测到把级别翻成 HIGH/MEDIUM/LOW 的字面量映射：{pairs}"
        )


def test_risk_distribution_fields_are_the_levels_themselves():
    """D2：分布桶名 = 级别（小写只是 JSON 风格），不再是 high/medium/low。"""
    fields = _class_fields(_results_text(), "RiskDistribution")
    assert fields == {"s", "a", "b", "unknown"}, (
        f"RiskDistribution 字段漂移为 {sorted(fields)}；ADR-0045 D2 要求它等于级别本身"
    )


def test_trend_fourth_state_is_unknown_not_none():
    """D2：趋势第四态并到 UNKNOWN；D4：UNKNOWN 与 B 必须是两个桶。"""
    text = _results_text()
    fields = _class_fields(text, "RiskTrendBucket")
    assert "NONE" not in fields, "趋势又出现 NONE 第四态（D2 已并入 UNKNOWN）"
    assert {"S", "A", "B", "UNKNOWN"} <= fields, f"趋势桶不全：{sorted(fields)}"
    assert "NONE" not in _code_string_constants(text), (
        "results.py 的代码里又出现 NONE 字面量（对外词表已按 D2 收敛为 UNKNOWN；"
        "注释里提到历史词不算）"
    )


def test_bucket_translation_tables_are_gone():
    """两张展示层映射表整体删除——留着它们，早晚又会长出一套第二词表。

    同样走 AST：注释里**必然**要写"以前这里有什么"（不写就成了无解释的删除），
    子串判据会把说明本身判成违规。
    """
    names = _module_names(_results_text())
    leftovers = {"_RISK_LABEL_BY_LEVEL", "_RISK_BUCKET_BY_LEVEL"} & names
    assert not leftovers, (
        f"results.py 又定义了展示层映射表 {sorted(leftovers)}；"
        "ADR-0045 D2 要求判定级别原样出 API，翻译只发生在前端一处。"
    )
