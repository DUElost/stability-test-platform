# PR #3705 R1 浏览器 / 内存证据（复核者可访问）

本目录把云端验收产物落到仓库内，供独立复核者无需 Cursor Agent / `/tmp` 即可审阅。

| 字段 | 值 |
|---|---|
| 证据绑定 tip（入库前） | `f796d8ba699a150ec5d5c7a4a0ec61ff11322c0a` |
| 产品修复 head | `265461eec77e7da0d59ddec2e4e7ad8ffc02d61b` |
| 浏览器 `report.json` 内 recorded head | `5f7b3896…`（跑 harness 时的工作树基线；产品字节与 `265461ee` 相同） |
| Draft PR | https://github.com/DUElost/stability-test-platform/pull/3705 |

## 文件

- `report.json` — Chromium 触屏 + 桌面完整报告（三入口、pageerror、请求样本、值快照）
- `summary.json` — 本轮验收计数摘要
- `screenshots/` — touch open/closed/reopen 与 desktop hover/focus/escape（18 张）
- `mutations/summary.json` — C.5 触屏路径回退红/恢复绿
- `memory-probe.txt` — cgroup / systemd / free / meminfo / prlimit / Node 堆探测原文
- `memory-limits.json` — 同上结构化结果
- `MANIFEST.txt` — 路径与字节数

## 浏览器关键结论（摘自 report.json）

- `touchPass=true`，`desktopPass=true`
- viewport 390×844，`isMobile=true`，`hasTouch=true`
- 三入口（editor / list / inspector）各自 open → close → reopen
- `pageerrors=[]`；无业务 PUT/POST；问号交互不改 values/step

## 内存限制探测结论（摘自 memory-limits.json）

- 进程 cgroup：`/system.slice/pod-…/cursor-agent/workload`
- 自 workload 上溯至 `system.slice`：`memory.max=max`、`memory.high=max`、`memory.swap.max=max`（**无硬顶**）
- `memory.current`（采集时）：workload ≈ 2.7 GiB；pod ≈ 3.4 GiB
- `systemd-run --user --scope`：`Failed to connect to bus: No medium found`；`/run/user/$(id -u)/bus` 不存在 → **无法在此环境建立 MemoryMax scope**
- 机器：`MemTotal=16387416 kB`（≈15 GiB），`SwapTotal=0`；`free -h` 同步
- 实际用于 vitest/浏览器的 V8 上限：`NODE_OPTIONS=--max-old-space-size=2048` → `heap_size_limit=2197815296`（≈2.05 GiB）；无该选项时 baseline ≈4.05 GiB
- **说明**：`NODE_OPTIONS` 只约束 V8 堆，不是 cgroup RSS；本探针如实记录「cgroup 无界 + user bus 不可用 + V8 堆帽 + 主机 MemTotal/Swap=0」。
