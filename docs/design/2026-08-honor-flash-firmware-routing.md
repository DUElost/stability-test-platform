# Honor 刷机自动化：固件指纹路由 + manifest + 版本核验 + 多设备门控（flash_firmware）

- 日期：2026-08-22
- 状态：**已实施**（随 `feat/honor-flash-firmware-v120` 合入）
- 上游决策：[ADR-0029 v2](../adr/ADR-0029-project-taxonomy-and-param-layering.md)（执行差异归脚本路由，D1 params_override 挂起）、
  MTBF P0 [`param_or_env` 先例](./2026-08-mtbf-p0-runner-design.md) §3.2
- 范围：MLD（260 台，MTK）先行，ELA（20 台，MTK）跟进加路由表项即用；Z258（UNISOC）不适用 SP Flash Tool，不在范围

## 0. 结论摘要

刷机能力从「脚本存在但从未进编排」推进到「可编入 Plan 的闭环」：固件解析从
手工三参数（firmware_dir/da/scatter）升级为**设备指纹路由 + NFS 固件仓库 +
manifest 清单**，补齐刷前版本比对（同版本跳过）与刷后版本回读核验。
参数通道对齐 MTBF P0 的 `STP_STEP_PARAMS > STP_FLASH_* env > 代码默认`，
不复议 ADR-0029 D1。

## 1. NFS 固件仓库布局（中心存储）

```
{STP_AEE_NFS_ROOT}/firmware/
├── MLD/                          # 机型族 = 一级目录（与设备无关，运维建）
│   ├── latest.json               # 族级版本指针：{"version": "8.0.1.100"}
│   └── 8.0.1.100/                # 版本目录（版本号 = 刷机包版本字符串）
│       ├── manifest.json         # 清单（见 §2）
│       ├── scatter.txt           # SP Flash Tool scatter
│       ├── da.bin                # DA 文件
│       └── ...                   # 其余固件分区文件
└── ELA/                          # ELA 固件包到位后同样结构
```

- `latest.json` 是**指针文件**而非 symlink：中心存储是 CIFS，symlink 不可靠。
  切换目标版本 = 改这一个文件，无需 hot-update、无需重启。
- 固件包由测试交付渠道（研发刷机包）解压放入；平台不负责下载。
- 20 台 Agent host 挂同一中心存储，天然共享，无需分发。

## 2. manifest.json 规范（每版本目录一份）

```json
{
  "family": "MLD",
  "version": "8.0.1.100",
  "version_prop": "ro.build.version.incremental",
  "scatter_file": "scatter.txt",
  "da_file": "da.bin",
  "models": ["MLD_LX2", "MLD_LX3"]
}
```

| 字段 | 必填 | 语义 |
|------|------|------|
| `family` | 建议 | 机型族；与目录层级一致性由人保证 |
| `version` | **是**（路由模式） | 目标版本号，与 `getprop {version_prop}` 比对 |
| `version_prop` | 否 | 比对用系统属性，默认 `ro.build.version.incremental` |
| `scatter_file` / `da_file` | **是** | 相对版本目录或绝对路径 |
| `models` | 否 | 适用机型白名单；指纹机型不在列 → fail-fast |

路由模式（无显式 `firmware_dir`）下 manifest 必读且 `version` 必须与目录名
一致，不一致 fail-fast（防放错目录）。显式 `firmware_dir` 模式下 manifest
存在则用于补缺 da/scatter 与提供比对版本，不存在则维持 v1.1.0 手工三参数
行为（完全向后兼容）。

## 3. 脚本行为

```
STP_STEP_PARAMS > STP_FLASH_* env（hot-update 可同步）> 代码默认
```

| 环节 | 行为 |
|------|------|
| 固件解析 | 显式 `firmware_dir` 优先；缺省 `getprop ro.product.model` → `_MODEL_FAMILY_ROUTES`（MLD_LX2/LX3→MLD，ELA_LX2/LX3→ELA）→ `{root}/{family}/{version}/`。version 取 `STP_FLASH_FIRMWARE_VERSION` 或 `latest.json`。机型不在表 → fail-fast（错误信息列出已知机型） |
| 刷前比对 | `skip_if_current`（默认 true）：`getprop {version_prop}` == manifest 版本 → `{"success": true, "skipped": true}`，不碰锁与 flash_tool。adb 不可达（设备已在 BROM）不阻断，记录后照刷 |
| 刷后核验 | `verify_version`（默认 true）：等设备回 adb（`get-state == device`，上限 `verify_wait_seconds` 默认 180s）→ 回读版本；不一致 / 等不到设备 / 回读失败 → 整步失败（重试由 `PlanStep.retry` 承接）。verify 关闭时维持 v1.1.0「枚举慢只记录」语义 |
| 审计 | 路由决策（`decided_by`/`model`/`family`/`version`/manifest 路径）全量写 `metrics.route`，step_trace 可查；新增 PROGRESS 阶段 `version-check` / `verify-wait` / `verify` |

### 3.1 多设备门控 / 重试环 / 环境预检

SPFT Linux console 版 `-p <port>` 打开层损坏（扫描层能匹配、打开层不动作，
跨工具代际一致），多设备同 host 消歧改由**门控**承担：

| 参数 | 默认 | 行为 |
|------|------|------|
| `gate_other_mtk` | true | adb reboot **前**按 `STP_DEVICE_SERIAL` 经 sysfs 反查目标口（BROM 态无 serial，必须趁早）；把其它处于刷机态（pid ∈ {0003,2000,2001,201c,2026,3000}）的 MTK 口 `authorized=0`，普通态(2046)手机不受影响；刷完 try/finally 恢复。无 serial / 无写权限 / 非 Linux 时跳过并记录原因 |
| `max_attempts` | 2（cap 4) | 整链路重试环（每次 = 重启 + flash_tool + 等结果）；launch 类环境错误立即终止不重试；attempt>1 先退避再重画门控（authorized 只对当前设备实例生效） |
| `retry_backoff_seconds` | 10 | 相邻尝试间隔，负值钳 0 |
| `strict_env_check` | false | 启动前置检：可执行位 / ldd 缺库 / adb（流程需要时）/ ttyACM 写入路径（dialout 组或 udev 0666 规则）。前三者硬失败在拿锁前短路；ttyACM 不明默认仅 WARNING，strict 升级为失败 |

metrics 新增 `attempts[]` / `attempt_count` / `gating` / `env_precheck`；
v1.2.0 全部顶层 metrics 键保留（exit_code/stdout_tail 等取最后一次尝试）。
背景与实测证据链见
[Agent Note](../notes/feature/2026-08-25-mtk-flash-fleet-automation.md)。

主机级 flock（`/tmp/stp-flash-firmware.lock`）+ lock-wait 打戳不变：门控
解决「工具抓错设备」，锁保证同一时刻一台 host 只有一场刷写。非 root USB
权限由 dialout 组 + udev 规则（`KERNEL=="ttyACM*", ATTRS{idVendor}=="0e8d",
MODE="0666"`）提供，预检会在缺失时给出修复动作。

## 4. env 键（hot-update fleet 白名单）

| 键 | 默认 | 用途 |
|----|------|------|
| `STP_FLASH_FIRMWARE_ROOT` | `{STP_NFS_ROOT}/firmware` | 固件根覆盖（路径键，推送时远端校验存在） |
| `STP_FLASH_FIRMWARE_VERSION` | 空（读 latest.json） | 战役级版本 pin，优先于指针文件 |
| `STP_FLASH_SKIP_IF_CURRENT` | true | 同版本跳过开关 |
| `STP_FLASH_VERIFY_VERSION` | true | 刷后核验开关 |
| `STP_FLASH_GATE_OTHER_MTK` | true | 多设备门控开关（§3.1） |
| `STP_FLASH_MAX_ATTEMPTS` | 2 | 整链路尝试次数（1..4） |
| `STP_FLASH_RETRY_BACKOFF` | 10 | 重试间隔秒 |
| `STP_FLASH_STRICT_ENV_CHECK` | false | 环境预检 WARNING 升级为失败 |

空值不推送（Agent 本地值保留）。控制面不消费这些键（纯 Agent 侧），同值
语义成立，进 `_FLEET_ENV_KEYS`。

## 5. 注册表

- 迁移 `a7b8c9d0e1f2`：seed v1.3.0 的 param_schema/default_params（部署顺序
  无关）；deactivate v1.0.0/v1.0.1/v1.1.0，**v1.2.0 留 active 作回滚路径**
  （`u7v8w9x0y1z2` 先例）。v1.2.0 由迁移 `u7v8w9x0y1z2` seed。
- `default_params` 只含固定期望键（command/boot_mode/timeout/reboot_*）；
  skip_if_current / verify_version / gate_other_mtk / max_attempts /
  retry_backoff_seconds / strict_env_check / version / firmware_root
  **故意不进** default_params——参数优先于 env，种进去会杀死 hot-update
  逃生阀。
- 配套修复：scan 的 is_active 改为单向语义（盘上缺失 → 停用；人工停用 →
  不复活），否则 seed/admin 的 deactivate 会被下次 scan 推翻（v1.0.0 重活
  的根因）。

## 6. 上线路径（运维）

1. 合入后 hot-update 推 Agent 代码（20 台）→ `POST /api/v1/scripts/scan` 注册 v1.3.0。
2. 放首个 MLD 固件包 + manifest + latest.json（§1 布局）。
3. 按 [runbook](../operations/honor-flash-runbook.md) 建「MLD 刷机」Plan，单台真机验证后放量。
4. ELA：固件包就位即可用（路由表已含 ELA_LX2/LX3）。

## 7. 放弃的备选

| 备选 | 放弃原因 |
|------|---------|
| 复议 ADR-0029 D1（`plan_step.params_override`） | 复议条件（脚本路由表维护成本失控）未触发；控制面迁移+dispatcher+precheck+前端全套成本，收益仅「计划级显式选版本」 |
| 固件注册表（新表） | manifest + 目录约定已覆盖版本→路径→适用机型；等出现「跨 NF S 布局/多源固件」需求再议 |
| 独立 admin 刷机战役工具 | 绕过 Plan 编排（stall 检测/barrier/step_trace 全部失明），与「Plan 为唯一执行单元」相悖 |
| symlink 做 latest | CIFS 上 symlink 不可靠 |

## 8. 重议触发

- 路由表机型 > ~10 或固件版本随 build 频繁漂移 → 复议 D1/固件注册表（ADR-0029 预设条件）。
- 出现按 USB 控制器分片并行的吞吐需求 → 当前主机级串行是吞吐上限（约每 host 同时 1 台）。
