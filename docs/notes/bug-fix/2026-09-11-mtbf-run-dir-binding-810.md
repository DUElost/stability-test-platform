# mtbf 三件套 run_dir 绑定（#810）

Status: implemented
Class: bug-fix

## Decision

新增三个脚本版本（已发布版本不可原地改）：`mtbf_setup/v1.4.0`、
`mtbf_check/v1.5.0`、`mtbf_finish/v1.6.0`，把 run_dir 从「各步各自取最新」改为
**setup 观测并绑定 → check/finish 只认绑定**：

- **共享库（三份 `_lib.py` 同源新增）**：`run_dir_marker_path()` /
  `save_run_dir(project, run_dir)` / `load_run_dir(project)` / `clear_run_dir()`。
  绑定落在 **Agent 主机本地**状态文件，按设备 serial 键控、按 project 匹配
  （三件套在同一主机同设备的同一 Job 生命周期内串行执行，主机本地交接最简且
  不依赖 adb 推文件）。
- **setup v1.4.0**：记录步骤起点 `started_at`，`_wait_run_dir(timeout, newer_than)`
  要求候选目录设备端 mtime 不早于起点（留 2s 容差）——残留的上一轮 run_dir
  不再让新任务「没起来也报启动成功」；成功后 `save_run_dir(project, run_dir)`。
- **check v1.5.0**：`_bound_run_dir(project)` 优先读绑定；无绑定才回退最新目录
  并打戳 `mtbf_check_run_dir_unbound_fallback`（兼容旧 setup / 手动执行）。
- **finish v1.6.0**：`_pull_results(project)` 只 pull 绑定 run_dir（无绑定回退
  最新），归档成功后 `clear_run_dir()`，避免下一 Job 误用残留绑定。

设计文档 §3.5 同步补记 v1.6.0 绑定语义（原「以 setup 记录的 run_dir 为准」由
本 PR 落地）。纯行为变更，无 `default_params`/`param_schema` 变化 → 无需 seed
迁移；扫描自动建行。

影响面：三个新版本目录 + `tests/test_mtbf_run_dir_binding_810.py` +
`docs/design/2026-08-mtbf-p0-runner-design.md`。

## Alternatives

- **经 STP_STEP_PARAMS 回传 run_dir**：该通道由控制面在派发时注入，无法承载
  setup 运行时才产生的值；设备/主机本地状态文件是可行交接面。
- **设备端文件交接**：需 adb push/pull 与权限/清理，主机本地文件更简且三件套
  同主机已成立；跨机场景不存在（设备绑定单一 host）。
- **仅改 `_wait_run_dir` 不绑定**：check/finish 仍会取最新目录，残留目录导致
  check 数上一轮数据、finish 混入历史 entries，未闭环。
- **一次性改现有版本**：违反不可变门禁并使在途 Plan precheck 失败。

## Verification

- `tests/test_mtbf_run_dir_binding_810.py`：绑定往返 + 项目匹配 + 按 serial
  键控；setup 写绑定且含 `newer_than`；check 优先绑定且有回退留痕；finish
  优先绑定并清理；新旧版本目录并存。6 passed。
- `python tools/dev/check-script-version-immutability.py --base origin/main` →
  无已发布版本被原地改动。
- 脚本目录 `ruff` 由 `ruff.toml` extend-exclude 排除（与既有脚本一致）。

## Revisit

- 绑定不清理由 setup 覆盖 + finish 清理双保险；异常中断残留由下一次 setup 覆盖。
- 若三件套未来可能跨主机执行，绑定需迁到设备端或控制面（当前架构不成立）。
