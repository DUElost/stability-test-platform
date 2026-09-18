# #2754 自愈半边：批量 adb offline 的 reconnect offline 自动巡检（默认关）

Status: implemented
Class: bug-fix

## Decision

host .81 实证（#2650 G2 / #2754）：15/16 台 adb offline、`lsusb` 枚举层完好、
`adb reconnect offline` 一条命令 15/15 即时恢复——但 Agent 无此巡检，无人处置
就永远停在 offline，且 host 级面板完全不可见（观测半边由另一 Execution 交付）。
本切片给 Agent 补**自愈半边**：

- **采集/执行原语**：`device_discovery.adb_reconnect_offline`——host 级
  `adb reconnect offline`（只作用于 offline 设备），失败只记日志返回 False，
  自愈动作自身不得产生告警噪声；
- **触发器**：`HeartbeatThread._maybe_auto_reconnect_offline`，门控全部从严——
  ①`STP_ADB_RECONNECT_AUTO=1` 显式启用（**默认关**，同 #160
  `STP_ADB_AUTO_REPAIR` 的 opt-in 形态）；②阈值 offline ≥ 2 且 ≥ 半数设备
  （.81 形态 15/16）；③**连续 2 拍**满足才触发（躲瞬时抖动），恢复健康清零；
  ④冷却 `STP_ADB_RECONNECT_COOLDOWN_SECONDS`（默认 600s，进 #2086 同款
  正值钳制）；⑤执行放后台 daemon 线程（多设备 reconnect 可达数十秒，不拖慢
  心跳主循环），恢复与否由下一拍 `adb_state` 自然反映；
- **接线**：`_tick` 内独立于既有 server 级修复块；`reload_from_settings`
  实例级 re-apply 冷却值（#2086 模式）；env 双登记（清单表格 +
  `backend/agent/.env.example`）。

## Alternatives

- **默认开启**：否决。自愈自动化改变 fleet 行为，先行落**能力**（opt-in），
  「翻开开关」留给 owner 的部署决策——与 #160 完全同款节奏，能力上线 ≠ 行为变更。
- **复用 #160 的 `ensure_single_adb_server`**：否决。那是 server 级收敛（杀
  fork-server + 重启目标端口 + 全量重枚举），要求 `active_count == 0`——而链段
  交接的批量掉线恰恰发生在任务流动期，等空闲窗口会永远等不到；`reconnect
  offline` 只重连 offline 设备、不重启 server、不打扰其它设备在途会话，
  **无需空闲守卫**（这是两个动作的本质分界，不是守卫遗漏）。
- **阈值只看绝对数（≥3）**：否决。2 台小 host 两台全 offline 同是 .81 形态；
  「≥2 且 ≥半数」的复合判据同时覆盖大 host 的比例面与小 host 的绝对面。
- **恢复后主动上报/清理**：不引入。下一拍 discovery 自然把 `adb_state` 翻回
  `device`，控制面既有链路照常收敛——额外上报是冗余面。

## Verification

- `python -m pytest backend/agent/tests/test_adb_reconnect_selfheal_2754.py -q`
  → 5 passed：门控关/settings None 永不触发且不推进计数；阈值边界（2 里 1 台、
  10 里 2 台不触发，3 里 2 台触发）；持久两拍 + 健康复位重算；冷却抑制与过期；
  命令形状 `["adb", "reconnect", "offline"]` + rc≠0/异常 → False。
- **变异自证（实跑，每步清 `__pycache__`）**：`< 2` 改 `< 1` → 持久性用例红；
  还原 → 5 绿。⚠️ 第一轮变异自证出现过「还原后仍红」——根因是同秒内等长
  sed + cp 使陈旧 `.pyc` 通过 mtime+size 校验，清缓存后行为正确；变异类
  验证必须在每步之间清 `__pycache__`（教训入档）。
- `python -m pytest backend/agent/tests/ -q` → **2185 passed**（78.73s）。
- `tests/test_env_inventory.py` 全绿（双旋钮已双登记）；
  `check_governance_surface.py --check` 全绿；`run_gates.py check:quick`
  **12 gates 全绿**。

## Revisit

- **放量**：先在 1-2 台 host 的 agent env 打开 `STP_ADB_RECONNECT_AUTO=1`
  （reload_config 热生效），观察 `adb_reconnect_offline_triggered` 日志与
  掉线恢复情况，再决定 fleet 级推开——默认关的含义是这一步永远是人拍的板。
- 与 qwen 在飞的观测半边（per-host adb_state gauge + 批量掉线波次告警）合并
  后，「告警 → agent 已自愈/未自愈」的对照面才完整：告警窗口内若看到
  triggered 日志说明自愈已动作，恢复失败再升级人工。
- xhci 硬件死亡形态（incident-2026-07-29）reconnect 无效——冷却节流保证该
  形态下只是每 600s 一次无害尝试，不会风暴；根因判据树仍走人工排障 SOP。
