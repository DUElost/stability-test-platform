"""#2400：实时通道「两端接线」的结构守卫（#2448 增判据 4、收紧判据 1；#2400 残余增判据 5）。

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
4. **#2448：前端事件表与后端生产者脱节**：前三条都在「服务端 emit / 前端订阅」
   这一侧，没有任何一条以 `SOCKET_MESSAGE_TYPES`（前端 `switch` 判据的值域）为
   全集回头看生产者——`DEPLOY_UPDATE` 这种「前端有消费、后端零生产者」的分支
   因此无门禁（本判据即其补位；该类型已按两端同删收口）。
5. **#2400 残余：agent 发的事件在服务端无 handler**：反向的最后一侧——agent
   `_emit` 出的事件若在 `AgentNamespace` 没有 `on_*`，python-socketio **静默丢弃**
   （无错误无日志）。`step_update` 就这样活了很久（agent 侧有方法与路由分支、
   服务端零 handler），四条判据都不覆盖 agent → server 这一侧。

五条判据都是**纯静态文本扫描**（`tests/` 准入判据：纯离线 + 秒级），分别对应
上述五种漂移。每条都带**非空断言**防止扫描面塌成恒真（例如函数被改名后正则
匹配为空、白名单解析失败等），并对扫描面本身有钉子：

- `broadcast_*` 定义数必须 ≥ 5（当前 5 个，且每个都有生产调用方）；
- 订阅描述符导出数必须 ≥ 3（dashboard / fleet / plan_run / console）；
- `_ROOM_PATTERN` 解析出的房间族必须 ≥ 2（plan_run / console / fleet）；
- `SOCKET_MESSAGE_TYPES` 值域必须 ≥ 8（#2448）；
- agent `_emit` 事件数与 `AgentNamespace` handler 数必须各 ≥ 2（#2400 残余）。

判据 1 的「有人在用」按 **AST 标识符**判定（#2448）：文本计数会把注释/文档
字符串里提到的函数名也算成调用方，据此判绿会掩盖真实死角。

要恢复被删的通道（例如将来真的要做步骤级实时日志），正确姿势是**两端一起接**：
emit 位 + 订阅工厂 + 房间白名单（+ 前端事件表常量），齐了本守卫自然放行。
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SERVER = REPO_ROOT / "backend" / "realtime" / "socketio_server.py"
FRONTEND_CONFIG = REPO_ROOT / "frontend" / "src" / "config" / "index.ts"
FRONTEND_SOCKET_EVENTS = REPO_ROOT / "frontend" / "src" / "utils" / "socketEvents.ts"
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


def _python_identifiers(path: Path) -> set[str]:
    """文件里被**引用**的标识符（AST）——注释与文档字符串不算（#2448）。

    文本计数（`text.count(needle)`）会把注释/文档字符串里提到的函数名也算作
    「有人在用」，据此判「有生产调用方」会假绿。
    """
    tree = ast.parse(_read(path))
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    names |= {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    return names


def _backend_identifier_index() -> dict[Path, set[str]]:
    """生产面每个 Python 文件的标识符集合（一次解析，供判据复用）。"""
    return {path: _python_identifiers(path) for path in _backend_production_files()}


# ── 判据 1：broadcast_* 必须有生产调用方 ─────────────────────────────────────


def _broadcast_definitions() -> list[str]:
    return re.findall(r"^async def (broadcast_\w+)\(", _read(SERVER), re.M)


def test_broadcast_scan_surface_is_not_empty():
    """防恒真：扫描面塌了（改名 / 语法变化）不能让本文件变成永远绿。"""
    names = _broadcast_definitions()
    assert len(names) >= 5, f"broadcast_* 定义面塌了：{names}"
    assert "broadcast_plan_run_status" in names


def test_every_broadcast_has_production_caller():
    identifiers = _backend_identifier_index()
    dead = []
    for name in _broadcast_definitions():
        if not any(
            name in names
            for path, names in identifiers.items()
            if path != SERVER
        ):
            dead.append(name)
    assert dead == [], (
        "以下 broadcast_* 没有生产调用方（只有测试直调、注释提及不算接线）——"
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


# ── 判据 4：前端事件表（SOCKET_MESSAGE_TYPES）必须有服务端生产者（#2448）──────


# 无服务端生产者的消息类型 → 保留理由。当前为空：两端已对齐。
# 新增条目必须写明「为什么可以没有生产者」，否则判据失去意义。
_ALLOWED_WITHOUT_PRODUCER: dict[str, str] = {}


def _message_types() -> set[str]:
    """`SOCKET_MESSAGE_TYPES` 的值域——前端 `switch (msg.type)` 的判据全集。"""
    src = _read(FRONTEND_SOCKET_EVENTS)
    block = re.search(r"SOCKET_MESSAGE_TYPES\s*=\s*\{(.*?)\}\s*as const", src, re.S)
    assert block is not None, "SOCKET_MESSAGE_TYPES 未找到（改名/格式变化？）"
    return set(
        re.findall(r"^\s*[A-Z][A-Z0-9_]*:\s*'([A-Z][A-Z0-9_]*)'", block.group(1), re.M)
    )


def _emitted_event_names() -> set[str]:
    """生产面 `sio.emit("…")` / `schedule_emit("…")` 的事件名字面量。"""
    names: set[str] = set()
    for path in _backend_production_files():
        names |= set(
            re.findall(r'(?:sio\.emit|schedule_emit)\(\s*"([^"]+)"', _read(path))
        )
    return names


def _norm_event(name: str) -> str:
    """`DEVICE_UPDATE` ↔ `device_update` 归一（type 与 wire 名的大小写/分隔差）。"""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def test_message_type_scan_surface_is_not_empty():
    types = _message_types()
    assert len(types) >= 8, f"前端事件表扫描面塌了：{types}"
    assert "DEVICE_UPDATE" in types


def test_every_message_type_has_a_server_producer():
    """#2448：前端消费的每个 message type 都要有服务端 emit 对应物。

    前三判据都只看「服务端 emit / 前端订阅」这一侧，没有一条以事件表为全集回头看
    生产者——`DEPLOY_UPDATE`（前端有 `switch` 分支、后端零 emit、且它失效的
    `['deployments']` 键在前端也不存在）正是这样长期躺在库里。本判据补上该方向。
    """
    emitted = {_norm_event(name) for name in _emitted_event_names()}
    assert emitted, "emit 名扫描面塌了（emit 调用形态变化？）"
    types = _message_types()

    missing = sorted(
        t for t in types
        if _norm_event(t) not in emitted and t not in _ALLOWED_WITHOUT_PRODUCER
    )
    assert not missing, (
        "以下前端消息类型没有服务端生产者（分支永不执行）——"
        f"要么接上 emit，要么连前端常量 + 消费分支一起删：{missing}"
    )

    stale = sorted(
        t for t in _ALLOWED_WITHOUT_PRODUCER
        if t not in types or _norm_event(t) in emitted
    )
    assert not stale, f"豁免表过期（类型已有生产者或已不存在），应移出：{stale}"


# ── 判据 5：agent 发出的事件必须有服务端 handler（#2400 残余）────────────────


AGENT_SOCKET_CLIENT = BACKEND / "agent" / "socketio_client.py"

# 有意发而不处理的 agent 事件 → 保留理由。当前为空：两端已对齐。
_ALLOWED_AGENT_EMITS_WITHOUT_HANDLER: dict[str, str] = {}


def _agent_emitted_events() -> set[str]:
    """agent 侧 `_emit("<name>", …)` 的字面量事件名。

    只看字面量：`_emit(msg_type or "message", …)` 这类动态转发不属于「声明了某个
    事件」；要新增通道就必须写出字面量，本判据才看得见。
    """
    return set(re.findall(r'_emit\(\s*"([a-z_]+)"', _read(AGENT_SOCKET_CLIENT)))


def _agent_namespace_handlers() -> set[str]:
    """`AgentNamespace` 的 `on_<name>` handler 集（socketio 的事件入口）。"""
    src = _read(SERVER)
    block = re.search(r"class AgentNamespace\(.*?(?=\nclass )", src, re.S)
    assert block is not None, "AgentNamespace 未找到（改名？）"
    return set(re.findall(r"async def on_(\w+)\(", block.group(0)))


def test_agent_emit_scan_surface_is_not_empty():
    events = _agent_emitted_events()
    handlers = _agent_namespace_handlers()
    assert len(events) >= 2, f"agent emit 扫描面塌了：{events}"
    assert len(handlers) >= 2, f"AgentNamespace handler 扫描面塌了：{handlers}"


def test_every_agent_emit_has_a_server_handler():
    """#2400 残余：agent `_emit` 的事件必须在 `AgentNamespace` 有 handler。

    服务端没有对应 `on_*` 时 python-socketio **静默丢弃**（无错误、无日志）——
    这正是 `step_update` 长期存活的方式：agent 侧有方法、有 `send()` 路由分支，
    服务端零 handler，于是「通道看起来是通的」。豁免需带理由登记。
    """
    handlers = _agent_namespace_handlers()
    events = _agent_emitted_events()

    missing = sorted(
        e for e in events
        if e not in handlers and e not in _ALLOWED_AGENT_EMITS_WITHOUT_HANDLER
    )
    assert not missing, (
        "以下 agent 事件在 AgentNamespace 没有 handler（发出即被静默丢弃）——"
        f"要么补 handler，要么连 agent 侧发射点一起删：{missing}"
    )

    stale = sorted(
        e for e in _ALLOWED_AGENT_EMITS_WITHOUT_HANDLER
        if e not in events or e in handlers
    )
    assert not stale, f"豁免表过期（事件已有 handler 或已不再发射），应移出：{stale}"
