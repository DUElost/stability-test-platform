# -*- coding: utf-8 -*-
"""#2131：幂等评论发布工具的单测（纯离线，注入 fake gh）。

覆盖四态（created / skipped / updated / unknown）与两类真实故障：
「发布已落地但响应丢失」与「无法判定」（fail-safe 不发布）。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "gh_comment_once", REPO_ROOT / "tools" / "dev" / "gh_comment_once.py"
)
assert _spec and _spec.loader
_mod = importlib.util.module_from_spec(_spec)
sys.modules["gh_comment_once"] = _mod  # dataclass 解析 __module__ 需要
_spec.loader.exec_module(_mod)


class FakeGh:
    """最小 fake：实现 post_once 依赖的接口（find_by_marker/attempts/backoff/...）。"""

    def __init__(
        self,
        *,
        comments: list | None = None,
        create_error: Exception | None = None,
        create_lands: bool = False,
        list_error: Exception | None = None,
        list_ok_times: int | None = None,
        attempts: int = 3,
        fail_create_times: int = 0,
    ) -> None:
        self.comments = list(comments or [])
        self.attempts = attempts
        self.create_calls: list[str] = []
        self.update_calls: list[tuple[int, str]] = []
        self.backoffs: list[int] = []
        self._create_error = create_error
        self._create_lands = create_lands
        self._list_error = list_error
        self._list_ok_times = list_ok_times      # 前 N 次 list 正常，之后报错
        self._fail_create_times = fail_create_times
        self._next_id = 1000

    # ── 接口 ──
    def backoff(self, attempt: int) -> None:
        self.backoffs.append(attempt)

    def list_comments(self) -> list:
        if self._list_ok_times is not None:
            if self._list_ok_times <= 0:
                raise self._list_error or _mod.GhUnavailable("EOF")
            self._list_ok_times -= 1
        elif self._list_error is not None:
            raise self._list_error
        return list(self.comments)

    def find_by_marker(self, marker: str):
        for c in self.list_comments():
            if _mod.extract_marker(c.body) == marker:
                return c
        return None

    def create_comment(self, body: str):
        self.create_calls.append(body)
        fail = self._fail_create_times > 0
        if fail:
            self._fail_create_times -= 1
        if fail or self._create_error is not None:
            if self._create_lands:
                # 模拟「POST 已落地、响应丢失」
                self._next_id += 1
                self.comments.append(_mod.Comment(id=self._next_id, body=body, url="u"))
            raise self._create_error or _mod.GhError("simulated transport failure")
        self._next_id += 1
        created = _mod.Comment(id=self._next_id, body=body, url=f"https://x/{self._next_id}")
        self.comments.append(created)
        return created

    def update_comment(self, comment_id: int, body: str):
        self.update_calls.append((comment_id, body))
        for i, c in enumerate(self.comments):
            if c.id == comment_id:
                updated = _mod.Comment(id=comment_id, body=body, url=c.url)
                self.comments[i] = updated
                return updated
        raise _mod.GhError("comment not found")


def _existing_marker(body: str, marker: str) -> list:
    return [_mod.Comment(id=7, body=_mod.with_marker(body, marker), url="https://x/7")]


# ── marker 纯函数 ──


def test_derive_marker_stable_and_content_sensitive():
    a = _mod.derive_marker("hello\nworld")
    assert a == _mod.derive_marker("hello\r\nworld  \n")   # 换行/尾随空白归一
    assert a != _mod.derive_marker("hello\nworld 2")
    assert len(a) == 12


def test_with_marker_is_idempotent():
    once = _mod.with_marker("body", "mk1")
    twice = _mod.with_marker(once, "mk1")
    assert once.count(_mod.MARKER_PREFIX) == 1
    assert twice.count(_mod.MARKER_PREFIX) == 1


def test_extract_marker_roundtrip():
    assert _mod.extract_marker(_mod.with_marker("b", "abc123")) == "abc123"
    assert _mod.extract_marker("no marker here") is None


def test_parse_repo_from_remote_forms():
    assert _mod.parse_repo_from_remote("https://github.com/O/R.git") == "O/R"
    assert _mod.parse_repo_from_remote("https://github.com/O/R") == "O/R"
    assert _mod.parse_repo_from_remote("git@github.com:O/R.git") == "O/R"
    assert _mod.parse_repo_from_remote("not-a-url") is None


# ── 四态 ──


def test_creates_when_absent():
    gh = FakeGh()
    out = _mod.post_once(gh, body="报告正文", marker="m1", log=lambda _m: None)
    assert out["action"] == "created"
    assert len(gh.create_calls) == 1
    assert "<!-- gh-comment-once:m1 -->" in gh.create_calls[0]


def test_skips_when_marker_present():
    gh = FakeGh(comments=_existing_marker("旧正文", "m1"))
    out = _mod.post_once(gh, body="新正文", marker="m1", log=lambda _m: None)
    assert out["action"] == "skipped"
    assert out["comment_id"] == 7
    assert gh.create_calls == []          # 关键：绝不重复发布


def test_updates_when_requested():
    gh = FakeGh(comments=_existing_marker("旧正文", "m1"))
    out = _mod.post_once(gh, body="新正文", marker="m1", update=True, log=lambda _m: None)
    assert out["action"] == "updated"
    assert gh.create_calls == []
    assert len(gh.update_calls) == 1
    cid, body = gh.update_calls[0]
    assert cid == 7 and "新正文" in body and "<!-- gh-comment-once:m1 -->" in body


# ── 故障路径 ──


def test_response_lost_but_comment_landed_is_not_reposted():
    """#735 事故形态：POST 已落地、响应丢失 → 复读发现 → 不重发。"""
    gh = FakeGh(create_error=_mod.GhError("EOF"), create_lands=True)
    out = _mod.post_once(gh, body="报告", marker="m1", log=lambda _m: None)
    assert out["action"] == "created"
    assert out.get("detected_after_error") is True
    assert len(gh.create_calls) == 1            # 只发过一次
    assert sum(1 for c in gh.comments if _mod.extract_marker(c.body) == "m1") == 1


def test_unknown_state_refuses_to_post():
    """查重不可用 → fail-safe：不发（退出码 2 由 main 兜）。"""
    gh = FakeGh(list_error=_mod.GhUnavailable("EOF"))
    out = _mod.post_once(gh, body="报告", marker="m1", log=lambda _m: None)
    assert out["action"] == "unknown"
    assert gh.create_calls == []


def test_create_error_then_unavailable_never_reposts():
    """创建失败后复读不可用 → unknown，且不得重发。"""
    gh = FakeGh(create_error=_mod.GhError("EOF"), list_ok_times=1)
    out = _mod.post_once(gh, body="报告", marker="m1", log=lambda _m: None)
    assert out["action"] == "unknown"
    assert len(gh.create_calls) == 1


def test_retries_only_after_verified_absent():
    """创建失败且复读确认不存在 → 允许退避后重试（第 2 次成功）。"""
    gh = FakeGh(fail_create_times=1)
    out = _mod.post_once(gh, body="报告", marker="m1", log=lambda _m: None)
    assert out["action"] == "created"
    assert len(gh.create_calls) == 2
    assert gh.backoffs == [1]


def test_exhausted_retries_returns_unknown():
    gh = FakeGh(fail_create_times=99)
    out = _mod.post_once(gh, body="报告", marker="m1", log=lambda _m: None)
    assert out["action"] == "unknown"
    assert len(gh.create_calls) == gh.attempts     # 每轮都先复读确认不存在
    assert len({c for c in gh.create_calls}) == 1  # 内容一致，无半成品


def test_force_posts_once_even_when_list_unavailable():
    gh = FakeGh(list_error=_mod.GhUnavailable("EOF"))
    out = _mod.post_once(gh, body="报告", marker="m1", force=True, log=lambda _m: None)
    assert out["action"] == "created"
    assert out["forced"] is True
    assert len(gh.create_calls) == 1


# ── CLI 层 ──


def test_main_exit_codes_and_json(capsys, tmp_path):
    body = tmp_path / "note.md"
    body.write_text("正文", encoding="utf-8")

    gh = FakeGh()
    rc = _mod.main(["--issue", "735", "--body-file", str(body), "--repo", "O/R", "--json"], gh=gh)
    assert rc == 0
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["action"] == "created" and payload["repo"] == "O/R" and payload["issue"] == 735

    gh2 = FakeGh(list_error=_mod.GhUnavailable("EOF"))
    rc2 = _mod.main(["--pr", "2097", "--body", "x", "--repo", "O/R", "--json"], gh=gh2)
    assert rc2 == 2                     # unknown → 非零，提示可安全重跑
    assert json.loads(capsys.readouterr().out.strip())["action"] == "unknown"


def test_main_missing_body_is_arg_error(capsys):
    rc = _mod.main(["--issue", "1", "--repo", "O/R"], gh=FakeGh())
    assert rc == 1
    assert "必须提供" in capsys.readouterr().err


def test_main_marker_override(capsys, tmp_path):
    body = tmp_path / "n.md"
    body.write_text("x", encoding="utf-8")
    gh = FakeGh(comments=_existing_marker("y", "pinned-key"))
    rc = _mod.main(["--issue", "1", "--body-file", str(body), "--repo", "O/R",
                    "--marker", "pinned-key"], gh=gh)
    assert rc == 0
    assert gh.create_calls == []


# ── GhApi 分页（打桩 subprocess，不触网）──


def test_ghapi_list_comments_paginates(monkeypatch):
    pages = [
        [{"id": i, "body": f"c{i}", "html_url": f"u{i}"} for i in range(100)],
        [{"id": 100, "body": "last", "html_url": "u100"}],
    ]
    calls: list[list[str]] = []

    class _Proc:
        def __init__(self, stdout: str) -> None:
            self.returncode = 0
            self.stdout = stdout
            self.stderr = ""

    def fake_run(args, capture_output=True, text=True, timeout=None, input=None):
        calls.append(list(args))
        return _Proc(json.dumps(pages[len(calls) - 1]))

    monkeypatch.setattr(_mod.subprocess, "run", fake_run)
    api = _mod.GhApi("O/R", 5, sleep=lambda _s: None)
    comments = api.list_comments()
    assert len(comments) == 101
    assert comments[-1].id == 100
    assert "page=2" in calls[1][-1]


def test_ghapi_list_comments_raises_unavailable_after_retries(monkeypatch):
    attempts = {"n": 0}

    class _Proc:
        returncode = 1
        stdout = ""
        stderr = "EOF"

    def fake_run(*_a, **_k):
        attempts["n"] += 1
        return _Proc()

    monkeypatch.setattr(_mod.subprocess, "run", fake_run)
    api = _mod.GhApi("O/R", 5, attempts=3, sleep=lambda _s: None)
    with pytest.raises(_mod.GhUnavailable):
        api.list_comments()
    assert attempts["n"] == 3
