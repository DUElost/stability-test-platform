# host 脚本在位矩阵：第五道闸（常设 sweep + 五态 + 双告警）

Status: implemented
Class: bug-fix

## Decision

把「预期会用到哪些脚本版本 × 主机上到底有没有」做成**常设账**（issue #2958 主体）。

**为什么不能只靠既有机制**：run 作用域已有 host 侧核验（`admission_pump._verify_scripts_phase`
→ `verify_scripts` RPC → SFTP 自愈），但它只覆盖「本 run 的 host × 本 run 快照」——
维护窗 / 近期无派发的 host 无账。`.89` 实证：DB 面全绿（`--pending-activation` 无待激活项）
而机器上缺 3 个版本目录，只能 ansible 手工核。

**落地面**（经用户三项裁决 + 落库方案核对）：

1. **频率**：每日 09:30（`SCRIPT_PRESENCE_SWEEP_CRON`，本机时区，与 `stp-script-guard.timer`
   同窗、避开链派发窗）+ **单机按需** `POST /api/v1/script-presence/refresh?host_id=…`。
   账本是存量可见性，不做实时；全 fleet 刷新不放进请求路径（48 × 10s 墙钟）。
2. **告警**：**两条成对规则**——`StabilityHostScriptPresenceGap`（`missing + mismatch > 0`）
   + `StabilityScriptPresenceSweepStale`（`absent(…) or time() - … > 48h`）。第二条是必须的：
   只有第一条时「sweep 死了」会读成绿（#2881/#2900/#2984 的同族教训）。维护窗内缺口记
   `maintenance` 态、**不进 gap series**（窗口兼作升级锁，归队前补分发由 #2865 遗留项盯）。
3. **呈现**：host 表 fleet 聚合行（缺口/未知/维护窗/陈旧）+ 展开行按需拉单机明细；
   API 先行。
4. **落库形态**（不占裁决）：`host_script_presence` 一行 = host × 目标版本 × 当前态，
   每轮全量 upsert（`checked_at`/`sweep_id` 刷新）；全集口径 = `plan_step.enabled` ∩
   `script.is_active`（与运行快照同源），`n_a` 表示「该 host 可达集外」（不判红）。

**闭词表**：`present / missing / mismatch / unknown / n_a / maintenance`——`unknown`（agent
不可达）不是绿；`missing`（文件缺失/不可读）与 `mismatch`（sha 或 support 文件不符）是缺口；
support 文件与入口同为一等契约（`script_verifier._verify_support_files`）。

## Alternatives

1. **只靠派发期核验**（现状）：实现为零，但维护窗/无 run 的 host 永远无账——本单要治的正是它。
2. **agent 心跳携带脚本清单**：需改 agent 与上报体积控制，且只判「存在」不判 sha；既有 RPC
   已能给出 sha 级事实，无额外收益。
3. **跑 ansible `test -f`**：现成但判存在不判内容、依赖清单凭据、不产生常设账（只适合一次性核对）。
4. **落 `host.extra`**：心跳每拍按白名单重建 `extra`，sweep 写入的键 ≤1 个心跳周期被冲掉
   （已核 `heartbeat.py:286-301`）——不可用。
5. **调度进程直接 `gauge.set()`**：gauge registry 是进程内的，API 进程的 `/metrics` 看不见；
   必须「落库 + 抓取期现算」（与 `_refresh_host_health_gauges` 同法）。
6. **扩 `agent_code_sync_status`**：ADR-0040 v1.1「判据唯一性」有前端测试钉死，脚本级语义
   必须独立（新面）。
7. **保守全量 51×48 不判 n_a**：实测每族实跑 host 数 1–46 不均（`unisoc_*` 仅 3 台），
   会给不跑的 host 报假缺口 → 引入可达集与 `n_a`。
8. **新增 per-host 列表端点给徽标**：会把 48×51 行搬进列表接口 → 用 fleet 聚合行 + 抽屉。

## Verification

- 服务层 `backend/tests/services/test_script_presence.py`（6 例，纯函数 + `run_sweep` 端到端
  落库，含 unknown 不判绿、maintenance 只改缺口、n_a 与全集求交、可达集外不发 RPC）；
- API 层 `backend/tests/api/test_script_presence_api.py`（4 例：无行 stale、六态计数与
  缺口 host 去重、单机 items 过滤 n_a、refresh 单机作用域与 404）；
- promtool 场景 3 块（缺口/陈旧/账本缺失 → absent 分支），随
  `tests/test_prometheus_alerts_contract.py` 的「每条规则必须有场景断言 + 逐条阈值变异自证」；
- 形状契约：`ScriptPresence{Item,Counts} / HostScriptPresenceOut / ScriptPresenceSummaryOut /
  ScriptPresenceSweepOut` ↔ TS 五对，登记进 `_MODEL_PAIRS`（轴线 C 双向对拍）；
- 前端：vitest 3 文件 55 passed、`tsc --noEmit` 0 错、`eslint src --max-warnings 0` 0 问题；
- `python scripts/run_gates.py check:quick` 12 gates 全绿；`ruff check backend/ tests/` 通过。

## Revisit

- **本机（生产控制面）的迁移与首轮 sweep 未跑**：迁移随部署窗口执行（`pr-migrate-empty-db`
  在 CI 已验干净库）；首轮 sweep 在下次 09:30 或手动触发调度后才有行——在此之前
  `stale=true` 且 `StabilityScriptPresenceSweepStale` 会按设计告警（账本未建立 ≠ 绿）。
- **未知态不判红**：`unknown`（agent 不可达）只计数不成缺口；若长期大面积 unknown，
  需要另一条覆盖类告警（当前由 `StabilityUsbKernelLogChannelDark` 一族承担类似角色）。
- **按需刷新是单机**：全 fleet 频次由 timer 决定；若将来需要「部署完立刻全线重核」，
  应做成后台作业 + 完成通知，而不是请求内等待。
- **`n_a` 随计划变化**：目标集/可达集每轮现算，计划增删会让某 host 的 n_a 抖动——
  属预期（判据是「当前可达集」），但 UI 上不应把 n_a 变化当事故。
