# Host 设备可见性排查固化为四层判别表

Status: implemented
Class: process

## Decision

新增运维文档 [`docs/operations/host-device-visibility-triage.md`](../../operations/host-device-visibility-triage.md)：
把「Hosts 页 在线 vs USB 差值 / 设备从 adb 消失」的排查固定为**四层判别**——
L1 内核/USB 子系统、L2 主机 adb server、L3 设备 adbd/授权、L4 设备 USB 功能集——
每层给出只读判据（含可复制的 sysfs/ps/dmesg 命令）、处置边界与「哪些层 adb 侧无解」，
并写入 2026-09-14 的两次现场实证。文档入口挂到
[`docs/operations/README.md`](../../operations/README.md)（Agent 部署节）与
[`production-diagnostics.md`](../../operations/production-diagnostics.md)（安全边界节）。

关键结论（文档正文 §1/§4）：

- 判别主轴是 **「ADB 接口(ff:42) 设备集合 vs `adb devices` 列表集合」的包含关系**：
  `接口 ⊋ 列表` 才判 L2（主机 adb server）；两侧相等则属 L3/L4（设备侧）。
- L4 的实测形态：MediaTek 机型 `0e8d:2046` 暴露 `01:01`(iInterface=`MIDI function`)
  +`01:03`，无 ADB 接口 → 任何 server 都看不到；`0e8d:201c` 才是 `ff:42`
  `ADB Interface`（正常态）。此口径与 `backend/agent/scripts/flash_firmware/*`
  注释里「2046=普通态」的表述不同——那里指「非刷机态」，不代表有 ADB。
- 2026-09-14 全 fleet（48 台）15 台不一致中：**0 台满足 L2 判据**（每台单一
  5037 fork-server、0 台 `adb_multiple_servers`）；≈93 台为 L4、≈26 台为 L3。
- kill/start-server 对照实验（5 台，含健康对照）证实 **server 重启不改变任何一层
  的枚举结果** → 「重启 adb server」不应作为默认修复动作。

影响面：纯文档（新增 1 文件 + 2 处入链 + 本 note），无代码、契约、env 或行为变化。

## Alternatives

- **只在 issue/评论留痕**：下次排查仍要重新摸索四层判据与命令；且 2026-09-11 起
  `USB n` 列已把该症状暴露给所有看板用户，排查方法应随症状一起入库。
- **直接并入 `production-diagnostics.md`**：该文是凭据来源与安全边界（稳定面），
  而排查表会随机型/现场演进、且需要代码行号锚点；混入会把边界文档稀释成 runbook。
- **只写「设备侧」结论、不列 L2**：L2 是被明确问到的一类；不显式列出，会让
  「重启 server」成为默认反射（本次实测无效），也无法说明何时它才是对症动作。
- **顺带上报接口层计数并加告警（agent 改动）**：属行为/契约变更，需独立评估
  （页面对 L2/L3/L4 的不可区分见文档 §3 缺口），不在本次范围。

## Verification

- 全量只读探针（2026-09-14，15 台不一致主机逐台）：`接口集合 == adb 列表集合`；
  每台 `pgrep` 仅 1 个 5037 fork-server；DB 侧 48 台全 HEALTHY、0 台
  `adb_multiple_servers` / `adb_low_healthy_devices`。
- kill/start-server 对照实验（5 台，含 1 台健康对照）：重启前后 `adb devices` 与
  接口数逐台不变（L3 的 offline 行也原样保留）、server PID 换新、心跳 7–14s 恢复、
  对照机 20/20 无回归。
- 文档锚点：正文引用的 file:line（`capacity_reporter.py:117,141,156`、
  `device_discovery.py:178`）与入链目标均已在本分支核对存在。
- 门禁：`python scripts/run_gates.py check:quick`（本 PR 分支实跑，见 PR 描述）。

## Revisit

- Agent 若补报接口层信息（`ff:42` 计数或按 state 的 adb 计数）落地：页面/告警可从
  「人工上机探针」升级为远程自诊断，届时删除文档 §3 的缺口一节。
- 若现场再出现 `接口 ⊋ 列表`（L2 实例）：需补充 server 卡死的现场特征（fd 上限、
  重枚举规模、多 server 端口组合），并复测 kill/start-server 在该形态下是否恢复。
- 机型口径变化（新 PID、新 USB 功能集）时：更新文档 §1 的实测注记，不新建文档。
