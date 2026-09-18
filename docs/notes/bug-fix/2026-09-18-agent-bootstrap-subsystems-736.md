# #736 切片：disk/watcher 启动抽出 `bootstrap_subsystems`

Status: implemented
Class: bug-fix

## Decision

把 `main()` 里 LogArchiver → scan/upload → EventUploader → LocalDiskMonitor →
可选 Watcher 栈的 **configure/start** 整段迁到
`backend/agent/bootstrap_subsystems.start_disk_and_watcher_subsystems`，返回
`OutboxDrainer | None` 供停机分支使用。

`reload_config` 仍留在 `main`（force=True 窄集、不重开 watcher）——避免把热更新
语义绑进冷启动路径。

同 PR 棘轮：`main.py` 1177 → **1087**，封顶 **1142**（×1.05）。

## Alternatives

- **连同 reload_config 一并抽象**：弃——热更新与冷启动参数集不同，强行共用易
  引入 watcher 重复 configure。
- **直接上 `AgentApplication`**：弃——先垂直搬家，再谈阶段容器。

## Verification

- `pytest backend/agent/tests/test_bootstrap_subsystems_736.py` +
  `test_main_lifecycle_784.py` → **14 passed**
- `check:quick`（除本机 `schema-at-head`）：pending

## Revisit

- 下一刀：control handler / 身份注册段，或真正的 `AgentApplication` 薄壳挂
  `initialize` / `start_background` / `run_loop` / `shutdown`。
