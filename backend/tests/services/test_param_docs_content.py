"""B5-U5 首批十族参数说明内容验收（#3653 §3.3/§3.6/§4.2）。

结构合法性由 ``test_param_docs_registry.py``（U1 框架）负责；本文件只验内容边界：

1. 首批恰好十族、族名与文件名一致，且十族之外没有说明（§6 不补全其它族）；
2. 每族**登记头版本**（``tool_manifest.json`` 最新未退役条目 = 族树重建 sha 所对应的
   版本，由 ``check_script_packages``/``tool-manifest`` 门禁保证）实际读取的每个参数
   键都能在登记表查到说明；登记表也不得为族树没读的键编造说明；
3. 派单指定的其余版本按发布源码的读取面取值：那些版本没读的键必须回落（不套用后一
   版才出现的含义），读到的键必须有说明；
4. 跨版本同名异义与范围兜底（§3.3 R2）：同一路径的通用条目与范围条目并存时按版本取值；
5. 敏感标记：``monkey_setup`` 的 ``['wifi','password']`` 与 ``connect_wifi`` 的
   ``['password']`` 必须标敏感，且字面键 ``['wifi.password']`` 与嵌套路径不相等；
6. 无参数族（aee_prepare）不给空说明，只留逐版本无参数证据。

版本→发布源码的可追溯性（每个 (族, 版本) 的包内容按 ``package_sha256`` 复核）记录在
``docs/notes/feature/2026-10-10-b5-u5.md``，本测试不依赖 git 历史或生产库。
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from backend.services.param_docs import (
    load_param_docs,
    resolve_doc,
    sensitive_registry_paths,
    validate_param_doc_document,
)
from backend.services.parameter_redaction import union_sensitive_paths
from tools.dev.source_anchor import SourceGuard

REPO_ROOT = Path(__file__).resolve().parents[3]
DOCS_DIR = REPO_ROOT / "backend" / "schemas" / "param_docs"
SCRIPTS_ROOT = REPO_ROOT / "backend" / "agent" / "scripts"
MANIFEST = json.loads((REPO_ROOT / "tool_manifest.json").read_text(encoding="utf-8"))

#: #3653 §3.6 首批十族与派单指定版本（引用量降序前八族 + D4 安全覆盖两族）。
FIRST_BATCH: dict[str, list[str]] = {
    "check_device": ["1.0.0", "1.0.2"],
    "ensure_root": ["1.0.0", "1.0.2"],
    "aee_prepare": ["1.0.0", "1.0.1"],
    "unisoc_probe": ["1.0.1"],
    "monkey_teardown": ["1.0.2", "1.0.3"],
    "flash_firmware": ["1.3.15", "1.3.18"],
    "monkey_check": ["2.0.2", "2.0.3"],
    "monkey_launch": ["5.0.0", "5.2.0"],
    "monkey_setup": ["2.3.13"],
    "connect_wifi": ["1.0.0", "1.0.1", "1.0.2"],
}

#: 非头版本的读取面（按该版本发布源码逐字取证，见 Agent Note 的溯源表）。
#: 键为参数路径，值即「该版本确实读了这个键」。
PINNED_VERSION_READS: dict[str, dict[str, list[list[object]]]] = {
    "check_device": {
        "1.0.0": [["expect_root"]],
    },
    "ensure_root": {
        "1.0.0": [["max_attempts"], ["retry_delay_seconds"]],
    },
    "monkey_teardown": {
        "1.0.2": [
            ["process_names"], ["pull_paths"], ["clear_aee"],
            ["cleanup"], ["cleanup_paths"], ["verify_cleanup"],
        ],
    },
    "monkey_check": {
        "2.0.2": [["process_names"], ["watchdog_script"]],
    },
    "monkey_launch": {
        "5.0.0": [["need_nohup"], ["watchdog_script"], ["max_wait_seconds"]],
    },
    "flash_firmware": {
        "1.3.15": [
            ["firmware_dir"], ["da_file"], ["scatter_file"], ["command"], ["boot_mode"],
            ["timeout_seconds"], ["flash_tool_dir"], ["reboot_to_flash"], ["reboot_target"],
            ["pre_reboot_wait_seconds"], ["firmware_root"], ["version"], ["family"],
            ["model_ready_wait_seconds"], ["skip_if_current"], ["verify_version"],
            ["verify_wait_seconds"], ["boot_stabilize_seconds"], ["boot_stabilize_max_wait"],
            ["gate_other_mtk"], ["max_attempts"], ["retry_backoff_seconds"], ["strict_env_check"],
        ],
    },
    "connect_wifi": {
        "1.0.0": [["ssid"], ["password"], ["timeout_seconds"]],
        "1.0.1": [["ssid"], ["password"], ["timeout_seconds"]],
    },
}

#: 族步骤函数名 → 计划参数里承载该步骤配置的键（monkey_setup 的嵌套形态）。
_STEP_KEYS = {"steps"}


def _entry_file(family: str) -> Path:
    return DOCS_DIR / f"{family}.json"


def _head_release_version(family: str) -> str:
    """``tool_manifest.json`` 里该族最新未退役条目（族树重建 sha 必须等于它）。"""
    versions = [
        entry for entry in MANIFEST["tools"][family]["versions"]
        if not entry.get("retired")
    ]
    assert versions, f"{family} 没有未退役登记版本"
    def key(text: str) -> tuple[int, ...]:
        return tuple(int(part) for part in text.split("."))
    return max((entry["version"] for entry in versions), key=key)


def _entry_source(family: str) -> Path:
    tree = SCRIPTS_ROOT / family
    candidates = [
        path for path in sorted(tree.iterdir())
        if path.is_file() and not path.name.startswith("_")
        and path.suffix in {".py", ".sh"}
    ]
    assert candidates, f"{family} 族树没有入口文件"
    return candidates[0]


def _read_paths(family: str) -> set[tuple]:
    """从族树入口源码抽出「该版本实际读取的参数路径」。

    识别三种读取形态：``args.get(key)`` / ``cfg.get(key)``（步骤内层配置）、
    ``_params().get(key)`` 与 ``_param_or_env(args, key, env, default)``。
    ``step_<名称>`` 函数里的读取归到 ``<名称>`` 这一层（monkey_setup 的嵌套形态），
    其余归到顶层。
    """
    module = ast.parse(_entry_source(family).read_text(encoding="utf-8", errors="replace"))

    def key_of(call: ast.Call) -> str | None:
        func = call.func
        if isinstance(func, ast.Attribute) and func.attr == "get" and call.args:
            base = func.value
            name = base.id if isinstance(base, ast.Name) else None
            if name is None and isinstance(base, ast.Call) and isinstance(base.func, ast.Name):
                name = base.func.id
            if name in {"args", "cfg", "params", "_params"}:
                first = call.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    return first.value
            return None
        if isinstance(func, ast.Name) and func.id == "_param_or_env" and len(call.args) >= 2:
            second = call.args[1]
            if isinstance(second, ast.Constant) and isinstance(second.value, str):
                return second.value
        return None

    flat: set[str] = set()
    by_step: dict[str, set[str]] = {}

    def collect(node: ast.AST, step: str | None) -> None:
        for child in ast.walk(node):
            if not isinstance(child, ast.Call):
                continue
            key = key_of(child)
            if key is None:
                continue
            if step is None:
                flat.add(key)
            else:
                by_step.setdefault(step, set()).add(key)

    for top in module.body:
        if isinstance(top, ast.FunctionDef) and top.name.startswith("step_"):
            collect(top, top.name[len("step_"):])
        else:
            collect(top, None)

    paths = {key for key in flat if key not in _STEP_KEYS}
    if family == "monkey_setup":
        return {("steps",)} | {(step, key) for step, keys in by_step.items() for key in keys}
    return {(key,) for key in paths} | {(step, key) for step, keys in by_step.items() for key in keys}


def _documented_paths(family: str) -> set[tuple]:
    loaded = load_param_docs(family)
    assert loaded.errors == (), f"{family} 登记表结构不合法：{loaded.errors}"
    return {entry.path for entry in loaded.entries}


@pytest.mark.parametrize("family", sorted(FIRST_BATCH))
def test_first_batch_file_exists_and_is_valid(family: str) -> None:
    assert _entry_file(family).is_file(), f"缺少 {family}.json"
    data = json.loads(_entry_file(family).read_text(encoding="utf-8"))
    assert data["schema_version"] == 1
    assert data["script_name"] == family
    assert validate_param_doc_document(data) == []
    assert data["entries"] or family == "aee_prepare"


def test_first_batch_is_exactly_the_ten_families() -> None:
    """§6：首批之外的族不补说明；登记表面不得悄悄扩族。"""
    on_disk = {path.stem for path in DOCS_DIR.glob("*.json")}
    assert on_disk == set(FIRST_BATCH)


@pytest.mark.parametrize("family", sorted(FIRST_BATCH))
def test_pinned_versions_are_registered_release_units(family: str) -> None:
    registered = {entry["version"] for entry in MANIFEST["tools"][family]["versions"]}
    for version in FIRST_BATCH[family]:
        assert version in registered, f"{family} {version} 不在 tool_manifest.json"


@pytest.mark.parametrize("family", sorted(FIRST_BATCH))
def test_head_version_documents_every_key_the_script_reads(family: str) -> None:
    """头版本（族树重建 sha 对应的登记版本）读取的每个键都要有说明。"""
    head = _head_release_version(family)
    read_paths = _read_paths(family)
    documented = _documented_paths(family)
    missing = sorted(str(list(path)) for path in read_paths - documented)
    assert not missing, f"{family}@{head} 有未登记的参数键：{missing}"


@pytest.mark.parametrize("family", sorted(FIRST_BATCH))
def test_head_version_has_no_invented_entries(family: str) -> None:
    """登记表也不得出现族树根本没读的顶层键（不制造空说明）。"""
    if family == "aee_prepare":
        pytest.skip("无参数族由 test_aee_prepare_has_no_params 专门覆盖")
    documented = _documented_paths(family)
    read_paths = _read_paths(family)
    invented = sorted(str(list(path)) for path in documented - read_paths)
    assert not invented, f"{family} 登记了源码不读取的键：{invented}"


def test_aee_prepare_is_a_no_param_family() -> None:
    """§3.6：无参数族给逐版本无参数证据，不给空说明。

    证据：aee_prepare 1.0.0 与 1.0.1 的发布源码都只 import
    ``adb_path/device_serial/output_result``，不取 ``params()``，也不引用
    ``STP_STEP_PARAMS``（1.0.1 相对 1.0.0 只改了恢复开发者设置的时机与
    adbd 就绪轮询，见 ``#816``）。头版本即 1.0.1，故此处对族树入口的判定
    等价于对 1.0.1 发布内容的判定。
    """
    head = _head_release_version("aee_prepare")
    assert head == "1.0.1"
    assert FIRST_BATCH["aee_prepare"] == ["1.0.0", "1.0.1"]
    guard = (
        SourceGuard.of_repo_path("backend/agent/scripts/aee_prepare/aee_prepare.py")
        .anchored("from _adb import adb_path, device_serial, output_result")
    )
    guard.assert_absent("params()", why="U5 登记该族为无参数族；取步骤参数即说明本断言已过期")
    guard.assert_absent("STP_STEP_PARAMS", why="同上：出现即该族开始消费步骤参数")
    assert _read_paths("aee_prepare") == set()
    assert load_param_docs("aee_prepare").entries == ()


@pytest.mark.parametrize("family", sorted(FIRST_BATCH))
def test_nested_key_lookup_uses_string_int_segments(family: str) -> None:
    """§3.3：path 用段数组；首批说明不声明数组位置，元素含义写在容器键上。"""
    for path in _documented_paths(family):
        assert path, f"{family} 出现空 path"
        assert not any(isinstance(segment, int) for segment in path), (
            f"{family} 的 {list(path)} 绑定了数组位置——按 R2 约定元素含义写在容器键，"
            "不随下标漂移"
        )


@pytest.mark.parametrize(
    ("family", "version"),
    [
        (family, version)
        for family, pinned in PINNED_VERSION_READS.items()
        for version in pinned
    ],
)
def test_pinned_version_read_surface_is_documented(family: str, version: str) -> None:
    """非头版本按「该版本发布源码的读取面」核对：读了就必须有说明。"""
    entries = load_param_docs(family).entries
    for path in PINNED_VERSION_READS[family][version]:
        resolved = resolve_doc(entries, path, version, None)
        assert resolved.from_registry, f"{family}@{version} 的 {path} 查不到说明"
        assert resolved.label and resolved.meaning


@pytest.mark.parametrize(
    ("family", "version", "undocumented"),
    [
        ("check_device", "1.0.0", ["max_attempts", "wait_ready_seconds", "total_budget_seconds", "retry_delay_seconds"]),
        ("ensure_root", "1.0.0", ["wait_ready_seconds", "total_budget_seconds"]),
        ("monkey_teardown", "1.0.0", ["cleanup_paths", "verify_cleanup"]),
        ("flash_firmware", "1.3.15", ["nonexistent_key"]),
        ("monkey_setup", "2.3.13", ["att_clean"]),
    ],
)
def test_keys_not_read_by_a_version_do_not_get_a_doc(family: str, version: str, undocumented: list) -> None:
    """该版本没读的键必须回落 schema/键名，不能套用后来才出现的含义。"""
    entries = load_param_docs(family).entries
    for key in undocumented:
        resolved = resolve_doc(entries, [key], version, None)
        assert not resolved.from_registry, f"{family}@{version} 不应命中 {key} 的登记说明"
        assert resolved.label == key


def test_wifi_budget_keys_arrive_only_with_the_retry_rewrite() -> None:
    """跨版本变化：1.0.2 的「撞峰吸收」四键不得回落到 1.0.0/1.0.1。"""
    entries = load_param_docs("check_device").entries
    for version in ("1.0.0", "1.0.1"):
        for key in ("max_attempts", "wait_ready_seconds", "total_budget_seconds", "retry_delay_seconds"):
            assert not resolve_doc(entries, [key], version, None).from_registry
    assert resolve_doc(entries, ["total_budget_seconds"], "1.0.2", None).from_registry
    assert resolve_doc(entries, ["total_budget_seconds"], "1.1.0", None).from_registry


def test_same_key_different_meaning_across_versions() -> None:
    """§3.3 R2 反例：同键跨版本含义变化时按版本取对应条目。"""
    flash = load_param_docs("flash_firmware").entries
    old = resolve_doc(flash, ["pre_reboot_wait_seconds"], "1.3.2", None)
    new = resolve_doc(flash, ["pre_reboot_wait_seconds"], "1.3.15", None)
    assert old.from_registry and new.from_registry
    assert old.meaning != new.meaning
    assert "reboot 之后" in old.meaning and "reboot 之前" in new.meaning

    setup = load_param_docs("monkey_setup").entries
    before = resolve_doc(setup, ["wifi", "ssid"], "2.3.12", None)
    after = resolve_doc(setup, ["wifi", "ssid"], "2.3.13", None)
    assert before.from_registry and after.from_registry
    assert "转义" in after.meaning and "转义" not in before.meaning


def test_range_misfit_falls_back_to_universal_entry() -> None:
    """通用与一条范围并存是合法兜底：范围不命中时用通用条目。"""
    entries = load_param_docs("monkey_setup").entries
    hit = resolve_doc(entries, ["clean", "log_dirs"], "2.3.13", None)
    miss = resolve_doc(entries, ["clean", "log_dirs"], "2.3.10", None)
    assert hit.from_registry and miss.from_registry
    assert hit.meaning != miss.meaning
    assert hit.cautions and "非法项" in hit.cautions
    assert "非法项" not in (miss.cautions or "")
    assert "每一项都是 /data/ 或 /sdcard/ 下的绝对路径" in hit.meaning
    assert "每一项" not in miss.meaning


def test_sensitive_paths_and_literal_key_are_not_equivalent() -> None:
    """D4/§3.6：monkey_setup 的 wifi.password 与 connect_wifi 的 password 必须标敏感。"""
    setup_paths = sensitive_registry_paths(load_param_docs("monkey_setup").entries)
    assert ("wifi", "password") in setup_paths
    assert ("wifi.password",) not in setup_paths
    assert ("wifi", "ssid") not in setup_paths

    wifi_paths = sensitive_registry_paths(load_param_docs("connect_wifi").entries)
    assert ("password",) in wifi_paths

    # 版本不匹配也不取消掩码：并集按路径取值。
    assert ("wifi", "password") in union_sensitive_paths("monkey_setup", setup_paths)
    assert ("password",) in union_sensitive_paths("connect_wifi", wifi_paths)


def test_only_credential_paths_are_marked_sensitive() -> None:
    """首批里只有口令类路径标敏感，避免把普通参数掩成不可确认。"""
    sensitive = {
        (family, path)
        for family in FIRST_BATCH
        for path in sensitive_registry_paths(load_param_docs(family).entries)
    }
    assert sensitive == {
        ("monkey_setup", ("wifi", "password")),
        ("connect_wifi", ("password",)),
    }


#: #3675 逐族内容复核（R1–R7）确认的语义钉：说明文字必须包含的事实与不得回潮的错误说法。
#: 每条都对应发布源码里的实际动作，复核者可在同一位点复验；它不是结构校验的替代。
_SEMANTIC_PINS: list[tuple[str, list, str, tuple[str, ...], tuple[str, ...]]] = [
    # R1：读了键不等于旋钮生效。
    ("monkey_setup", ["fill", "timeout_seconds"], "2.3.13",
     ("没有使用这个入参", "不控制填盘耗时"), ("有效内层等待上限", "填盘的等待上限")),
    ("monkey_setup", ["fill", "block_size_kb"], "2.3.13",
     ("取整",), ("每次 dd",)),
    ("monkey_setup", ["push", "bundle"], "2.3.13",
     ("归档文件",), ("资源目录", "主机侧目录路径")),
    ("monkey_setup", ["install", "apk_path"], "2.3.13",
     ("APK **文件**路径",), ("或目录",)),
    ("monkey_setup", ["install", "pkg_name"], "2.3.13",
     ("安装前",), ("安装后要核对", "安装后核对")),
    ("monkey_setup", ["install", "required_version"], "2.3.13",
     ("子串命中",), ("完全一致",)),
    ("monkey_setup", ["wifi", "ssid"], "2.3.13",
     ("转义", "不做连接后复验"), ("填了却连不上仍是硬失败",)),
    # R2：命令级超时、固定复验窗口、1.0.2 谓词、注入写进步骤参数。
    ("connect_wifi", ["timeout_seconds"], "1.0.2",
     ("命令级", "固定 10 秒"), ("连接等待上限",)),
    ("connect_wifi", ["ssid"], "1.0.2",
     ("裸 token",), ("按分隔符切开",)),
    ("connect_wifi", ["ssid"], "1.0.0",
     ("步骤参数",), ("派发期注入的 STP_WIFI_SSID",)),
    ("connect_wifi", ["password"], "1.0.2",
     ("步骤参数",), ("派发期注入的 STP_WIFI_PASSWORD",)),
    # R3：false 仍后台启动；参数只改变匹配；旧版本轮询不打戳。
    ("monkey_launch", ["need_nohup"], "5.0.0",
     ("两个分支最终发出的都是 nohup",), ("断开即连带结束", "前台执行")),
    ("monkey_launch", ["watchdog_script"], "5.0.0",
     ("不改变",), ("本步要启动",)),
    ("monkey_launch", ["max_wait_seconds"], "5.0.0",
     ("不打 PROGRESS 戳",), ("必须逐次打 PROGRESS 戳",)),
    # R4：不能把 2.0.3 的重启复验写成通用保证。
    ("monkey_check", ["watchdog_script"], "2.0.2",
     ("rc 为 0",), ("MonkeyWatchdog 两个",)),
    ("monkey_check", ["watchdog_script"], "2.0.3",
     ("固定",), ()),
    ("monkey_check", ["process_names"], "2.0.2",
     ("不排除 MonkeyWatchdog",), ("2.0.3 起",)),
    ("monkey_check", ["process_names"], "2.0.3",
     ("显式排除 MonkeyWatchdog",), ("不排除 MonkeyWatchdog",)),
    # R5：刷机时序、核验前提、strict 覆盖面、真实回落链名称。
    ("flash_firmware", ["reboot_to_flash"], "1.3.15",
     ("回调里才发 reboot",), ("启动刷机工具前先 adb reboot",),),
    ("flash_firmware", ["verify_version"], "1.3.18",
     ("skipped", "目标版本"), ()),
    ("flash_firmware", ["strict_env_check"], "1.3.18",
     ("全部", "推断"), ("只把 ttyACM",),),
    ("flash_firmware", ["firmware_root"], "1.3.18",
     ("STP_FLASH_FIRMWARE_ROOT",), ("同名环境变量",),),
    ("flash_firmware", ["da_file"], "1.3.18",
     ("显式指定 firmware_dir",), ()),
    # R6：回收与停测边界比清单更宽；optional 不是万能豁免。
    ("monkey_teardown", ["pull_paths"], "1.0.3",
     ("追拉", "无法区分"), ("不会被回收",)),
    ("monkey_teardown", ["process_names"], "1.0.2",
     ("固定 force-stop",), ("不会被停",)),
    # R7：设计意图不是参数安全保证。
    ("unisoc_probe", ["extra_paths"], "1.0.1",
     ("原样拼进",), ("只能增加被 ls 的路径",)),
    # 非阻塞澄清：ensure_root 不主动重启设备。
    ("ensure_root", ["max_attempts"], "1.0.2",
     ("不主动重启设备",), ("重启后再试",)),
]


@pytest.mark.parametrize(
    ("family", "path", "version", "must_contain", "must_not_contain"),
    _SEMANTIC_PINS,
    ids=[f"{f}:{'.'.join(map(str, pa))}@{v}" for f, pa, v, _, _ in _SEMANTIC_PINS],
)
def test_content_review_pins_hold(
    family: str,
    path: list,
    version: str,
    must_contain: tuple[str, ...],
    must_not_contain: tuple[str, ...],
) -> None:
    """#3675 内容复核 R1–R7 的纠正不得回潮。

    断言对象是 ``resolve_doc`` 在该版本**实际选中**的那一条目——也就是人在界面上会读到的
    文字；不拼整份 JSON，否则范围条目与通用条目的文字会互相污染判定。
    """
    resolved = resolve_doc(load_param_docs(family).entries, path, version, None)
    assert resolved.from_registry, f"{family}@{version} 的 {path} 未命中登记表"
    prose = "\n".join(
        str(value or "")
        for value in (resolved.label, resolved.meaning, resolved.unit, resolved.cautions)
    )
    for needle in must_not_contain:
        assert needle not in prose, f"{family} {path}@{version} 回潮了错误说法 {needle!r}"
    assert any(needle in prose for needle in must_contain), (
        f"{family} {path}@{version} 的说明缺少复核确认的事实之一：{must_contain}"
    )


@pytest.mark.parametrize("family", sorted(FIRST_BATCH))
def test_documentation_carries_no_credentials(family: str) -> None:
    """§3.3：说明只写含义，不得带实际凭据或口令值。"""
    data = json.loads(_entry_file(family).read_text(encoding="utf-8"))
    prose = "\n".join(
        str(entry.get(field) or "")
        for entry in data["entries"]
        for field in ("label", "meaning", "unit", "cautions")
    )
    for needle in ('STP_WIFI_PASSWORD=', 'ssid=', 'password="', "Bearer ", "-redacted-"):
        assert needle not in prose, f"{family} 的说明文字里出现 {needle!r}"
