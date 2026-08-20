# -*- coding: utf-8 -*-
"""backend.services.mtbf_suite 单元测试（无 DB，走快速 agent 门禁）。

golden 基准：fixtures/mtbf/runtask.xml 为真实 runtask.xml 快照
（130 testpoint / 137 testcase，源自 /mnt/automation-toolkit/.../stability_MTBF-Test/config/）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from backend.services.mtbf_suite import (
    analyze_runtask,
    collect_global_refs,
    parse_global_params,
    parse_runtask,
    patch_runtask_times,
    preview_payload,
)

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "mtbf"


@pytest.fixture(scope="module")
def real_runtask() -> bytes:
    return (_FIXTURES / "runtask.xml").read_bytes()


@pytest.fixture(scope="module")
def real_global() -> bytes:
    return (_FIXTURES / "ui_automator_test_data.xml").read_bytes()


# ---------------------------------------------------------------------------
# 解析（golden：真实文件）
# ---------------------------------------------------------------------------


class TestParseRealRuntask:
    def test_counts(self, real_runtask):
        suite = parse_runtask(real_runtask)
        assert len(suite.testpoints) == 130
        assert sum(len(tp.exec_descs) for tp in suite.testpoints) == 137
        assert sum(1 for tp in suite.testpoints if len(tp.exec_descs) > 1) == 5

    def test_root_config(self, real_runtask):
        suite = parse_runtask(real_runtask)
        assert suite.name == "模拟老化(仿RM老化+MTBF)_Trassion_2023_8_23"
        assert suite.root_config["times"] == "1000"
        assert suite.root_config["testTimeOut"] == "259200000"
        assert suite.root_config["stopWhenFail"] == "false"

    def test_first_testpoint(self, real_runtask):
        suite = parse_runtask(real_runtask)
        tp = suite.testpoints[0]
        assert tp.name == "关闭软件商店的Wian自动更新消息提醒开关"
        assert tp.times == 1
        desc = tp.exec_descs[0]
        assert desc.klass == "com.tinno.reliabilityuiautomatortest.test.cases.honor.ImitationRM_AgeingTest"
        assert desc.method == "test_Reliability0141_CloseStoreWlan"
        assert desc.apk == "ReliabilityUiautomatorTestTest.apk"
        assert desc.args == {"wifiName": "@@gWifiName", "wifiPWD": "@@gWifiPwd"}

    def test_class_spelling_preserved(self, real_runtask):
        """标识符原样保留（含疑似拼写错误），不得「修正」。"""
        suite = parse_runtask(real_runtask)
        klasses = {d.klass for tp in suite.testpoints for d in tp.exec_descs}
        assert "com.tinno.reliabilityuiautomatortest.test.cases.honor.RelaibalityOreoTestTranssion" in klasses

    def test_all_methods_nonempty(self, real_runtask):
        suite = parse_runtask(real_runtask)
        assert all(d.method for tp in suite.testpoints for d in tp.exec_descs)


class TestGlobalRefs:
    def test_parse_global_params(self, real_global):
        params = parse_global_params(real_global)
        assert params["wifiName"] == "example-wifi"
        assert params["wifiPWD"] == "example-pass"
        assert params["number"] == "13800000000"
        assert params["googleAccount"] == "example@example.com"

    def test_collect_refs(self, real_runtask):
        refs = collect_global_refs(parse_runtask(real_runtask))
        assert refs == ["gWifiName", "gWifiPwd"]

    def test_bad_global_xml_is_empty(self):
        assert parse_global_params(b"<not-xml") == {}


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------


class TestValidate:
    def test_real_file_valid_with_advisory_warnings(self, real_runtask, real_global):
        an = analyze_runtask(real_runtask, global_params=parse_global_params(real_global))
        assert an.valid is True
        codes = {i.code for i in an.issues}
        assert "GLOBAL_REF_CUSTOM" in codes          # @@g* 由测试 APK 消费
        assert not any(i.severity == "error" for i in an.issues)

    def test_g_prefix_inference_hits_sim_keys(self, real_runtask, real_global):
        an = analyze_runtask(real_runtask, global_params=parse_global_params(real_global))
        msgs = [i.message for i in an.issues if i.code == "GLOBAL_REF_CUSTOM"]
        assert any("'wifiName'" in m for m in msgs)
        assert any("'wifiPWD'" in m for m in msgs)

    def test_duplicate_testpoint_name_is_error(self):
        content = (
            b'<runtask name="t">'
            b'<testpoint name="dup"><testcase><class name="c"/><method name="m1"/></testcase></testpoint>'
            b'<testpoint name="dup"><testcase><class name="c"/><method name="m2"/></testcase></testpoint>'
            b"</runtask>"
        )
        an = analyze_runtask(content)
        assert an.valid is False
        assert any(i.code == "TESTPOINT_NAME_DUPLICATE" for i in an.issues)

    def test_missing_class_and_method_are_errors(self):
        content = (
            b'<runtask name="t"><testpoint name="x">'
            b'<testcase/></testpoint></runtask>'
        )
        an = analyze_runtask(content)
        assert an.valid is False
        codes = {i.code for i in an.issues}
        assert "TESTCASE_MISSING_CLASS" in codes
        assert "TESTCASE_MISSING_METHOD" in codes
        assert "TESTPOINT_NO_TESTCASE" not in codes  # 有 testcase，只是属性缺失

    def test_no_testcase_is_error(self):
        content = b'<runtask name="t"><testpoint name="x"/></runtask>'
        an = analyze_runtask(content)
        assert an.valid is False
        assert any(i.code == "TESTPOINT_NO_TESTCASE" for i in an.issues)

    def test_bad_xml_single_fatal_issue(self):
        an = analyze_runtask(b"<runtask><unclosed")
        assert an.valid is False
        assert an.suite is None
        assert [i.code for i in an.issues] == ["XML_PARSE_ERROR"]

    def test_wrong_root_tag(self):
        an = analyze_runtask(b"<task name='t'><testpoint name='x'/></task>")
        assert an.valid is False
        assert [i.code for i in an.issues] == ["XML_PARSE_ERROR"]

    def test_osm_fixed_ref_warning(self):
        content = (
            b'<runtask name="t"><testpoint name="x"><testcase><class name="c"/><method name="m"/>'
            b'<attribute><arg name="a" value="@@WifiAccount"/></attribute>'
            b"</testcase></testpoint></runtask>"
        )
        an = analyze_runtask(content)
        assert an.valid is True
        assert any(i.code == "OSM_FIXED_REF_UNVERIFIED" for i in an.issues)


# ---------------------------------------------------------------------------
# times patch
# ---------------------------------------------------------------------------


class TestPatchTimes:
    def test_replace(self, real_runtask):
        patched = patch_runtask_times(real_runtask, 100)
        head = patched[:512]                       # 真实文件为 CRLF，根元素在首行之后
        assert b'times="100"' in head
        assert b'times="1000"' not in head

    def test_roundtrip_still_parses(self, real_runtask):
        patched = patch_runtask_times(real_runtask, 7)
        suite = parse_runtask(patched)
        assert suite.root_config["times"] == "7"
        assert len(suite.testpoints) == 130

    @pytest.mark.parametrize("times", [0, -1])
    def test_non_positive_keeps_original(self, real_runtask, times):
        assert patch_runtask_times(real_runtask, times) == real_runtask

    def test_times_not_first_attribute(self):
        content = b'<runtask name="t" stopWhenFail="false" times="42"><testpoint name="x"/></runtask>'
        patched = patch_runtask_times(content, 9)
        assert b'times="9"' in patched


# ---------------------------------------------------------------------------
# 预览载荷
# ---------------------------------------------------------------------------


class TestPreview:
    def test_preview_structure(self, real_runtask):
        an = analyze_runtask(real_runtask)
        pv = preview_payload(an)
        assert pv is not None
        assert pv["suite_name"].startswith("模拟老化")
        assert pv["root_config"]["times"] == "1000"
        assert pv["global_refs"] == ["gWifiName", "gWifiPwd"]
        assert len(pv["testpoints"]) == 130
        first = pv["testpoints"][0]
        assert first["name"] == "关闭软件商店的Wian自动更新消息提醒开关"
        assert first["exec_descs"][0]["class"].endswith("ImitationRM_AgeingTest")
        assert first["exec_descs"][0]["args"]["wifiName"] == "@@gWifiName"

    def test_preview_none_when_unparseable(self):
        an = analyze_runtask(b"<broken")
        assert preview_payload(an) is None
