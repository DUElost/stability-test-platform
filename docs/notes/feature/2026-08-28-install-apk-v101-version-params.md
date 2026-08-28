# install_apk@1.0.1：APK 路径入 default_params 的新版本

Status: implemented
Class: feature

## Decision

平台「版本即参数」不变量（CLAUDE.md §关键约定）：PlanStep 无 params 字段，
脚本参数完全来自脚本版本 `default_params`。`install_apk@1.0.0` 的
`default_params` 为空，无法携带 `apk_path`。为把 TNST-APK-Aging-1.0.0.3
signed APK 安装到 build `MLD-LX2-16-260810V62` 的 18 台设备：

- 新建 **install_apk@1.0.1**（script id=63），`default_params={"apk_path":
  "apk-repo/incoming/TNST-APK-Aging-1.0.0.3-20260826_signed.apk"}`。
  `apk_path` 为**相对路径**：脚本内 `_resolve_path` 拼 Agent 的
  `STP_NFS_ROOT`（=`/mnt/stp-aee`）。代码与 v1.0.0 逐字节相同
  （`content_sha256=ce4a0d7d…`，v1.0.0/v1.0.1 一致）。
- APK 从 SMB 仓库 `/data/apk-repo/incoming/` 复制到中心存储
  `/mnt/stp-aee/apk-repo/incoming/`：控制面即 NFS server 172.21.8.202，
  Agent 挂载同盘（`/mnt/stp-aee`），单份共享、所有 host 同路径可见。
  NFS 顶层新增 `apk-repo/` 目录族，不属既有 jobs/devices/dedup/jira 布局。
- 三端同步：控制面 `STP_SCRIPT_ROOT`（`backend/agent/scripts/
  install_apk/v1.0.1/`）+ 两台 Agent `/opt/stability-test-agent/agent/
  scripts/install_apk/v1.0.1/`；Agent 重启后 ScriptRegistry 重新加载
  （registry 无热刷新机制，`initialize()` 仅启动时执行，新增版本必须
  restart Agent）。
- 执行形态为**一次性运维 Plan**（init-only、`patrol_interval_seconds=null`）
  而非常驻 patrol：安装任务不属专项测试，常驻会占设备（见 PATROL
  阶段约定）；Plan #14 `install-tnst-apk-aging-v62`，`failure_threshold=0.5`。

## Alternatives

- 直接改 `install_apk@1.0.0` 的 `default_params`：版本不可变（422），
  不变量硬约束，否决。
- `apk_path` 传控制面绝对路径 `/data/apk-repo/incoming/…`：Agent 机器
  无此路径（SMB 仓库只在控制面），`adb install` 在 Agent 侧执行，否决。
- APK 逐机拷贝到各 Agent 本地：每台多一份副本、新增 host 即失配，
  不如 NFS 单份共享，否决。
- 复用既有脚本版本：62 条脚本记录全查过，无任何含 `apk_path` 的版本，
  必须新建版本。

## Verification

- PlanRun #251（2026-08-28）：18/18 COMPLETED、exit 0、pass_rate 1.0、
  run SUCCESS。
- 设备侧抽查 3 台（host 172-21-15-68 ×2 + 172-21-15-76 ×1）：
  `adb shell pm list packages` → `com.ape.aging` 已安装。
- 三端 sha 一致：DB `content_sha256` = 控制面磁盘 = 两台 Agent 磁盘
  （`ce4a0d7d…`）。

## Revisit

- 换包/重装：`apk_path` 不可变 → 需再建新版本。若「一次性参数版本」
  变频繁，应重议步骤级 params 注入（当前唯一特例是 WiFi 资源池注入，
  `plan_dispatcher_core.py:377`）。
- `/mnt/stp-aee/apk-repo/` 若成为常规 APK 仓，应补进 AGENTS.md
  NFS 路径约定表。
