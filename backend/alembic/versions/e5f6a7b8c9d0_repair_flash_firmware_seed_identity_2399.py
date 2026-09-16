"""修复 flash_firmware 的 seed 身份错位：1.3.8 行装 1.3.9 内容 + 1.3.9 根本没有行（#2399）

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-09-16

## 缺陷

两条 seed 迁移把「守卫版本 / 写入版本」比「本文件真正 seed 的版本」小了一整个版本
（复制粘贴 off-by-one，从 v1.3.10 起模板已改对）：

- ``t2u3v4w5x6y7``（文件名 v138）守卫并写入 ``1.3.7``，却带 v1.3.8 的 sha/nfs；
- ``u3v4w5x6y7z8``（文件名 v139）守卫并写入 ``1.3.8``，却带 v1.3.9 的 sha/nfs。

两条分支的后果不同，所以它长期没被发现：

- **老库（含生产）**：守卫行已存在 → 走 UPDATE 分支，只覆盖 ``default_params`` /
  ``param_schema`` / ``is_active``，**从不碰 sha/nfs**，注册表看起来是对的；代价是把
  后一版的参数原地写到了前一版行上（#942 语义）。本族 v1.3.7/1.3.8/1.3.9 三版参数字节
  相同，故未观察到实害——这一点由 ``tests/test_seed_revision_version_guard.py`` 断言，
  不再依赖「恰好相同」的运气。
- **空库自举**（新部署 / dev / CI ``pr-migrate-empty-db``）：``t2u3v4w5x6y7`` 只插了
  ``1.3.7`` 行，``u3v4w5x6y7z8`` 的守卫找不到 ``1.3.8`` → INSERT 出「版本=1.3.8、
  内容=1.3.9」的幽灵行，且**链上再没有第二条迁移创建 v1.3.9**。

## 为什么必须在这里修，而不是重扫

``backend/services/script_catalog.py`` 以 ``content_sha256`` 判 conflict：入口文件 sha 与
行内 sha 不一致时**只报 conflict 并跳过**，除非管理员显式 ``force_rebaseline``。所以新环境
的幽灵行不会被 scan 自愈（#2399 现象里的 ``conflicts: [flash_firmware 1.3.8]``）。
反过来，只要 sha 对上，同一段代码会把 ``nfs_path`` 重新锚到站点 ``runtime_root``——
因此本迁移修 sha 即足以让路径也随之自愈。

危害不止「扫不过」：那行 ``nfs_path`` 指向 ``…/v1.3.9/…`` 却自称 1.3.8，在挂了 ``/opt``
的路径布局下会**静默执行错误版本**，直接破坏「已发布版本不可变、新行为用新版本表达」
的可追溯性（同 #751 / #1276 族）。

## 本迁移做什么

历史 revision 不可改（#2258），所以在链尾收敛，全部是 ``WHERE`` 命中才写的幂等自愈
（先例：#1717 的 ``f6a5b4c3d2e1``、#2322 的 ``d4e5f6a7b8c9``）：

1. **纠正 1.3.8 行身份**：仅当它的 sha 等于 v1.3.9 的、或 ``nfs_path`` 落在
   ``/flash_firmware/v1.3.9/`` 目录（= 幽灵行的确切签名）时，改写为 v1.3.8 的真值。
   生产上该行本就是这个值 → 零行命中，no-op。
2. **补 v1.3.9 行**：仅当缺失时插入，内容取 ``u3v4w5x6y7z8`` 自己的常量。
   ``is_active = false``：链上 ``j0k1l2m3n4o5``（seed v1.3.10）的停用列表按错位世界写到
   ``1.3.8`` 为止，若补成 active 会给新环境凭空多出一个可派发版本；``false`` 与「该行
   今天不存在」对派发面等价，要不要启用交给 scan 或人工。
3. **不碰任何版本的 ``default_params`` / ``param_schema`` / ``is_active``**（#942 纪律）。
   1.3.8 行现有的参数来自 v1.3.9 的 seed，而三版参数字节相同（见上），故不产生残留错配。

常量逐字抄自被修复的两条 seed 文件，磁盘真值由 ``tests/test_seed_revision_version_guard.py``
比对 ``backend/agent/scripts/flash_firmware/v*/`` 守住——迁移本身不读磁盘（Agent 脚本树
按站点挂载，控制面进程未必看得见）。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from alembic import op
from sqlalchemy import text

revision = "e5f6a7b8c9d0"
down_revision = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None

_NAME = "flash_firmware"

#: v1.3.8 真值——与 ``t2u3v4w5x6y7`` 里的常量逐字相同。
_SHA_138 = "2fad6bab28034232bf01927a895a4b9f4981971cbdc6e4d608655da8dfd4436e"
_NFS_138 = "/opt/stability-test-agent/agent/scripts/flash_firmware/v1.3.8/flash_firmware.py"

#: v1.3.9 真值——与 ``u3v4w5x6y7z8`` 里的常量逐字相同。
_SHA_139 = "06fa9fa39cd93b212d931d47f763e55bc00783f845f531d03726dd1de6acbc9a"
_NFS_139 = "/opt/stability-test-agent/agent/scripts/flash_firmware/v1.3.9/flash_firmware.py"

#: 幽灵行的签名：自称 1.3.8，身份却是 1.3.9。
_GHOST_SHA = _SHA_139
_GHOST_PATH_MARK = "/flash_firmware/v1.3.9/"

_PARAM_SCHEMA = {
    "firmware_dir": {
        "type": "string",
        "required": False,
        "label": "固件目录",
        "description": "NFS 相对或绝对路径。缺省走指纹路由",
    },
    "da_file": {"type": "string", "required": False, "label": "DA 文件"},
    "scatter_file": {"type": "string", "required": False, "label": "Scatter 文件"},
    "family": {
        "type": "string", "required": False, "label": "机型族",
        "enum": ["MLD", "ELA"],
    },
    "version": {
        "type": "string", "required": False, "label": "目标固件版本",
        "description": "缺省按机型读 latest.json 的 versions 映射或单键 version",
    },
    "firmware_root": {
        "type": "string", "required": False, "label": "固件根目录",
    },
    "skip_if_current": {
        "type": "boolean", "required": False, "label": "同版本跳过",
        "default": True,
    },
    "verify_version": {
        "type": "boolean", "required": False, "label": "刷后版本核验",
        "default": True,
    },
    "verify_wait_seconds": {
        "type": "integer", "required": False, "label": "核验等待(秒)",
        "default": 300, "minimum": 30,
    },
    "boot_stabilize_seconds": {
        "type": "integer", "required": False, "label": "boot 稳定窗口(秒)",
        "default": 20, "minimum": 5,
        "description": "verify 通过后 boot_completed=1 且 USB 拓扑稳定的持续"
                       "窗口——首刷二次重启在锁内消化",
    },
    "boot_stabilize_max_wait": {
        "type": "integer", "required": False, "label": "boot 稳定等待上限(秒)",
        "default": 120, "minimum": 30,
        "description": "超时按「设备确认卡死」放行,不判失败",
    },
    "command": {
        "type": "string", "required": False, "label": "刷机命令",
        "enum": ["firmware-upgrade", "format-download", "readback",
                 "download-only"],
        "default": "firmware-upgrade",
    },
    "boot_mode": {
        "type": "string", "required": False, "label": "启动模式",
        "enum": ["auto", "da", "boot1"], "default": "auto",
    },
    "timeout_seconds": {
        "type": "integer", "required": False, "label": "超时(秒)",
        "default": 1200, "minimum": 60,
    },
    "flash_tool_dir": {
        "type": "string", "required": False, "label": "Flash Tool 目录",
    },
    "reboot_to_flash": {
        "type": "boolean", "required": False, "label": "刷前重启设备",
        "default": True,
    },
    "reboot_target": {
        "type": "string", "required": False, "label": "重启方式",
        "enum": ["normal", "bootloader", "fastboot"], "default": "normal",
    },
    "pre_reboot_wait_seconds": {
        "type": "integer", "required": False, "label": "重启提前量(秒)",
        "default": 5, "minimum": 0,
    },
    "gate_other_mtk": {
        "type": "boolean", "required": False, "label": "门控其它 MTK 口",
        "default": True,
    },
    "max_attempts": {
        "type": "integer", "required": False, "label": "尝试次数",
        "default": 2, "minimum": 1, "maximum": 4,
    },
    "retry_backoff_seconds": {
        "type": "integer", "required": False, "label": "重试间隔(秒)",
        "default": 10, "minimum": 0,
    },
    "strict_env_check": {
        "type": "boolean", "required": False, "label": "严格环境预检",
        "default": False,
    },
}

_DEFAULT_PARAMS = {
    "command": "firmware-upgrade",
    "boot_mode": "auto",
    "timeout_seconds": 1200,
    "reboot_to_flash": True,
    "reboot_target": "normal",
    "pre_reboot_wait_seconds": 5,
}


def upgrade() -> None:
    conn = op.get_bind()
    now = datetime.now(timezone.utc)

    # 1) 只把「带幽灵签名」的 1.3.8 行收敛到 v1.3.8 真值；生产上零行命中。
    conn.execute(
        text(
            "UPDATE script SET content_sha256 = :sha, nfs_path = :nfs, "
            " updated_at = :now "
            "WHERE name = :name AND version = '1.3.8' "
            "  AND (content_sha256 = :ghost_sha OR nfs_path LIKE :ghost_mark)"
        ),
        {
            "sha": _SHA_138,
            "nfs": _NFS_138,
            "now": now,
            "name": _NAME,
            "ghost_sha": _GHOST_SHA,
            "ghost_mark": f"%{_GHOST_PATH_MARK}%",
        },
    )

    # 2) 补 v1.3.9 行（只在缺失时），is_active=false 见模块 docstring。
    exists = conn.execute(
        text("SELECT 1 FROM script WHERE name = :name AND version = '1.3.9'"),
        {"name": _NAME},
    ).fetchone()
    if exists is None:
        conn.execute(
            text(
                "INSERT INTO script "
                "(name, display_name, category, script_type, version, nfs_path, "
                " content_sha256, param_schema, default_params, is_active, "
                " description, created_at, updated_at) "
                "VALUES (:name, :display, :cat, :stype, :ver, :nfs, "
                " :sha, CAST(:pschema AS jsonb), CAST(:dparams AS jsonb), false, "
                " :desc, :now, :now)"
            ),
            {
                "name": _NAME,
                "display": _NAME,
                "cat": "device",
                "stype": "python",
                "ver": "1.3.9",
                "nfs": _NFS_139,
                "sha": _SHA_139,
                "pschema": json.dumps(_PARAM_SCHEMA),
                "dparams": json.dumps(_DEFAULT_PARAMS),
                "desc": (
                    "MTK SP Flash Tool firmware flash — v1.3.9 + 门控保持收窄"
                    "（BROM-only 压制）；补行由 #2399 修 seed 链守卫 off-by-one"
                ),
                "now": now,
            },
        )


def downgrade() -> None:
    # 自愈没有逆操作：把 sha 改回幽灵值只会重新制造同一个缺陷（同 #2322 的取舍）。
    pass
