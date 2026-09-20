# clear_recents v1.0.1：排除 ZTE「主屏幕」残留卡

Status: implemented
Class: bug-fix

## Decision

试跑 plan_run 462（v1.0.0）点到了 `remove_all_button_layout`，但验收按
`task_view_single` 计数——MiFavor 清空后仍留「主屏幕」卡，导致 2/3 job 失败。
v1.0.1 改为只计**应用任务卡**（snapshot content-desc 排除 `主屏幕`/`Home`），
raw 卡数进 metrics 留痕。

## Alternatives

- 把「点过即成功」：太松，漏点无法发现，否决。
- 原地改 v1.0.0：违反脚本不可变，否决。

## Verification

- 本地：`_count_app_tasks` 对「Chrome+主屏幕」→1、「仅主屏幕」→0。
- 合入后 scan + 热更新；Plan 57 改指 `1.0.1` 再试跑 / 全量。

## Revisit

若其它 OEM 清空后仍留非 home 残卡，再加 OEM 特判。
