# host 健康 reason 指标化 + USB 失明告警 + 内核日志通道可见化（#2900 / #2957）

Status: implemented
Class: bug-fix

## Decision

#2900 的检测半边（PR #2911）把 xHCI 死亡判据落在了 agent 上，但**控制面收到 reason 之后
没人响**：`api/routes/heartbeat.py` 只做 `host_extra["health"] = payload.health`，指标注册表
没有按 host 暴露 reason 的 gauge，告警文件里没有任何 expr 引用 degraded/reasons。本单补那
最后一跳，并在补的过程中撞出一个更大的事实，一并处理。

1. **拉取期现算，不在心跳里 push**（`_refresh_host_health_gauges`）。理由不是风格：
   `/metrics` 由任意 worker 渲染，push 式 per-host gauge 会被"哪个 worker 接到这次抓取"
   决定可见性（本仓未启用 `PROMETHEUS_MULTIPROC_DIR`）；而 DB 里的那份 `host.extra` 对所有
   worker 同值。与 `_refresh_fleet_gauges` / `_refresh_host_device_adb_gauges`（#1258/#2754）
   同族口径：在册（`retired_at IS NULL`，ADR-0038 D5）+ `status=ONLINE`，DB 抖动只跳过本组
   不拖垮整次抓取，旧 child 用差集 `remove`（#2791）。

2. **两个 gauge 的差集各自独立**。第一版共用一份"在册 host"集合，实测是错的：一台 host 可以
   通道仍上报、`health.reasons` 消失（agent 重启后 health 块未重建），共用差集会把它上一轮的
   `usb_host_controller_dead 1.0` 留在表上——**冻结的故障值比缺失更坏**，它看起来像"还在失明"。
   用例 `test_reason_children_sweep_even_when_channel_still_reports` 就是为这个真错写的（M3 变异验证）。

3. **reason 词表是封闭的，并绑回 agent 源码**（`_HEALTH_REASONS` +
   `tests/test_host_health_reason_surface.py`）。不在控制面 import agent 模块：词表一半是
   `capacity_reporter` 的字面量、一半是 `kernel_usb_faults` 的常量，混两种口径比统一抄一遍
   更容易漂；守卫测的是真值本身（AST 抽 agent 侧，双向对拍），抄错当场红。词表穿透四个地方
   （agent 产出 → 控制面分桶 → 告警选择器 → 前端 `REASON_LABELS`），任何一处缺值都是**静默**
   失效，所以四处都在对拍面里。**本轮实测抓到一处存量缺口**：#2902 新增的 `usb_tree_empty`
   没有前端标签（页面上会显示裸键），随本单补上。

4. **只告 #2900 自己的两条 reason**。`usb_tree_empty`（#2902，零权限判据）今天就有 2 台命中，
   但这 2 台的 `device` 表行数为 0——"本来没接设备"与"整树死亡"在该判据里同形。给它建告警
   等于上线即 2 台常亮，而 paging 面一旦有已知误报，真 reason 就会被一起忽略。判据需要补的
   那道合取属 #2902/#2957 的射程，不在本单里替它拍阈值。

5. **通道可用性单独可见化（#2957）**——本单过程中撞出的更大事实：

   - `backend/agent/install_agent.sh` 写死 `User=android`，全文只 `usermod -aG dialout`，
     从未加入 `adm`/`systemd-journal`；
   - 本机同构复现（uid 1000、groups 不含二者）：`journalctl -k --boot` **退出码 0**、
     stdout 只有 `-- No entries --`、stderr 是权限提示——与"内核干净"完全同形，正是
     `kernel_usb_faults` 按"未知"处理的那条路；
   - 零权限备用路也不存在：`/proc/sys/kernel/dmesg_restrict = 1`，非特权 `open("/dev/kmsg")`
     实测 `[Errno 1] Operation not permitted`（`dmesg` 走 syslog(2) 同被拦）；
   - 生产库只读实测：48/48 台在册 host 全部 `ONLINE`、全部带 `extra.health`、心跳最大滞后
     21s；`health.reasons` 词频只有 `usb_tree_empty`（2 台），两条 USB 内核 reason **0 台**；
     48/48 的 `agent_code_revision` 已含 #2911。⇒ **代码到位、通道全黑**。

   结论：只建前两条告警会得到一条永不触发的规则（#1958"死锁四周零指标"、#1257"引用不存在
   的标签"的同族）。所以本单同时上报判据通道本身（`capacity.usb_kernel_log`），并加**一条
   fleet 级**规则（过半且 ≥5 台 unavailable 才红）——per-host 会在上线时点出 48 条红灯，
   把"覆盖缺口"这条真信息淹成噪声。

   通道态刻意放 `capacity` 不放 `health.reasons`：它是传感器状态不是主机故障，进 reasons 会
   把 fleet 全刷成 DEGRADED，而"页面一片红却没有一台设备真出问题"正是让运维学会忽略红色的
   那类做法。它与 `usb_device_count` 同族：纯观测，不参与槽位/打闸（用例钉住）。

6. **心跳 payload 增量单独计预算**，不并进 #2902 那条"三键合计 <100B"断言：合并会让两个
   issue 的验收项互相绑架（任一侧改名就把对方打红）；实测本键最坏取值 31 字节。

## Alternatives

- **在 `heartbeat.py` 里 push gauge**：省一次查询，但多 worker 下可见性取决于抓取落到哪个
  进程，且退役/掉线 host 的末值会冻结。否决。
- **不 import 而是直接把 agent 词表搬进控制面（无守卫）**：#1257 那类"选择器指向不存在的值"
  会重新变成无人看的东西。否决，改为 AST 双向对拍。
- **agent 直接判"日志不可读"就上报 `usb_host_controller_dead`**：把未知当故障，会在所有
  非特权 host 上恒亮——比现状更糟。否决。
- **给 agent 用户加 `systemd-journal` 组让通道亮**：这是权限扩张（全机 journal 可读，含
  其他用户条目），且 48 台都要重跑安装步骤，属 ops 决策。留作 #2957 的 A 选项，不在代码里
  替它签字。
- **fleet 全量升级前不建任何告警**：那等于回到"平台收到但没人响"。选择"建告警 + 建覆盖缺口
  告警"，让绿/红都携带真信息。

## Verification

真实跑过的命令与结果（未跑的在下方 pending）：

- `promtool check rules deploy/prometheus/alerts-stability-platform.yml` → `SUCCESS: 29 rules found`
- `promtool test rules deploy/prometheus/alerts-stability-platform.test.yml` → `SUCCESS`，38.5s；
  同机基线（origin/main 的两个文件）32.25s ⇒ 本单增量约 +6s。6h 窗口的三组场景用 `interval: 10m`
  把仿真步数降一个数量级（`for: 6h` 的判据不需要分钟级分辨率）。
- `pytest tests/test_prometheus_alerts_contract.py -q` → `39 passed in 168.66s`（含逐条阈值漂移
  自证：新 3 条规则各被单独打红）
- `pytest tests/test_host_health_reason_surface.py -q` → `9 passed`
- `pytest backend/tests/api/test_metrics_host_health_gauges.py -q` → `6 passed`
- `pytest backend/agent/tests/test_kernel_usb_faults.py backend/agent/tests/test_capacity_reporter.py -q`
  → `65 passed`（本单新增 5 + 4 条用例与 1 条接线断言）
- `pytest backend/tests/api backend/agent/tests -q` → 见下"存量对照"段
- `ruff check`（改动的 py 文件）→ All checks passed
- **变异自证**（每次改完即还原并复跑）：
  - M1 从 `/metrics` handler 摘掉刷新调用 → `6 failed`（含 `test_reason_gauge_is_refreshed_on_the_scrape_path`）
  - M2 把"未知"当"干净"（无 health 也落全 0）→ 第 1 轮**只被 1 条用例抓到**：另一条用例因
    "刷新整段抛错也表现为 series 缺失"而**假绿**；已加同一次抓取的对照组 host 补掉这个假阴性，
    复跑 M2 → `2 failed`
  - M3 两个 gauge 共用一份差集（本单第一版的真错）→ `1 failed`，正是那条专门为此写的用例
- **生产只读实测**（psycopg3，`application_name='diag-2900-*'`，只 SELECT）：见 Decision 5 的
  4 条数据；连接串取自 `.env.backend` 且未落任何文件/输出（一次 psycopg 异常把 DSN 回显在
  终端，已确认未写入仓库、评论与 Note）
- **中心告警装载面实测**：`/etc/prometheus/prometheus.yml` `rule_files: rules/*.yml`，
  实跑 `/usr/bin/prometheus`（127.0.0.1:9091）+ alertmanager；仓库文件 vs 副本的 alertname
  集合：副本 23 条、仓库 26 条，缺 `StabilityChainCoverageGap` / `StabilitySkillUsageHollow` /
  `StabilitySkillUsageUntrusted`，**副本无手改痕迹**（`live - repo` 为空）。
  `tools/dev/check-monitoring-assets.py` 同点报出该文件漂移（它按 #2643 方向 1 期望的是
  `site-alerts.yml` 的 2 条，与中心这份人工副本本就是两套口径）。
- 顺带纠了一处文档谎话：`docs/operations/README.md` 写"实测该副本落后仓库 2 条规则"，
  今日实测为 3 条——该计数不可被任何门禁派生，已改为指向派生方法而非抄数字（#2663 同一口径）。

**pending（未完成，勿当已验证）**：

- [ ] 本 PR 的 3 条规则**尚未进入生产装载面**：中心侧是人工副本，需按 `docs/operations/README.md`
      §6 重放 + `POST /-/reload`。合并 ≠ 生效。
- [ ] `capacity.usb_kernel_log` 需 agent 代码分发后才出现；在那之前 fleet 全为 `unknown`
      （计入分母不计入分子，故不会伪报覆盖，也不会误报"读不到"）。
- [ ] 无实弹：不注入真实 xHCI 死亡（issue 已声明不做自动 rebind，也不做破坏性验证）；判据以
      离线回放 + promtool 场景为证据。
- [ ] 观察窗内 0 误报的验收要等规则真上线后另记。
- [ ] #2957 的 A/B/C 裁决未发生 ⇒ `usb_host_controller_dead` 在现网**预期恒不触发**，这是
      已知状态而不是"修好了"。

## Revisit

- 若 #2957 选 B（承认零权限通道）：给 `usb_tree_empty` 补"DB 里本来有设备"的合取后再接告警，
  阈值由该判据的 owner 按 fleet 现状定（本单已给出误报形状与实测台数）。
- 若选 A/C：`state="unknown"` 占比应在一次 fleet 发布后归零；两周后仍非零 → 查 agent 代码发布
  链路（与 `StabilityScriptGuard*` 那批"仓库改了、站点没跑"同族失效）。
- `StabilityUsbKernelLogChannelDark` 是**过渡告警**：终态出口就是它长期为绿（通道全覆盖）后被
  删除，或降级为纯面板指标。留久了它会变成又一个没人看的红灯。
- ADR-0011 的正式挂载落地后，删掉 `docs/operations/README.md` 与本 Note 里"人工副本"的口径。
- `host.extra` 里 `health.reasons` 的自由度仍大于本单词表：若将来 agent 侧改为可配置 reason
  集，`other` 兜底桶需要一条自己的告警（现在是刻意不告，避免把"新 reason 刚发布"当故障）。
