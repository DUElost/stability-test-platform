# 心跳逐设备日志：稳态降 DEBUG、事实翻转才 INFO（#2960）

Status: implemented
Class: bug-fix

## Decision

现场量级（issue 正文，2026-09-20 实测）：`logs/backend.log` 轮转后单文件 **776 MB /
5,745,482 行**，其中 `backend.api.routes.heartbeat` 占 **3,924,674 行 = 68%**，两条源头都是
**每设备每心跳一条 INFO**（`device_adb_update`、`device_status_online`）。626 台 ONLINE 设备
× 每 5s 一拍 ⇒ 这是稳定量级而不是突发。日志体积决定的是「出事后还查得到多久之前的现场」，
所以这不是审美项。

1. **按事实变化留痕，不按拍留痕**。同一台设备的同一事实每 5s 重播一次，第 2 次起信息量为 0；
   有用的那部分是「什么时候翻的」——正好等于只在变化时记。
2. **降级别，不删日志**，且**文案逐字不变**：稳态走 DEBUG。排查单机时把该 logger 调回 DEBUG
   即恢复全量轨迹，既有 grep 习惯不破。只测「INFO 没了」的守卫会放过最坏的实现（直接删掉），
   所以这一条被单独钉成用例 `test_steady_state_is_downgraded_not_deleted`。
3. **日志门与落库门共用同一份判定**：把 `device_update_is_material` 的前三条（adb_state /
   adb_connected）抽成 `device_connectivity_changed`，写库与日志各调它一次。两份定义迟早朝
   相反方向漂移——日志先变吵，或者审计先变哑。
4. **状态四支的级别判定移到赋值之后**。本单**第一版就写错了**：`status_level` 在 if/elif 链
   之前算，比的是 `device.status`（那时仍是旧值）与 `prev_status`（同一个旧值）⇒ 恒判稳态，
   连真翻转都会降成 DEBUG。是新用例 `test_adb_fact_flip_still_logs_at_info` 当场抓的红，
   不是人工读出来的。现在四支共用模块级 `_device_status_log_level(prev_status, new_status,
   is_new_device)`，签名把「新值 vs 心跳前的旧值」写成显式，判据只有一处。
5. **不新增「每拍汇总」日志行**。按 48 台在册 host × 12 拍/分钟估算 ≈ 83 万行/日，相当于
   用一种新的重复去替换旧的重复（21% 量级），不划算；统计量已经有 Prometheus（`stability_host_*`
   族），要汇总去指标里要。

## Alternatives

- **整块降 DEBUG（不按变化判）**：最省事，但把「设备什么时候掉的」这条最该留痕的事实一起变哑——
  #107 / #2569 辛苦建的成因链会退回原点和日志里找。否决。
- **采样 / 每 host 每 N 秒一条**：稳态仍被重复记录，而且采样点会让**翻转时刻**模糊到 N 秒，
  恰好毁掉唯一有价值的信息。要少又要准，"只在变化时记"严格优于采样。
- **删掉 INFO 行不留 DEBUG**：现场排障单机时失去逐设备轨迹，等价于把成本转嫁给 DB 手查
  （而 #2632 刚刚把「不要用超级用户手查生产库」写成红线）。
- **顺手做结构化日志改造**：本单的问题只是体积与信噪比，不做格式迁移（那是另一个 Requirement）。

## Verification

跑过什么：

- `pytest backend/tests/api/test_heartbeat_device_log_noise_2960.py` → `4 passed`
- `pytest backend/tests/api/test_heartbeat_device_log_noise_2960.py backend/tests/api/test_heartbeat.py`
  → `35 passed`（既有 31 条心跳用例无回归）
- `pytest backend/tests -q`（全量 backend，含 `dashboard_summary` 的 materiality 用例）→ 结果见 PR 回填
- `pytest tests/ -q --deselect tests/test_prometheus_alerts_contract.py` → 结果见 PR 回填
- `ruff check backend/api/routes/heartbeat.py backend/services/dashboard_summary.py
  backend/tests/api/test_heartbeat_device_log_noise_2960.py` → All checks passed
- **变异自证**（每次改完即还原复跑）：
  - M1 把 `device_adb_update` 退回无条件 `logger.info` → `3 failed`（稳态用例 + "翻转仍 INFO"用例 + 降级用例全红）
  - M2 稳态时完全不记（`CRITICAL+100` 模拟「删日志」）→ `1 failed`，红在
    `test_steady_state_is_downgraded_not_deleted`——这一条正是为这种实现准备的
  - M0（**真实经过**，不是构造）：第一版在 if/elif 链前算 `status_level` → `test_adb_fact_flip_still_logs_at_info`
    当场红，捕获日志里只有 `device_adb_update` 而缺 `device_status_offline`

未验证 / pending：

- [ ] **合并后的实际字节数没有实测**。上面「稳态不再产 INFO ⇒ 该模块量级从每设备每拍 2 行降到
      每设备每次翻转 2 行」是**结构性推断**，不是测量：fleet 的翻转频率（含 BUSY↔ONLINE 随 job
      起止）要真实窗口才能定。任何"减少 X%"的说法在本单里都不成立。
- [ ] 未动轮转（#2205 / #1265 已生效）。体积降下来后是否放宽 rotate 阈值，属另一次带数据的决定。

## Revisit

- 需要稳态全量轨迹时的运维口径：`logging.getLogger("backend.api.routes.heartbeat").setLevel(DEBUG)`
  ——**不要**为此把这几行改回无条件 INFO。
- BUSY↔ONLINE 每次 job 起止各一条：若将来出现秒级高频 job（monkey 类），这四支仍可能重回高量级。
  那时该问的是「BUSY 该不该占 INFO」，而不是再调本单的判据（判据是对的，是语义选择变了）。
- `test_steady_state_beats_stay_out_of_info` 是防回归钉子，不是实现细节：将来若引入结构化日志，
  文案可整体替换，这条断言必须跟着搬过去，否则 68% 会以新格式原地复发。
- 同形问题仍有一处未收：`logger.warning` 级别的 `device_host_reassigned`（#2569 已按"重复即噪声"
  处理过稳态路径）；本单不动它，因为它已是按事件记。
