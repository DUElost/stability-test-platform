# /devices 设备页新增存储空间指标（disk_total/disk_used 渲染半边）

Status: implemented
Class: feature

## Decision

设备存储指标以**纯前端渲染**交付：#2757 已把 `adb shell df /data` 的
`disk_total/disk_used`（字节）打通到 Agent 心跳 → device 表 → `DeviceOut` →
`types.ts`，唯独 /devices 页没有消费。本次在 `ExpandableDeviceTable` 加「存储」
列（电量同款进度条 + 「剩 X GiB」文本）与展开详情「存储空间」卡（已用/百分比/
可用/总容量），`DevicesPage` 透传 `disk_total/disk_used`；可用空间由
`disk_total - disk_used` 前端推导，不新增采集字段、不动后端与迁移。

字节→可读串新增 `frontend/src/utils/format.ts:formatBytes`（1024 进制
B/KiB/MiB/GiB/TiB，与 FileServerPage 本地实现同口径；df 解析本身即 1024 进制）。
用量配色复用 `resourceUsage*Class`（≥90% critical、≥70% warning），满盘设备
（如 #3066 的 .75 100% 机）在列表一眼可见。列遵循 B7 惯例：全部设备无上报时
整列不渲染；行内无数据显式「—」，不伪造 0。

## Alternatives

- **后端新增 `disk_avail` 列**：avail 在 `parse_df_data` 里已折进 total（单行形态
  total=used+avail），再存一列是冗余事实源；拒绝。
- **把 WS `DEVICE_UPDATE` 补上 disk 字段做实时推送**：设备列表本就 10s 轮询，
  disk 采样节流 300s，推流增益为零；改动面还牵扯 heartbeat 路由与 materiality
  判定；拒绝，留作 Revisit。
- **直接改 FileServerPage 复用其私有 formatBytes**：属顺手重构，越出本 Requirement
  边界；新增共享 helper 即可。

## Verification

- `npx vitest run`（format / ExpandableDeviceTable / DevicesPage 三文件）28/28 绿；
  含列渲染、全空隐藏、详情卡与页面透传断言。
- `npx tsc --noEmit` 绿；`scripts/run_gates.py check:quick` 14 门禁全绿。
- 真浏览器核验（build + vite preview 挂 /api 代理到 127.0.0.1:8000，Playwright
  登录 admin 后只读浏览 /devices）：「存储」列出现于温度后，采样 50 行中 42 行
  显示真实「剩 X GiB」，未上报行为「—」；展开行详情卡完整读出「存储空间
  已用 29.6 GiB 62% 可用 18.4 GiB 总容量 (/data)」；无 console 错误。
- 生产库只读核对（SELECT only）：627 台 30 分钟内活跃设备中 467 台已有
  disk_total——#3066 所述「589 台全空」在 #2757 部署后已痊愈，渲染半边有真实
  数据可显示。

## Revisit

- 覆盖率仍非 100%（约 74%），未上报多为老 Agent 版本或静态设备；若需按版本
  推进可走 control-plane-deploy 的 scan 核验，不属本单。
- 若将来要在列表页直接排序/筛选低余量设备，需要后端把 avail 语义固化（当前
  df 采样节流 300s，排序抖动可接受度届时再判）。
- WS `DEVICE_UPDATE` 是否需要携带 disk 字段，取决于是否出现「10s 轮询不够快」的
  真实运维诉求（#3066 场景窗前提早暴露可借此再议）。
