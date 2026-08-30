# flash_firmware v1.3.7 锁内 boot 稳定等待

Status: implemented
Class: feature

## Decision

`flash_firmware` 升到 **v1.3.7**：post-flash verify 通过后**继续持有 host 串行锁**，
直到 `sys.boot_completed=1` 且 **MTK** USB 拓扑指纹连续稳定 `boot_stabilize_seconds`
（默认 20s）才交出锁，把「首刷二次重启窗口」消化在锁内。

- 新增参数（均有缺省，不破坏 v1.3.6 调用方）：`boot_stabilize_seconds`（默认 20）、
  `boot_stabilize_max_wait`（默认 120）。
- 新增 PROGRESS 阶段 `boot-stabilize`，打在 `done` **之前**；metrics 新增顶层键
  `boot_stable`（`boot_completed` / `stable_seconds_elapsed` / `ok` / `reason`）。
- 拓扑指纹 `_usb_topology_fingerprint` **只取 MTK 口**（vid `0e8d`），复用
  `_list_mtk_ports`——判据须与风险面同构：下一任持锁者的 flash_tool 只可能撞上
  MTK 可捕获态。非 MTK 的 USB 事件（hub 重枚举、串口适配器、键鼠插拔、邻机
  非 MTK 设备）不进指纹。
- 稳定窗口从**首轮读数**起算：首轮无上一次指纹可比，只记基线；此刻若已
  `boot_completed`，窗口即从当前读数开始累积，不空烧一个 poll 周期（默认 5s）。
- 失败语义保持 v1.3.4 兜底：boot 稳定超时（`ok=False`）**不判失败**（卡死设备不会
  重启，无窗口可撞）；verify 失败路径不等待、立即结算。

涉及文件：

- `backend/agent/scripts/flash_firmware/v1.3.7/flash_firmware.py`（新脚本版本）
- `backend/agent/scripts/flash_firmware/v1.3.7/capabilities.json`
- `backend/agent/tests/test_flash_firmware_v137.py`
- `backend/alembic/versions/s2t3u4v5w6x7_seed_flash_firmware_v137_params.py`
  （播种 v1.3.7 的 `param_schema` / `default_params` / `content_sha256`，
  并停用 1.0.0–1.3.5；v1.3.6 保留 active 作为回滚路径）

## Alternatives

**1. 不动锁，改为在 verify 里多等一段固定时间。**
放弃：二次重启的触发时点不可预估（取决于设备初始化耗时），固定 sleep 要么不够、
要么白等；且无法区分「已重启完」与「还没开始重启」，等待时长只能按最坏情况取，
对快 host 是纯损耗。

**2. 等 `boot_completed=1` 就放行，不看 USB 拓扑。**
放弃：`boot_completed=1` 只说明 Android 起来了，**不能**说明设备不会立刻再重启。
run 258 的 11 台错刷发生在「Download Succeeded」之后——设备已经起过一次，正是
`boot_completed` 会为真的时刻。拓扑指纹的作用是把「不再发生枚举变化」变成可观测
的持续条件，而不是一次性取值。

**3. 拓扑指纹取整棵 USB 树（初版实现）。**
放弃：host 上任何无关 USB 事件都会重置稳定窗口。只要有邻机周期性重启，窗口几乎
不可能收敛，每台都要耗满 `boot_stabilize_max_wait` 才按超时放行——守卫退化成每轮
白等（.68 串行 14 台 ≈ 多 21min）。收窄到 MTK 后，无关的 USB 抖动不再影响判定。

**4. 指纹只取目标端口本身。**
放弃：设备 reboot 时 sysfs 实例名可能变化（docstring 记的「新 USB 实例」），
只盯一个端口名会在重枚举瞬间读到空/变名，把「正在重启」误判成「稳定」。
MTK 全集既覆盖目标口的 pid 迁移，又对重枚举健壮。

**5. boot 稳定超时判失败。**
放弃：卡死设备不会重启，也就没有可撞的窗口——判失败只会把「刷写已成功」的设备
报成失败，与 v1.3.4 确立的「确认卡死即放行」兜底语义冲突。超时按放行处理，
`boot_stable.ok=False` 留在 metrics 里供诊断。

## Verification

- `pytest backend/agent/tests/test_flash_firmware_v137.py -q` → 16 passed。覆盖：
  - 指纹只含 MTK，排序稳定，非 MTK 抖动不改变指纹；
  - MTK 拓扑持续变化 → 窗口反复重置 → 超时 `ok=False`；
  - 非 MTK 抖动 → 不影响窗口收敛（本次收窄的回归守卫）；
  - 首轮即 `boot_completed` → 窗口不推迟一轮；
  - adb 不可达 / `boot_completed` 卡 0 / 空指纹 → `ok=False` 且不判失败；
  - main 集成：`boot-stabilize` 在 `_settle_lock()` 之前、verify 失败不等待、
    超时仍 `success=True`、`metrics.boot_stable` 落账。
- `pytest backend/agent/tests/ -q`（需 `JWT_SECRET_KEY`）→ 1261 passed，无回归。
- 迁移：`alembic` 单 head 校验为 `s2t3u4v5w6x7`；`pr-migrate-empty-db` 空库升级
  守卫；`_CONTENT_SHA256` 与磁盘 `sha256sum` 逐字比对一致。
- 真机判据（合入后观察）：run 258 同形态批量（.68 串行多首刷）中，
  「Download Succeeded 但目标设备未变」应从 11 台降到 0，且每台额外耗时
  应落在 `boot_stabilize_seconds` 量级（~30s），而非 `boot_stabilize_max_wait`
  量级（120s）——后者说明指纹又被无关事件拖住了。

## Revisit

- 若真机观察中 `boot_stable.ok=False` 占比偏高，先查 `metrics.boot_stable.reason`
  与同 host 的其它 MTK 设备活动：可能是邻机 MTK 重启把窗口拖住。届时考虑把指纹
  进一步收窄到「目标口 + 当前门控集合」，但须先确认目标口在重枚举期间 sysfs 名
  是否稳定。
- 若新增机型/平台（非 MTK）接入刷机，`_FLASH_STAGE_PIDS` 与 MTK-only 指纹都要
  跟着扩——指纹的 vid 过滤目前硬编码 `0e8d`，与门控同假设。
- `boot_stabilize_seconds=20` / `max_wait=120` 是 run 258 单批实证的初值，未经
  多 host 统计。跑满一轮 fleet 后应按 `boot_stable.stable_seconds_elapsed` 的
  分布重新标定。
