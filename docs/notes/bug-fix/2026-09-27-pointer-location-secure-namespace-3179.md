# pointer_location 写入 Settings.Secure 命名空间（#3179，批次 B1 / G6）

Status: implemented
Class: bug-fix

关联：[#3179](https://github.com/DUElost/stability-test-platform/issues/3179)、[#3463](https://github.com/DUElost/stability-test-platform/issues/3463)（批次 B1 规划方案 §3 G6 / §1 独立项）。
前置：[device-bulk-swipe-trail Note](../feature/2026-09-22-device-bulk-swipe-trail.md)。不关单。

## Decision

按 #3463 §3 G6：`pointer_location` 属 `Settings.Secure`，原实现以 `settings put system pointer_location`
写入（[`control_handler.py:111`](../../../backend/agent/control_handler.py) 的循环里硬编码 `"system"`），
system 命名空间下该键不生效——「滑动留痕」批量开关实际只打开 `show_touches`。

`_SWIPE_TRAIL_SETTINGS` 改为携带命名空间：
`(("system", "show_touches"), ("secure", "pointer_location"))`，循环改为 `for namespace, key in ...`
并透传 `namespace`，其余白名单/超时/逐 serial 失败语义不变。仍是硬编码两条，不接受任意 shell。

## Alternatives

- **保留 `_SWIPE_TRAIL_SETTINGS` 只存键名、在循环里按 key 特判命名空间**：把「哪个键属于哪个命名空间」的
  知识埋进条件分支，新增键时容易再错。未采用。
- **两条命令各自硬编码**：重复三遍 `settings put <ns> <key>` 的 argv 组装，与现有单循环结构冲突。未采用。

## Verification

- `pytest backend/agent/tests/test_control_handler_736.py -q -k "control_handler or swipe_trail"`：
  新增 index 6 命名空间断言（`calls[0][6] == "system"` / `calls[1][6] == "secure"`），全部通过。
- 反例：把 `("secure", "pointer_location")` 临时改回 `("system", "pointer_location")`，新断言失败；改动已恢复。
- 通用门禁（`check_script_packages.py` / `check_tool_manifest.py --base origin/main` / `check:quick`）结果见 PR 正文。

## Revisit

设备端真实效果（触摸点 + 指针轨迹同开同关）需在 Agent 热更新后由生效阶段真机抽验；本 PR 不部署、不发布。
批次 §6 明确不做其余族 F2 解码与跨族 helper 一致性守卫，本单元不涉及。
