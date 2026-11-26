---
name: stability-plan-launch
description: >-
  稳定性测试专项 Plan（Monkey / MTBF / PowerCycle / Sleep / GPU / Aging DDR / DDR / Standby）
  的建单、前置编排与放量开跑 SOP（脚本与设备前检 → init 强制加入 sync_device_time 同步控制面时钟
  → 干跑预览 → 单机冒烟验收 → 批量放量）。触发时机：新建或修改稳定性测试 Plan、排查专项前置步骤
  缺失（如未同步设备时间或未开 AEE 日志）、准备启动新一轮稳定性专项 PlanRun、或为存量稳定性 Plan
  补齐 sync_device_time 前置步骤。
---

# 稳定性测试专项建单与开跑 SOP

适用条件：在控制面新建、修改或触发任何产出设备日志的稳定性测试专项 Plan（Monkey、MTBF、
PowerCycle、Sleep、GPU、Aging DDR、DDR、Standby 等）。刷机类前置 Plan（如 `flash_firmware`、
`flash_firmware_unisoc`、`flash_firmware_unisoc_bsl`）不适用本流程中的 `sync_device_time`
强制前置要求。完整专项接入流程见 `docs/operations/new-specialty-onboarding-runbook.md`。

> 警告：执行者不得省略稳定性测试 Plan 的 `sync_device_time` 前置步骤。离线测试设备普遍存在
> 3 小时至 2 年以上的时钟漂移，未先与控制面时钟对齐会直接导致 AEE/mobilelog 目录名、
> `ExpTime` 跨 PlanRun 基线拆分与人工对拍失真。

> 警告：执行者不得在未完成干跑预览与单机冒烟验收的情况下，直接对整批设备触发新编排的稳定性 PlanRun。
> 脚本版本未注册、墙钟预算不足或设备租约残留会导致整批准入失败或设备悬挂占用。

## 一、脚本激活态与目标设备前检

按顺序执行以下步骤，不得跳步：

1. 获取控制面管理员 token（按 `control-plane-deploy` §0，在发布根导出站点 env 后请求
   `POST http://127.0.0.1:8000/api/v1/auth/token`）。
   预期：取得非空 `access_token`。
2. 查询 `sync_device_time` 与专项所用脚本在控制面库中的激活状态：
   `curl -s -H "Authorization: Bearer $TOK" http://127.0.0.1:8000/api/v1/scripts | jq '[.data[] | select(.name=="sync_device_time") | {id,version,is_active,package_sha256}]'`
   预期：目标版本（当前为 `sync_device_time@1.0.1`）存在、`is_active` 为 `true` 且 `package_sha256` 非空。
3. 如果第 2 步缺少目标版本或 `is_active` 为 `false`，停止本流程，先按 `script-version-lifecycle` 完成发包与 `POST /api/v1/scripts/scan`。
4. 查询目标设备与所在 Agent host 的在线态及租约占用：
   `curl -s -H "Authorization: Bearer $TOK" 'http://127.0.0.1:8000/api/v1/devices?status=ONLINE&limit=200'`
   `curl -s -H "Authorization: Bearer $TOK" "http://127.0.0.1:8000/api/v1/hosts/<host_id>" | jq '{id,status,active_job_count}'`
   预期：目标设备均为 `ONLINE` 且无未释放的 `ACTIVE` 设备租约，目标 Agent host 为 `ONLINE`。
5. 如果目标设备处于 `BUSY` 且关联作业已卡死或离线，停止建单开跑，先按 `diagnose-device-stall` 排查或按 `device-lease-release` 释放异常租约。

## 二、编排稳定性 Plan（强制 `init` 前置顺序）

以下各条均为对操作者的要求：

- 稳定性测试 Plan 的 `init` 阶段必须按顺序包含 `check_device` 与 `sync_device_time`：
  `check_device` → `sync_device_time`（`version: "1.0.1"`，`timeout_seconds: 60`，`retry: 1`，
  `params: {"timezone": "Asia/Shanghai"}`）→ `unisoc_sysdump_config`（`version: "1.0.0"`，
  `timeout_seconds: 60`，`retry: 1`，`params: {"enabled": true}`；仅展锐平台生效，非展锐平台自动
  `skipped=true`）→ `ensure_root` / `clean_env` / `aee_prepare`（按专项需要）→ 专项 `*_setup`。
- 针对展锐（UNISOC）设备的稳定性测试 Plan，操作者必须在 `sync_device_time` 之后接入
  `unisoc_sysdump_config@1.0.0`（`params.enabled=true`），确保首次命中 Kernel Panic / Watchdog 时
  保留完整内存现场；刷机类 Plan 不得接入该步骤。
- `sync_device_time` 必须排在 `clean_env`、`aee_prepare`、logger 启动及所有测试负载启动之前，
  不得在日志服务已产出文件后再跳变设备时钟（防打乱 `mobilelog` 文件名时间排序与关联窗口）。
- `sync_device_time` 的 `params` 必须显式固定 `timezone`（默认 `"Asia/Shanghai"`），不得留空依赖单台
  Agent host 的本地 `/etc/localtime` 配置。
- 9 个内置稳定性模板（`backend/schemas/pipeline_templates/{aging_ddr,ddr,gpu,monkey,monkey_watcher_patrol,mtbf,powercycle,sleep,standby}.json`）
  均已默认接入 `sync_device_time@1.0.1` 与 `unisoc_sysdump_config@1.0.0`；新建 Plan 时操作者建议直接从内置模板派生，避免手工漏配。
- 为存量稳定性 Plan 追加或更新步骤时，操作者必须同步核对 `specialty_key`、精确 `script_version`、
  `barrier_timeout_seconds`（含长耗时 `init` 前置时抬高预算）、以及 `stall_seconds`（仅可用于具备
  `progress_stamps` 能力的脚本）。

按顺序执行以下步骤：

1. 创建新 Plan（`POST /api/v1/plans`）或更新存量 Plan（`PUT /api/v1/plans/<plan_id>`），确认 `steps`
   的 `init` 列表中 `sync_device_time` 位于 `check_device` 之后、任何日志/负载初始化步骤之前。
   预期：接口返回 HTTP 200/201，返回体中 `steps` 包含 `script_name="sync_device_time"`、
   `script_version="1.0.1"` 与 `params.timezone="Asia/Shanghai"`。
2. 如果接口返回 422 `INVALID_SCRIPT_REFS` 或壁钟预算校验错误，停止派发，核对脚本版本激活态或放大
   对应步骤 `timeout_seconds` 后重试第 1 步。

## 三、干跑预览、单机冒烟与放量

按顺序执行以下步骤：

1. 对目标 Plan 执行干跑预览（不占设备）：
   `curl -s -X POST -H "Authorization: Bearer $TOK" -H 'Content-Type: application/json' http://127.0.0.1:8000/api/v1/plans/<plan_id>/run/preview -d '{"device_ids":[<sample_device_id>]}'`
   预期：返回 HTTP 200 且无阻断性错误。
2. 如果第 1 步返回错误，停止触发 PlanRun，先修正 Plan 配置或设备状态后重跑第 1 步。

> 警告：第 3 步会占用目标设备并真实执行设备端初始化命令。操作者必须先选 1 台空闲 `ONLINE` 设备冒烟，
> 不得跳过干跑预览与单机冒烟直接对全批设备下发 PlanRun。

3. 挑 1 台空闲 `ONLINE` 设备触发单机冒烟 PlanRun：
   `curl -s -X POST -H "Authorization: Bearer $TOK" -H 'Content-Type: application/json' http://127.0.0.1:8000/api/v1/plans/<plan_id>/run -d '{"device_ids":[<sample_device_id>],"note":"single-device smoke before rollout"}'`
   预期：返回新建的 `plan_run_id`，随后通过 `GET /api/v1/plan-runs/<run_id>/timeline` 观测到 `init` 阶段的
   `check_device` 与 `sync_device_time` 均完成且 `device_failed=0`。
4. 检查该台 Agent host 上 `sync_device_time` 的包核验标记与运行日志：
   `ls -la /opt/stability-test-agent/tools_cache/sync_device_time/1.0.1/.stp-verified`
   `grep -h -E 'time_source|drift_after_seconds|method' /opt/stability-test-agent/logs/runs/<job_id>/init_sync_device_time.out.log`
   预期：`.stp-verified` 存在；日志中 `"success": true`、`"time_source": "control_plane"`、
   `"timezone_after": "Asia/Shanghai"` 且 `|drift_after_seconds| <= 5`。
5. 如果日志中 `host_clock_skew_warning` 为 `true` 或 `time_source` 回落为 `host_fallback`，停止对问题
   Agent host 放量，先检查该主机 NTP 同步（`timedatectl status`）与 `API_URL` 连通性。
6. 确认单机冒烟通过后，按批触发全量目标设备的稳定性 PlanRun。
   预期：PlanRun 进入 `RUNNING`，各设备 `init` 阶段顺利通过 `sync_device_time` 并进入 `patrol` 巡检。

## 四、常见阻断与排障出口

| 现象 | 根因定位 | 处置出口 |
|---|---|---|
| 建 Plan 返回 422 `INVALID_SCRIPT_REFS` | `sync_device_time@1.0.1` 尚未发包或未 `scan` 激活 | 操作者必须先走 `script-version-lifecycle` §A 完成 `--publish` 与 `POST /api/v1/scripts/scan` |
| precheck 报 `script_verify_failed` | Agent host 无法从中心存储拉取包或整包 sha 不匹配 | 操作者必须核对 `/mnt/stp-aee/packages/sync_device_time/1.0.1.tar.gz` 与控制面 `package_sha256` 一致性 |
| `sync_device_time` 报 `cmd_alarm` 被拒且 `adb root unavailable` | 纯 `user` 无 root 固件裁剪了 `com.android.shell` 的 `SET_TIME` 权限 | 操作者不得静默跳过校时。操作者必须记录 `device_sdk` / `device_build_type` / `device_debuggable` 并升级交由 Owner 裁决该机型适配策略 |
| `host_clock_skew_warning=true` | 该 Agent host 自身时钟偏离控制面超过阈值（设备已按控制面时钟校齐） | 操作者建议在批次间隙排查该主机 `systemd-timesyncd` 与 NTP 同步状态 |
| 专项完结后日志去重与 Jira 交付 | 进入收尾阶段 | 操作者转入 `monkey-aee-dedup-handoff` 执行跨 PlanRun 去重汇总与交付 |
