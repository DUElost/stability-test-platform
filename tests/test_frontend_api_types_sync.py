"""前后端 API 类型同步契约（#2032）：后端响应模型的字段必须都出现在前端 types.ts。

#3458 扩展：后端枚举成员（EventType）⊆ 前端联合类型（AlertRule.event_type）——
枚举新增成员而前端漏同步时，前端下拉/展示映射会静默缺失。本文件只做跨语言单向断言；
前端展示映射（EVENT_LABELS）的穷尽性由 TypeScript `satisfies` 在 `npm run type-check`
侧保证，不被 Python 解析。

背景：`AGENTS.md` 硬不变量写明「前端 API 类型以 `frontend/src/utils/api/types.ts`
为入口，并与后端 schema 同步」，但此前**没有可机器查的检查**——`HostOut.ssh_auth_type`
与 `DeviceOut.serial_suspect` 长期缺失（#2032）。本文件用纯文本解析（离线、秒级）把
该不变量变成门禁：后端模型新增响应字段而未同步前端时，PR 侧即红。

方向：**只做单向断言**（后端 → 前端）。前端多出的字段（派生/兼容）允许存在——
它们不影响「响应字段不会静默丢失」这一不变量的目的。

例外：若某字段确实**有意不下发前端**，把它加入 `ALLOWED_MISSING[模型名]` 并注明理由，
不要放宽断言。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
TYPES_TS = REPO_ROOT / "frontend/src/utils/api/types.ts"

# (后端文件, 后端响应模型, 前端接口名)
MODELS = [
    ("backend/api/schemas/host.py", "HostOut", "Host"),
    ("backend/api/schemas/device.py", "DeviceOut", "Device"),
]

# 有意不下发前端的字段（需注明理由）；当前为空
ALLOWED_MISSING: dict[str, set[str]] = {}


def _backend_response_fields(path: Path, cls: str) -> set[str]:
    text = path.read_text(encoding="utf-8")
    match = re.search(rf"class {cls}\(.*?\):\n(.*?)(?=\nclass |\Z)", text, re.S)
    assert match, f"未找到模型 {cls}（{path.name}）——解析器需随 schema 结构更新"
    return set(re.findall(r"^\s{4}([a-z_]+)\s*:", match.group(1), re.M))


def _frontend_interface_fields(path: Path, iface: str) -> set[str]:
    text = path.read_text(encoding="utf-8")
    match = re.search(rf"export interface {iface} \{{(.*?)\n\}}", text, re.S)
    assert match, f"未找到接口 {iface}（{path.name}）"
    return set(re.findall(r"^\s{2}([a-z_]+)\??:", match.group(1), re.M))


@pytest.mark.parametrize("backend_rel,cls,iface", MODELS)
def test_backend_response_fields_present_in_frontend_types(backend_rel, cls, iface):
    backend_fields = _backend_response_fields(REPO_ROOT / backend_rel, cls)
    frontend_fields = _frontend_interface_fields(TYPES_TS, iface)

    # 守卫解析器本身：空集或哨兵字段缺失都说明解析失效（否则断言恒真）
    assert "id" in backend_fields, f"{cls} 解析异常（未取到 id）"
    assert "id" in frontend_fields, f"{iface} 解析异常（未取到 id）"

    missing = backend_fields - frontend_fields - ALLOWED_MISSING.get(cls, set())
    assert not missing, (
        f"{cls} 的响应字段未同步到前端 {iface}：{sorted(missing)}\n"
        f"→ 在 frontend/src/utils/api/types.ts 的 {iface} 补齐（硬不变量：与后端 schema 同步）；"
        f"确属有意不下发的，加入 ALLOWED_MISSING 并注明理由。"
    )


# ---- #3458：后端枚举成员 → 前端联合类型（跨语言单向断言） ----

NOTIFICATION_MODEL = REPO_ROOT / "backend/models/notification.py"


def _backend_enum_members(path: Path, cls: str) -> set[str]:
    text = path.read_text(encoding="utf-8")
    match = re.search(rf"class {cls}\(.*?\):\n(.*?)(?=\nclass |\Z)", text, re.S)
    assert match, f"未找到枚举 {cls}（{path.name}）——解析器需随结构更新"
    return set(re.findall(r"^\s{4}([A-Z_]+)\s*=", match.group(1), re.M))


def _frontend_union_members(path: Path, iface: str, field: str) -> set[str]:
    text = path.read_text(encoding="utf-8")
    iface_match = re.search(rf"export interface {iface} \{{(.*?)\n\}}", text, re.S)
    assert iface_match, f"未找到接口 {iface}（{path.name}）"
    field_match = re.search(rf"^\s{{2}}{field}\s*:\s*([^;]+);", iface_match.group(1), re.M)
    assert field_match, f"未找到字段 {iface}.{field}（{path.name}）"
    return set(re.findall(r"'([A-Z_]+)'", field_match.group(1)))


def test_notification_event_types_present_in_frontend_union():
    backend_members = _backend_enum_members(NOTIFICATION_MODEL, "EventType")
    frontend_members = _frontend_union_members(TYPES_TS, "AlertRule", "event_type")

    # 守卫解析器本身：空集或哨兵成员缺失都说明解析失效（否则断言恒真）
    assert "RUN_COMPLETED" in backend_members, "EventType 解析异常（未取到 RUN_COMPLETED）"
    assert "RUN_COMPLETED" in frontend_members, (
        "AlertRule.event_type 解析异常（未取到 RUN_COMPLETED）"
    )

    missing = backend_members - frontend_members
    assert not missing, (
        f"backend/models/notification.py 的 EventType 成员未同步到前端 AlertRule.event_type："
        f"{sorted(missing)}\n"
        f"→ 在 frontend/src/utils/api/types.ts 的 AlertRule.event_type 联合补齐；"
        f"并按同一键集同步 EVENT_LABELS（由 satisfies 穷尽检查强制）。"
    )
