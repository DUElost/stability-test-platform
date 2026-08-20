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
    content_fingerprint,
    patch_runtask_times,
    preview_payload,
    render_global,
    render_runtask,
    suite_from_rows,
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


# ---------------------------------------------------------------------------
# 渲染（golden：逐字节同构，P1 设计 §7 #6）
# ---------------------------------------------------------------------------


class TestRenderGolden:
    """导出物必须与 P0 已验证的设备面输入**逐字节**相同。

    这条断言的价值不在「渲染器写对了」，而在锁死四个易漂特征：CRLF 行尾、
    `@` 的 `&#64;` 写法、根属性顺序、末尾无换行。任一项被「优化」掉，
    设备端拿到的就是一个从未在真机上跑过的文件形态。
    """

    def test_roundtrip_is_byte_identical(self, real_runtask):
        assert render_runtask(parse_runtask(real_runtask)) == real_runtask

    def test_crlf_and_no_trailing_newline(self, real_runtask):
        out = render_runtask(parse_runtask(real_runtask))
        assert b"\r\n" in out
        # 每个 LF 都必须由 CR 引导——不能混进裸 LF
        assert out.count(b"\n") == out.count(b"\r\n")
        assert out.endswith(b"</runtask>")

    def test_at_sign_stays_numeric_ref(self, real_runtask):
        out = render_runtask(parse_runtask(real_runtask))
        assert b'value="&#64;&#64;gWifiName"' in out
        assert b"@@" not in out

    def test_times_override_matches_patch_semantics(self, real_runtask):
        suite = parse_runtask(real_runtask)
        assert b'times="777"' in render_runtask(suite, times=777)
        # times<=0 = 不覆盖，与 patch_runtask_times 同语义
        assert render_runtask(suite, times=0) == real_runtask
        assert render_runtask(suite, times=-1) == real_runtask

    def test_disabled_cases_excluded_from_render(self):
        cases = [
            {"name": "a", "ordinal": 1, "times": 1, "enabled": True, "exec_descs": []},
            {"name": "b", "ordinal": 2, "times": 1, "enabled": False, "exec_descs": []},
        ]
        out = render_runtask(suite_from_rows(name="s", root_config={}, cases=cases))
        assert b'name="a"' in out
        assert b'name="b"' not in out


class TestRenderGlobal:
    def test_test_set_attrs_survive_export(self):
        """TestSet 根属性必须带出——丢了 TakeScreenshot 就是换了个设备端没见过的文件。"""
        out = render_global(
            {
                "sim": {"wifiName": "w", "wifiPWD": "p"},
                "test_set_attrs": {"name": "UiAutomatorTestData", "TakeScreenshot": "true"},
                "test_package_ref": None,
            }
        )
        assert b'<TestSet name="UiAutomatorTestData" TakeScreenshot="true">' in out
        assert b'<SIM wifiName="w" />' in out
        assert out.count(b"\n") == out.count(b"\r\n")


# ---------------------------------------------------------------------------
# 库内容指纹（结构性漂移检测，P1 设计 §2 总则）
# ---------------------------------------------------------------------------


def _cases():
    return [
        {
            "name": "case-a", "ordinal": 1, "times": 1, "enabled": True,
            "exec_descs": [{"class": "C", "method": "m", "args": {"k": "v"}}],
        },
        {
            "name": "case-b", "ordinal": 2, "times": 3, "enabled": True,
            "exec_descs": [{"class": "D", "method": "n", "args": {}}],
        },
    ]


def _fp(root=None, glob=None, cases=None):
    return content_fingerprint(
        root_config=root if root is not None else {"times": "1000"},
        global_params=glob if glob is not None else {"sim": {"wifiName": "w"}},
        cases=cases if cases is not None else _cases(),
    )


class TestContentFingerprint:
    def test_deterministic_regardless_of_key_and_row_order(self):
        """指纹不能受 dict 键序 / 行序影响——JSONB 往返后键序本就不保证。"""
        base = _fp()
        assert base == _fp(root={"times": "1000"})
        reordered = list(reversed(_cases()))
        assert base == _fp(cases=reordered)
        assert base == _fp(glob={"sim": {"wifiName": "w"}})

    @pytest.mark.parametrize(
        "mutate,label",
        [
            (lambda cs: cs + [{"name": "new", "ordinal": 3, "times": 1,
                               "enabled": True, "exec_descs": []}], "新增用例"),
            (lambda cs: cs[:1], "删除用例"),
            (lambda cs: [{**cs[0], "name": "renamed"}, cs[1]], "改名"),
            (lambda cs: [{**cs[0], "times": 99}, cs[1]], "改 times"),
            (lambda cs: [{**cs[0], "enabled": False}, cs[1]], "停用"),
            (lambda cs: [{**cs[0], "exec_descs": [{"class": "X"}]}, cs[1]], "改 exec_descs"),
            (lambda cs: [{**cs[0], "ordinal": 9}, cs[1]], "改顺序"),
        ],
    )
    def test_every_case_mutation_flips_fingerprint(self, mutate, label):
        """七条用例变更路径全部翻转——「库改了没导出」结构上不可能漏。"""
        assert _fp(cases=mutate(_cases())) != _fp(), label

    def test_suite_level_mutations_flip_fingerprint(self):
        assert _fp(root={"times": "2000"}) != _fp()
        assert _fp(glob={"sim": {"wifiName": "other"}}) != _fp()

    def test_disabled_case_still_counted(self):
        """指纹取全量（含停用）：改一条停用用例也要重导，产物子集口径不适用。"""
        cs = _cases()
        cs[1]["enabled"] = False
        disabled_base = _fp(cases=cs)
        cs2 = [dict(c) for c in cs]
        cs2[1]["times"] = 42
        assert _fp(cases=cs2) != disabled_base
