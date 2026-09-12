"""#1585：memory-lint 机械化体检（纯逻辑，不依赖真实 memory 目录）。"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

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
