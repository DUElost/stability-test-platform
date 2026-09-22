# clear_recents v1.0.4：读失败先消耗一次尝试，不再零重试即终结整步（#3104）

Status: implemented
Class: bug-fix

## Decision

`#2976` 把「UI dump 读失败判成 success 并伪造 `tasks_after=0`」修成「读失败一律转红」，
方向正确，但落地形状过紧：`_fail_read` 在重试循环内部被无条件调用后 `return`，于是

- `max_attempts`（docstring 自述「打开概览+点击的重试次数」）对**读失败**这一类失败
  **完全不可达** —— 一次瞬时 `uiautomator dump` 抖动就让整步变红；
- 而本脚本记录的目标故障形态（设备重启窗里 dump 失败）恰恰是**瞬时**的；
- 且模板/Plan 侧该步 `retry` 未设（默认 0），引擎不会补重试 ⇒ 这一层重试只能由脚本
  自己承担。

新建 `clear_recents/v1.0.4/`（v1.0.3 不可原地改）：

1. 新增 `_retry_read(reason, attempt_no)`：循环内读失败记入 `metrics.read_errors`
   并 `continue` 消耗一次尝试；**只有最后一次尝试仍失败**才走 `_fail_read` 转红。
2. 四个读失败位全部改走它（首次读取 + tap 复核 + 两处 swipe 复核）—— 不是只修
   首次读取：复核位失败同样是「这一次尝试没结论」。
3. `metrics` 增 `read_errors: []`（瞬时失败的证据，成功路径也能看到抖动过几次）。
4. docstring 补 v1.0.4 段并修正 `max_attempts` 的语义描述（现在它同时覆盖读失败）。

**判据一条都没放松**：读失败永不落成功、`tasks_after` 永不编造、耗尽后
`ui_read_failed=true` —— 本版本动的是「几次尝试算耗尽」，不是「失败算不算失败」。

## Alternatives

- **在 v1.0.3 上原地加重试**：违反「已发布版本不可原地修改」硬不变量。否决。
- **只重试首次读取，复核位读失败仍立刻红**：半修。复核位失败与首次读取失败同因
  （同一 dump 命令、同一设备状态），只修一半会让「点击后抖动」这一常见形态继续整步红。
- **改模板/Plan 的 `retry` 让引擎补重试**：不成立。引擎重试是**整步重跑**，会把已
  完成的 tap 语义再走一遍；而本缺陷要的是「同一次尝试内不因读失败丢掉这一轮」。
  两者语义不同，且 `retry` 会连带放大设备操作次数。
- **把 `_dump_ui` 自己做成内部重试 3 次**：也能覆盖，但它把「几次」藏进函数内部，
  与 `max_attempts` 形成两套节奏；本实现让读失败与其它失败**共用同一个尝试预算**，
  语义只有一处。

## Verification

- `pytest -q backend/agent/tests/test_clear_recents_v104.py backend/agent/tests/test_clear_recents_v103.py`
  → **12 passed**（新增 5 例 + v1.0.3 既有 7 例无回归）
- 新增用例逐条对 AC：
  - 首次读失败 + 第二次读到 ⇒ **绿**，`attempts=2`、`read_errors` 恰 1 条、成功分支的
    `tasks_after` 出自一次成功读取；
  - 所有尝试都读失败 ⇒ 仍**红**、`ui_read_failed=true`、`tasks_after` 保持 None、
    `read_errors` 2 条、错误文案「2 次尝试均未读到 UI 层级」；
  - `max_attempts=1` ⇒ 一次失败即红（不吞重试）；
  - tap 后复核位读失败 ⇒ 同样进入重试并最终绿（`tapped=true`）；
  - **对照锚点**：同一「瞬时读失败」输入下 `v1.0.3` 判红且无 `read_errors` 字段 ——
    差异确由 v1.0.4 引入（锚点若失效，本文件前提需重审）。
- `scripts/run_gates.py check:quick` → OK；`check_governance_surface.py` → OK（S1–S15、S5x）。

## Revisit

- **生效前置是 scan + 重指**：只读视图现为
  `pending-activation: clear_recents v1.0.4 -> unregistered`。新版本要经
  `POST /scripts/scan` 注册激活后才可被选中；而**在飞的 plan 57 快照仍钉 `1.0.2`**
  （假绿版本），要走重指/重建才切到修复版。本 PR 只保证「下次用到时行为正确」。
- 本版本**不覆盖**「read 失败但 dump 文件是上一次的陈旧内容」这一形态：`_dump_ui`
  读的是 `dump_path`，而 `uiautomator dump` 每次覆盖写同名文件；若某次 dump 静默
  失败而 cat 读到旧文件，仍会被当成一次成功读取。要堵它需要在 dump 前 `rm -f`
  （`docs/notes/bug-fix/2026-09-20-...` 的 H5 建议里提过）—— 本 PR 不夹带，因为
  它会改变「dump_path 由参数指定、可能被并发复用」的既有语义，需独立裁决。
- `read_errors` 目前只落 metrics（stdout JSON）；是否要进 `StepTrace` 之类的观测面
  取决于在飞步骤指标出口的裁决（同 `#2975` 的 `STP_SHARED_METRICS` 无消费者问题）。
