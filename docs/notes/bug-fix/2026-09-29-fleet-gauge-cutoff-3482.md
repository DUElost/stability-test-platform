# #3482：`device_online{offline}` 陈旧剔除 cutoff 冻结在 import 时——Device 判据改惰性构造、每拍现算

Status: implemented
Class: bug-fix

## Decision

`backend/api/routes/metrics.py` 的 `_FLEET_GAUGES` 中 Device 行的 `extra_filter` 由
`Device.retired_at.is_(None) & not_stale_condition()`（import 时求值一次，7d cutoff 被
烤成 SQL 字面量）改为 **callable 惰性构造** `lambda: ... & not_stale_condition()`；
`_refresh_fleet_gauges` 循环内对 callable 形态每拍调用求值，与同函数里 `stale_count`
的 `stale_condition()` 同形态。指标名、标签、Host 行、其余 gauge 全部不动。

- 缺陷（#3470 引入）：cutoff 冻结为进程启动时刻后，运行中新转陈旧的设备仍满足
  冻结判据、继续计入 `device_online{status="offline"}`，同一拍又按当下判据落
  `stability_device_stale`——一台双计、`device_online` 各桶之和 ≠ 容量口径，
  漂移量 = 启动后新转陈旧台数，随 uptime 线性增长、重启才自愈。
- 范围核实（grep ground truth）：全仓 `not_stale_condition()` 消费点 4 处，仅
  `metrics.py:76` 这一处把求值放进模块常量；`metrics.py:159`（adb 分桶）与
  `devices.py:440`（列表）本就是每拍/每请求现算，不受影响、不在本单扩大。

## Alternatives

- **把 Device 行的 filter 整体移出元组、在 `_refresh_fleet_gauges` 里硬编码**：issue 给的
  另一合法形态，但会破坏元组驱动的统一循环（Host/Device 两行同构遍历），为省一个
  callable 分支引入特例分支，不采纳。
- **让 `not_stale_condition()` 默认返回未绑定时间的延迟求值对象**：改动服务层单一真源
  的返回契约，波及 4 个消费点与它们的测试，远超本单必需范围，不采纳。

## Verification

- `backend/tests/api/test_metrics_fleet_gauge_cutoff_3482.py`（新增）+ 既有
  `test_metrics_device_lifecycle_2962.py`：4 passed（cgroup 硬顶 + testcontainers PG）。
  - 跨拍反例：monkeypatch `services.device_lifecycle.normalize_now` 为可控假钟，
    同一进程内 T0 时刻拉一拍（offline=1、stale=0），时钟推进 +2d 越过 7 天界后再拉
    一拍（offline=0、stale=1）——判据消费方之外无任何状态变化，两桶按当下重算、
    无一台双计；
  - 对账断言（验收可选项）：任一拍 `Σ device_online 各桶 + stability_device_stale`
    恰等于 DB 直查的非退役设备总数（双计 2>1 / 漏计 0<1 当场红），推进前后各对账一拍。
- **变异自证**：把 lambda 还原为 import 时直接求值（复现 #3470 缺陷形态）→
  跨拍反例转红（`1 failed`：拍 2 offline 桶仍为 1.0，与 stale=1.0 双计）；恢复修复后
  4 passed。
- `python scripts/run_gates.py check:quick` → OK（见 PR 正文命令记录）。

## Revisit

- 无。cutoff 语义（`STALE_AFTER_DAYS = 7`、现算不落库）未动；若未来陈旧阈值改为可配置，
  本 callable 形态天然跟随每拍读取，不需要再改。
