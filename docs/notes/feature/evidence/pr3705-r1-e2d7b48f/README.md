# PR #3705 R1 — 硬顶隔离重跑证据

本目录是在**可验证 cgroup v2 内存硬顶**内重跑方案 C 的入库证据（复核者可走 GitHub blob/raw，无需 Cursor `/tmp`）。

| 字段 | 值 |
|---|---|
| 验收时 tip（= `report.json` → `head`） | `e2d7b48fca6743e6c0f633758069874617591480` |
| 产品树 | `265461eec77e7da0d59ddec2e4e7ad8ffc02d61b`（`frontend/` 与 tip 相对仅 docs；产品字节同此 SHA） |
| Draft PR | https://github.com/DUElost/stability-test-platform/pull/3705 |
| 硬顶 | `/sys/fs/cgroup/stp-r1-hardcap` → **`memory.max=3221225472`（3 GiB）**，`memory.swap.max=0` |
| 峰值 | `memory.peak=1182744576`（≈1.10 GiB） |

上一轮 `pr3705-r1-f796d8ba/` 记录的是祖先 cgroup `memory.max=max`（无硬顶）环境；**不能**当作硬顶验收。本目录取代其为 R1 硬顶补强权威证据。

## 硬顶如何建立

```bash
sudo mkdir -p /sys/fs/cgroup/stp-r1-hardcap
sudo sh -c 'echo 3221225472 > /sys/fs/cgroup/stp-r1-hardcap/memory.max'
sudo sh -c 'echo 0 > /sys/fs/cgroup/stp-r1-hardcap/memory.swap.max'
# 验收进程：echo $$ > .../cgroup.procs 后执行
```

曾失败的路径（见 `hardcap-limits.json` → `alternatives_failed`）：

- `systemd-run --user`：无 user bus
- `systemd-run` system：主机非 systemd PID 1
- `docker run --memory=…`：环境无 docker
- 在 workload 子 cgroup 写 `memory.max`：Permission denied（`nsdelegate`）

另附 `NODE_OPTIONS=--max-old-space-size=2048`（V8 堆帽）；**主硬顶是有限 `memory.max`，不是 NODE_OPTIONS**。

## 文件

- `report.json` — Chromium 触屏+桌面完整报告；**`head` = tip `e2d7b48f…`**
- `summary.json` — 计数与硬顶摘要
- `hardcap-limits.json` — 有限 memory.max / swap.max / peak / 建立方式
- `logs/hardcap-before.txt` / `hardcap-after-browser.txt` / `hardcap-after-vitest.txt` / `hardcap-after-all.txt`
- `screenshots/` — touch open/closed/reopen + desktop hover/focus/escape
- `logs/vitest-11.log` / `type-check.log` / `check-quick.log`

## 结果摘要

- Browser：`touchPass=true`，`desktopPass=true`；三入口 open→close→reopen；`pageerror=[]`；无业务写
- vitest 11 文件：**208 passed** / exit 0（`--maxWorkers=2`）
- type-check：exit 0
- check:quick：**16 gates OK** / exit 0
