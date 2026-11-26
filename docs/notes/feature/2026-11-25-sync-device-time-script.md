# 新增设备时间与时区前置同步脚本 sync_device_time（v1.0.0 / v1.0.1）

Status: implemented
Class: feature

## Decision

新增独立 Agent 原子脚本族 `sync_device_time`（`1.0.0` → `1.0.1`），用于稳定性测试计划
`init` 阶段（`check_device` 之后、`clean_env` / `aee_prepare` 之前）将设备时区与系统墙钟
强制对齐到 STP 控制面权威时钟，从源头消除因离线设备时钟漂移引发的日志时间戳失真、
AEE/mobilelog 对拍困难与跨 run 基线误判。

涉及文件：

- `backend/agent/scripts/sync_device_time/sync_device_time.py`
- `backend/agent/scripts/sync_device_time/capabilities.json`
- `tool_manifest.json`（登记 `sync_device_time@1.0.0` 与 `sync_device_time@1.0.1`）
- `backend/agent/tests/test_sync_device_time_v100.py`

实现方案（经现网全机群 16 机型只读摸底 + 9 台代表性真机实测 + 78 台主机时钟盘点收敛）：

1. **权威时钟源（v1.0.1 升级，零控制面改动）**：启动时读取继承的 `API_URL`（或参数
   `api_url`，仅接受 `http/https`），向 `{API_URL}/health` 发起一次轻量无鉴权 GET（超时 3s），
   解析 HTTP `Date` 响应头并按半程 RTT 修正，再以 `time.monotonic()` 锚定推进。即使未来某台
   Agent 主机因未开 NTP / `timesyncd` 异常 / BIOS 电池失效发生时钟偏移，也不会把错钟传导给
   挂载设备；同时在 `metrics` 中记录 `time_source`、`host_drift_vs_control_plane_seconds` 与
   `host_clock_skew_warning`。控制面不可达时默认平滑回退主机 `time.time()`；设
   `require_control_plane_time=true` 时 fail-closed。
2. **主路径（零 root、零网络、零机型分支）**：通过 Android `AlarmManagerService` Shell
   命令 `cmd alarm set-timezone <IANA_TZ>` 与 `cmd alarm set-time <authoritative_epoch_ms>`
   直接写入目标时区与毫秒时间戳。实测覆盖 MTK/UNISOC × HONOR/Infinix × Android 14–16
   （SDK 34–36）× `user/0` / `user/1` / `userdebug/1` × `uid=0` / `uid=2000` 非 root，
   耗时 107–370ms，同步后系统钟差归 0，且底层硬件 `/dev/rtc0` 自动落盘更新、不改动
   `auto_time` / `auto_time_zone`。
3. **唯一兜底分支**：仅当主路径失败或回读未达标且 `allow_root_fallback=true` 时触发，
   尝试 `adb root` 后执行 `setprop persist.sys.timezone` + `date -u @<authoritative_epoch_s>` +
   best-effort `hwclock -w`，兼容 Android ≤ 11 或裁剪 `Shell` 权限的固件。
4. **强制回读闭环核验**：回读 `getprop persist.sys.timezone` 与 `date +%s`，必须满足
   `tz == target_tz` 且 `|dev_epoch - ref_epoch| <= max_drift_seconds`（默认 5s），并把
   `drift_before_seconds` / `drift_after_seconds` 写入 `metrics`；不达标显式失败。

**合入后上线收尾边界（本 PR 不直接改写模板）**：按 `docs/development/script-versioning.md`
约束，未在站点发包并 `scan` 注册为 active 的新版本不得预先 pin 进
`backend/schemas/pipeline_templates/*.json`（否则新建 Plan 触发 422 `INVALID_SCRIPT_REFS`）。
合入后运维按顺序收尾：`--publish --packages-root /mnt/stp-aee/packages` → 控制面发版 +
`POST /api/v1/scripts/scan` → 单台预热验证 → 再按需接入稳定性计划模板（显式传
`params.timezone="Asia/Shanghai"`）与存量 Plan 的 `init` 阶段（刷机类计划不强制挂此步）。

## Alternatives

1. **仅同步时区（不同步系统墙钟）**：放弃。2026-11-25 现网 16 机型基线抽样显示，16/16
   台设备时区本身已是 `Asia/Shanghai`，但 14/16 台存在 3 小时至 747 天（2 年+）的墙钟
   漂移；只改时区无法解决主因。
2. **仅信任 Agent 主机时钟（v1.0.0 原形态）**：升级替代。虽然 2026-11-26 全机群 78 台在线
   主机盘点显示 77/77 SSH 可达主机当前与控制面时钟误差在 `-1.0s ~ +0.1s`、NTP 全同步且
   全为 `Asia/Shanghai`，但一旦未来个别新机或故障主机出现时钟偏移，仅信任主机钟会将其直接
   传导给整机设备；v1.0.1 零后端改动即通过 `/health` HTTP `Date` 头切断该传导链。
3. **Agent Host 给设备供网（USB RNDIS / `gnirehtet` VPN / `adb reverse` 本地 NTP / 临时连 Wi-Fi）再自动对时**：放弃。
   - USB RNDIS 会触发 USB 重枚举、破坏多口 Hub 稳定性，且需 Host root/`CAP_NET_ADMIN`（违背 ADR-0037）；
   - VPN 反向共享需要首次人工点击系统授权弹窗且挤占设备现有 `tun0`；
   - `adb reverse` 仅支持 TCP，无法转发 Android SNTP/NTP 所需的 UDP 123；
   - 临时连 Wi-Fi 受无线环境与外网/内网 NTP 可达性影响，且污染离线/功耗类测试网络态。
4. **直接 `date -u` + `setprop persist.sys.timezone` 作为主路径**：放弃。在 `uid=2000` 非 root
   下均报 `Operation not permitted` / `Failed to set property`，且裸 `setprop` 不会主动通知
   已运行框架进程，因此降级为仅兜底分支。
5. **做成 `PipelineEngine` 内置隐式前置钩子**：放弃。刷机类计划前置阶段设备可能关机或无 ADB，
   且隐式副作用绕过步骤追踪、超时预算与版本追溯（ADR-0023/0051）。

## Verification

- 现网全机群基线只读摸底（16 个在位 `平台 × 机型` 族全覆盖，16/16 支持 `cmd alarm` 且
  `com.android.shell` 已授 `SET_TIME` / `SET_TIME_ZONE`）；
- 真机同步实测（本机 `ABBQNX6B****0073` 在 `adb unroot` `uid=2000` 下验证 `UTC` ↔ `Asia/Shanghai`
  + 校时 + `/dev/rtc0` 同步；另选 8 台跨 MTK/UNISOC、HONOR/Infinix、SDK 34–36、`user/0`/`user/1`/`userdebug/1`、
  `uid=0`/`uid=2000` 空闲真机逐台验证，8/8 耗时 107–370ms 且钟差与可读 RTC 全部归 0）；
- 2026-11-26 全机群 78 台 Agent 主机时钟与时区只读盘点（77/77 SSH 可达主机 `|drift| <= 1.0s`、
  `NTPSynchronized=yes`、`Asia/Shanghai`），以及对本机真实控制面 `http://127.0.0.1:8000/health`
  执行 `_resolve_clock_reference` 实测（验证 `time_source=control_plane`、HTTP `Date` 解析与 RTT 计算）；
- 单元测试：`venv/bin/python scripts/run_pytest.py backend/agent/tests/test_sync_device_time_v100.py -v`；
- 门禁检查：`venv/bin/python tools/dev/check_script_packages.py`、`venv/bin/python tools/dev/check_tool_manifest.py --base origin/main`、
  `venv/bin/python scripts/run_gates.py check:quick`。

## Revisit

- 若未来接入纯量产无 root（`ro.build.type=user` 且 `ro.debuggable=0` 且 `uid=2000`）且
  OEM 裁剪了 `com.android.shell` `SET_TIME` 权限的新机型，需重新评估是否补充针对性授权通道
  或该机型的降级策略；
- `sync_device_time@1.0.1` 完成站点发包与 `scan` 激活后，在后续 PR 中将稳定测试模板
  （`monkey.json` / `monkey_watcher_patrol.json` / `mtbf.json` / `sleep.json` / `powercycle.json` /
  `gpu.json` / `aging_ddr.json`）的 `init` 阶段接入本步骤（显式指定 `timezone: "Asia/Shanghai"`），
  并统一清理或收窄存量下游时间漂移手工补偿路径。
