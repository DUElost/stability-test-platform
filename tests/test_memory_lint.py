"""#1585：memory-lint 机械化体检（纯逻辑，不依赖真实 memory 目录）。"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location(
    "memory_lint", REPO_ROOT / "tools" / "dev" / "memory_lint.py",
)
_mod = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
sys.modules["memory_lint"] = _mod
_spec.loader.exec_module(_mod)

FM = "---\nname: {name}\ndescription: desc for {name}\ntype: {type}\n---\n\nbody\n"


def _build(tmp_path: Path, entries: dict[str, dict] | None = None,
           index: str | None = None, extra_files: dict[str, str] | None = None) -> Path:
    mem = tmp_path / "memory"
    mem.mkdir(parents=True, exist_ok=True)
    entries = entries if entries is not None else {
        "user_role.md": {"name": "用户", "type": "user"},
        "feedback_a.md": {"name": "A", "type": "feedback"},
    }
    for fname, meta in entries.items():
        (mem / fname).write_text(FM.format(**meta), encoding="utf-8")
    if index is None:
        index = "# Memory Index\n\n" + "\n".join(
            f"- [{fname}]({fname})" for fname in entries
        ) + "\n"
    (mem / "MEMORY.md").write_text(index, encoding="utf-8")
    for fname, content in (extra_files or {}).items():
        (mem / fname).write_text(content, encoding="utf-8")
    return mem


def _lint(mem: Path, tmp_path: Path) -> _mod.Report:
    return _mod.lint_memory_dir(mem, repo_root=tmp_path)


def _touch_repo(tmp_path: Path, *rel: str) -> None:
    for r in rel:
        p = tmp_path / r
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x", encoding="utf-8")


def _both_harness_dirs(repo_root: Path) -> tuple[Path, Path]:
    """造出 CodeBuddy + ZCode 两份**在用**记忆目录（#2065 的多命中场景）。

    调用前必须先 monkeypatch ``Path.home``——候选表是按当时的 home 现算的。
    """
    codebuddy = next(
        (path for name, path in _mod.memory_dir_candidates(repo_root)
         if name == "codebuddy"),
        None,
    )
    assert codebuddy is not None
    codebuddy.mkdir(parents=True, exist_ok=True)
    zcode = (
        Path.home() / ".zcode" / "cli" / "memories" / "projects"
        / f"{repo_root.name}-abc123" / "memory"
    )
    zcode.mkdir(parents=True, exist_ok=True)
    return codebuddy, zcode


class TestStructure:
    def test_valid_set_passes(self, tmp_path):
        r = _lint(_build(tmp_path), tmp_path)
        assert r.errors == [] and r.warnings == []

    def test_missing_frontmatter_is_error(self, tmp_path):
        mem = _build(tmp_path, extra_files={"feedback_b.md": "no frontmatter\n"})
        (mem / "MEMORY.md").write_text(
            "- [user_role.md](user_role.md)\n- [feedback_a.md](feedback_a.md)\n"
            "- [feedback_b.md](feedback_b.md)\n", encoding="utf-8",
        )
        r = _lint(mem, tmp_path)
        assert any("feedback_b.md: 缺 frontmatter" in e for e in r.errors)

    def test_bad_type_is_error(self, tmp_path):
        r = _lint(_build(tmp_path, {
            "weird.md": {"name": "W", "type": "misc"},
        }), tmp_path)
        assert any("type=" in e for e in r.errors)

    def test_index_must_not_have_frontmatter(self, tmp_path):
        mem = _build(tmp_path, index="---\nname: idx\ntype: user\n---\n\n- a\n")
        r = _lint(mem, tmp_path)
        assert any("索引文件不应有 frontmatter" in e for e in r.errors)


class TestIndexConsistency:
    def test_dead_link_is_error(self, tmp_path):
        mem = _build(tmp_path, index="- [gone.md](gone.md)\n- [user_role.md](user_role.md)\n"
                     "- [feedback_a.md](feedback_a.md)\n")
        r = _lint(mem, tmp_path)
        assert any("死链" in e for e in r.errors)

    def test_unindexed_file_is_error(self, tmp_path):
        mem = _build(tmp_path, index="- [user_role.md](user_role.md)\n")
        r = _lint(mem, tmp_path)
        assert any("未索引文件 feedback_a.md" in e for e in r.errors)

    def test_overlong_index_line_is_error(self, tmp_path):
        long_line = "- [user_role.md](user_role.md) — " + "x" * 200
        mem = _build(tmp_path, index=long_line + "\n- [feedback_a.md](feedback_a.md)\n")
        r = _lint(mem, tmp_path)
        assert any("行宽" in e for e in r.errors)


class TestMemoryDirResolution:
    """#2065：默认目标必须命中**在用**的 harness 记忆目录，找不到即报错。"""

    def test_candidates_cover_codebuddy_and_zcode(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        (home / ".zcode" / "cli" / "memories" / "projects" / f"{tmp_path.name}-abc123" / "memory").mkdir(parents=True)
        monkeypatch.setattr(Path, "home", staticmethod(lambda: home))

        harnesses = [name for name, _ in _mod.memory_dir_candidates(tmp_path)]
        assert "codebuddy" in harnesses and "zcode" in harnesses

    def test_resolve_prefers_existing_zcode_dir(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        zcode_mem = (
            home / ".zcode" / "cli" / "memories" / "projects"
            / f"{tmp_path.name}-deadbeef" / "memory"
        )
        zcode_mem.mkdir(parents=True)
        monkeypatch.setattr(Path, "home", staticmethod(lambda: home))

        resolved = _mod.resolve_memory_dir(tmp_path)
        assert resolved is not None
        harness, path = resolved
        assert harness == "zcode" and path == zcode_mem

    def test_resolve_returns_none_when_no_candidate_exists(self, tmp_path, monkeypatch):
        """不静默回落：一个候选都不存在时返回 None，由 CLI 显式报错并退出 2。"""
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "empty-home"))
        assert _mod.resolve_memory_dir(tmp_path) is None


    def test_resolve_returns_none_when_multiple_harnesses_in_use(self, tmp_path, monkeypatch):
        """#2065：多个 harness 同时在用是常态——多命中同样不静默选第一个。"""
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "home"))
        codebuddy, zcode = _both_harness_dirs(tmp_path)
        assert codebuddy.is_dir() and zcode.is_dir()
        assert _mod.resolve_memory_dir(tmp_path) is None

    def test_resolve_with_explicit_harness_picks_that_one(self, tmp_path, monkeypatch):
        """指定 harness 后各自解析到自己的目录（不再依赖候选表顺序）。"""
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "home"))
        codebuddy, zcode = _both_harness_dirs(tmp_path)
        assert _mod.resolve_memory_dir(tmp_path, harness="codebuddy") == (
            "codebuddy", codebuddy,
        )
        assert _mod.resolve_memory_dir(tmp_path, harness="zcode") == ("zcode", zcode)

    def test_main_multi_match_exits_2_and_lists_harnesses(self, tmp_path, monkeypatch, capsys):
        """多命中时 CLI 退出 2 并列出在用目录；指定 --harness 后不再因此拒绝。"""
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "home"))
        _both_harness_dirs(tmp_path)
        assert _mod.main(["--repo-root", str(tmp_path)]) == 2
        out = capsys.readouterr().out
        assert "codebuddy" in out and "zcode" in out and "--harness" in out
        # 目录为空 → 缺索引文件，退出 1；关键是不再因多命中而退出 2
        assert _mod.main(["--repo-root", str(tmp_path), "--harness", "zcode"]) == 1


class TestPathReferences:
    def test_missing_repo_path_is_error(self, tmp_path):
        mem = _build(tmp_path, extra_files={
            "feedback_a.md": FM.format(name="A", type="feedback")
            + "见 `docs/does/not/exist.md`。\n",
        })
        r = _lint(mem, tmp_path)
        assert any("断链" in e and "docs/does/not/exist.md" in e for e in r.errors)

    def test_existing_repo_path_ok(self, tmp_path):
        _touch_repo(tmp_path, "docs/real.md")
        mem = _build(tmp_path, extra_files={
            "feedback_a.md": FM.format(name="A", type="feedback")
            + "见 `docs/real.md`。\n",
        })
        r = _lint(mem, tmp_path)
        assert not any("断链" in e for e in r.errors)

    def test_path_with_line_suffix_is_not_broken(self, tmp_path):
        """#2065：`path:123` / `path:123-456` / `path#L12` 是 AGENTS.md 推荐的引用形式。

        剥离位置后缀前，仓库里**每一条** `path:line` 都被报成断链——实测当时 288 个
        error 中 181 个属此类，真正可执行的信号被淹没（工具因此被闲置）。
        """
        _touch_repo(tmp_path, "backend/agent/main.py", "backend/core/x.py")
        mem = _build(tmp_path, extra_files={
            "feedback_a.md": FM.format(name="A", type="feedback")
            + "见 `backend/agent/main.py:741`、`backend/agent/main.py:741-760`、"
              "`backend/core/x.py#L12`。\n",
        })
        r = _lint(mem, tmp_path)
        assert not any("断链" in e for e in r.errors), r.errors

    def test_path_with_line_suffix_still_reports_missing_file(self, tmp_path):
        """剥离后仍要判存在性——真缺失的 `path:line` 必须继续报。"""
        mem = _build(tmp_path, extra_files={
            "feedback_a.md": FM.format(name="A", type="feedback")
            + "见 `docs/nope/missing.py:42`。\n",
        })
        r = _lint(mem, tmp_path)
        assert any("断链" in e and "docs/nope/missing.py" in e for e in r.errors)

    def test_brace_glob_reference_is_skipped(self, tmp_path):
        """#2065：`{a,b}` brace glob 无法做存在性判定——跳过而非报断链。"""
        mem = _build(tmp_path, extra_files={
            "feedback_a.md": FM.format(name="A", type="feedback")
            + "两处 `backend/agent/{,aee/}CLAUDE.md`。\n",
        })
        r = _lint(mem, tmp_path)
        assert not any("断链" in e for e in r.errors), r.errors

    def test_multi_location_suffix_is_not_broken(self, tmp_path):
        """#2065：位置后缀可以有**多个**位置（`, `/` `/:` 分隔），同样不是路径的一部分。

        `path:303,331` / `path:75/134/159` / `path:14,117,122-126` / `path:20/:31/:53`
        这类写法在变更审计笔记里最常见；只剥单个 `:N` 时它们仍被报成断链
        （实测该 store 的 22 条残留误报全属此类）。
        """
        _touch_repo(tmp_path, "backend/tasks/saq_worker.py", "docs/design/x.md",
                    "docs/adr/ADR-0007-x.md", "docs/adr/ADR-0015-y.md")
        mem = _build(tmp_path, extra_files={
            "feedback_a.md": FM.format(name="A", type="feedback")
            + "见 `backend/tasks/saq_worker.py:303,331`、`docs/design/x.md:75/134/159`、"
              "`backend/tasks/saq_worker.py:14,117,122-126`、"
              "`docs/adr/ADR-0007-x.md:20/:31/:53`、`docs/adr/ADR-0015-y.md:29/:75`。\n",
        })
        r = _lint(mem, tmp_path)
        assert not any("断链" in e for e in r.errors), r.errors

    def test_multi_location_suffix_still_reports_missing_file(self, tmp_path):
        """多位置后缀剥离后仍判存在性——真缺失的 `path:12,16` 必须继续报。"""
        mem = _build(tmp_path, extra_files={
            "feedback_a.md": FM.format(name="A", type="feedback")
            + "见 `docs/nope/missing.py:12,16`。\n",
        })
        r = _lint(mem, tmp_path)
        assert any("断链" in e and "docs/nope/missing.py" in e for e in r.errors)

    def test_missing_absolute_path_is_warn_only(self, tmp_path):
        mem = _build(tmp_path, extra_files={
            "feedback_a.md": FM.format(name="A", type="feedback")
            + "路径 `~/.no-such-harness-dir-xyz/`。\n",
        })
        r = _lint(mem, tmp_path)
        assert not r.errors
        assert any("绝对路径引用不存在" in w for w in r.warnings)

    def test_command_token_checks_path_part(self, tmp_path):
        _touch_repo(tmp_path, "tools/dev/real_tool.py")
        mem = _build(tmp_path, extra_files={
            "feedback_a.md": FM.format(name="A", type="feedback")
            + "跑 `python tools/dev/real_tool.py --strict` 即可。\n"
            + "坏的：`python tools/dev/gone.py`。\n",
        })
        r = _lint(mem, tmp_path)
        assert not any("real_tool.py" in e for e in r.errors)
        assert any("tools/dev/gone.py" in e for e in r.errors)

    def test_placeholders_and_bare_names_are_skipped(self, tmp_path):
        mem = _build(tmp_path, extra_files={
            "feedback_a.md": FM.format(name="A", type="feedback")
            + "占位 `docs/...`、`tools/dev/<name>.py`、裸名 `ci.yml`。\n",
        })
        r = _lint(mem, tmp_path)
        assert not r.errors and not r.warnings


class TestSuspiciousAssertions:
    def test_absolute_phrases_warn(self, tmp_path):
        mem = _build(tmp_path, extra_files={
            "feedback_a.md": FM.format(name="A", type="feedback")
            + "它不会跑，而且完全不跑；存量 **7 个**。\n",
        })
        r = _lint(mem, tmp_path)
        assert not r.errors
        assert sum("可疑表述" in w for w in r.warnings) >= 3

    def test_strict_exit_code(self, tmp_path, capsys):
        mem = _build(tmp_path, extra_files={
            "feedback_a.md": FM.format(name="A", type="feedback") + "零容忍。\n",
        })
        assert _mod.main(["--path", str(mem), "--repo-root", str(tmp_path)]) == 0
        assert _mod.main(
            ["--path", str(mem), "--repo-root", str(tmp_path), "--strict"]
        ) == 1

    def test_main_error_exit_code(self, tmp_path, capsys):
        mem = _build(tmp_path, index="- [gone.md](gone.md)\n- [user_role.md](user_role.md)\n"
                     "- [feedback_a.md](feedback_a.md)\n")
        assert _mod.main(["--path", str(mem), "--repo-root", str(tmp_path)]) == 1


def test_default_memory_dir_slug():
    p = _mod.default_memory_dir(Path("/home/debian13/stability-test-platform"))
    assert p == (
        Path.home() / ".codebuddy" / "projects"
        / "home-debian13-stability-test-platform" / "memory"
    )


# ── #2156：索引预算 + 无损压缩 ───────────────────────────────────────────────

def _long_index_line(width: int = 400) -> str:
    """造一条明显超宽的索引行（含分句，便于验证在分句边界断开）。"""
    hook = "；".join(f"第{i}段的内容描述" for i in range(1, width // 8))
    return f"- [长条目](long_entry.md) — {hook}"


def _mem_with_long_line(tmp_path: Path) -> Path:
    return _build(
        tmp_path,
        entries={
            "long_entry.md": {"name": "长条目", "type": "project"},
            "other.md": {"name": "其他", "type": "user"},
        },
        index=f"# Memory index\n{_long_index_line()}\n- [其他](other.md) — 短行\n",
    )


class TestIndexBudget:
    def test_default_thresholds_match_store_contract(self):
        """三级阈值是契约数字（记忆 store 治理）：18.0 / 19.5 / 24.4 KB。"""
        assert _mod.INDEX_BUDGET_TARGET_KB == 18.0
        assert _mod.INDEX_BUDGET_SOFT_KB == 19.5
        assert _mod.INDEX_BUDGET_HARD_KB == 24.4

    def test_counts_entries_and_bytes(self, tmp_path):
        mem = _build(tmp_path)
        b = _mod.index_budget(mem)
        assert b.entries == 2
        assert b.size_bytes == len((mem / "MEMORY.md").read_bytes())
        assert b.level == "ok"

    def test_ok_reports_budget_and_exits_0(self, tmp_path, capsys):
        mem = _build(tmp_path)
        rc = _mod.main(["--path", str(mem), "--repo-root", str(tmp_path), "--budget"])
        assert rc == 0
        assert "索引预算：" in capsys.readouterr().out

    def test_soft_trigger_exits_1(self, tmp_path, capsys):
        mem = _build(tmp_path)
        rc = _mod.main([
            "--path", str(mem), "--repo-root", str(tmp_path),
            "--budget", "--soft-kb", "0.01", "--hard-kb", "99",
        ])
        assert rc == 1
        assert "超软触发" in capsys.readouterr().out

    def test_hard_wall_message_when_over_hard(self, tmp_path, capsys):
        mem = _build(tmp_path)
        rc = _mod.main([
            "--path", str(mem), "--repo-root", str(tmp_path),
            "--budget", "--soft-kb", "0.01", "--hard-kb", "0.02",
        ])
        assert rc == 1
        out = capsys.readouterr().out
        assert "超硬墙" in out and "静默丢条目" in out


class TestIndexFix:
    def test_dry_run_does_not_write(self, tmp_path, capsys):
        mem = _mem_with_long_line(tmp_path)
        index_before = (mem / "MEMORY.md").read_text(encoding="utf-8")
        entry_before = (mem / "long_entry.md").read_text(encoding="utf-8")
        rc = _mod.main(["--path", str(mem), "--repo-root", str(tmp_path), "--fix"])
        # 行宽超限本身就是 lint error，故干跑仍 exit 1；关键是「没写盘」
        assert rc == 1
        assert "[FIX ]" in capsys.readouterr().out
        assert (mem / "MEMORY.md").read_text(encoding="utf-8") == index_before
        assert (mem / "long_entry.md").read_text(encoding="utf-8") == entry_before

    def test_apply_is_lossless_and_within_width(self, tmp_path):
        mem = _mem_with_long_line(tmp_path)
        original = _long_index_line()
        rc = _mod.main([
            "--path", str(mem), "--repo-root", str(tmp_path), "--fix", "--apply",
        ])
        assert rc == 0  # 压缩后行宽 error 消失，索引自洽
        index_text = (mem / "MEMORY.md").read_text(encoding="utf-8")
        fixed = [l for l in index_text.splitlines() if "long_entry.md" in l]
        assert len(fixed) == 1
        assert len(fixed[0]) <= _mod.INDEX_LINE_MAX_CHARS
        assert fixed[0].endswith("…")
        # 无损：原文逐字落在目标条目文件里，且带 HTML 注释与块标题
        entry = (mem / "long_entry.md").read_text(encoding="utf-8")
        assert original in entry
        assert _mod.APPENDIX_MARKER in entry
        assert "索引精简（#2156）" in entry

    def test_apply_is_idempotent(self, tmp_path, capsys):
        mem = _mem_with_long_line(tmp_path)
        argv = ["--path", str(mem), "--repo-root", str(tmp_path), "--fix", "--apply"]
        assert _mod.main(argv) == 0
        assert "改写 1 行" in capsys.readouterr().out
        entry_once = (mem / "long_entry.md").read_text(encoding="utf-8")
        assert _mod.main(argv) == 0
        assert "没有行宽 >" in capsys.readouterr().out
        assert (mem / "long_entry.md").read_text(encoding="utf-8") == entry_once

    def test_stale_plan_does_not_duplicate_appendix(self, tmp_path):
        """拿到陈旧方案（索引行已被压缩过）二次执行，也不重复追加附录块。"""
        mem = _mem_with_long_line(tmp_path)
        plans = _mod.plan_index_fixes(mem)
        _mod.apply_index_fixes(mem, plans, today="2026-09-15")
        entry_once = (mem / "long_entry.md").read_text(encoding="utf-8")
        _mod.apply_index_fixes(mem, plans, today="2026-09-15")
        assert (mem / "long_entry.md").read_text(encoding="utf-8") == entry_once

    def test_skips_when_target_file_missing(self, tmp_path, capsys):
        broken = _long_index_line().replace("long_entry.md", "gone.md")
        mem = _build(tmp_path, index=(
            "# Memory index\n"
            f"{broken}\n"
            "- [user_role.md](user_role.md) — 短行\n"
            "- [feedback_a.md](feedback_a.md) — 短行\n"
        ))
        plans = _mod.plan_index_fixes(mem)
        assert len(plans) == 1
        assert "目标文件不存在" in plans[0].skipped
        index_before = (mem / "MEMORY.md").read_text(encoding="utf-8")
        # 行宽 + 死链两个 lint error 仍在 → exit 1；重点是索引一个字节都没动
        assert _mod.main([
            "--path", str(mem), "--repo-root", str(tmp_path), "--fix", "--apply",
        ]) == 1
        assert "[SKIP] MEMORY.md:2: 目标文件不存在" in capsys.readouterr().out
        assert (mem / "MEMORY.md").read_text(encoding="utf-8") == index_before

    def test_skips_line_without_link(self, tmp_path):
        mem = _build(tmp_path, index=(
            "# Memory index\n"
            "- [user_role.md](user_role.md) — 短行\n"
            "- [feedback_a.md](feedback_a.md) — 短行\n"
            f"- 一条没有链接的散行{'x' * 200}\n"
        ))
        plans = _mod.plan_index_fixes(mem)
        assert len(plans) == 1
        assert "不是 `- [标题](file.md) — hook` 形态" in plans[0].skipped

    def test_apply_without_fix_is_usage_error(self, tmp_path):
        mem = _build(tmp_path)
        with pytest.raises(SystemExit) as exc:
            _mod.main(["--path", str(mem), "--repo-root", str(tmp_path), "--apply"])
        assert exc.value.code == 2


class TestCompressIndexLine:
    def test_prefers_clause_boundary(self):
        out, why = _mod.compress_index_line("- [t](a.md) — 第一句；第二句；第三句", width=30)
        assert why == ""
        assert out == "- [t](a.md) — 第一句…"
        assert len(out) <= 30

    def test_hard_truncates_without_separator(self):
        out, why = _mod.compress_index_line("- [t](a.md) — " + "字" * 100, width=30)
        assert why == ""
        assert len(out) == 30
        assert out.endswith("…")

    def test_rejects_non_index_line(self):
        out, why = _mod.compress_index_line("随便一行没有链接的文本", width=30)
        assert out == ""
        assert why

