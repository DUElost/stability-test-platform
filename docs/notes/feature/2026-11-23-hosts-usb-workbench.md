# `/hosts` 主机 USB / Hub 故障感知工作台升级（S2）

Status: implemented
Class: feature

## Decision

1. **确立 `/hosts` 为主机硬件 / USB / Hub 异常主工作台**（不塞入 `/storage`，`/storage` 保持纯存储与挂载域）：
   - `ExpandableHostTable` 顶部 `FilterWorkbench` 新增 **`USB / Hub 异常`** 快捷视图（URL 同步 `?sync=usb_fault`），汇总所有存在 USB/Hub/主控/探针对账异常且未退役的主机，支持「全部主机 ↔ USB / Hub 异常」一键往返切换；
   - 列表默认按 `hostUsbFaultPriority` 稳定排序，严重异常（主控死亡、探针连续告警、在用总线 Hub 半枚举）自动置顶；搜索词同时匹配 `host.id` 与中文故障标签，确保通知深链 `/hosts?q=<host_id>` 直达目标主机；
   - 主行直接外显最多 2 条紧凑故障芯片（如 `USB 主控失联`、`USB 链路劣化`、`SS Hub 半枚举(1)`、`探针对账异常`），不再把具体原因藏在「降级」hover tooltip 里。
2. **展开行新增「USB / Hub 与对账诊断」全宽卡片**：
   - 并排呈现 L1–L4 三数对照（USB 枚举 / ADB 接口 `ff:42` / ADB 在线 + state 分桶）、sysfs 外接级联 Hub 与无驱动半枚举 Hub（明确区分「闲置 SS 分支」与「在用总线」）、近 1 小时内核 USB 错误明细，以及控制面 SSH 拓扑对账探针结论；
   - 面向管理员提供一键「标记设备面已处置（空置）」/「解除设备面已处置」按钮（复用 `api.hosts.setDeviceIntent` / `clearDeviceIntent`），方便直接收口空柜/撤机产生的长期 `UsbBlind` 告警。
3. **与切片 S1 解耦兼容**：`HostsPage.tsx` 直接从 `host.capacity` 与 `host.extra.health_probe` 安全解析可选字段，不改动 `types.ts`，与 S1（PR #4112）零合并冲突，且在老 Agent 主机上平滑降级为「待 Agent 升级后上报」。

## Alternatives

- 将 USB/Hub 异常栏放在 `/storage` 页的 Agent 状态表中：放弃。`/storage` 职责限定为中心存储、NFS/CIFS 挂载与设备日志盘，混入 USB 硬件会破坏领域边界。
- 为 `/hosts` 新增独立子 Tab 拆分「主机列表」与「USB 监控」：放弃。复用 `FilterWorkbench` 快捷视图与展开诊断卡片无需页面跳转，运维可在同一行直接做诊断与操作（热更新 / 刷机前置 / 标记 `emptied`）。

## Verification

- Vitest 单测：`ExpandableHostTable.test.tsx` 与 `HostsPage.test.tsx`（覆盖互斥造数、`全部主机 ↔ USB / Hub 异常` 往返切换、异常置顶、主行外显芯片、展开诊断卡片及 `emptied` 按钮回调）。
- 静态与类型门禁：`npx tsc --noEmit`、`npx eslint`、`tests/test_host_health_reason_surface.py`、`scripts/run_gates.py check:quick`。
- 隔离预览与实机自检：待合入或按需走 `ui-preview-review` 核验双视口布局。

## Revisit

- 切片 S4（xHCI / L3 自愈接线与 `self_heal` 状态上报）落地后，在「USB / Hub 与对账诊断」卡片补充最近自愈动作、结果与熔断状态字段。
