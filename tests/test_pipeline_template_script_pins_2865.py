"""#2865/#2998：pipeline 模板的 version pin 必须等于「已注册且激活的最新版」。

背景：脚本执行按精确版本解析、无 latest 兜底；目录合入后若模板仍钉旧版，
新建 Plan 永远带不到修复（#2802 D0 / #2777 降级都曾因此「合入了但不生效」）。

#2998 把名单从两族扩到**模板出现的全部脚本族**——扩面依据不是偏好而是本守卫
自己的扩张条款（「再出现一次同形审计即扩」）：#2998 就是复发实例，且 git 考古
证实各钉旧值全是**引入时默认值从未跟随**（无任何"故意钉旧"的成文决定，模板
最后的 pin 变更恰是 #2865 往最新版追）。"误伤故意钉旧"的担忧由 EXCEPTIONS
承接而非全族豁免：故意钉旧必须在这里登记 (版本, 理由+删除条件) 二元组，
无理由的例外过不了本文件自己的一致性断言。

pin 上界语义（#2998 实现时暴露）：**磁盘最新版 ≠ 可 pin**。prepare 的
`_validate_script_refs` 按 script 表校验（不存在/未激活 → 422），磁盘 head
未注册时 pin 上去会让该模板新建 Plan 全灭。故对拍基准 = 磁盘 head，除非
EXCEPTIONS 登记滞后项；登记与清除由 `--pending-activation` 视图（#2931）驱动
——视图清空即说明磁盘 head 已在库且激活，例外就没有存续理由（首批三条已于
09-21 按此清除，见 `EXCEPTIONS` 注释与本文件的牙测试）。
"""

from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = REPO_ROOT / "backend/schemas/pipeline_templates"
SCRIPTS_DIR = REPO_ROOT / "backend/agent/scripts"

# 全 16 族：模板里出现过的每个 `action: script:<name>`（#2998 扩面；名单由
# `grep -h "script:" backend/schemas/pipeline_templates/*.json` 派生，新增
# 脚本族进模板时必须同步加行——漏加会被 test_template_script_actions_covered
# 抓住，见文件底部）。
PINNED_SCRIPTS: dict[str, str] = {
    "script:check_device": "check_device",
    "script:ensure_root": "ensure_root",
    "script:gpu_check": "gpu_check",
    "script:gpu_finish": "gpu_finish",
    "script:gpu_setup": "gpu_setup",
    "script:monkey_check": "monkey_check",
    "script:monkey_launch": "monkey_launch",
    "script:monkey_resource_push": "monkey_resource_push",
    "script:monkey_setup": "monkey_setup",
    "script:monkey_teardown": "monkey_teardown",
    "script:powercycle_check": "powercycle_check",
    "script:powercycle_finish": "powercycle_finish",
    "script:powercycle_setup": "powercycle_setup",
    "script:sleep_check": "sleep_check",
    "script:sleep_finish": "sleep_finish",
    "script:sleep_setup": "sleep_setup",
}

#: 故意钉旧/滞后豁免：action → (pin 版本, 理由+删除条件)。
#: 理由必须可核查（引用 issue/账），删除条件必须可达（不是"以后再说"）。
#: 09-21 #3044 已清空 ensure_root / gpu_setup / powercycle_setup@1.2.1 三条
#: （scan 收口）并把 pin 追到当时已注册 head；判据抽成 `_exception_lag_reason`，
#: 空清单下由 `test_exception_shape_predicate_has_teeth` 变异自证。
#: 09-22 清空 #2975/#2979/#2980 引入的五条（monkey_setup / powercycle_setup /
#: gpu_finish / powercycle_finish / sleep_finish）：各条写的删除条件——
#: 「部署 scan 注册激活 vX 后 pin 追平并移除」——已按 `--pending-activation`
#: 视图判据满足（输出「全部脚本族磁盘 head 版本均已在库且 active（无待激活项）」），
#: 故 pin 一并追平到磁盘 head 并移除本条。清单再次为空是本文件设计的稳态：
#: 例外只在「磁盘 head 未注册」的窗口内存在，该窗口由 scan 收口。
#:
#: 随后本 PR 的 #3107 又把 monkey_setup 磁盘 head 推到 v2.3.11（log_dirs 参数校验）
#: ——尚未 scan 注册，故重开一条例外；pin 停在已注册的 2.3.10。
EXCEPTIONS: dict[str, tuple[str, str]] = {
    "script:monkey_setup": (
        "2.3.10",
        "#3107：v2.3.11（log_dirs 参数校验）已合但 script 表无行"
        "（#2975 旧例外已随 scan 收口清空；本条是 #3107 新开的滞后窗口）。"
        "删除条件：部署 scan 注册激活 v2.3.11 后 pin 追平并移除本条。",
    ),
}


def _exception_lag_reason(version: str, reason: str, head: str) -> str | None:
    """例外仍成立的判据（纯函数）：成立返回 None，不成立返回原因。

    三条（#2998）：理由须可核查（引 issue/账）；豁免版本已追平磁盘 head 即须删；
    不得钉一个磁盘不存在的更高版本（那是反向幻觉，pin 上去 scan 也注册不出来）。
    """
    if not reason.strip() or "#" not in reason:
        return "例外缺 issue 引用的理由"
    if version == head:
        return f"豁免版本 {version} 已等于磁盘 head {head}——滞后已消除，删例外并追 pin"
    version_tuple = tuple(int(part) for part in version.split("."))
    head_tuple = tuple(int(part) for part in head.split("."))
    if version_tuple > head_tuple:
        return f"豁免版本 {version} 超过磁盘 head {head}"
    return None


def _latest_on_disk(script_name: str) -> str:
    versions = []
    for path in (SCRIPTS_DIR / script_name).glob("v*"):
        if not path.is_dir():
            continue
        parts = path.name[1:].split(".")
        if not all(p.isdigit() for p in parts):
            continue
        versions.append((tuple(int(p) for p in parts), path.name[1:]))
    assert versions, f"no version dirs under {script_name}"
    versions.sort()
    return versions[-1][1]


def _iter_script_steps(obj: object):
    if isinstance(obj, dict):
        action = obj.get("action")
        if isinstance(action, str) and action in PINNED_SCRIPTS:
            yield action, obj.get("version")
        for value in obj.values():
            yield from _iter_script_steps(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from _iter_script_steps(item)


def _all_template_script_actions(obj: object):
    """遍历模板里**所有** `script:<name>` action（不经 PINNED_SCRIPTS 过滤）。"""
    if isinstance(obj, dict):
        action = obj.get("action")
        if isinstance(action, str) and action.startswith("script:"):
            yield action
        for value in obj.values():
            yield from _all_template_script_actions(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from _all_template_script_actions(value)


def test_pinned_template_scripts_track_latest_on_disk() -> None:
    expected = {
        action: EXCEPTIONS[action][0] if action in EXCEPTIONS
        else _latest_on_disk(name)
        for action, name in PINNED_SCRIPTS.items()
    }
    template_files = sorted(TEMPLATES_DIR.glob("*.json"))
    assert template_files, "no pipeline templates"

    mismatches: list[str] = []
    seen: set[str] = set()
    for path in template_files:
        data = json.loads(path.read_text(encoding="utf-8"))
        for action, version in _iter_script_steps(data):
            seen.add(action)
            want = expected[action]
            if version != want:
                mismatches.append(f"{path.name}: {action} pin={version!r} latest={want!r}")

    assert not mismatches, (
        "pipeline 模板 pin 落后于磁盘最新版（#2865：合入新版本必须同批钉模板）:\n  "
        + "\n  ".join(mismatches)
    )
    missing = set(PINNED_SCRIPTS) - seen
    assert not missing, (
        f"守卫名单 {sorted(missing)} 在模板里已无引用——删名单项或恢复模板步骤，"
        "勿留空守卫"
    )


def test_template_script_actions_all_in_guard_list() -> None:
    """新脚本族进模板必须同步进名单（#2998 扩面的防回潮：漏名单=漏守卫）。"""
    actions: set[str] = set()
    for path in sorted(TEMPLATES_DIR.glob("*.json")):
        actions.update(_all_template_script_actions(json.loads(path.read_text(encoding="utf-8"))))
    unguarded = sorted(actions - set(PINNED_SCRIPTS))
    assert not unguarded, (
        f"模板引用了名单外的脚本族 {unguarded}——加进 PINNED_SCRIPTS"
        "（确有故意钉旧理由的进 EXCEPTIONS 并写明删除条件）"
    )


def test_exceptions_are_real_lags_with_reasons() -> None:
    """例外三断言：有非空可核查理由；豁免版本确实≠磁盘 head（head 追上后必须删）；
    豁免版本 <= disk head（不允许钉一个磁盘不存在的高版本——那是反向幻觉）。"""
    for action, (version, reason) in EXCEPTIONS.items():
        name = PINNED_SCRIPTS[action]
        problem = _exception_lag_reason(version, reason, _latest_on_disk(name))
        assert problem is None, f"{action} 例外不成立：{problem}"


def test_exception_shape_predicate_has_teeth() -> None:
    """清单清空后 `test_exceptions_are_real_lags_with_reasons` 会空转——判据自身的牙
    改由本用例变异自证：三种失效各命中一次，且真滞后必须放行（否则出口形同虚设）。"""
    head = _latest_on_disk("check_device")
    assert _exception_lag_reason("1.0.0", "以后再说吧", head), "无理由的例外被放行了"
    assert _exception_lag_reason("1.0.0", "   ", head), "空白理由的例外被放行了"
    assert _exception_lag_reason(head, "#2998：示例理由", head), "已追平 head 的例外被放行了"
    future = ".".join(str(int(part) + 9) for part in head.split("."))
    assert _exception_lag_reason(future, "#2998：示例理由", head), "超过 head 的例外被放行了"
    assert _exception_lag_reason("0.0.1", "#2998：示例理由", head) is None, "真滞后被误杀"


def _version_dir_exists(script_name: str, version: str) -> bool:
    return (SCRIPTS_DIR / script_name / f"v{version}").is_dir()


def test_every_pin_and_exception_resolves_to_a_real_version_dir() -> None:
    """pin 与例外值都必须对应磁盘上**真实存在**的版本目录（#3109）。

    `_exception_lag_reason` 只比版本号大小（`0.0.1 < head` 即放行），所以一个不存在
    的版本号能被写进 EXCEPTIONS 而不报错；而模板 pin 一个不存在的版本会让该模板
    新建 Plan 全 422（`plans._validate_script_refs` 只认 script 表）。守卫此前只对
    版本号做大小比较，写错一个字符就能静默引用一个既不在磁盘也不在 DB 的版本。
    """
    bad: list[str] = []
    for action, name in PINNED_SCRIPTS.items():
        version = EXCEPTIONS[action][0] if action in EXCEPTIONS else _latest_on_disk(name)
        if not _version_dir_exists(name, version):
            bad.append(f"{action}：期望版本 v{version} 在 scripts/{name}/ 下不存在")
    for action, (version, _reason) in EXCEPTIONS.items():
        name = PINNED_SCRIPTS[action]
        if not _version_dir_exists(name, version):
            bad.append(f"EXCEPTIONS[{action}]：豁免版本 v{version} 在 scripts/{name}/ 下不存在")
    assert not bad, (
        "pin / 例外引用了磁盘上不存在的版本：\n  " + "\n  ".join(bad)
    )


def test_version_dir_existence_check_has_teeth() -> None:
    """变异自证：不存在的版本必须判否、真实存在必须判是（否则上面那条是空转）。"""
    assert _version_dir_exists("check_device", _latest_on_disk("check_device"))
    assert not _version_dir_exists("check_device", "99.99.99")
    assert not _version_dir_exists("no_such_family", "1.0.0")


def test_guard_has_teeth_when_template_lags_disk() -> None:
    """变异自证：模板故意钉旧版时判据必须红。"""
    latest_check = _latest_on_disk("check_device")
    assert latest_check != "0.0.0"
    poisoned = {"lifecycle": {"init": [
        {"action": "script:check_device", "version": "0.0.0"},
    ]}}
    bad = [
        f"poison.json: script:check_device pin='0.0.0' latest={latest_check!r}"
        for action, version in _iter_script_steps(poisoned)
        if version != _latest_on_disk(PINNED_SCRIPTS[action])
    ]
    assert bad, "变异未命中：判据与模板遍历已脱钩"
