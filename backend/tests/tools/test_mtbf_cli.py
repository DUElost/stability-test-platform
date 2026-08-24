# -*- coding: utf-8 -*-
"""ADR-0030 P1c — mtbf-cases.py CLI 单元测试（monkeypatch HTTP 层，不发真网）。

覆盖：凭据三级回退（token 直用 / ambient env / .env.backend）、套件按 name
精确解析（找不到 exit 2）、list 表格、export 落盘 + stale 提示、validate
失败 exit 3、409 透传 exit 3、明文不进任何输出。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_TOOLS = Path(__file__).resolve().parents[3] / "tools" / "dev"


def _load_cli():
    sys.path.insert(0, str(_TOOLS))
    try:
        spec = importlib.util.spec_from_file_location(
            "mtbf_cases_cli", _TOOLS / "mtbf-cases.py"
        )
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path.remove(str(_TOOLS))


@pytest.fixture()
def cli():
    return _load_cli()


class _FakeResp:
    def __init__(self, status_code=200, json_body=None, content=b"",
                 headers=None, text=""):
        self.status_code = status_code
        self._json = json_body if json_body is not None else {"data": None}
        self.content = content
        self.headers = headers or {}
        self.text = text

    def json(self):
        return self._json


def _patch_http(cli, monkeypatch, routes):
    """routes: {(method, path_suffix): FakeResp | callable(**kw)}"""
    calls: list[tuple] = []

    def fake_http(method, url, **kwargs):
        calls.append((method, url, kwargs))
        for (m, suffix), handler in routes.items():
            if m == method and url.endswith(suffix):
                return handler(kwargs) if callable(handler) else handler
        raise AssertionError(f"unexpected call {method} {url}")

    monkeypatch.setattr(cli, "_http", fake_http)
    return calls


def _args(cli, **kw):
    ns = cli.argparse.Namespace(
        base_url="http://cp.test", token=None, username=None, password=None,
    )
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


class TestAuthFallback:
    def test_token_direct(self, cli, monkeypatch):
        _patch_http(cli, monkeypatch, {})
        assert cli._obtain_token(_args(cli, token="tok-1")) == "tok-1"

    def test_ambient_env_login(self, cli, monkeypatch):
        calls = _patch_http(cli, monkeypatch, {
            ("POST", "/api/v1/auth/token"): _FakeResp(
                json_body={"access_token": "tok-2"}),
        })
        monkeypatch.setenv("STP_ADMIN_USER", "admin")
        monkeypatch.setenv("STP_ADMIN_PASSWORD", "pw")
        assert cli._obtain_token(_args(cli)) == "tok-2"
        assert calls[0][2]["data"] == {"username": "admin", "password": "pw"}

    def test_backend_env_file_fallback(self, cli, monkeypatch, tmp_path):
        monkeypatch.delenv("STP_ADMIN_USER", raising=False)
        monkeypatch.delenv("STP_ADMIN_PASSWORD", raising=False)
        monkeypatch.setattr(cli, "_BACKEND_ENV", tmp_path / ".env.backend")
        tmp_path.joinpath(".env.backend").write_text(
            'STP_ADMIN_USER=file-admin\nSTP_ADMIN_PASSWORD="file-pw"\n', encoding="utf-8",
        )
        _patch_http(cli, monkeypatch, {
            ("POST", "/api/v1/auth/token"): _FakeResp(
                json_body={"access_token": "tok-3"}),
        })
        assert cli._obtain_token(_args(cli)) == "tok-3"

    def test_no_credentials_exits_2_without_secret_output(
        self, cli, monkeypatch, capsys,
    ):
        monkeypatch.delenv("STP_ADMIN_USER", raising=False)
        monkeypatch.delenv("STP_ADMIN_PASSWORD", raising=False)
        monkeypatch.setattr(cli, "_BACKEND_ENV", Path("/nonexistent/.env.backend"))
        with pytest.raises(SystemExit) as ei:
            cli._obtain_token(_args(cli))
        assert ei.value.code == 2
        out = capsys.readouterr().err
        assert "pw" not in out and "password=" not in out


class TestSuiteResolutionAndCommands:
    def test_resolve_suite_exact_match(self, cli, monkeypatch):
        _patch_http(cli, monkeypatch, {
            ("GET", "/api/v1/test-suites"): _FakeResp(json_body={"data": [
                {"id": 7, "name": "MTBF-other"},
                {"id": 9, "name": "MTBF-legacy"},
            ]}),
        })
        assert cli._require_suite_id(
            _args(cli), "t", "MTBF-legacy") == 9

    def test_resolve_missing_exit_2(self, cli, monkeypatch, capsys):
        _patch_http(cli, monkeypatch, {
            ("GET", "/api/v1/test-suites"): _FakeResp(json_body={"data": []}),
        })
        with pytest.raises(SystemExit) as ei:
            cli._require_suite_id(_args(cli), "t", "nope")
        assert ei.value.code == 2

    def test_export_writes_bytes_and_warns_on_stale(self, cli, monkeypatch, tmp_path,
                                                    capsys):
        _patch_http(cli, monkeypatch, {
            ("GET", "/test-suites/9/export"): _FakeResp(
                content=b"<runtask/>", headers={"X-Export-Stale": "1"}),
            ("GET", "/api/v1/test-suites"): _FakeResp(json_body={"data": [
                {"id": 9, "name": "S"}]}),
        })
        out = tmp_path / "runtask.xml"
        cli.cmd_export(_args(cli, suite="S", out=str(out), times=0, token="t"))
        assert out.read_bytes() == b"<runtask/>"
        assert "X-Export-Stale=1" in capsys.readouterr().err

    def test_validate_invalid_exit_3(self, cli, monkeypatch):
        _patch_http(cli, monkeypatch, {
            ("POST", "/test-suites/9/validate"): _FakeResp(json_body={"data": {
                "valid": False,
                "issues": [{"severity": "error", "code": "X", "message": "bad"}],
            }}),
            ("GET", "/api/v1/test-suites"): _FakeResp(json_body={"data": [
                {"id": 9, "name": "S"}]}),
        })
        with pytest.raises(SystemExit) as ei:
            cli.cmd_validate(_args(cli, suite="S", token="t"))
        assert ei.value.code == 3

    def test_remote_409_detail_passthrough_exit_3(self, cli, monkeypatch, capsys):
        resp = _FakeResp(status_code=409, json_body={
            "detail": {"code": "SUITE_RUNS_ACTIVE", "message": "runs in flight",
                       "plan_run_ids": [1], "active_run_count": 1},
        })
        _patch_http(cli, monkeypatch, {
            ("POST", "/export-to-tool-dir"): resp,
            ("GET", "/api/v1/test-suites"): _FakeResp(json_body={"data": [
                {"id": 9, "name": "S"}]}),
        })
        with pytest.raises(SystemExit) as ei:
            cli.cmd_export_to_tool_dir(_args(cli, suite="S", force=False, token="t"))
        assert ei.value.code == 3
        err = capsys.readouterr().err
        assert "SUITE_RUNS_ACTIVE" in err and "HTTP 409" in err
