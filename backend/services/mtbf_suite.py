# -*- coding: utf-8 -*-
"""MTBF 用例集解析 / 校验 / 预览（P0）— ADR-0030 D2 / P0 设计 §5.1。

runtask.xml 解析与校验的**控制面侧唯一实现**；脚本侧 `_lib.py` 的 times patch
与其同规则，两侧以 golden 测试对齐（fixtures 同源）。纯函数、无 DB 依赖。

结构对齐设备端 `OfflineScriptManager` 消费的 runtask.xml：
``<runtask>`` 为根，``<testpoint>``（用例粒度）内 1..N 个 ``<testcase>``（执行描述）。
"""
from __future__ import annotations

import hashlib
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import List, Optional

# `@@gWifiName` / `@@gWifiPwd` 式全局变量引用（值来自 UiAutomatorTestData.xml）
_GLOBAL_REF_RE = re.compile(r"@@([A-Za-z0-9_.]+)")

# OfflineScriptManager 反编译代码（utils/m/d.java）里的固定 @@ 清单。
# 实测：这些字段在解析器里只有读没有写（SIM 属性只进 TestSet 模型），
# OSM 侧解析固定清单实际得到空串；`@@g*` 等自定义引用由测试 APK 消费。
# 因此离线校验对引用只能给 advisory warning，不能作为 error。
_OSM_FIXED_REFS = frozenset(
    {
        "localnumber1", "localnumber2", "remotenumber1",
        "devicebnumber1", "devicebnumber2",
        "EmailAccount", "EmailPassword", "GoogleAccount", "GooglePassword",
        "WifiAccount", "WifiPassword",
    }
)

# times patch：`<runtask ... times="N" ...>`，属性顺序不定
_RUNTASK_TIMES_RE = re.compile(r'(<runtask\b[^>]*\btimes=")\d+(")')


@dataclass(frozen=True)
class TestcaseExec:
    """单个 testcase 执行描述（对应 runtask.xml 的 <testcase>）。"""

    type_: str = "uiautomator2"
    apk: str = ""
    package: str = ""
    klass: str = ""      # class 为 Python 关键字，字段名用 klass
    method: str = ""
    runner: str = ""
    device: str = ""
    args: dict = field(default_factory=dict)   # arg name -> value（含 @@var 引用）
    # <testcase times="N">：生产 runtask.xml 全为 1，但往返渲染必须还原，
    # 否则导出物与设备面输入不再逐字节同构（字段置于末尾保持位置参数兼容）。
    times: int = 1


@dataclass(frozen=True)
class Testpoint:
    """用例（粒度 = testpoint，用户视角的「一条用例」）。"""

    name: str
    times: int = 1
    exec_descs: List[TestcaseExec] = field(default_factory=list)


@dataclass(frozen=True)
class RuntaskSuite:
    """解析后的整套用例集（≈ 一个 runtask.xml）。"""

    name: str
    root_config: dict
    testpoints: List[Testpoint] = field(default_factory=list)


@dataclass(frozen=True)
class Issue:
    """校验问题条目。severity: error | warning。"""

    severity: str
    code: str
    message: str
    testpoint: Optional[str] = None


@dataclass(frozen=True)
class RuntaskAnalysis:
    """analyze_runtask 的产物：valid / issues / suite / global_refs。"""

    valid: bool
    issues: List[Issue]
    suite: Optional[RuntaskSuite]
    global_refs: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------

def _int_attr(root: ET.Element, name: str, default: int = 0) -> int:
    raw = root.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def parse_runtask(content: bytes) -> RuntaskSuite:
    """解析 runtask.xml 字节内容为结构化套件。

    Raises:
        ValueError: XML 非良构或根元素不是 <runtask>。
    """
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        raise ValueError(f"XML 解析失败: {exc}") from exc
    if root.tag != "runtask":
        raise ValueError(f"根元素必须是 <runtask>，实际为 <{root.tag}>")

    root_config = dict(root.attrib)
    testpoints: List[Testpoint] = []
    for tp in root.findall("testpoint"):
        exec_descs: List[TestcaseExec] = []
        for tc in tp.findall("testcase"):
            args: dict = {}
            for arg in tc.findall("attribute/arg"):
                name = arg.get("name")
                if name:
                    args[name] = arg.get("value", "")
            exec_descs.append(
                TestcaseExec(
                    type_=tc.get("type", "uiautomator2"),
                    apk=_attr_or_empty(tc, "apk"),
                    package=_attr_or_empty(tc, "package"),
                    klass=_attr_or_empty(tc, "class"),
                    method=_attr_or_empty(tc, "method"),
                    runner=_attr_or_empty(tc, "runner"),
                    device=_attr_or_empty(tc, "device"),
                    args=args,
                    times=_int_attr(tc, "times", 1),
                )
            )
        testpoints.append(
            Testpoint(
                name=tp.get("name", ""),
                times=_int_attr(tp, "times", 1),
                exec_descs=exec_descs,
            )
        )
    return RuntaskSuite(
        name=root.get("name", ""),
        root_config=root_config,
        testpoints=testpoints,
    )


def _attr_or_empty(tc: ET.Element, tag: str) -> str:
    el = tc.find(tag)
    return el.get("name", "") if el is not None else ""


def parse_global_params(content: bytes) -> dict:
    """解析 UiAutomatorTestData.xml 的 <SIM .../> 属性合并为 {name: value}。

    ``<TestSet><SIM wifiName=".." wifiPWD=".." number=".." googleAccount=".."/></TestSet>``
    多个 SIM 元素的属性合并（后出现的同名属性覆盖前者）。
    """
    params: dict = {}
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return params
    for sim in root.findall("SIM"):
        params.update({k: v for k, v in sim.attrib.items()})
    return params


def collect_global_refs(suite: RuntaskSuite) -> List[str]:
    """收集用例参数里引用的全部 @@var 名（去重、保序）。"""
    refs: List[str] = []
    seen = set()
    for tp in suite.testpoints:
        for desc in tp.exec_descs:
            for value in desc.args.values():
                for match in _GLOBAL_REF_RE.finditer(value):
                    ref = match.group(1)
                    if ref not in seen:
                        seen.add(ref)
                        refs.append(ref)
    return refs


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------

def _validate_suite(suite: RuntaskSuite, global_params: Optional[dict]) -> List[Issue]:
    issues: List[Issue] = []

    if not suite.name:
        issues.append(Issue("warning", "SUITE_NAME_EMPTY", "runtask 缺少 name 属性"))

    seen_names = set()
    for tp in suite.testpoints:
        if not tp.name:
            issues.append(Issue("error", "TESTPOINT_NAME_EMPTY", "testpoint 缺少 name 属性"))
        elif tp.name in seen_names:
            issues.append(
                Issue(
                    "error",
                    "TESTPOINT_NAME_DUPLICATE",
                    f"testpoint 名称重复: {tp.name!r}",
                    testpoint=tp.name,
                )
            )
        seen_names.add(tp.name)

        if not tp.exec_descs:
            issues.append(
                Issue("error", "TESTPOINT_NO_TESTCASE", "testpoint 没有 testcase 执行描述", testpoint=tp.name)
            )
        for desc in tp.exec_descs:
            if not desc.klass:
                issues.append(
                    Issue("error", "TESTCASE_MISSING_CLASS", "testcase 缺少 class 属性", testpoint=tp.name)
                )
            if not desc.method:
                issues.append(
                    Issue("error", "TESTCASE_MISSING_METHOD", "testcase 缺少 method 属性", testpoint=tp.name)
                )

    # @@var 引用：advisory 检查（设备端解析语义见模块头注释，离线不可证明）
    for ref in collect_global_refs(suite):
        if ref in _OSM_FIXED_REFS:
            issues.append(
                Issue(
                    "warning",
                    "OSM_FIXED_REF_UNVERIFIED",
                    f"@@{ref} 属 OfflineScriptManager 固定清单，但反编译代码未见 SIM 属性接线"
                    "（解析结果可能为空串），若用例实际使用请核对设备端行为",
                )
            )
            continue
        candidates = [ref]
        if ref[:1] == "g" and len(ref) > 1:
            candidates.append(ref[1:])   # g 前缀约定推断（gWifiName → WifiName → wifiName）
        sim_keys = {k.lower(): k for k in (global_params or {}).keys()}
        matched = [c for c in candidates if c.lower() in sim_keys]
        if matched:
            issues.append(
                Issue(
                    "warning",
                    "GLOBAL_REF_CUSTOM",
                    f"@@{ref} 为自定义引用（测试 APK 消费，离线不可校验）；"
                    f"按 g 前缀约定大小写不敏感命中 SIM 属性 {sim_keys[matched[0].lower()]!r}，仅供参考",
                )
            )
        else:
            issues.append(
                Issue(
                    "warning",
                    "GLOBAL_REF_CUSTOM",
                    f"@@{ref} 为自定义引用（测试 APK 消费，离线不可校验）；"
                    "SIM 属性中未找到候选键，请核对 UiAutomatorTestData.xml",
                )
            )
    return issues


def analyze_runtask(content: bytes, global_params: Optional[dict] = None) -> RuntaskAnalysis:
    """解析 + 校验 + 收集全局变量引用（预览 API 的唯一入口）。

    结构级错误（XML 非良构 / 根元素不符）返回单条 fatal issue、``suite=None``。
    """
    try:
        suite = parse_runtask(content)
    except ValueError as exc:
        return RuntaskAnalysis(
            valid=False,
            issues=[Issue("error", "XML_PARSE_ERROR", str(exc))],
            suite=None,
        )
    issues = _validate_suite(suite, global_params)
    return RuntaskAnalysis(
        valid=not any(i.severity == "error" for i in issues),
        issues=issues,
        suite=suite,
        global_refs=collect_global_refs(suite),
    )


# ---------------------------------------------------------------------------
# 预览载荷（API 契约，见 docs/operations/mtbf-api.md §1）
# ---------------------------------------------------------------------------

def preview_payload(analysis: RuntaskAnalysis) -> Optional[dict]:
    """把分析结果转成 API 的 preview 字段；suite 不可解析时为 None。"""
    if analysis.suite is None:
        return None
    suite = analysis.suite
    return {
        "suite_name": suite.name,
        "root_config": suite.root_config,
        "global_refs": analysis.global_refs,
        "testpoints": [
            {
                "name": tp.name,
                "times": tp.times,
                "exec_descs": [
                    {
                        "type": d.type_,
                        "apk": d.apk,
                        "package": d.package,
                        "class": d.klass,
                        "method": d.method,
                        "runner": d.runner,
                        "args": d.args,
                    }
                    for d in tp.exec_descs
                ],
            }
            for tp in suite.testpoints
        ],
    }


# ---------------------------------------------------------------------------
# times patch（脚本侧 _lib.py 同规则；两侧 golden 测试对齐）
# ---------------------------------------------------------------------------

def patch_runtask_times(content: bytes, times: int) -> bytes:
    """把 <runtask ... times="N"> 的 times 替换为给定值。

    与脚本侧 `_lib.py:patch_runtask_times` 保持同规则；times <= 0 时原样返回
    （调用方语义：不覆盖）。
    """
    if times <= 0:
        return content
    return _RUNTASK_TIMES_RE.sub(lambda m: f"{m.group(1)}{times}{m.group(2)}", content.decode("utf-8")).encode("utf-8")


# ---------------------------------------------------------------------------
# 渲染（库 → XML 字节）— P1 设计 §6 P1a
# ---------------------------------------------------------------------------
#
# 手工字符串渲染而非 ElementTree.tostring，理由是**逐字节同构**（P1 设计 §7 #6）：
# 导出物要与 P0 已在设备上跑了多轮的输入完全一致，才能消除「设备端 OSM 对某种
# 写法敏感」的未验证假设。ET 在四处与生产文件不同，且都不可配置：
#   1. 行尾 LF（生产为 CRLF）；2. 属性顺序按 dict 序（JSONB 往返后不保序）；
#   3. `@` 不转义（生产为 &#64;）；4. XML 声明写法与末尾换行。
# 固定规范属性序 + 显式转义把这四项全部钉死，golden 判据因此收紧到**零容差**。
_XML_DECL = "<?xml version='1.0' encoding='UTF-8' ?>"
_EOL = "\r\n"

# 生产 runtask.xml 的根属性顺序（P1 设计 §1.1 同序）；未知键排在其后按名排序，
# 保证任何输入都渲染出确定字节。
_RUNTASK_ATTR_ORDER = (
    "name", "times", "testTimeOut", "takeScreenshot", "stopWhenFail",
    "taskRegressionType", "caseRegressionType", "testpointRegressionTimes",
)

# <testcase> 子元素顺序：实测 137/137 一致（device 在首，attribute 在末）。
_TESTCASE_CHILD_ORDER = ("device", "apk", "package", "class", "method", "runner")


def _esc(value) -> str:
    """XML 属性值转义。

    前四项与 ET 的 `_escape_attrib` 同集合（含 `>`，生产文件里有 3 处 &gt;）；
    末项 `@` → `&#64;` 是生产文件特征（`@@gWifiName` 存为 `&#64;&#64;gWifiName`）：
    两种写法解析等价，但保留原写法才能逐字节同构。
    """
    text = str(value)
    text = (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
    text = text.replace("\n", "&#10;").replace("\r", "&#13;").replace("\t", "&#09;")
    return text.replace("@", "&#64;")


def _render_attrs(attrs: dict, order: tuple = ()) -> str:
    """按 order 优先、其余按名排序渲染属性串（确定性，不依赖 dict 序）。"""
    known = [k for k in order if k in attrs]
    rest = sorted(k for k in attrs if k not in order)
    return "".join(f' {k}="{_esc(attrs[k])}"' for k in known + rest)


def render_runtask(suite: RuntaskSuite, times: int = 0) -> bytes:
    """渲染套件为 runtask.xml 字节（CRLF、2 空格缩进、末尾无换行）。

    Args:
        suite: 库内容构造的套件（testpoints 顺序即导出顺序）。
        times: >0 时覆盖根 `times`（派发按需覆盖；<=0 用库内默认，与
            `patch_runtask_times` 的「不覆盖」语义一致）。
    """
    root = dict(suite.root_config or {})
    if times > 0:
        root["times"] = str(times)

    lines = [_XML_DECL, f"<runtask{_render_attrs(root, _RUNTASK_ATTR_ORDER)}>"]
    for tp in suite.testpoints:
        lines.append(f'  <testpoint name="{_esc(tp.name)}" times="{int(tp.times)}">')
        for desc in tp.exec_descs:
            lines.append(
                f'    <testcase type="{_esc(desc.type_)}" times="{int(desc.times)}">'
            )
            values = {
                "device": desc.device, "apk": desc.apk, "package": desc.package,
                "class": desc.klass, "method": desc.method, "runner": desc.runner,
            }
            for tag in _TESTCASE_CHILD_ORDER:
                lines.append(f'      <{tag} name="{_esc(values[tag])}" />')
            if desc.args:
                lines.append("      <attribute>")
                for name, value in desc.args.items():
                    lines.append(
                        f'        <arg name="{_esc(name)}" value="{_esc(value)}" />'
                    )
                lines.append("      </attribute>")
            lines.append("    </testcase>")
        lines.append("  </testpoint>")
    lines.append("</runtask>")
    return _EOL.join(lines).encode("utf-8")


def render_global(global_params: Optional[dict]) -> bytes:
    """渲染 UiAutomatorTestData.xml 字节。

    `global_params` 形状见 P1 设计 §1.1：``{"sim": {...}, "test_set_attrs": {...},
    "test_package_ref": "<原文或 null>"}``。

    注意与 runtask 的不对称：``parse_global_params`` 是**有损**的（把 N 个
    ``<SIM/>`` 的属性合并成一个平铺 dict），因此本函数按「每属性一个 ``<SIM/>``」
    还原——与生产文件形态一致，但不保证对任意输入逐字节往返。TestSet 根属性
    与 TestPackage 参考块原样带出，避免导出物丢掉设备端可能依赖的
    ``TakeScreenshot`` 等声明。
    """
    params = global_params or {}
    sim = params.get("sim") or {}
    test_set_attrs = params.get("test_set_attrs") or {}
    package_ref = params.get("test_package_ref")

    lines = [_XML_DECL, f"<TestSet{_render_attrs(test_set_attrs, ('name',))}>"]
    for name in sim:
        lines.append(f'  <SIM {name}="{_esc(sim[name])}" />')
    if package_ref:
        lines.extend(str(package_ref).splitlines())
    lines.append("</TestSet>")
    return _EOL.join(lines).encode("utf-8")


# ---------------------------------------------------------------------------
# 库行 ↔ 套件转换（渲染与指纹共用唯一实现）
# ---------------------------------------------------------------------------

def exec_desc_to_dict(desc: TestcaseExec) -> dict:
    """TestcaseExec → `test_case.exec_descs` JSONB 元素（键名对齐 XML 语义）。"""
    return {
        "type": desc.type_,
        "apk": desc.apk,
        "package": desc.package,
        "class": desc.klass,
        "method": desc.method,
        "runner": desc.runner,
        "device": desc.device,
        "args": dict(desc.args),
        "times": desc.times,
    }


def exec_desc_from_dict(raw: dict) -> TestcaseExec:
    """`exec_descs` JSONB 元素 → TestcaseExec（缺省值容忍旧数据）。"""
    raw = raw or {}
    return TestcaseExec(
        type_=raw.get("type") or "uiautomator2",
        apk=raw.get("apk") or "",
        package=raw.get("package") or "",
        klass=raw.get("class") or "",
        method=raw.get("method") or "",
        runner=raw.get("runner") or "",
        device=raw.get("device") or "",
        args=dict(raw.get("args") or {}),
        times=int(raw.get("times") or 1),
    )


def suite_from_rows(
    *,
    name: str,
    root_config: Optional[dict],
    cases: List[dict],
    include_disabled: bool = False,
) -> RuntaskSuite:
    """库行构造 RuntaskSuite（导出/校验入口）。

    默认只取 `enabled` 用例并按 `ordinal` 排序——**渲染产物 = 启用用例**；
    指纹另走 `content_fingerprint`（取全量，含停用），两者刻意不同：产物只关心
    跑什么，漂移检测关心库里任何改动。
    """
    rows = [c for c in cases if include_disabled or bool(c.get("enabled", True))]
    rows.sort(key=lambda c: (int(c.get("ordinal") or 0), c.get("name") or ""))
    return RuntaskSuite(
        name=name,
        root_config=dict(root_config or {}),
        testpoints=[
            Testpoint(
                name=c.get("name") or "",
                times=int(c.get("times") or 1),
                exec_descs=[exec_desc_from_dict(d) for d in (c.get("exec_descs") or [])],
            )
            for c in rows
        ],
    )


# ---------------------------------------------------------------------------
# 库内容指纹（结构性漂移检测）— P1 设计 §2 总则 / §3.3 第 3 步
# ---------------------------------------------------------------------------
def content_fingerprint(
    *,
    root_config: Optional[dict],
    global_params: Optional[dict],
    cases: List[dict],
) -> str:
    """对库内容算规范化 sha256——「库改了没导出」由此**算出来**而非靠端点置空纪律。

    入参取**库的全量内容**（含 `enabled=false` 的用例），不是渲染产物的子集：
    这样「新增 / 删除 / 改任意字段 / 改顺序 / 停用」六条变更路径全部翻转指纹，
    将来新增任何写端点都不可能漏（P1 设计 §2 总则）。

    Args:
        cases: ``[{name, ordinal, times, enabled, exec_descs}]``；本函数内部按
            (ordinal, name) 排序，调用方无需保证顺序。
    """
    payload = {
        "root_config": root_config or {},
        "global_params": global_params or {},
        "cases": sorted(
            (
                {
                    "name": c.get("name") or "",
                    "ordinal": int(c.get("ordinal") or 0),
                    "times": int(c.get("times") or 1),
                    "enabled": bool(c.get("enabled", True)),
                    "exec_descs": c.get("exec_descs") or [],
                }
                for c in cases
            ),
            key=lambda c: (c["ordinal"], c["name"]),
        ),
    }
    canonical = json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
