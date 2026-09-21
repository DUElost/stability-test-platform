"""`tools/dev/skill_usage_report.py` 的契约测试——不连库、不联网、不读真实转录。

要守住的性质（#2785）：
1. **分型窗口不被压平**：event 型（紧急释放/扩容类低频场景）在 persistent
   窗口内不得判洞——否则 2026-09-19 的结构性误报复发，逼人删掉该留的 SOP。
2. **判洞只看强信号**：Codex 读取列是审计噪声未甄别的弱信号，不得参与判洞。
3. **退出码契约**：无数据源 = 不可观测 ≠ 违规，必须 exit 0（timer 在缺转录源
   的站点恒绿）；--strict 且有洞才 exit 1。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "skill_usage_report", REPO_ROOT / "tools" / "dev" / "skill_usage_report.py")
_mod = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
sys.modules["skill_usage_report"] = _mod
_spec.loader.exec_module(_mod)


# ---------------------------------------------------------------- 判洞表

@pytest.mark.parametrize(
    "age,calls,stype,expected",
    [
        (20, 0, "persistent", True),   # 老牌常驻型零强信号：洞
        (20, 0, "event", False),       # event 型同龄不判：分型窗口的本意
        (61, 0, "event", True),        # event 型超 60 天窗口：洞
        (20, 3, "persistent", False),  # 有强信号调用永不判洞
        (None, 0, "persistent", False),  # 出生未知不判（证据不足）
    ],
)
def test_hollow_verdict_table(age, calls, stype, expected):
    assert _mod.is_hollow(age, calls, stype) is expected


def test_hollow_verdict_requires_strong_source_present():
    """#2851：强信号源不在场时，任何年龄/任何调用数都**不判洞**——缺源 ≠ 零调用。"""
    assert _mod.is_hollow(999, 0, "persistent", strong_source_present=False) is False
    assert _mod.is_hollow(61, 0, "event", strong_source_present=False) is False


# ---------------------------------------------------------------- 扫描器红绿

def _make_tree(tmp_path: Path, skill_types: dict[str, str],
               claude_lines: list[str], codex_body: str):
    skills = tmp_path / "skills"
    claude = tmp_path / "claude"
    codex = tmp_path / "codex" / "2026" / "09" / "12"
    for slug in skill_types:
        (skills / slug).mkdir(parents=True)
        extra = f"\ntype: {skill_types[slug]}" if skill_types[slug] else ""
        (skills / slug / "SKILL.md").write_text(
            f"---\nname: {slug}\ndescription: 夹具{extra}\n---\n\n正文\n",
            encoding="utf-8")
    claude.mkdir()
    (claude / "s1.jsonl").write_text("\n".join(claude_lines) + "\n",
                                     encoding="utf-8")
    codex.mkdir(parents=True)
    (codex / "rollout-2026-09-12T08-00-00-x.jsonl").write_text(
        codex_body, encoding="utf-8")
    return skills, claude, tmp_path / "codex"


def test_scan_claude_counts_real_invocations_only(tmp_path):
    skills, claude, _ = _make_tree(
        tmp_path,
        {"alpha": "", "beta": ""},
        claude_lines=[
            # 真调用：Skill + tool_use + slug 同行
            '{"timestamp":"2026-09-10T10:00:00.000Z","name":"Skill",'
            '"input":{"skill":"alpha"},"tool_use":true}',
            # 缺 tool_use：清单/正文中提及 slug，不得计
            '{"timestamp":"2026-09-11T10:00:00.000Z","name":"Skill",'
            '"note":"beta"}',
        ],
        codex_body="",
    )
    slugs = ["alpha", "beta"]
    result = _mod.scan_claude(str(claude), slugs)
    assert result["alpha"][0] == 1
    assert result["beta"] == (0, None)


def test_scan_codex_reads_sessions_not_writes(tmp_path):
    skills, _, codex_root = _make_tree(
        tmp_path,
        {"alpha": "", "beta": ""},
        claude_lines=[],
        codex_body=(
            # 带引号 sed 参数（2026-09-19 实测主形态）：计 1 会话
            '{"cmd":"sed -n \'1,80p\' .claude/skills/beta/SKILL.md"}\n'
            # apply_patch 写入与 grep 提及：不得计
            '{"cmd":"apply_patch skills/beta/SKILL.md"}\n'
            '{"cmd":"grep -r skills/beta/SKILL.md ."}\n'
        ),
    )
    result = _mod.scan_codex(str(codex_root), ["alpha", "beta"])
    assert result["beta"] == (1, "2026-09-12")
    assert result["alpha"] == (0, None)


def test_inventory_type_parsing(tmp_path):
    skills, _, _ = _make_tree(
        tmp_path,
        {"plain": "", "ev": "event", "weird": "typo-type"},
        claude_lines=[], codex_body="",
    )
    # 行内注释须按 YAML 语义剥离（SKILL.md 实物即此形态）
    p = skills / "ev" / "SKILL.md"
    p.write_text(p.read_text(encoding="utf-8").replace(
        "type: event", "type: event  # 低频事件场景"), encoding="utf-8")
    by = {i["name"]: i for i in _mod.inventory(str(skills))}
    assert by["plain"]["type"] == "persistent"   # 缺省
    assert by["ev"]["type"] == "event"           # 带注释仍识别
    assert by["weird"]["type"] == "persistent"   # 未知值保守降级（更早亮灯）


# ---------------------------------------------------------------- 退出码契约

def _patch_world(monkeypatch, tmp_path, items, claude_exists, codex_exists):
    claude = tmp_path / "claude_src"
    codex = tmp_path / "codex_src"
    for d, exists in ((claude, claude_exists), (codex, codex_exists)):
        if exists:
            d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(_mod, "inventory", lambda: items)
    monkeypatch.setattr(_mod, "TRANSCRIPT_DIR", str(claude))
    monkeypatch.setattr(_mod, "CODEX_DIR", str(codex))
    monkeypatch.setattr(_mod, "scan_claude",
                        lambda d, slugs: {s: (0, None) for s in slugs})
    monkeypatch.setattr(_mod, "scan_codex",
                        lambda d, slugs: {s: (0, None) for s in slugs})


_SKILL = {"dir": "x", "name": "x", "desc": "d", "birth": 1_000_000,
          "type": "persistent"}


def test_exit_zero_when_no_transcript_source(monkeypatch, tmp_path, capsys):
    # timer 在缺转录源的站点必须恒绿：不可观测 ≠ 违规
    _patch_world(monkeypatch, tmp_path, [_SKILL], False, False)
    monkeypatch.setattr(sys, "argv", ["skill_usage_report.py", "--strict"])
    assert _mod.main() == 0
    assert "skip" in capsys.readouterr().out


def test_claude_source_missing_does_not_judge_hollow(monkeypatch, tmp_path, capsys):
    """#2851：**弱信号源在场、强信号源缺源**（他机/新站点形态）⇒ 不判洞、--strict 退 0。

    修前：`claude={}` ⇒ 每个 skill 强信号都是 0 ⇒ 超过观察窗的全部误判 HOLLOW，
    `stp-skill-usage.timer` 与 `check:gov` 恒红；表格还把未扫描的源印成
    「Claude 调用 0 次 最近 从未」——把观测缺口说成了观测事实。
    """
    _patch_world(monkeypatch, tmp_path, [_SKILL], False, True)
    monkeypatch.setattr(sys, "argv", ["skill_usage_report.py", "--strict"])

    assert _mod.main() == 0
    out = capsys.readouterr().out
    assert "未扫" in out, out
    assert "HOLLOW" not in out, out


def test_json_path_missing_strong_source_reports_zero_hollow(monkeypatch, tmp_path, capsys):
    """#2977：--json 与表格同判——缺强信号源时 hollow=0（即使 skill 已超观察窗）。

    修前 json 分支漏传 strong_source_present，默认 True ⇒ hollow=1，探针 textfile
    与裸 Hollow 告警恒红，与表格路径「不判洞」相反。
    """
    import json

    # birth 极早 ⇒ age_days 远超 persistent 窗口；强源缺席时仍不得判洞。
    _patch_world(monkeypatch, tmp_path, [_SKILL], False, True)
    monkeypatch.setattr(sys, "argv", ["skill_usage_report.py", "--json", "--strict"])

    assert _mod.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["strong_source_present"] is False
    assert payload["hollow"] == 0
    assert all(not row["hollow"] for row in payload["skills"])


@pytest.mark.parametrize("strict,expected", [(True, 1), (False, 0)])
def test_strict_exit_contract(monkeypatch, tmp_path, capsys, strict, expected):
    args = ["skill_usage_report.py"] + (["--strict"] if strict else [])
    _patch_world(monkeypatch, tmp_path, [_SKILL], True, True)
    monkeypatch.setattr(sys, "argv", args)
    assert _mod.main() == expected
