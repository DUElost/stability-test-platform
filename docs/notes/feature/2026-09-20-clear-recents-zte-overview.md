# clear_recents：概览键清空最近任务（中兴样机批量）

Status: implemented
Class: feature

Execution：`clear-zte-recents-overview`

## Decision

1. **新脚本 `clear_recents/v1.0.0`**：用 ADB 打开系统 Overview（`KEYCODE_APP_SWITCH`），
   点击「全部清除」类控件，用于批量清空最近任务界面——不是卸包/清日志
   （那是 `clean_env`）。
2. **ZTE 选择器以实机 dump 为准**（Z2581 / MiFavor）：
   `com.zte.mifavor.launcher:id/remove_all_button_layout`（可点）+
   `remove_all_button`（content-desc=`全部清除`）。脚本同时接受通用文案
   「清除全部 / Clear all」以便非 ZTE 机型尽力而为。
3. **必须先 HOME 再 APP_SWITCH**：实机上若前台是 Chrome，单按概览键会进
   Chrome 标签页切换器而非系统最近任务；脚本固定唤醒→解锁→HOME×2→再开概览。
4. **执行路径**：停掉占用中兴样机的 RUNNING PlanRun → 脚本合入并
   `POST /scripts/scan` → 新建单步 Plan（`script:clear_recents`）→ 按项目
   `A57`（customer=中兴）圈选样机执行。

## Alternatives

- **扩 `clean_env`**：语义混杂（卸包/清日志 vs UI 最近任务），否决。
- **只 swipe 关掉卡片**：比点「全部清除」慢且易漏，否决为默认路径。
- **`am stack remove` / service call**：API 随 Android 版本漂移，ZTE 上未验证，
  保留为日后 fallback，本版不做。

## Verification

- 只读生产库：customer=中兴 / 项目 `A57` 共 281 台（Z2581/Z2582）；当时
  RUNNING PlanRun **457**（约 173 台租约）、**458**（2 台）。
- 实机 UI dump（空闲 ONLINE 样机）：确认 `overview_panel` +
  `remove_all_button` / content-desc `全部清除`。
- 门禁：`python tools/dev/check-script-version-immutability.py --base origin/main`
  （新版本目录，应无 conflict）。
- 合入后：控制面 `POST /api/v1/scripts/scan` → `created` 含 `clear_recents@v1.0.0`，
  `conflicts=0`；小流量 1～3 台 Plan 成功后再全量。

## Revisit

- 若其它 OEM 概览无「全部清除」而只有逐卡关闭，再加 swipe fallback 版本。
- 实机点按全链路验证曾被本会话自动审批拦截；合入后应用小流量 Plan 做真机验收。
