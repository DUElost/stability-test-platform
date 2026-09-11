# Hosts 页新增 lsusb 设备数对照列

Status: implemented
Class: feature

## Decision

在 Hosts 页（`/hosts`）「设备 / 任务」列新增 `USB n` 徽标，数据来源为 Agent 侧
`lsusb`，与既有 `在线 n`（来源 `adb devices`）**并排对照**，用于确认 Android 设备的
实际物理连接数、暴露「设备在 USB 上但 ADB 未枚举」的形态。

口径与语义（本次一并定死，后续按现场调整）：

- **只统计疑似 Android/手机设备**：排除 Linux Foundation root hub（`1d6b:`）、
  键盘/鼠标/Hub/存储/网卡/摄像头等外设关键词命中项。宁可漏算不虚高——虚高会让
  USB 与 adb 的差值失去诊断意义。
- **lsusb 有数就显示**，不受 Host `ONLINE/OFFLINE` 限制：断连故障时 `在线 0` +
  `USB n>0` 正是最有价值的信号；沿用「仅 ONLINE 才显示」会把该信号抹成 0。
- **`null` ≠ `0`**：`lsusb` 缺失/超时/非零退出 → 存 `null`，前端渲染 `USB —`
  并提示「未采集到」；`0` 仅表示成功采集且确无目标设备。两者不可混同。
- **纯观测，不参与调度**：`usb_device_count` 刻意不进 `device_slots` /
  `effective_slots` / `health` 任何计算。若让 USB 数参与门禁，会出现「USB 看得到
  就放行」而绕过设备租约语义的路径；且 ADB 全死的主机不能被 USB 数救活。

影响面（数据链，未新增 DB 列/迁移）：

| 层 | 文件 | 改动 |
|---|---|---|
| 采集 | `backend/agent/device_discovery.py` | 新增 `parse_lsusb_output()`（纯函数）/ `count_usb_devices()`（`subprocess` + 5s 超时，异常降级 `None`） |
| 计算 | `backend/agent/capacity_reporter.py` | `compute_capacity(..., usb_device_count=None)` 写入 `capacity` 新键 |
| 心跳 | `backend/agent/heartbeat_thread.py` | 每 tick 采集一次；USB≠ADB 时 `logger.info("usb_adb_device_mismatch ...")` |
| 落库/回显 | `backend/api/routes/heartbeat.py`、`_host_to_out()` | **无需改动**：整个 `capacity` dict 已存 `Host.extra['capacity']` 并回填 `HostOut.capacity`；`HeartbeatRequest.capacity` 为 `Optional[Dict[str, Any]]`，新键自由通过 |
| 类型 | `frontend/src/utils/api/types.ts` | `Host.capacity.usb_device_count?: number \| null` |
| 映射/渲染 | `HostsPage.tsx`、`ExpandableHostTable.tsx` | 徽标三态：未知 `—`（中性）/ `USB > 在线`（warning + 差值解释 tooltip）/ 正常（中性）；列宽 `min-w-[112px]` → `[188px]` 容纳三个徽标 |

## Alternatives

- **统计 lsusb 全部条目**：实现最简、无歧义，但数值恒大于 adb（root hub + 键鼠），
  用户需自行心算扣除，差值失去直接可比性。仅在「先要个粗数」时更优。
- **同时显示总数与疑似 Android 数**：信息最全，但单元格已承载 3 个徽标，第 4 个会
  显著挤压列宽且需 tooltip 展开明细；当前诉求是「确认实际连接数」，双值属过度设计。
- **仅当 Host ONLINE 时显示**：与既有 `在线` 徽标口径严格一致，但会掩盖
  「adb 服务异常而 USB 正常」这一最需要被看见的故障形态。
- **新增独立 DB 列 / 独立接口**：`capacity` 已是心跳快照的天然载体，新列需迁移且
  与既有快照语义重复；ADR-0019 的 `capacity` 结构本就是可扩展 dict。
- **让 USB 数参与可用槽位**：会让 USB 枚举成功但 adb 不可用的设备被派发任务，
  必然失败并污染租约；且 `_compute_health_limit` 的 adb 全死门禁会被绕过。
- **逐设备 USB 明细（VID:PID / 设备名列表）**：诊断力更强，但属独立需求
  （页面需要可展开明细区），不在本次「确认连接数」范围内。

## Verification

- 后端单测（新增 14 例，全绿）：
  - `python -m pytest backend/agent/tests/test_device_discovery.py -q`
    — root hub/键鼠/存储过滤、空输出、畸形行、`lsusb` 缺失(`FileNotFoundError`)/超时/非零退出 → `None`、
    成功但无目标设备 → `0`、`subprocess.run` 调用参数（`["lsusb"]`, timeout=5）。
  - `python -m pytest backend/agent/tests/test_capacity_reporter.py -q`
    — 新键正确上报；默认 `None`；**回归保护**：`usb∈{0,1,8,99}` 时
    `health` 与除 `usb_device_count` 外全部 capacity 字段与不传时逐字段一致；
    USB 有设备也不能救活 adb 全死主机（`effective_slots==0` + `UNSCHEDULABLE`）。
- 前端单测：`npx vitest run src/components/network/ExpandableHostTable.test.tsx`
  — 5 例覆盖 正常 / `USB>ADB` 告警且 `在线` 不被污染 / `null` 显示 `—` / 字段缺失(旧心跳)
  显示 `—` / `0` 与未知区分；`HostsPage.test.tsx` 13 例同步通过。
- 全量回归：`python -m pytest backend/agent/tests/ -q` → **1560 passed**
  （需 `JWT_SECRET_KEY` 环境变量，否则 2 个 API 契约测试在 collection 期报错，为既有现象）。
- 门禁：`ruff check` 全通过；`tsc --noEmit` 退出 0；`eslint` 0 warning；
  `run_gates.py check:quick` 中 ruff/eslint/tsc 通过，**knip 报 `AssignProjectDialog.tsx`
  未使用——已 stash 本次前端改动复现，确认为既有问题，与本变更无关**（该文件为
  untracked，来自并行工作）。
- 现场取证：本机 `lsusb` 实测输出含 `Bus 001 Device 013: ID 0e8d:2046 MediaTek Inc. MLD-LX2`
  与 root hub/键鼠，解析结果 = 1（正确排除非目标设备）；端到端模拟
  「USB=2 / adb=1」产出 `capacity.usb_device_count=2`，前端应显示 `在线 1 | USB 2`(告警)。

## Revisit

- **口径误伤**：关键词黑名单是启发式的，真机若描述串命中 `camera`/`storage` 等词会被
  漏算。若现场出现 USB 数明显偏低，改为白名单（VID 命中已知厂商表）或直接数
  `/sys/bus/usb/devices/*/idVendor`（不依赖 `usb.ids` 描述文本）更稳。
- **`lsusb` 不在 PATH**：部分精简镜像未装 `usbmuxd`/`usbutils`。当前降级为 `—`；
  若大量主机长期显示 `—`，应考虑 Agent 安装期检查依赖或改读 sysfs（无外部命令依赖）。
- **告警阈值**：`USB > 在线` 仅上色提示，未进 `health.reasons`。若运维希望该形态
  参与降级/禁调，需单开 ADR 讨论——那会改变调度语义，不能由本 note 决定。
- **多 ADB fork-server（#160）**：`USB - 在线` 的差值目前无法区分「授权/驱动问题」与
  「另一 ADB server 抢占了设备」。若要精确定位，需结合 `capacity` 之外的
  `adb_multiple_servers` health reason 一并判读，或上报逐设备 USB↔adb 映射。
