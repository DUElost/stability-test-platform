# B-1 收尾：unauthorized 设备可操作提示（USB 调试未授权）

Status: implemented
Class: bug-fix

## 根因

生产 2 台设备（A2WENX6628xxxx @ .69、A2WENX6628xxxx @ .77）长期
`status=ERROR / adb_state=unauthorized / model=NULL / platform=UNKNOWN`，
成为「未映射」型号级待办的真身（型号级口径下待办 = 2 台）。

SSH 实测确认：`adb devices` 显示 `unauthorized usb:1-8`，同 host 其余 16-17
台全 `device`。`adb -s <serial> reconnect` 无效（设备端拒绝，需点弹窗）。
Agent 侧 `device_discovery.py:collect_device_info` 对非 "device" 状态直接判
error 不探测 shell——**平台无 bug，是设备端 USB 调试授权未确认**（物理
动作：设备上允许授权 / 重新插拔）。

## Decision

平台侧可操作化（无法替人点弹窗，但要让「型号采不到」有解释）：

- `DeviceTableData` 增 `adb_state` 透传（types 权威源 types.ts 已有该字段）
- 设备行 model 旁新增「未授权」badge（destructive 色），tooltip 写明
  「USB 调试未授权：请在设备上允许授权，否则无法采集型号/平台」
- 顺手修 `plan_dispatcher_sync.py` 一处 `.where()` 参数缩进错位（B-3，
  E128 级别，ruff 不拦）

恢复路径：授权确认后下一轮心跳 `model=MLD-LX3`（同 host 同款设备实测
型号），经成员行自动归入 V552AA，「未映射」待办自动清零——派生模型
无需任何回填。

## Alternatives

- SSH `adb kill-server` 重启 host adb server 触发重新弹窗：影响同 host
  全部设备瞬时重连 + 打断在跑 job 的 adb 会话，生产风险不可接受，放弃
- 平台侧自动跳过/标记设备：掩盖了「未授权」这个可修复状态，放弃

## Verification

- 前端 4 文件 13 测试全绿（DevicesPage / ExpandableDeviceTable 等）
- 后端 test_plan_dispatcher 全绿（缩进改动无行为变化）
- 生产：SSH 只读确认 unauthorized（前）→ 待授权后心跳验证 model 恢复

## Revisit

若 unauthorized 设备反复出现，可考虑 Agent 侧 `adb -s <serial> reconnect`
重试或心跳级告警；当前 2 台属一次性物理问题，不做。
