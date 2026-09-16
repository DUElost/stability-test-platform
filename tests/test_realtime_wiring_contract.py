"""#2400：实时通道「两端接线」的结构守卫。

背景：`/dashboard` 事件面反复出现**同一形态**的漂移——一端有、另一端没有，
运行时两端都不报错，只有在真实数据下才看得出来（#2129 家族）：

1. **有 producer 无 consumer**：`broadcast_run_update` / `broadcast_report_ready`
   / `broadcast_job_log` / `broadcast_precheck_update` 在 origin/main 上零生产
   调用点，只有 `backend/tests/api/test_websocket.py` 直调自证「函数可用」；
   前端却留着对应事件分支或订阅位（PRECHECK 的 emit 另有 `precheck/notify.py`
   承担，函数本身是死角重复）。
2. **有 consumer 无 producer**：Agent `step_log` 每行向 `job:{id}` / `run:{id}`
   双投，而这两个房间**没有订阅方**（`jobLogsSubscription` /
   `runLogsSubscription` 零生产调用点）。
3. **白名单与 emit 面脱节**：`_ROOM_PATTERN` 自称「合法形态 = 后端 emit 端全集」，
   `job:` / `run:` 已无 emit 端却仍在白名单里，客户端可以订阅一个永远收不到
   消息的房间。

三条判据都是**纯静态文本扫描**（`tests/` 准入判据：纯离线 + 秒级），分别对应
上述三种漂移。每条都带**非空断言**防止扫描面塌成恒真（例如函数被改名后正则
匹配为空、白名单解析失败等），并对扫描面本身有钉子：

- `broadcast_*` 定义数必须 ≥ 5（当前 5 个，且每个都有生产调用方）；
- 订阅描述符导出数必须 ≥ 3（dashboard / fleet / plan_run / console）；
- `_ROOM_PATTERN` 解析出的房间族必须 ≥ 2（plan_run / console / fleet）。

要恢复被删的通道（例如将来真的要做步骤级实时日志），正确姿势是**两端一起接**：
emit 位 + 订阅工厂 + 房间白名单，三处齐了本守卫自然放行。
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SERVER = REPO_ROOT / "backend" / "realtime" / "socketio_server.py"
FRONTEND_CONFIG = REPO_ROOT / "frontend" / "src" / "config" / "index.ts"
BACKEND = REPO_ROOT / "backend"
FRONTEND_SRC = REPO_ROOT / "frontend" / "src"

# 生产面 = 去掉测试目录；测试直调不算「有人用」（本守卫的立身之本）。
_BACKEND_TEST_DIRS = ("backend/tests/", "backend/agent/tests/")
_FRONTEND_TEST_RE = re.compile(r"\.test\.tsx?$")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _backend_production_files() -> list[Path]:
    return [
        p
        for p in BACKEND.rglob("*.py")
        if not any(
            str(p.relative_to(REPO_ROOT)).startswith(d) for d in _BACKEND_TEST_DIRS
        )
    ]


def _frontend_production_files() -> list[Path]:
    return [
        p
        for p in FRONTEND_SRC.rglob("*.ts*")
        if not _FRONTEND_TEST_RE.search(p.name)
    ]


def _count_in(files: list[Path], needle: str, *, exclude: Path) -> int:
    total = 0
    for path in files:
        if path == exclude:
            continue
        total += _read(path).count(needle)
    return total


# ── 判据 1：broadcast_* 必须有生产调用方 ─────────────────────────────────────


def _broadcast_definitions() -> list[str]:
    return re.findall(r"^async def (broadcast_\w+)\(", _read(SERVER), re.M)


def test_broadcast_scan_surface_is_not_empty():
    """防恒真：扫描面塌了（改名 / 语法变化）不能让本文件变成永远绿。"""
    names = _broadcast_definitions()
    assert len(names) >= 5, f"broadcast_* 定义面塌了：{names}"
    assert "broadcast_plan_run_status" in names


def test_every_broadcast_has_production_caller():
    files = _backend_production_files()
    dead = []
    for name in _broadcast_definitions():
        if _count_in(files, name, exclude=SERVER) == 0:
            dead.append(name)
    assert dead == [], (
        "以下 broadcast_* 没有生产调用方（只有测试直调不算接线）——"
        f"要么接上调用点，要么连同前端消费端一起删：{dead}"
    )


# ── 判据 2：订阅描述符必须有生产消费方（前端）────────────────────────────────


def _subscription_exports() -> list[str]:
    """config/index.ts 里导出的订阅描述符（常量或工厂）。"""
    # 大小写都要吃：config 里既有 camelCase 工厂（planRunSubscription），
    # 也有 SCREAMING_SNAKE 常量（DASHBOARD_SUBSCRIPTION / FLEET_DEVICES_SUBSCRIPTION）。
    return re.findall(
        r"^export (?:const|function) (\w*Subscription\w*)",
        _read(FRONTEND_CONFIG),
        re.M | re.I,
    )


def test_subscription_scan_surface_is_not_empty():
    names = _subscription_exports()
    assert len(names) >= 3, f"订阅描述符扫描面塌了：{names}"
    assert "planRunSubscription" in names


def test_every_subscription_descriptor_has_production_consumer():
    files = _frontend_production_files()
    dead = []
    for name in _subscription_exports():
        if _count_in(files, name, exclude=FRONTEND_CONFIG) == 0:
            dead.append(name)
    assert dead == [], (
        "以下订阅描述符没有生产消费方（组件从不订阅它）——"
        f"要么接上订阅点，要么连服务端 emit 一起删：{dead}"
    )


# ── 判据 3：房间白名单 = emit 端全集 ─────────────────────────────────────────


def _room_kinds() -> set[str]:
    """从 `_ROOM_PATTERN` 的源码字面量里取房间族（`^kind:` / `^kind$`）。"""
    src = _read(SERVER)
    block = re.search(
        r"_ROOM_PATTERN = re\.compile\((.*?)\n\)", src, re.S
    )
    assert block is not None, "_ROOM_PATTERN 未找到（改名？）"
    kinds = set(re.findall(r"\^([a-z_]+)(?::|\$)", block.group(1)))
    return kinds


def test_room_scan_surface_is_not_empty():
    kinds = _room_kinds()
    assert len(kinds) >= 2, f"房间白名单解析面塌了：{kinds}"
    assert "plan_run" in kinds


def test_every_whitelisted_room_has_an_emit_site():
    """白名单自称「合法形态 = 后端 emit 端全集」——无 emit 端的房间不得留在里面。

    探针按房间族在**整个生产面**找 emit 位（不只是 socketio_server.py）：
    `f"<kind>:` 形式的房间字面量（fleet 走 `FLEET_DEVICES_ROOM` 常量）——
    例如 `console:` 只由 `api/routes/dedup.py` / `services/run_console.py` 投递，
    `plan_run:` 由 server 与若干服务投递。
    """
    backend_src = "\n".join(_read(p) for p in _backend_production_files())
    probes = {
        "plan_run": 'f"plan_run:',
        "console": 'f"console:',
        "fleet": "FLEET_DEVICES_ROOM",
    }
    kinds = _room_kinds()
    missing = [k for k in sorted(kinds) if k not in probes or probes[k] not in backend_src]
    assert not missing, (
        f"白名单里存在没有 emit 端的房间族（订阅了也永远收不到消息）：{missing}"
    )
    # 反向：探针表里若有白名单已删的房间族，说明守卫自己过期了
    assert set(probes) == kinds, (
        f"探针表与白名单不一致：探针 {sorted(probes)} vs 白名单 {sorted(kinds)}"
    )
