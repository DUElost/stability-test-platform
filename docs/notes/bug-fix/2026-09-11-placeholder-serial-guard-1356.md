# 占位 serial 设备检测标记与漂移告警（#1356）

Status: implemented
Class: bug-fix

## Decision

GPU 压测派发两次被 `device_host_drift` 拦截（run 360/362）：设备 280 的
serial 是 adb 默认占位值 `01234567****CDEF`（设备未上报真实 serial），同一
serial 被 `172-21-x-x` 与 `172-21-x-x` 两台 host 同时识别——
`device.host_id` 随心跳反复漂移（哪个 host 后上报就改归属），派发窗口内
快照与当前不一致被保护拦截。保护机制工作正确，缺的是**事实可见化**：
设备列表仍显示 ONLINE、归属不稳无任何标记。

修复（issue 方向 1/2 的可见化部分，不改归属更新语义与派发保护）：

1. 心跳 upsert 检测已知占位 serial（内置 `01234567****CDEF` /
   `12345678****CDEF` / `0000000000000000`，`STP_PLACEHOLDER_DEVICE_SERIALS`
   可扩展）→ `device.tags` 打 `placeholder_serial`（去重）+ 首次 warning；
2. 同一占位 serial 跨 host 上报（`previous_host_id != host.id`）→
   `placeholder_serial_host_drift` warning（记录 from/to host）；
3. 设备列表的 tags 列/筛选即见标记——运维据此执行方向 3（人工确认设备、
   重刷/换线使上报真实 serial）。

## Alternatives

- **占位 serial 设备自动排除出可派发池**——放弃：归属不稳≠不可用（设备
  280 历史 28 jobs/17 COMPLETED）；自动排除会剥夺可用性且掩盖问题；标记
  + 告警让人工在「用或换」之间决策（issue 方向 3）；
- **阻断占位 serial 的 host_id 更新（冻结归属防漂移）**——放弃：冻结到
  错误 host 会让 job 派到没有该设备的机器（claim 失败静默），比漂移拦截
  更难诊断；
- **模式匹配（如全同字符/16 位十六进制）泛化识别**——放弃：误报风险高
  （真实 serial 形态多样）；内置已知值 + env 扩展是可审计的保守面。

## Verification

- **反例实证**：回退 heartbeat.py 保留测试 → 2 用例失败（无标记/无漂移
  告警）；修复版全绿；
- 新增用例（`test_heartbeat.py::TestPlaceholderSerialGuard` +2）：
  首次心跳打标 + `placeholder_serial_detected` 告警 / 跨 host 漂移
  `placeholder_serial_host_drift` 告警且**打标不重复、归属更新语义不变**；
- `test_heartbeat.py` 全套 **30 passed**；
- `check:quick` 与 PR 门禁：见 PR 描述。

## Revisit

- 运维动作（issue 方向 3）未含在本单：设备 280 需人工确认（重刷/换线让
  上报真实 serial）；处理后 `placeholder_serial` tag 可人工移除（重新
  上报真实 serial 不会再打标）；
- 若占位设备反复造成派发失败，可评估「派发预检显式提示占位设备」的
  增强（当前仅列表标记 + 派发保护拦截）。
