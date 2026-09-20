# capacity 增报 L2/L3/L4 分辨信号 + `total==0` 空树门禁（#2902）

Status: implemented
Class: bug-fix

## Decision

把 `docs/operations/host-device-visibility-triage.md` §3 里「仅记录、未改行为」的两处缺口
转成**平台信号**（全部只读，不参与槽位/健康计算）：

1. **L2/L4 分辨**：`device_discovery.count_adb_interface_devices()` —— 纯 Python 只读
   `/sys/bus/usb/devices/*:*.*/bInterfaceClass`（= `ff`）与 `SubClass`（= `42`），
   按 `:` 前设备名去重。读不到 sysfs → `None`（未知），**不当 0**（否则制造假 L4）。
   上报到 `capacity.adb_interface_count`；
2. **L3 分辨**：`device_discovery.bucket_adb_states(devices_list)` —— 复用本拍已抓到的
   设备列表按 `adb_state` 分桶（`device` / `offline` / `unauthorized` / `other`），
   **只统计 adb 可见行**（`adb_connected is True`）——不在 adb 列表里的设备正是 L2/L4
   要暴露的差集对象，混进桶里会让差值失去分辨力。上报到 `capacity.adb_state_counts`；
3. **空树门禁**：`capacity_reporter._usb_tree_empty()` + 新 reason **`usb_tree_empty`**
   （warning → DEGRADED）：`usb_device_count ≤ usb_root_hub_count`（lsusb 只剩 `1d6b:` 控制器）
   且 `total_devices == 0`。补的是**最严重却唯一不告警**的形态——旧判据
   `adb_low_healthy_devices` 要求 `total_devices > 0`，整树死亡（xHCI 失联/解绑）时恒显
   HEALTHY（.63 / 8.87 实测）。
   `usb_device_count`/`usb_root_hub_count` 任一为 `None`（采集失败）→ 不报警（未知 ≠ 空）。

**心跳开销**：`lsusb` 由「每拍 1 次」保持 1 次——新增
`count_usb_devices_and_root_hubs()` 一次采集取两数，`count_usb_devices()` 保留为薄包装
（历史调用方与测试不变）；state 桶复用本拍 `devices_list`，**零额外 adb 调用**。
payload 增幅**实测 98B**（<100B 预算）：`usb_root_hub_count` **不上报**（只作判据输入，
上报会到 121B——测量见 Note 的 Alternatives）。

**未做（issue 明示可选）**：Hosts 表加 L2/L3/L4 列/徽标——本单先把信号与门禁打通，
页面展示按 Hosts 页需求另单；`capacity` 两键已可直接被前端消费。

## Alternatives

- **继续靠 SSH 四层探针人工定位**：否决——#2902 的立单动机就是「页面信息量止步于在线 vs USB 差值」，
  值班每次都要上机；信号已在同一拍数据里，成本仅是序列化几个标量。
- **state 桶另起一次 `adb devices` 采集**：否决——本拍 `devices_list` 就是同一口径、同一时刻的数据，
  再调一次既多一次进程又可能与前文不一致。
- **把 `usb_root_hub_count` 一并上报**：否决——实测增幅 121B > issue 的 <100B 验收线；
  它只是判据输入，页面用不到（若将来要展示，按「同屏两个 key」的预算另议）。
- **让 `usb_tree_empty` 成为 blocking（UNSCHEDULABLE）**：否决——空树时本就没有设备可调度，
  打闸没有额外收益；warning 级足够让页面/告警可见，也避免与既有 blocking reason 语义混淆。
- **sysfs 读失败时返回 0（视同无 ADB 接口）**：否决——会把「权限/路径问题」误报成 L4，
  正是本单要消灭的假信号。
- **解析 `/sys/.../interface` 名称识别 MIDI（L4 细分）**：暂不做——`ff42 == 0 且 usb>0` 已能判 L4；
  名称级细分归 triage 文档的现场口径（避免把平台判据绑死在单一机型的 iInterface 文案上）。

## Verification

- `pytest backend/agent/tests/test_capacity_reporter.py backend/agent/tests/test_device_discovery.py -q`
  （`env -i`）→ **83 passed**（新增 15 例：空树 reason 的红/绿/未知面、L2-L4 键上报与缺省、
  payload 预算、sysfs 去重/大小写/不可读、root hub 解析、单次 lsusb、state 分桶）；
- `pytest backend/agent/tests/ -q -k "heartbeat or capacity or device_discovery or discovery"`
  → **202 passed**（心跳接线不回归）；
- `ruff check`（5 个改动文件）→ All checks passed；
- payload 实测量（json，紧凑分隔符）：`{"adb_interface_count":16,"adb_state_counts":{…}}` = **98 B**；
- `check:quick` → **[OK] 12 gates**；`check:pr` → **[OK] 21 gates**（含 agent-tests、
  pr-migrate 空库迁移 + seed 身份对拍）。

## Revisit

- **fleet 灰度核验**：与 #2900（xHCI 死亡告警）同窗落地时，合并做一次全 fleet 观察——
  重点看 `usb_tree_empty` 是否在「正常空树」（如备用机未接设备）上误报；若是，
  再评估加「连续 N 拍」或白名单。
- **页面展示**：Hosts 表 L2/L3/L4 徽标（issue 第 3 项，可选）待页面侧需求确认。
- **中间形态**：`total_devices == 0` 但 USB 树非空（有外设、无 Android 设备）仍只能上机探针——
  已在 triage §3 记为「遗留」。
- **采集成本**：sysfs 扫描是本地文件读（纳秒级）；若未来 hub 数量级变化导致心跳开销可见，
  再评估采样降频。
