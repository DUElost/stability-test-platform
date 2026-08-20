# -*- coding: utf-8 -*-
"""MTBF runtask validate API tests（P0，docs/operations/mtbf-api.md §1）。"""

from __future__ import annotations

import json
from pathlib import Path

from backend.services.mtbf_suite import parse_runtask

_FIXTURES = Path(__file__).resolve().parents[2] / "agent" / "tests" / "fixtures" / "mtbf"
_REAL_RUNTASK = (_FIXTURES / "runtask.xml").read_bytes()
_REAL_GLOBAL = (_FIXTURES / "ui_automator_test_data.xml").read_bytes()


def _post_files(client, headers, *, runtask: bytes, global_: bytes | None = None):
    files = {"file": ("runtask.xml", runtask, "text/xml")}
    if global_ is not None:
        files["global"] = ("UiAutomatorTestData.xml", global_, "text/xml")
    return client.post("/api/v1/mtbf/runtask/validate", headers=headers, files=files)


class TestAuth:
    def test_unauthenticated_returns_401(self, client):
        resp = client.post("/api/v1/mtbf/runtask/validate", files={"file": ("x.xml", b"<x/>", "text/xml")})
        assert resp.status_code == 401


class TestMultipart:
    def test_real_runtask_valid_with_preview(self, client, auth_headers):
        resp = _post_files(client, auth_headers, runtask=_REAL_RUNTASK)
        assert resp.status_code == 200
        body = resp.json()["data"]
        assert body["valid"] is True
        assert len(body["preview"]["testpoints"]) == 130
        assert body["preview"]["global_refs"] == ["gWifiName", "gWifiPwd"]
        assert body["preview"]["root_config"]["times"] == "1000"
        codes = {i["code"] for i in body["issues"]}
        assert "GLOBAL_REF_CUSTOM" in codes

    def test_with_global_file_infers_sim_keys(self, client, auth_headers):
        resp = _post_files(client, auth_headers, runtask=_REAL_RUNTASK, global_=_REAL_GLOBAL)
        assert resp.status_code == 200
        msgs = [i["message"] for i in resp.json()["data"]["issues"] if i["code"] == "GLOBAL_REF_CUSTOM"]
        assert any("'wifiName'" in m for m in msgs)
        assert any("'wifiPWD'" in m for m in msgs)

    def test_bad_xml_returns_valid_false(self, client, auth_headers):
        resp = _post_files(client, auth_headers, runtask=b"<runtask><broken")
        assert resp.status_code == 200
        body = resp.json()["data"]
        assert body["valid"] is False
        assert body["preview"] is None
        assert body["issues"][0]["code"] == "XML_PARSE_ERROR"

    def test_missing_input_returns_422(self, client, auth_headers):
        resp = client.post("/api/v1/mtbf/runtask/validate", headers=auth_headers)
        assert resp.status_code == 422
        assert resp.json()["detail"]["code"] == "MISSING_INPUT"


class TestJsonPathMode:
    def test_path_mode(self, client, auth_headers, tmp_path):
        target = tmp_path / "runtask.xml"
        target.write_bytes(_REAL_RUNTASK)
        resp = client.post(
            "/api/v1/mtbf/runtask/validate",
            headers={**auth_headers, "Content-Type": "application/json"},
            content=json.dumps({"path": str(target)}),
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["valid"] is True

    def test_path_mode_picks_sibling_global(self, client, auth_headers, tmp_path):
        (tmp_path / "UiAutomatorTestData.xml").write_bytes(_REAL_GLOBAL)
        target = tmp_path / "runtask.xml"
        target.write_bytes(_REAL_RUNTASK)
        resp = client.post(
            "/api/v1/mtbf/runtask/validate",
            headers={**auth_headers, "Content-Type": "application/json"},
            content=json.dumps({"path": str(target)}),
        )
        msgs = [i["message"] for i in resp.json()["data"]["issues"] if i["code"] == "GLOBAL_REF_CUSTOM"]
        assert any("'wifiName'" in m for m in msgs)

    def test_unreadable_path_returns_400(self, client, auth_headers):
        resp = client.post(
            "/api/v1/mtbf/runtask/validate",
            headers={**auth_headers, "Content-Type": "application/json"},
            content=json.dumps({"path": "/nonexistent/mtbf/runtask.xml"}),
        )
        assert resp.status_code == 400
        assert resp.json()["detail"]["code"] == "PATH_UNREADABLE"


class TestPreviewConsistency:
    def test_preview_matches_parser(self, client, auth_headers):
        """API preview 与解析器输出一致（同源规则）。"""
        resp = _post_files(client, auth_headers, runtask=_REAL_RUNTASK)
        suite = parse_runtask(_REAL_RUNTASK)
        preview = resp.json()["data"]["preview"]
        assert preview["testpoints"][0]["name"] == suite.testpoints[0].name
        assert preview["testpoints"][0]["exec_descs"][0]["method"] == suite.testpoints[0].exec_descs[0].method
