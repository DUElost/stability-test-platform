# clear_recents v1.0.2：Clear all 后滑动关闭粘性残卡

Status: implemented
Class: bug-fix

## Decision

试跑 plan_run 463（v1.0.1）2/3 COMPLETED；设备 31 点到 `remove_all_button_layout`
后仍残留 `snapshot content-desc=Weather`，「Clear all」无法去掉。实机上滑
`task_view_single` 可关掉。v1.0.2：清除后若仍有应用卡，对残卡上滑再验收。

## Alternatives

- 把 Weather 加入排除名单：其它粘性卡仍会失败，否决。
- 只上滑、不点 Clear all：多卡场景更慢，保留 Clear all 作快路径。

## Verification

- 实机 Z2581（plan_run 463 失败样例）：Clear all 后 Weather 仍在；上滑后 `task_view`=0。
- 合入后 scan + 热更新；Plan 57 改指 `1.0.2` 再试跑 / 全量。

## Revisit

若某 OEM 上滑手势方向不同，再加 OEM 特判。
