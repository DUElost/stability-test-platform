# Honor 刷机自动化方向 A 落地：flash_firmware v1.2.0（指纹路由 + 版本核验）

Status: implemented
Class: feature

## Decision

- 刷机固件解析升级为**设备指纹路由**：`getprop ro.product.model` →
  `_MODEL_FAMILY_ROUTES`（MLD_LX2/LX3→MLD、ELA_LX2/LX3→ELA）→
  `{STP_NFS_ROOT}/firmware/{family}/{version}/`；version 缺省读族级
  `latest.json` 指针文件（CIFS 上 symlink 不可靠）。未列机型 fail-fast。
- 每版本目录 **manifest.json**（version/version_prop/scatter/da/models）为
  路由模式必读；显式 `firmware_dir`（v1.1.0 手工路径）完全向后兼容。
- **刷前比对**（`skip_if_current` 默认开）：当前版本 == manifest 版本 →
  `skipped:true` 短路，不碰锁与 flash_tool；adb 不可达不阻断。
- **刷后核验**（`verify_version` 默认开）：等设备回 adb（get-state==device，
  上限 `verify_wait_seconds` 默认 180s）→ 回读版本，不一致/等不到 → 整步
  失败；关闭时维持 v1.1.0「枚举慢只记录」语义。
- 参数链对齐 MTBF P0：`STP_STEP_PARAMS > STP_FLASH_* env > 代码默认`；
  `STP_FLASH_FIRMWARE_VERSION/_ROOT`、`STP_FLASH_SKIP_IF_CURRENT/_VERIFY_VERSION`
  进 hot-update fleet 白名单（空值不推），`_ROOT` 进路径校验键。
- 迁移 `u7v8w9x0y1z2`：seed v1.2.0 param_schema/default_params；deactivate
  v1.0.0/v1.0.1，v1.1.0 留 active 作回滚。default_params **只含固定期望键**
  （command/boot_mode/timeout/reboot_*）——开关类键种进 default_params 会
  因「参数优先于 env」杀死 hot-update 逃生阀。
- 顺手修复 scan 复活 bug：`scan_script_root` 对「盘上存在且未变但
  is_active=false」的行不再翻回 true（该状态只能来自 admin deactivate 或
  seed 迁移；b7c8d9e0f1a2 停用 v1.0.0 被 scan 推翻即此 bug）。is_active
  变为单向语义：缺失→停用，人工停用→不复活；force_rebaseline 仍显式复活。

## Alternatives

- **复议 ADR-0029 D1（plan_step.params_override）**：控制面全套（迁移 +
  dispatcher 深合并 + precheck + 前端表单），复议条件（路由表成本失控）
  未触发；收益仅「计划级显式选版本」，env pin + latest.json 已覆盖战役需求。
- **固件注册表（新表）**：manifest+目录约定已表达版本→路径→适用机型；
  出现多源固件/跨布局需求再议。
- **独立 admin 刷机战役工具**：绕过 Plan 编排，stall/barrier/step_trace
  全失明，与「Plan 为唯一执行单元」相悖。
- **post-flash 失败重试放脚本内做**：平台已有 `PlanStep.retry`，脚本内
  重试会掩盖失败信号且打戳语义混乱。

## Verification

- `backend/agent/tests/test_flash_firmware_v120.py`：路由（指针/env 版本、
  未知机型、adb 不可达、models 白名单、版本不一致、缺指针）、显式目录
  兼容（manifest 补缺/malformed）、precheck/verify 单元、参数链、main()
  接线（skip 短路不碰锁、核验失败判败）。
- `backend/tests/services/test_script_catalog_activation.py`：scan 单向
  is_active（不复活人工停用、缺失停用、新版本 active、force_rebaseline
  显式复活）。
- `backend/tests/services/test_agent_env_sync.py` 新增三例：flash 键设置
  下发、空值不推、_ROOT 进路径校验。
- 迁移链在一次性 postgres:16 容器上 `alembic upgrade head` + `downgrade -1`
  全通过，seed 行（1.2.0 active + 1.0.1 停用）核对无误。
- 真机单台验证属上线步骤，见
  [`docs/operations/honor-flash-runbook.md`](../../operations/honor-flash-runbook.md) §4。

## Revisit

- 路由表机型 > ~10 或固件版本随 build 频繁漂移 → 复议 D1 / 固件注册表
  （ADR-0029 v2 预设条件）。
- 出现按 USB 控制器并行刷机的吞吐需求 → 当前主机级 flock 串行是上限
  （SP Flash Tool CLI 不认序列号，串行是正确性约束不是优化）。
