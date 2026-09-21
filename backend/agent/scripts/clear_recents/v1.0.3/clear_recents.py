"""Clear Android Overview / Recents (概览键「最近任务」) on ONE target device.

v1.0.0：唤醒 → 解锁 → HOME → APP_SWITCH → 点「全部清除」类按钮 → 回 HOME。
ZTE MiFavor（Z2581/Z2582 实机）：`com.zte.mifavor.launcher:id/remove_all_button`
（content-desc=全部清除）；可点父布局 `remove_all_button_layout`。

v1.0.1：验收口径改为「应用任务卡」——ZTE 清空后仍会留「主屏幕」卡片，
v1.0.0 把 `task_view_single` 全算进 residual 导致点了清除仍判失败
（试跑 plan_run 462：2 FAILED / 1 COMPLETED）。

v1.0.2：部分机型「Clear all」后仍残留 Weather 等粘性卡（试跑 463 设备 31）；
点清除后若仍有应用卡，对 `task_view_single` 上滑关闭，再验收。

v1.0.3（#2976）：**UI 层级读失败不再判成功**。v1.0.0–v1.0.2 的 `_dump_ui`
丢弃两处 rc（`adb_shell` 只回 stdout，`cat` 失败时错误在 stderr）⇒ 设备重启窗
里 `uiautomator dump` 失败 → `cat` 空串 → 计数 0 → 命中「本来就没有任务卡」
提前成功分支，并写出从未测量的 `tasks_after: 0`。现 `_dump_ui` 对 dump rc、
cat rc、空输出、非 XML 内容四态显式报读失败：调用方一律转红并保留证据
（`metrics.ui_read_failed=true`），不再进入成功/无任务分支。连带修正：
`require_tasks=true` 此前只在尝试耗尽后判、无任务场景永远走提前成功分支，
形同虚设——现在首个成功 dump 后按 docstring 语义判失败。
所有 `tasks_after` / `task_cards_raw_after` 均出自一次成功读取的计数。

Environment:
    STP_DEVICE_SERIAL   (required)
    STP_ADB_PATH        (default: adb)
    STP_STEP_PARAMS     (optional JSON)

STP_STEP_PARAMS schema:
    open_settle_seconds : float  (default 1.8; 打开概览后等 UI 稳定)
    after_tap_seconds   : float  (default 1.5; 点击清除后等待)
    swipe_ms            : int    (default 300; 上滑关闭残卡手势时长)
    max_attempts        : int    (default 2; 打开概览+点击的重试次数)
    require_tasks       : bool   (default false; true 时若无最近任务则失败)
    dump_path           : str    (default /data/local/tmp/stp_clear_recents.xml)

Output (stdout):
    {"success": true/false, "error_message": "...", "metrics": {...}}
"""

from __future__ import annotations

import re
import time
from typing import Optional

from _adb import adb_shell, adb_shell_quiet, device_serial, output_result, params, progress_heartbeat

_KEYCODE_WAKE = 224
_KEYCODE_MENU_UNLOCK = 82
_KEYCODE_HOME = 3
_KEYCODE_APP_SWITCH = 187

_DUMP_PATH_DEFAULT = "/data/local/tmp/stp_clear_recents.xml"

# Prefer ZTE-stable ids, then generic labels (CN/EN).
_RESOURCE_ID_PREFERENCE = (
    "remove_all_button_layout",
    "remove_all_button",
    "clear_all",
    "clearAll",
    "btn_clear",
)
_LABEL_PATTERN = re.compile(
    r"(全部清除|清除全部|一键清理|清除|Clear all|Clear All|CLEAR ALL|Clear)",
    re.IGNORECASE,
)
_NODE_RE = re.compile(r"<node\b[^>]*>")


def _as_bool(value, default: bool) -> bool:
    if value is None or str(value).strip() == "":
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _as_float(value, default: float) -> float:
    if value is None or str(value).strip() == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value, default: int) -> int:
    if value is None or str(value).strip() == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _wake_unlock_home() -> None:
    adb_shell(f"input keyevent {_KEYCODE_WAKE}", timeout=15)
    adb_shell(f"input keyevent {_KEYCODE_MENU_UNLOCK}", timeout=15)
    adb_shell(f"input keyevent {_KEYCODE_HOME}", timeout=15)
    time.sleep(0.4)
    adb_shell(f"input keyevent {_KEYCODE_HOME}", timeout=15)
    time.sleep(0.4)


def _open_overview() -> None:
    adb_shell(f"input keyevent {_KEYCODE_APP_SWITCH}", timeout=15)


def _dump_ui(path: str) -> tuple[Optional[str], str]:
    """Dump + read back the UI hierarchy. Returns ``(xml, failure_reason)``.

    #2976：读失败**必须显式可见**——返回 ``(None, reason)`` 而不是空串。
    v1.0.0–v1.0.2 用只回 stdout 的 `adb_shell` 丢 rc，`uiautomator dump` 的
    瞬时失败（设备重启窗 / 拿不到 UI 层级）与「层级里真没有任务卡」同形，
    于是把 adb 故障判成绿色成功。现在 dump rc≠0、cat rc≠0、输出为空、
    输出非 XML 四态全部报读失败；调用方不得把失败态喂给计数函数。
    """
    try:
        dumped = adb_shell_quiet(f"uiautomator dump {path}", timeout=45)
    except Exception as exc:  # 子进程超时 / adb 不可用
        return None, f"uiautomator dump 异常: {exc}"
    if dumped.returncode != 0:
        detail = ((dumped.stderr or "") or (dumped.stdout or "")).strip()[:120]
        return None, f"uiautomator dump rc={dumped.returncode}" + (f"：{detail}" if detail else "")
    try:
        cat = adb_shell_quiet(f"cat {path}", timeout=30)
    except Exception as exc:
        return None, f"cat {path} 异常: {exc}"
    if cat.returncode != 0:
        detail = (cat.stderr or "").strip()[:120]
        return None, f"cat rc={cat.returncode}" + (f"：{detail}" if detail else "")
    text = cat.stdout or ""
    if "<hierarchy" not in text and "<node" not in text:
        return None, f"dump 内容非 UI 层级 XML（{len(text)} 字节）"
    return text, ""


_HOME_LABEL = re.compile(r"^(主屏幕|Home|Home screen|桌面)$", re.IGNORECASE)


def _count_tasks(xml: str) -> int:
    """All overview task cards (includes ZTE lingering 主屏幕 card)."""
    return len(re.findall(r'resource-id="[^"]*(?:task_view_single|task_view)[^"]*"', xml))


def _count_app_tasks(xml: str) -> int:
    """App task cards only — exclude ZTE/AOSP home/desktop cards.

    MiFavor keeps a ``主屏幕`` snapshot card after「全部清除」; counting raw
    ``task_view_single`` then falsely fails a successful clear (v1.0.0/#462).
    """
    # Prefer snapshot content-desc under task cards.
    snaps = re.findall(
        r'resource-id="[^"]*snapshot[^"]*"[^>]*content-desc="([^"]*)"',
        xml,
    )
    if snaps:
        return sum(1 for desc in snaps if desc.strip() and not _HOME_LABEL.match(desc.strip()))
    # Fallback: task_view nodes whose own content-desc is not home.
    count = 0
    for tag in _NODE_RE.findall(xml):
        attrs = _node_attrs(tag)
        rid = attrs.get("resource-id", "")
        if not (rid.endswith("task_view_single") or rid.endswith("task_view") or "/task_view" in rid):
            continue
        label = (attrs.get("content-desc") or attrs.get("text") or "").strip()
        if label and _HOME_LABEL.match(label):
            continue
        count += 1
    return count


def _node_attrs(tag: str) -> dict[str, str]:
    return {k: v for k, v in re.findall(r'([a-zA-Z0-9_:.-]+)="([^"]*)"', tag)}


def _bounds_center(bounds: str) -> Optional[tuple[int, int]]:
    m = re.search(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds or "")
    if not m:
        return None
    x1, y1, x2, y2 = map(int, m.groups())
    return (x1 + x2) // 2, (y1 + y2) // 2


def _find_clear_target(xml: str) -> Optional[dict]:
    """Return tap target metadata or None if already-clear / not found."""
    nodes = [_node_attrs(tag) for tag in _NODE_RE.findall(xml)]

    def score(attrs: dict[str, str]) -> tuple[int, int, int]:
        rid = attrs.get("resource-id", "")
        label = attrs.get("content-desc", "") or attrs.get("text", "")
        rid_score = 0
        for i, pref in enumerate(_RESOURCE_ID_PREFERENCE):
            if rid.endswith(pref) or rid.endswith("/" + pref):
                rid_score = 100 - i
                break
        label_score = 20 if label and _LABEL_PATTERN.search(label) else 0
        # Prefer the clickable layout over the non-clickable ImageView icon.
        clickable_bonus = 50 if attrs.get("clickable") == "true" else 0
        area = 0
        b = attrs.get("bounds", "")
        m = re.search(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", b)
        if m:
            x1, y1, x2, y2 = map(int, m.groups())
            area = max(0, x2 - x1) * max(0, y2 - y1)
        return (rid_score + label_score + clickable_bonus, area, 1 if clickable_bonus else 0)

    candidates = []
    for attrs in nodes:
        rid = attrs.get("resource-id", "")
        label = (attrs.get("content-desc", "") or attrs.get("text", "")).strip()
        if any(rid.endswith(p) or rid.endswith("/" + p) for p in _RESOURCE_ID_PREFERENCE):
            candidates.append(attrs)
            continue
        if label and _LABEL_PATTERN.search(label):
            candidates.append(attrs)

    if not candidates:
        return None

    best = max(candidates, key=score)
    bounds = best.get("bounds", "")
    center = _bounds_center(bounds)
    if center is None:
        return None
    return {
        "resource_id": best.get("resource-id", ""),
        "label": (best.get("content-desc") or best.get("text", "")),
        "bounds": bounds,
        "x": center[0],
        "y": center[1],
        "clickable": best.get("clickable") == "true",
    }


def _tap(x: int, y: int) -> None:
    adb_shell(f"input tap {x} {y}", timeout=15)


def _already_clear(xml: str) -> bool:
    """No remaining *app* tasks (ZTE may still show a 主屏幕 card)."""
    if _count_app_tasks(xml) == 0:
        return True
    return False


def _task_view_centers(xml: str) -> list[tuple[int, int, str]]:
    """Centers of task_view_single cards that still look like app tasks."""
    out: list[tuple[int, int, str]] = []
    for m in _NODE_RE.finditer(xml or ""):
        attrs = _node_attrs(m.group(0))
        rid = attrs.get("resource-id", "")
        if not (rid.endswith("task_view_single") or rid.endswith("/task_view")):
            continue
        bounds = attrs.get("bounds") or ""
        center = _bounds_center(bounds)
        if not center:
            continue
        # Prefer nested snapshot label if present in nearby window; fall back
        # to counting this card as dismissible whenever any app task remains.
        out.append((center[0], center[1], bounds))
    return out


def _swipe_dismiss_remaining(xml: str, swipe_ms: int) -> int:
    """Swipe up each task_view card; return how many swipes issued."""
    centers = _task_view_centers(xml)
    n = 0
    for x, y, bounds in centers:
        # Swipe from mid-card toward top of card (dismiss gesture).
        m = re.search(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds or "")
        if m:
            _x1, y1, _x2, y2 = map(int, m.groups())
            y_from = (y1 + y2) // 2
            y_to = max(y1 + 20, y_from - max(400, (y2 - y1) // 2))
        else:
            y_from, y_to = y, max(50, y - 500)
        adb_shell(f"input swipe {x} {y_from} {x} {y_to} {swipe_ms}", timeout=20)
        n += 1
        time.sleep(0.35)
    return n


def main() -> None:
    device_serial()
    cfg = params()
    open_settle = _as_float(cfg.get("open_settle_seconds"), 1.8)
    after_tap = _as_float(cfg.get("after_tap_seconds"), 1.5)
    swipe_ms = max(100, _as_int(cfg.get("swipe_ms"), 300))
    max_attempts = max(1, _as_int(cfg.get("max_attempts"), 2))
    require_tasks = _as_bool(cfg.get("require_tasks"), False)
    dump_path = str(cfg.get("dump_path") or _DUMP_PATH_DEFAULT)

    metrics: dict = {
        "attempts": 0,
        "tasks_before": None,
        "tasks_after": None,
        "task_cards_raw_before": None,
        "task_cards_raw_after": None,
        "tapped": False,
        "swipes": 0,
        "target": None,
        "already_clear": False,
        "ui_read_failed": False,
    }

    def _finish_home() -> None:
        adb_shell(f"input keyevent {_KEYCODE_HOME}", timeout=15)
        adb_shell_quiet(f"rm -f {dump_path}", timeout=10)

    def _fail_read(reason: str) -> None:
        """#2976：读失败一律转红并保留证据，绝不落「本来就没有任务卡」。"""
        metrics["ui_read_failed"] = True
        _finish_home()
        output_result(False, error_message=f"UI 层级读取失败: {reason}", metrics=metrics)

    with progress_heartbeat("clear_recents"):
        _wake_unlock_home()

        for attempt in range(1, max_attempts + 1):
            metrics["attempts"] = attempt
            _open_overview()
            time.sleep(open_settle)
            xml, dump_err = _dump_ui(dump_path)
            if xml is None:
                _fail_read(f"attempt {attempt}: {dump_err}")
                return
            app_before = _count_app_tasks(xml)
            raw_before = _count_tasks(xml)
            if metrics["tasks_before"] is None:
                metrics["tasks_before"] = app_before
                metrics["task_cards_raw_before"] = raw_before
                # require_tasks 挪到首个成功读取之后判定（v1.0.2 挂在尝试耗尽
                # 之后，而「无任务」场景总在提前成功分支返回——守卫不可达）。
                if require_tasks and app_before == 0:
                    _finish_home()
                    output_result(False, error_message="no recent tasks to clear", metrics=metrics)
                    return

            target = _find_clear_target(xml)
            if target is None:
                if app_before == 0 or _already_clear(xml):
                    # 走到这里 dump 必已成功：tasks_after=0 是**测出来的**。
                    metrics["already_clear"] = True
                    metrics["tasks_after"] = app_before
                    metrics["task_cards_raw_after"] = raw_before
                    _finish_home()
                    output_result(True, metrics=metrics)
                    return
                # No clear button but app cards remain: swipe-dismiss.
                swipes = _swipe_dismiss_remaining(xml, swipe_ms)
                metrics["swipes"] = int(metrics["swipes"] or 0) + swipes
                if swipes:
                    time.sleep(after_tap)
                    _open_overview()
                    time.sleep(open_settle)
                    verify_xml, verify_err = _dump_ui(dump_path)
                    if verify_xml is None:
                        _fail_read(f"swipe 复核 attempt {attempt}: {verify_err}")
                        return
                    app_after = _count_app_tasks(verify_xml)
                    metrics["tasks_after"] = app_after
                    metrics["task_cards_raw_after"] = _count_tasks(verify_xml)
                    if app_after == 0:
                        _finish_home()
                        output_result(True, metrics=metrics)
                        return
                # Overview may have been stolen (e.g. Chrome tab switcher): re-HOME.
                _wake_unlock_home()
                continue

            metrics["target"] = {
                "resource_id": target["resource_id"],
                "label": target["label"],
                "bounds": target["bounds"],
            }
            _tap(int(target["x"]), int(target["y"]))
            metrics["tapped"] = True
            time.sleep(after_tap)

            # Re-open overview to verify app task cards are gone.
            _open_overview()
            time.sleep(open_settle)
            verify_xml, verify_err = _dump_ui(dump_path)
            if verify_xml is None:
                _fail_read(f"tap 复核 attempt {attempt}: {verify_err}")
                return
            app_after = _count_app_tasks(verify_xml)
            raw_after = _count_tasks(verify_xml)

            if app_after > 0:
                # Sticky cards (e.g. Weather) survive Clear all — swipe them away.
                swipes = _swipe_dismiss_remaining(verify_xml, swipe_ms)
                metrics["swipes"] = int(metrics["swipes"] or 0) + swipes
                if swipes:
                    time.sleep(after_tap)
                    _open_overview()
                    time.sleep(open_settle)
                    verify_xml, verify_err = _dump_ui(dump_path)
                    if verify_xml is None:
                        _fail_read(f"swipe 复核 attempt {attempt}: {verify_err}")
                        return
                    app_after = _count_app_tasks(verify_xml)
                    raw_after = _count_tasks(verify_xml)

            metrics["tasks_after"] = app_after
            metrics["task_cards_raw_after"] = raw_after
            _finish_home()

            if app_after == 0:
                output_result(True, metrics=metrics)
                return

            # Tap/swipe appeared to miss; retry.
            _wake_unlock_home()

        _finish_home()
        output_result(
            False,
            error_message=(
                "failed to clear overview/recents after "
                f"{metrics['attempts']} attempt(s); "
                f"app_tasks_before={metrics['tasks_before']} "
                f"app_tasks_after={metrics['tasks_after']} "
                f"raw_cards_after={metrics['task_cards_raw_after']} "
                f"tapped={metrics['tapped']} swipes={metrics['swipes']}"
            ),
            metrics=metrics,
        )


if __name__ == "__main__":
    main()
