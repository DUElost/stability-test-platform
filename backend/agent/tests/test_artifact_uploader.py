"""ArtifactUploader 单测（ADR-0018 5B2）。

覆盖面：
  1. submit → worker POST → stats.posts_ok
  2. 幂等命中（后端 created=False） → stats.posts_conflict
  3. HTTP 500 → stats.posts_failed，不抛
  4. requests 抛 ConnectionError → stats.posts_failed，不抛
  5. submit 前未 start → 静默丢
  6. 队列满 → 溢出者 stats.submits_dropped
  7. 非法 payload（空 uri / 空 type / job_id=0） → 立即丢
  8. stop(drain=True) → 等队列排空
  9. stop(drain=False) → 残余条目进入 submits_dropped
  10. configure() after start() → 拒绝
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest

from backend.agent.artifact_uploader import ArtifactUploader


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, status_code: int = 200, body: Optional[Dict[str, Any]] = None):
        self.status_code = status_code
        self._body = body or {"data": {"artifact_id": 1, "created": True}, "error": None}
        self.text = str(self._body)

    def json(self) -> Dict[str, Any]:
        return self._body


class _FakeSession:
    """最小 requests.Session 替身：记录所有 POST，返回可配置响应。"""

    def __init__(self):
        self.posts: List[Dict[str, Any]] = []
        self._responses: List[_FakeResponse] = []
        self._default = _FakeResponse(200, {"data": {"artifact_id": 1, "created": True}})
        self._exc: Optional[Exception] = None
        self._lock = threading.Lock()

    def queue_response(self, resp: _FakeResponse) -> None:
        with self._lock:
            self._responses.append(resp)

    def set_exception(self, exc: Exception) -> None:
        with self._lock:
            self._exc = exc

    def post(self, url, *, json=None, headers=None, timeout=None):
        with self._lock:
            self.posts.append({"url": url, "json": json, "headers": headers})
            if self._exc is not None:
                raise self._exc
            if self._responses:
                return self._responses.pop(0)
            return self._default


def _wait_for(cond, timeout: float = 3.0, interval: float = 0.02) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(interval)
    return cond()


@pytest.fixture(autouse=True)
def _reset_uploader():
    ArtifactUploader._reset_for_tests()
    yield
    ArtifactUploader._reset_for_tests()


@pytest.fixture
def uploader_and_session():
    sess = _FakeSession()
    u = ArtifactUploader.instance()
    u.configure(
        api_url="http://fake-api:8000",
        agent_secret="s3cr3t",
        session=sess,
    )
    u.start()
    yield u, sess
    u.stop(drain=False, timeout=0.5)


# ----------------------------------------------------------------------
# 1. happy path
# ----------------------------------------------------------------------

def test_submit_then_post_increments_posts_ok(uploader_and_session):
    u, sess = uploader_and_session
    u.submit(
        job_id=101,
        artifact_type="aee_crash",
        storage_uri="/mnt/nfs/jobs/101/AEE/1700000000_db.0.0",
        size_bytes=2048,
        checksum="a" * 64,
        source_category="AEE",
        source_path_on_device="/data/aee_exp/db.0.0",
    )
    assert _wait_for(lambda: len(sess.posts) == 1)
    assert u.stats.posts_ok == 1
    assert u.stats.submits_total == 1
    assert u.stats.submits_dropped == 0

    post = sess.posts[0]
    assert post["url"].endswith("/api/v1/agent/jobs/101/artifacts")
    assert post["headers"]["X-Agent-Secret"] == "s3cr3t"
    body = post["json"]
    assert body["storage_uri"].endswith("db.0.0")
    assert body["artifact_type"] == "aee_crash"
    assert body["size_bytes"] == 2048
    assert body["source_category"] == "AEE"


# ----------------------------------------------------------------------
# 2. 幂等（后端 created=False）
# ----------------------------------------------------------------------

def test_conflict_response_counted_separately(uploader_and_session):
    u, sess = uploader_and_session
    sess.queue_response(_FakeResponse(
        200, {"data": {"artifact_id": 42, "created": False}, "error": None},
    ))
    u.submit(job_id=1, artifact_type="bugreport", storage_uri="/x/y")
    assert _wait_for(lambda: u.stats.posts_conflict == 1)
    assert u.stats.posts_ok == 0
    assert u.stats.posts_failed == 0


# ----------------------------------------------------------------------
# 3. 后端 500
# ----------------------------------------------------------------------

def test_http_5xx_counts_as_failed_but_does_not_raise(uploader_and_session):
    u, sess = uploader_and_session
    sess.queue_response(_FakeResponse(500, {"error": "boom"}))
    u.submit(job_id=1, artifact_type="aee_crash", storage_uri="/x")
    assert _wait_for(lambda: u.stats.posts_failed == 1)
    assert u.stats.posts_ok == 0


# ----------------------------------------------------------------------
# 4. 网络异常
# ----------------------------------------------------------------------

def test_connection_error_counts_as_failed_and_swallowed(uploader_and_session):
    u, sess = uploader_and_session
    sess.set_exception(ConnectionError("refused"))
    u.submit(job_id=1, artifact_type="aee_crash", storage_uri="/x")
    assert _wait_for(lambda: u.stats.posts_failed == 1)
    # 异常必须被吞掉 —— 下一次 submit 仍可工作
    sess.set_exception(None)  # type: ignore[arg-type]


# ----------------------------------------------------------------------
# 5. submit 前未 start（仅 configure）
# ----------------------------------------------------------------------

def test_submit_before_start_is_silently_dropped():
    u = ArtifactUploader.instance()
    sess = _FakeSession()
    u.configure(api_url="http://x", session=sess)
    # 未 start
    u.submit(job_id=1, artifact_type="aee_crash", storage_uri="/x")
    assert u.stats.submits_total == 1
    assert u.stats.submits_dropped == 1
    assert len(sess.posts) == 0


# ----------------------------------------------------------------------
# 6. 队列满
# ----------------------------------------------------------------------

def test_queue_full_drops_without_blocking():
    u = ArtifactUploader.instance()
    sess = _FakeSession()

    # 让 worker 永远阻塞住，使 queue 一直满：session.post 卡在 event 上
    blocker = threading.Event()

    class _BlockingSession(_FakeSession):
        def post(self, *a, **kw):
            blocker.wait(timeout=5.0)
            return _FakeResponse()

    bsess = _BlockingSession()
    u.configure(
        api_url="http://x",
        session=bsess,
        queue_maxsize=2,
    )
    u.start()
    try:
        # 1 条被 worker 立刻拎走卡住；之后 2 条填满队列；第 4 条必须直接丢
        u.submit(job_id=1, artifact_type="aee_crash", storage_uri="/a")
        time.sleep(0.05)  # 让 worker 把首条拎走
        u.submit(job_id=1, artifact_type="aee_crash", storage_uri="/b")
        u.submit(job_id=1, artifact_type="aee_crash", storage_uri="/c")
        u.submit(job_id=1, artifact_type="aee_crash", storage_uri="/d")  # full
        # 至少 1 条因队列满而 drop
        assert u.stats.submits_dropped >= 1
        assert u.stats.submits_total == 4
    finally:
        blocker.set()
        u.stop(drain=False, timeout=1.0)


# ----------------------------------------------------------------------
# 7. 非法 payload
# ----------------------------------------------------------------------

@pytest.mark.parametrize("kwargs", [
    {"job_id": 1, "artifact_type": "", "storage_uri": "/x"},
    {"job_id": 1, "artifact_type": "aee_crash", "storage_uri": ""},
    {"job_id": 0, "artifact_type": "aee_crash", "storage_uri": "/x"},
])
def test_invalid_payload_is_dropped_locally(uploader_and_session, kwargs):
    u, sess = uploader_and_session
    u.submit(**kwargs)
    # 本地直接拒绝；worker 不接到这条 → 没有 POST
    time.sleep(0.15)
    assert u.stats.submits_dropped == 1
    assert len(sess.posts) == 0


# ----------------------------------------------------------------------
# 8. drain 等队列排空
# ----------------------------------------------------------------------

def test_stop_with_drain_waits_queue_empty():
    u = ArtifactUploader.instance()
    sess = _FakeSession()
    u.configure(api_url="http://x", session=sess)
    u.start()
    for i in range(5):
        u.submit(
            job_id=1, artifact_type="aee_crash", storage_uri=f"/f/{i}",
        )
    u.stop(drain=True, timeout=3.0)
    assert u.stats.posts_ok == 5
    assert u.stats.submits_dropped == 0


# ----------------------------------------------------------------------
# 9. 非 drain → 残余入 submits_dropped
# ----------------------------------------------------------------------

def test_stop_without_drain_drops_residual():
    u = ArtifactUploader.instance()
    blocker = threading.Event()

    class _BlockingSession(_FakeSession):
        def post(self, *a, **kw):
            blocker.wait(timeout=5.0)
            return _FakeResponse()

    sess = _BlockingSession()
    u.configure(api_url="http://x", session=sess, queue_maxsize=10)
    u.start()
    # 让首条卡住，随后多投若干填进队列
    u.submit(job_id=1, artifact_type="aee_crash", storage_uri="/a")
    time.sleep(0.05)
    for i in range(3):
        u.submit(
            job_id=1, artifact_type="aee_crash", storage_uri=f"/q{i}",
        )
    u.stop(drain=False, timeout=0.3)
    blocker.set()
    # 残余 3 条被 stop 丢
    assert u.stats.submits_dropped >= 3


# ----------------------------------------------------------------------
# 10. configure() after start() 拒绝
# ----------------------------------------------------------------------

def test_configure_after_start_raises():
    u = ArtifactUploader.instance()
    u.configure(api_url="http://x", session=_FakeSession())
    u.start()
    try:
        with pytest.raises(RuntimeError, match="configure"):
            u.configure(api_url="http://y")
    finally:
        u.stop(drain=False, timeout=0.5)


# ----------------------------------------------------------------------
# 11. start() 前未 configure → 拒绝
# ----------------------------------------------------------------------

def test_start_before_configure_raises():
    u = ArtifactUploader.instance()
    with pytest.raises(RuntimeError, match="not configured"):
        u.start()
