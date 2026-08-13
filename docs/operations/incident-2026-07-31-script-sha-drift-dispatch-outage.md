# 2026-07-31 全平台派发中断复盘：脚本版本 sha 漂移

> **状态**：已闭环（PR #109 合入 `9ec8e1f`，生产 re-baseline 已执行，派发恢复）。派发恢复后暴露的 WiFi SSID 配置缺口另见 §4，非本事故根因。
> **影响范围**：**控制面全部 Plan 无法派发**。平台上仅有的 2 个 Plan（`Monkey专项-watcher-patrol`、`smoke-plan-001`）100% 在准入阶段 `script_verify_failed`，持续时间自 2026-07-23 首次脚本改动起（最严重的一批自 07-26 `ef8808e` 起）。无数据损坏，无设备侧影响。
> **关联**：[ADR-0020](../adr/ADR-0020-plan-step-one-shot-migration.md)（脚本目录契约）、Epic #107（20 台 host 全流程打通）。
> **区别于**：[9.126 硬挂事故](./incident-2026-07-28-host-9-126-hard-hang-and-bios-upgrade.md)、[8.87 xHCI USB 事故](./incident-2026-07-29-host-8-87-xhci-death-and-adb-outage.md)、[#93 AEE Reconciler 崩溃](./adr-0026-admission-and-scale-gray-rollout.md) —— 根因互不相同。

---

## 1. 现象

单设备触发 Plan 2（`POST /api/v1/plans/2/run`，`device_ids=[44]`）：

```
plan_run_id=108  QUEUED → FAILED（<5s）
dispatch=failed  last_error=script_verify_failed
backend log: admission_failed plan_run=108 reason=script_verify_failed
```

`total_job_count=0` —— 在准入阶段就被拒，未创建任何 Job 或租约。

初期误判为「同一 host 上跑了两个 Agent 实例导致 fencing 冲突」。清理掉 19/20 台 host 的重复 Agent 进程后**故障依旧**，该假设被证伪（重复进程是真问题，但与本故障无关，已单独处置）。

---

## 2. 根因

### 2.1 三方哈希比对

在 host `198.51.100.132` 上比对 Plan 2 引用的 7 个脚本：

| 脚本 | DB `content_sha256` | 控制面本地 | 实机 | 判定 |
|------|--------------------|-----------|------|------|
| `check_device` v1.0.0 | `12627fc7b` | `7d46e7229` | `7d46e7229` | 不一致 |
| `ensure_root` v1.0.0 | `7e28db943` | `2bdca06e4` | `2bdca06e4` | 不一致 |
| `monkey_setup` v1.0.0 | `4752a2e65` | `48fa87d90` | `48fa87d90` | 不一致 |
| `monkey_teardown` v1.0.0 | `dde7eda3c` | `e2a19d968` | `e2a19d968` | 不一致 |
| `monkey_check` v2.0.2 | `03b78ee7c` | `03b78ee7c` | `03b78ee7c` | 一致 |
| `monkey_launch` v5.0.0 | `f7ebce03e` | `f7ebce03e` | `f7ebce03e` | 一致 |
| `monkey_resource_push` v1.0.0 | `f8d512a65` | `f8d512a65` | `f8d512a65` | 一致 |

**关键观察：控制面本地 == 实机，全 7 个都一致。** 不一致只存在于 DB ↔ 实际文件之间。

### 2.2 为什么自愈推送修不好

`admission_pump` 的自愈链是 `gather_verify` → `push_mismatched_scripts`（SSH/SFTP）→ reverify。但推送的源是**控制面磁盘**，而实机上本来就是同样的字节 —— 推送是个 no-op，reverify 自然还是对不上 DB。这构成**永久失败循环**：每次准入都推一遍、每次都失败。

### 2.3 DB 侧为什么陈旧

`script` 表 27 行**全部**在 `2026-07-09 20:22` INSERT，`updated_at` 此后再没变过。而 ADR-0020 的扫描语义是：

> created(INSERT) / skipped(sha256一致) / **conflicts(sha256不一致,不动DB,须新建版本)** / deactivated

也就是说，文件被原地改过之后，无论点多少次「扫描脚本」，这些行的 `content_sha256` **永远不会更新** —— 这是设计使然，不是 bug。当时不存在任何受支持的修复通路。

### 2.4 是谁改的

2026-07-09 之后改动 `backend/agent/scripts/` 的提交：

| 提交 | 日期 | 说明 | 性质 |
|------|------|------|------|
| `ee1110f` | 07-23 | `refactor(agent): centralize AIMonkey resource path resolution` | 有意的行为重构 |
| `43e1be6` | 07-25 | `fix(agent): surface script failure detail and non-zero exits` | 有意的缺陷修复 |
| `ef8808e` | 07-26 | `refactor: ruff --fix 清理 262 处未使用导入/死代码` | **全仓机械改写，作者无意触碰脚本目录** |
| `cb56edd` | 07-28 | `feat(agent,docs): #72 AEE Reconciler 实机验收闭环` | 新增脚本 |

`ef8808e` 一次就原地改写了 14 个已发布版本目录。`ruff.toml` 当时没有排除 `backend/agent/scripts`，也没有任何门禁拦截 —— 一次常规 lint 清理静默打断了生产派发，且**故障要到下次派发才显现**。

### 2.5 全量影响面

全表 27 行中 **18 行漂移**：

```
check_device v1.0.0    clean_env v1.0.0       connect_wifi v1.0.0
ensure_root v1.0.0     fill_storage v1.0.0    install_apk v1.0.0
monkey_check v1.0.0    monkey_launch v1.0.0   monkey_launch v4.0.0
monkey_setup v1.0.0    monkey_setup v1.1.0    monkey_setup v1.2.0
monkey_setup v1.3.0    monkey_teardown v1.0.0 monkey_test v1.0.0
monkey_test v1.1.0     push_resources v1.0.0  stop_aimonkey v1.0.0
```

平台上仅有的 2 个 Plan 全部受阻。

---

## 3. 处置

### 3.1 恢复能力：`force_rebaseline`

新增 `POST /api/v1/scripts/scan?force_rebaseline=true`：把 conflicts 的 `content_sha256` / `nfs_path` 重锚到磁盘字节，返回 `rebaselined[{name, version, old_sha256, new_sha256}]`。

约束：

- 仅 admin（沿用 `require_admin`），审计动作记为 `scan_rebaseline`（区别于普通 `scan`）
- 有在途 PlanRun（`RUNNING`/`QUEUED`/`PRECHECK`）时返回 **409 `PLAN_RUN_IN_FLIGHT`** —— re-baseline 会改变 precheck 的期望值，不能在运行中途换
- 普通 `scan` 路径行为完全不变

**这是逃生阀，不是新的工作方式。** 它承认的是「契约已被上游破坏」的既成事实。正常改脚本一律新建版本。

#### 为什么不是「为 18 个漂移版本各建新版本」

那才是 ADR-0020 的正道，但在这个具体场景下代价大而收益为零：

- 要复制 18 个目录、改两个 Plan 的全部 PlanStep 版本引用
- **换不回已经丢掉的东西**：v1.0.0 的旧字节早已被 git 覆盖，历史 PlanRun 的可复现性在文件被改的那一刻就没了，DB sha 只是让它可见
- 不装门禁的话，下一次全仓 lint 会原样再来一遍

所以选择「一次性重锚 + 装门禁」。代价是明确的：**放弃「某个版本号恒等于同一批字节」这条历史保证一次** —— 而这条保证事实上已经在 07-23 就失效了。

### 3.2 防复发：CI 门禁

`tools/dev/check-script-version-immutability.py` —— 与 base ref 做三点 diff，任何对 `backend/agent/scripts/<name>/v<ver>/` 下文件的**修改/删除/改名**直接 CI 失败（新增版本目录放行）。

两处设计取舍：

- **连 `_` 开头的辅助模块一起拦**（如各版本目录里的 `_adb.py`）。扫描器不把它们算进 entry sha，所以改它们**连 conflicts 都不会报** —— 比改入口文件更隐蔽的漂移，只有门禁能拦。
- **不提供豁免开关**。违约后的正确动作是新建版本或把改动撤出该目录，加豁免等于把门禁关掉。

同时 `ruff.toml` 把 `backend/agent/scripts` 加进 `extend-exclude`，从源头挡住机械改写；门禁是手改的兜底。

门禁已用真实事故提交验证：以 `ef8808e~1` 为基线运行，准确拦下全部 14 个文件并退出码 1。

### 3.3 生产 re-baseline 执行记录

**执行时刻**：2026-07-31 13:52–13:57 CST（PR #109 合入 `9ec8e1f` 后）

前置检查：在途 PlanRun = 0、在途 Job = 0（守卫放行）；`stability-backend.service` 重启加载新代码，`/health` 的 `saq_ready` / `admission_queue_flag` / `admission_queue_pump_ready` / `admission_queue_enabled` 均 true。

干跑（普通 `scan`，不带 force）：`created=1 skipped=9 deactivated=0 conflicts=18` —— 与事故前的漂移扫描完全吻合。`created=1` 是 `cb56edd` 新增的 `aee_signal_trigger`，此前从未入库。

`force_rebaseline=true` 执行结果：**重锚 18 个版本，`conflicts` 归零**。

| 脚本 | 版本 | old sha | new sha |
|------|------|---------|---------|
| `check_device` | v1.0.0 | `12627fc7b` | `7d46e7229` |
| `clean_env` | v1.0.0 | `b837ef686` | `56c9989fb` |
| `connect_wifi` | v1.0.0 | `09ffef00f` | `abc30ee4d` |
| `ensure_root` | v1.0.0 | `7e28db943` | `2bdca06e4` |
| `fill_storage` | v1.0.0 | `28f56777e` | `fa49d826a` |
| `install_apk` | v1.0.0 | `0fa252cc6` | `ce4a0d7d7` |
| `monkey_check` | v1.0.0 | `40d22c435` | `f34e1f76c` |
| `monkey_launch` | v1.0.0 | `e8d02e036` | `9562a8ad1` |
| `monkey_launch` | v4.0.0 | `7d01d7475` | `0a8984f06` |
| `monkey_setup` | v1.0.0 | `4752a2e65` | `48fa87d90` |
| `monkey_setup` | v1.1.0 | `da3bf18ad` | `8aaf5d96d` |
| `monkey_setup` | v1.2.0 | `c208f8cd1` | `ccedac15a` |
| `monkey_setup` | v1.3.0 | `1de8b0121` | `9f36e129c` |
| `monkey_teardown` | v1.0.0 | `dde7eda3c` | `e2a19d968` |
| `monkey_test` | v1.0.0 | `d4d6d490d` | `50e9f2229` |
| `monkey_test` | v1.1.0 | `d4d6d490d` | `50e9f2229` |
| `push_resources` | v1.0.0 | `abe5d74a2` | `f89df98ab` |
| `stop_aimonkey` | v1.0.0 | `e29b95353` | `ebb0a753b` |

复扫确认稳定：`created=0 skipped=28 conflicts=0 rebaselined=0`。

**派发恢复验证** —— PlanRun 109（Plan 2，单设备 44 @ `198-51-100-132`）：

| 指标 | 事故中（PlanRun 108） | 修复后（PlanRun 109） |
|------|----------------------|----------------------|
| `dispatch` | `failed` | **`completed`** |
| `last_error` | `script_verify_failed` | **`None`** |
| `total_job_count` | `0`（准入即拒） | **`1`** |
| pipeline 执行 | 未开始 | `check_device` → `ensure_root` **通过**，停在 `monkey_setup` |

`script_verify_failed` 已消除，派发链路恢复。PlanRun 109 最终仍 FAILED，但**根因不同**，见 §4。

---

## 4. 派发恢复后暴露的下一个断点：WiFi SSID 未配置

PlanRun 109 的失败原因：

```
lifecycle init failed: step failed in init:
  monkey_setup: Step 'wifi' failed: No SSID configured
```

这是被派发中断**掩盖了的既有配置缺口**，与 sha 漂移无关。

`monkey_setup` 的 `step_wifi` 取 SSID 有两条来源：`cfg["ssid"]`（即 `default_params.wifi.ssid`）或环境变量 `STP_WIFI_SSID`。实测三条可能的供给路径**全部为空**：

| 供给路径 | 现状 |
|----------|------|
| `script.default_params` | `monkey_setup` v1.0.0–v1.3.0 **全部是 `{}`**；`plan_step` 表无 `params` 列，参数完全来自 `default_params` |
| 平台 ResourcePool 注入 | `inject_wifi_params` **只改 action 含 `connect_wifi` 的步骤**，且仅当 lifecycle 里存在该步骤时才分配。Plan 2 没有 `connect_wifi` 步骤 → 完全不触发 |
| Agent 环境变量 | 抽查 `9.132` / `9.93` / `9.131`，`/opt/stability-test-agent/.env` 里 **一条 `STP_WIFI_*` 都没有** |

生产库里 `resource_pool` / `resource_allocation` **两张表都存在**（`resource_pool.config` 存 `{ssid, password}`，`max_concurrent_devices` 控并发），`/api/v1/resource-pools` 的 CRUD 路由也齐全 —— **机制是建好的，只是 0 行、从未配置过任何 WiFi 池**。

设备侧实况（device 44 @ 9.132）：`Wifi is enabled` 但 `Wifi is not connected` —— 这一步确实有活要干，不是可以跳过的空转。

另一处关键事实：`monkey_setup` v1.0.0 与 v1.3.0 **全文只差一行**，就是缺省步骤表：

```python
v1.0.0:  step_names = args.get("steps", ["wifi", "root", "push", "install", "fill", "clean"])
v1.3.0:  step_names = args.get("steps", ["root", "push", "install", "clean"])
```

v1.3.0 已经不含 `wifi`（同时也去掉了 `fill`），而 Plan 2 指的是 v1.0.0。

可选处置（需业务侧确认 Monkey 测试是否必须联网）：

| 方案 | 动作 | 代价 |
|------|------|------|
| A. 配 Agent 环境变量 | 20 台 `.env` 加 `STP_WIFI_SSID` / `STP_WIFI_PASSWORD`，`reload_config` 热刷新 | 需要真实 SSID/密码；凭据散落在 20 台 host，且无法按执行选择 |
| B. 新建 `monkey_setup` 版本 | 缺省步骤表去掉 `wifi` | 仅当测试永不需要联网才成立；无法按执行选择 |
| C. 走资源池正道 | 配置 WiFi `resource_pool` + 执行时选池 | 机制已建好（表 + CRUD 路由 + 分配 + 并发上限），只需接通「执行时可选」这一段 |

> `default_params` 对已存在版本 422 不可变，方案 B 必须 `POST /api/v1/scripts/{name}/versions` 新建版本。

**业务侧结论（2026-07-31）**：WiFi 连接应当**在计划执行前可选**，并非必须连接，但要保留连接选项 —— 即方案 C 的方向。落地设计见 §7。

---

## 5. 复盘要点

| # | 要点 |
|---|------|
| 1 | **「不动 DB」的冲突策略需要配一条显式出路。** ADR-0020 让 scan 遇冲突不落库是对的（防止静默改变已发布版本），但没有任何受支持的解冲突通路，于是一旦发生就是死局。防御性设计要留可审计的逃生阀，而不是无路可走。 |
| 2 | **契约靠文档写着、不靠门禁拦着，就一定会被机械工具破坏。** ADR-0020 白纸黑字写了「须新建版本」，`ef8808e` 的作者也完全无意碰脚本目录 —— 全仓 `ruff --fix` 不看 ADR。 |
| 3 | **故障与成因隔了 5 天。** 07-26 埋下，07-31 派发时才炸。这类「下次使用才显现」的缺陷，唯一有效的拦截点是提交时。 |
| 4 | **自愈机制会掩盖根因。** `push_mismatched_scripts` 每次都"成功"推送并报告成功，让日志看起来像是在自愈，实际是 no-op。自愈动作应校验推送后哈希是否**真的变成了期望值**，而不只是校验 SFTP 传输成功。 |
| 5 | **先证伪再修。** 最初的「双 Agent 实例」假设被清理动作直接证伪（清完故障依旧）。重复 Agent 进程确实是真问题、也确实清理了，但它不是这个故障的原因 —— 两件事分开记。 |

---

## 6. 遗留项

| 项 | 说明 |
|----|------|
| entry sha 不覆盖 `_` 辅助模块 | `_adb.py` 改了不进 sha、verify 查不出。当前靠 CI 门禁堵，但运行期仍无检测能力。若要闭环，需把版本目录整体哈希（而非仅 entry）纳入 `content_sha256`。 |
| 自愈推送不校验结果哈希 | 见复盘要点 #4。 |
| 历史 PlanRun 可复现性 | 07-23 之后的 PlanRun 记录的脚本版本号已不能唯一确定当时执行的字节。仅作已知事实记录，不打算追溯修复。 |
