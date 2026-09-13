# UnisocUniviewReconciler _processed 集合防膨胀：滞回裁剪（#767）

Status: implemented
Class: bug-fix

## Decision

`UnisocUniviewReconciler._processed`（per-serial 持久化去重集，
`watcher:unisoc:{serial}:processed_event_dirs`）原实现只增不减、每有新事件
整集序列化重写——状态存储体积与写放大按设备历史事件总量线性增长（UNISOC
事件由设备持续产生，比 MTK db_history 高一个量级，见 issue）。

关键语义约束（决定裁剪判据）：集合存的是**设备事件目录名**（非 stamp
前缀），同时承担两个去重职责——`_sync_device_events_to_local` 靠它跳过
重拉、`tick_once` 靠它跳过重发。因此按 issue 首选的「按 stamp 滚动」直接
裁剪会让设备仍存留的旧事件被重拉+重发（重复信号）。改为**按可再触发性**
裁剪：一个名字只有仍在本拍设备列表（会被重拉）或仍在当前 stamp 本地树
（会被重扫）才需要留在集合；两者皆非且连续
`STP_WATCHER_UNISOC_PROCESSED_PRUNE_AFTER_TICKS`（默认 3）拍如此 → 移除。

实现要点：
- `_sync_device_events_to_local` 顺带记录本拍设备列表（`_last_listed`）：
  任一 root `ls` 失败（返回 None）→ 整拍记为「未知」，裁剪挂起并清零滞回
  计数（防 adb 抖动误删 → 误重拉重发）；成功但为空是权威「无名字」。
- 体积硬上限 `STP_WATCHER_UNISOC_PROCESSED_MAX_ENTRIES`（默认 20000）仅作
  最后防线，优先驱逐滞回计数最大（最久未见）的名字，驱逐打 warning。
- state key 与 JSON 格式不变——**零迁移**：存量全量集在 adb 恢复后随滞回
  自然收敛到设备实际存量量级（~设备现存事件数 + 当前 stamp 本地树）。
- MTK 侧 `db_history.py` 同模式不在增长路径（其 key 为 db_history 行，目录
  消费后不再新增，issue 正文已判明），不动。

## Alternatives

- 按 `run_date_stamp` 分 key 存储/滚动清理——需要 state schema 变更 + 旧键
  迁移，且跨 stamp 去重弱化为「每 stamp 至多重发一次」（设备未清理旧事件
  时产生重复信号），行为回归不可接受。
- 只加体积上限、不做语义裁剪——治标：超限驱逐仍可能驱逐「设备仍存留」的
  名字（重发），且写放大照旧；语义裁剪把集合天然压到设备存量量级后，
  上限仅是兜底。
- 等真机量化日事件量再定阈值——滞回拍数与上限均可 env 覆盖，默认值保守
  （3 拍 ≈ 9 分钟 @180s 间隔），无等待价值。

## Verification

- `backend/agent/tests/test_unisoc_reconciler.py` 12 passed（新增 7 例）：
  stale 名字滞回 2 拍后裁剪、live 名字保留且落盘；本地树名字（设备已清理
  但 stamp 未滚动）保留；列表失败挂起裁剪 + 滞回清零；存量 1000 名字集合
  2 拍收敛到设备列表；被裁剪名字不被重拉、设备仍在册名字不重拉；硬上限
  驱逐最久未见者且 live 保留；仅裁剪（无发射）也触发 state 落盘。
- 相邻面（reconciler/aee/unisoc/watcher/state 关键字）363 passed。
- `python scripts/run_gates.py check:quick` 7 gates 全绿。

## Revisit

- 阈值（滞回 3 拍 / 上限 20000）为保守缺省，真机量化单设备日事件量后可经
  env 调优；若实测设备端事件轮换快于 stamp 粒度，再评估按 stamp 分 key 的
  schema 方案（本单 Revisit 前提：语义裁剪已把增长压平，schema 改造无紧迫性）。
- `db_history.py` 的整集读写模式维持原判（不在增长路径），若 UNISOC 侧
  裁剪上线后观察到 MTK 侧 state 也膨胀，另立单。
