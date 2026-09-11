"""#794: /logs/query 多 job 分页 —— (job 序, 行号) cursor，翻页不丢段不超限。"""

from __future__ import annotations

import pytest

_TS = "2026-09-11T00:00:00Z"


def _write_console(root, jid: int, count: int, prefix: str) -> None:
    d = root / "jobs" / str(jid)
    d.mkdir(parents=True)
    lines = [f"{_TS} [INFO] [step] {prefix}-{i}\n" for i in range(count)]
    (d / "console.log").write_text("".join(lines), encoding="utf-8")


@pytest.fixture
def log_tree(tmp_path, monkeypatch):
    import backend.realtime.log_writer as lw

    monkeypatch.setattr(lw, "LOG_BASE_DIR", tmp_path)
    _write_console(tmp_path, 101, 300, "A")
    _write_console(tmp_path, 102, 300, "B")
    return tmp_path


def _page(client, auth_headers, limit: int, cursor: str | None = None):
    params: dict = {"job_ids": "101,102", "limit": limit}
    if cursor:
        params["cursor"] = cursor
    resp = client.get("/api/v1/logs/query", params=params, headers=auth_headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_pagination_covers_all_lines_without_gaps_or_over_limit(
    client, auth_headers, log_tree,
):
    """600 行 × limit 200：翻页收集必须恰好全量、不超限、不重复。"""
    seen: list[tuple[int, str]] = []
    cursor = None
    pages = 0
    while True:
        data = _page(client, auth_headers, 200, cursor)
        assert len(data["items"]) <= 200, "响应超 limit（旧实现内层 break 缺陷）"
        seen.extend((i["job_id"], i["message"]) for i in data["items"])
        pages += 1
        cursor = data["next_cursor"]
        if not cursor:
            break
        assert pages < 10, "分页未收敛"

    assert len(seen) == 600, f"丢段：只取到 {len(seen)}/600"
    assert len(set(seen)) == 600, "出现重复行"
    msgs = [m for _, m in seen]
    assert msgs[:300] == [f"A-{i}" for i in range(300)]
    assert msgs[300:] == [f"B-{i}" for i in range(300)]


def test_legacy_numeric_cursor_restarts_from_head(client, auth_headers, log_tree):
    """旧纯数字 cursor 无法表达跨文件位置——降级从头（宁可重复，不丢段）。"""
    data = _page(client, auth_headers, 200, cursor="251")
    assert data["items"][0]["message"] == "A-0"


def test_single_page_completes_without_cursor(client, auth_headers, log_tree):
    data = _page(client, auth_headers, 1000)
    assert len(data["items"]) == 600
    assert data["next_cursor"] is None
    assert data["has_more"] is False
