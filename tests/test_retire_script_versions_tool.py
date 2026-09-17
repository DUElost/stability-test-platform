"""#735 退役执行器（`tools/dev/retire_script_versions.py`）的契约测试。

只测「计划装配 + 写前校验 + 幂等/漂移/中止」这些**必须成立**的性质：
所有 HTTP 都走假客户端，测试不联网、不连生产库、不需要凭据。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location(
    "retire_script_versions", REPO_ROOT / "tools" / "dev" / "retire_script_versions.py"
)
_mod = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
sys.modules["retire_script_versions"] = _mod
_spec.loader.exec_module(_mod)

# autouse 夹具会把 _mod.ControlPlane 换成 FakeClient，要测真实登录序列必须先留一个
# 不被替换的引用——与文件里 _REAL_READ_ENV_KEY 同一手法。
_REAL_CONTROL_PLANE = _mod.ControlPlane

from backend.services.script_retirement import classify  # noqa: E402
from backend.services.script_retirement import ScriptVersionFact  # noqa: E402

TODAY = date(2026, 9, 16)


def _facts():
    return [
        ScriptVersionFact("gpu_setup", "1.0.0", True, 0, last_used_on=None),
        ScriptVersionFact("gpu_setup", "1.0.1", True, 0, last_used_on=date(2026, 8, 5)),
        ScriptVersionFact("gpu_setup", "1.0.10", True, 4, last_used_on=None),
        ScriptVersionFact("gpu_setup", "1.2.0", True, 0, last_used_on=None),
    ]


def _manifest(tmp_path: Path, items: list[dict]) -> Path:
    path = tmp_path / "retire.json"
    path.write_text(
        json.dumps({"generated_at": "x", "count": len(items), "items": items},
                   ensure_ascii=False),
        encoding="utf-8",
    )
    return path


class FakeClient:
    """记录调用的假控制面；deactivate/reactivate 会改内部状态以供写后复核。"""

    instances: list["FakeClient"] = []

    def __init__(self, base_url, env_file):
        self.base_url = base_url
        self.calls: list[str] = []
        FakeClient.instances.append(self)

    def set(self, sid, *, name, version, is_active):
        self.rows[sid] = {"id": sid, "name": name, "version": version, "is_active": is_active}

    rows: dict[int, dict] = {}

    def get_script(self, script_id):
        return self.rows.get(script_id)

    def deactivate(self, script_id):
        self.calls.append(f"delete:{script_id}")
        row = self.rows.get(script_id)
        if row is not None and row["refs_guard"]:
            return 409, json.dumps({"detail": {"code": "SCRIPT_STILL_REFERENCED"}})
        if row is not None:
            row["is_active"] = False
        return 200, "{}"

    def reactivate(self, script_id):
        self.calls.append(f"put:{script_id}")
        row = self.rows.get(script_id)
        if row is not None:
            row["is_active"] = True
        return 200, "{}"


_REAL_READ_ENV_KEY = _mod.read_env_key


@pytest.fixture(autouse=True)
def _fresh_client(monkeypatch):
    FakeClient.instances = []
    FakeClient.rows = {}
    monkeypatch.setattr(_mod, "ControlPlane", FakeClient)
    # 凭据闸在构造客户端之前——假客户端的用例需要一个能过闸的凭据源
    monkeypatch.setattr(_mod, "read_env_key", lambda path, key: "fake-credential")
    yield


def _args(tmp_path: Path, manifest: Path, **kw):
    base = dict(manifest=str(manifest), yes=True, limit=None,
                base_url="http://127.0.0.1:8000/api/v1",
                env_file=str(tmp_path / ".env.backend"), allow_remote=False)
    base.update(kw)
    return SimpleNamespace(**base)


# ---------------------------------------------------------------- plan 侧

def test_build_manifest_only_retire_rows_and_maps_ids():
    facts = _facts()
    verdicts = classify(facts, today=TODAY)
    ids = {("gpu_setup", "1.0.0"): 11, ("gpu_setup", "1.2.0"): 12}
    manifest = _mod.build_manifest(facts, verdicts=verdicts, script_ids=ids,
                                   today=TODAY, cooldown_days=60)
    assert [i["script_id"] for i in manifest["items"]] == [11]
    assert manifest["items"][0]["reason"]
    # 冷却期内的 1.0.1 与仍被引用的 1.0.10、承接面 1.2.0 都不进计划
    assert manifest["count"] == 1


def test_build_manifest_skips_rows_without_script_id():
    facts = _facts()
    verdicts = classify(facts, today=TODAY)
    manifest = _mod.build_manifest(facts, verdicts=verdicts, script_ids={},
                                   today=TODAY, cooldown_days=60)
    assert manifest["items"] == []
    # 只有 1.0.0 被判可退役（1.2.0 是承接面、1.0.1 在冷却期、1.0.10 仍被引用）
    assert manifest["skipped"] == [{"name": "gpu_setup", "version": "1.0.0", "why": "无 script_id"}]


# --------------------------------------------------------------- execute 侧

def test_execute_without_yes_is_dry_run_and_writes_nothing(tmp_path, capsys):
    manifest = _manifest(tmp_path, [{"script_id": 11, "name": "gpu_setup", "version": "1.0.0"}])
    args = _args(tmp_path, manifest, yes=False)
    assert _mod._apply(args, deactivate=True) == 0
    assert FakeClient.instances == []  # 连客户端都不该建
    assert "DRY-RUN" in capsys.readouterr().out


def test_execute_deactivates_and_verifies(tmp_path):
    manifest = _manifest(tmp_path, [{"script_id": 11, "name": "gpu_setup", "version": "1.0.0"}])
    FakeClient.rows[11] = {"id": 11, "name": "gpu_setup", "version": "1.0.0",
                           "is_active": True, "refs_guard": False}
    assert _mod._apply(_args(tmp_path, manifest), deactivate=True) == 0
    client = FakeClient.instances[0]
    assert client.calls == ["delete:11"]
    assert FakeClient.rows[11]["is_active"] is False


def test_execute_skips_drifted_row(tmp_path, capsys):
    manifest = _manifest(tmp_path, [{"script_id": 11, "name": "gpu_setup", "version": "1.0.0"}])
    FakeClient.rows[11] = {"id": 11, "name": "gpu_setup", "version": "9.9.9",
                           "is_active": True, "refs_guard": False}
    assert _mod._apply(_args(tmp_path, manifest), deactivate=True) == 1  # 漂移=非零
    assert FakeClient.instances[0].calls == []
    assert "漂移" in capsys.readouterr().out


def test_execute_treats_already_inactive_as_benign(tmp_path):
    manifest = _manifest(tmp_path, [{"script_id": 11, "name": "gpu_setup", "version": "1.0.0"}])
    FakeClient.rows[11] = {"id": 11, "name": "gpu_setup", "version": "1.0.0",
                           "is_active": False, "refs_guard": False}
    assert _mod._apply(_args(tmp_path, manifest), deactivate=True) == 0
    assert FakeClient.instances[0].calls == []


def test_execute_marks_referenced_and_exits_nonzero(tmp_path, capsys):
    manifest = _manifest(tmp_path, [{"script_id": 11, "name": "gpu_setup", "version": "1.0.0"}])
    FakeClient.rows[11] = {"id": 11, "name": "gpu_setup", "version": "1.0.0",
                           "is_active": True, "refs_guard": True}
    assert _mod._apply(_args(tmp_path, manifest), deactivate=True) == 1
    out = capsys.readouterr().out
    assert "REFERENCED" in out
    assert "SCRIPT_STILL_REFERENCED" in out or "409" in out


def test_execute_aborts_on_systemic_auth_failure(tmp_path):
    class AuthFail(FakeClient):
        def get_script(self, script_id):
            return {"id": script_id, "name": "a", "version": "1.0.0", "is_active": True}

        def deactivate(self, script_id):
            self.calls.append(f"delete:{script_id}")
            return 401, "invalid agent secret"

    _mod.ControlPlane = AuthFail
    manifest = _manifest(tmp_path, [
        {"script_id": 1, "name": "a", "version": "1.0.0"},
        {"script_id": 2, "name": "a", "version": "1.0.0"},
    ])
    assert _mod._apply(_args(tmp_path, manifest), deactivate=True) == 1
    assert AuthFail.instances[0].calls == ["delete:1"]  # 第一条失败即止


def test_reactivate_flips_back(tmp_path):
    manifest = _manifest(tmp_path, [{"script_id": 11, "name": "gpu_setup", "version": "1.0.0"}])
    FakeClient.rows[11] = {"id": 11, "name": "gpu_setup", "version": "1.0.0",
                           "is_active": False, "refs_guard": False}
    assert _mod._apply(_args(tmp_path, manifest), deactivate=False) == 0
    assert FakeClient.instances[0].calls == ["put:11"]
    assert FakeClient.rows[11]["is_active"] is True


def test_refuses_non_loopback_base_url(tmp_path):
    manifest = _manifest(tmp_path, [{"script_id": 11, "name": "a", "version": "1.0.0"}])
    args = _args(tmp_path, manifest, base_url="https://other-host.example/api/v1")
    with pytest.raises(SystemExit, match="非本机控制面"):
        _mod._apply(args, deactivate=True)
    args.allow_remote = True
    FakeClient.rows[11] = {"id": 11, "name": "a", "version": "1.0.0",
                           "is_active": True, "refs_guard": False}
    assert _mod._apply(args, deactivate=True) == 0


def test_loopback_guard_rejects_userinfo_spoofed_host():
    """审计 B（2026-09-17）：`127.0.0.1:8000` 写在 userinfo 里不是本机地址。

    旧的正则判据在这里放行，等于把 token 与批量 DELETE 发到外部主机——本函数
    所在路径正是 `execute --yes` / `reactivate` 的写入口。反证：把
    `ensure_base_url_allowed` 换回 `re.match(r"^[a-z]+://([^/:]+)")` 即红。
    """
    with pytest.raises(SystemExit, match="非本机控制面"):
        _mod.ensure_base_url_allowed(
            "http://127.0.0.1:8000@evil.example/api/v1", allow_remote=False)


def test_loopback_guard_accepts_ipv6_loopback():
    """审计 B 的反向缺陷：`http://[::1]:8000` 是合法本机地址，旧正则截成 `[` 误拒。"""
    _mod.ensure_base_url_allowed("http://[::1]:8000/api/v1", allow_remote=False)
    _mod.ensure_base_url_allowed("http://127.0.0.1:8000/api/v1", allow_remote=False)
    _mod.ensure_base_url_allowed("http://localhost:8000/api/v1", allow_remote=False)


def test_loopback_guard_rejects_non_http_scheme():
    """护栏不接受 file/无 scheme 之类——requests 的行为不可预期，直接拒。"""
    for bad in ("file:///etc/passwd", "127.0.0.1:8000/api/v1"):
        with pytest.raises(SystemExit, match="非 HTTP"):
            _mod.ensure_base_url_allowed(bad, allow_remote=False)


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _RecordingSession:
    """记录每条请求的 headers——`_login` 的缺陷正是"校验请求没带凭据"。"""

    def __init__(self, recorder, *, token_body=None, me_body=None):
        self._rec = recorder
        self._token_body = token_body if token_body is not None else {"access_token": "TOK"}
        self._me_body = me_body if me_body is not None else {"username": "u", "role": "admin"}
        self.headers = {}

    def post(self, url, **kw):
        self._rec.append(("POST", url, dict(kw.get("headers") or {})))
        return _FakeResp(self._token_body)

    def get(self, url, **kw):
        self._rec.append(("GET", url, dict(kw.get("headers") or {})))
        return _FakeResp(self._me_body)


def _env_file(tmp_path):
    f = tmp_path / ".env.backend"
    f.write_text("STP_ADMIN_USER=u\nSTP_ADMIN_PASSWORD=p\nAGENT_SECRET=s\n", encoding="utf-8")
    return f


def test_login_sends_bearer_on_the_admin_check_itself(monkeypatch, tmp_path):
    """回归（生产实跑暴露）：`/auth/me` 校验必须**在这一条请求上**带 Bearer。

    旧写法 `self._session.get("/auth/me")` 依赖 `__init__` 在 `_login()` **返回之后**才
    update 到 session 的 Authorization 头 ⇒ 校验请求实际是未认证请求（`/auth/token`
    不发 auth cookie，没有 cookie 可兜），生产上必 401，退役因此根本跑不动。此前
    `ControlPlane` 在单测里被整体打桩，这条真实调用序列从没被执行过。
    反证：把 `_login` 的 `headers={...}` 参数去掉，本测试即红。
    """
    rec = []
    monkeypatch.setattr(_mod.requests, "Session", lambda: _RecordingSession(rec))
    cp = _REAL_CONTROL_PLANE("http://127.0.0.1:8000/api/v1", _env_file(tmp_path))
    assert [m for m, _, _ in rec] == ["POST", "GET"]
    assert rec[1][0] == "GET" and rec[1][1].endswith("/auth/me")
    assert rec[1][2].get("Authorization") == "Bearer TOK", (
        f"校验请求未带 Bearer（实为未认证请求）：headers={rec[1][2]}")
    # 校验通过后，后续请求同样必须带凭据
    assert cp._session.headers.get("Authorization") == "Bearer TOK"


def test_login_rejects_missing_access_token_field(monkeypatch, tmp_path):
    """`/auth/token` 返回体不含 access_token 时必须显式报错，不能 KeyError 回溯。"""
    rec = []
    monkeypatch.setattr(
        _mod.requests, "Session",
        lambda: _RecordingSession(rec, token_body={"ok": True}))
    with pytest.raises(SystemExit, match="access_token"):
        _REAL_CONTROL_PLANE("http://127.0.0.1:8000/api/v1", _env_file(tmp_path))


def test_login_refuses_non_admin_identity(monkeypatch, tmp_path):
    """退役是写操作：身份不是 admin 就必须拒绝（护栏在 API 之前）。"""
    rec = []
    monkeypatch.setattr(
        _mod.requests, "Session",
        lambda: _RecordingSession(rec, me_body={"username": "u", "role": "operator"}))
    with pytest.raises(SystemExit, match="非 admin"):
        _REAL_CONTROL_PLANE("http://127.0.0.1:8000/api/v1", _env_file(tmp_path))


def test_manifest_schema_rejects_legacy_bare_list(tmp_path):
    """把旧的裸数组清单喂进来必须显式拒绝，不能 AttributeError 回溯。"""
    legacy = tmp_path / "legacy.json"
    legacy.write_text(json.dumps([{"script_id": 1, "name": "a", "version": "1"}]),
                      encoding="utf-8")
    with pytest.raises(SystemExit, match="形状不符"):
        _mod._apply(_args(tmp_path, legacy, yes=False), deactivate=True)


def test_manifest_row_missing_field_rejected(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"items": [{"script_id": 1, "name": "a"}]}), encoding="utf-8")
    with pytest.raises(SystemExit, match="缺字段"):
        _mod._apply(_args(tmp_path, bad, yes=True), deactivate=True)


def test_empty_manifest_does_not_build_client(tmp_path):
    manifest = _manifest(tmp_path, [])
    assert _mod._apply(_args(tmp_path, manifest, yes=True), deactivate=True) == 0
    assert FakeClient.instances == []


def test_missing_credentials_refused_before_any_request(tmp_path, monkeypatch):
    monkeypatch.setattr(_mod, "read_env_key", _REAL_READ_ENV_KEY)  # 真的读不到凭据
    calls: list[str] = []

    def _no_client(*a, **k):
        calls.append("constructed")
        return FakeClient(*a, **k)

    monkeypatch.setattr(_mod, "ControlPlane", _no_client)
    manifest = _manifest(tmp_path, [{"script_id": 11, "name": "a", "version": "1.0.0"}])
    with pytest.raises(SystemExit, match="STP_ADMIN_USER"):
        _mod._apply(_args(tmp_path, manifest, yes=True), deactivate=True)
    assert calls == []


def test_script_runs_when_invoked_by_path(tmp_path):
    """回归：以 `python tools/dev/retire_script_versions.py plan` 这种**路径形式**调用时，
    仓库根必须在 sys.path 上——#735 合入后文档给的正是这条命令，缺 bootstrap 就必炸。

    用 sqlite 内存库把 DATABASE_URL 变成可解析但无表的目标：只要求"不再死于
    ModuleNotFoundError"，不触任何真实数据库（本机可能就是生产库宿主）。
    """
    import subprocess

    script = REPO_ROOT / "tools" / "dev" / "retire_script_versions.py"
    proc = subprocess.run(
        [sys.executable, str(script), "plan"],
        cwd=str(tmp_path),
        env={**__import__("os").environ, "DATABASE_URL": "sqlite:///:memory:",
             "PYTHONPATH": ""},
        capture_output=True, text=True, timeout=120,
    )
    joined = proc.stdout + proc.stderr
    assert "No module named 'backend'" not in joined, joined[-400:]
    assert proc.returncode != 0          # 无表可查，必然失败——但必须是业务失败而非导入失败


# --------------------------------------------------------------- 凭据解析

def test_read_env_key_parses_plain_and_quoted(tmp_path):
    read = _REAL_READ_ENV_KEY  # autouse 夹具把模块属性换成了假凭据源，这里要测真实解析
    env = tmp_path / ".env.backend"
    env.write_text('A=simple\nB="quoted"\n# C=commented\nD=  spaced  \n', encoding="utf-8")
    assert read(env, "A") == "simple"
    assert read(env, "B") == "quoted"
    assert read(env, "D") == "spaced"
    assert read(env, "C") == ""      # 注释行不算
    assert read(tmp_path / "nope", "A") == ""  # 缺文件不抛


def test_credentials_never_appear_in_output(tmp_path, monkeypatch, capsys):
    """护栏：token/口令不经 stdout 泄漏（工具只打印 name/version/id/状态）。"""
    class LeakProbe(FakeClient):
        def _login_probe(self):
            pass

    monkeypatch.setattr(_mod, "read_env_key",
                        lambda path, key: "SECRET-VALUE" if key == "STP_ADMIN_PASSWORD" else "u")
    manifest = _manifest(tmp_path, [{"script_id": 11, "name": "a", "version": "1.0.0"}])
    FakeClient.rows[11] = {"id": 11, "name": "a", "version": "1.0.0",
                           "is_active": True, "refs_guard": False}
    assert _mod._apply(_args(tmp_path, manifest), deactivate=True) == 0
    assert "SECRET-VALUE" not in capsys.readouterr().out
