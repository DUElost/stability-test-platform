# ADR-0038 ⑤：退役主机心跳（保持退役 + 单次告警去重）（#1806）

Status: implemented
Class: architecture

## Decision

按 ADR-0038 v0.2 D4 实现分解 **⑤/6（心跳联动）**；依赖 ① 的 `retire_alerted_at`。

**行为**（两条心跳端点一致）：

- **如实记录**：退役主机的 `status` / `last_heartbeat` / 版本 / 身份字段照常更新
  （调用点本就不触碰 `retired_at`，代码注释显式写明「不自动解除退役」）；
- **保持退役**：不复活、不解除，`retired_by` / `retire_reason` 痕迹不动；
- **单次告警**：`HOST_RETIRED_HEARTBEAT` 事件经通知链出栈（`dispatch_notification_async`，
  ADR-0036 生产入口），去重载体 = `retire_alerted_at`。

**去重判据（共享单源）**：`services/host_retirement.should_alert_retired_heartbeat(host, prev_status=…)`
——纯属性函数（不触 Session API），sync 权威路径与 async 轻量路径复用同一实现：

| 场景 | 结果 |
|---|---|
| 非退役主机 | 不告警 |
| 退役首拍（`retire_alerted_at` 空） | **1 条**并打戳 |
| 持续 N 拍（仍 ONLINE→ONLINE） | 仍 **1 条** |
| 离线/降级 → ONLINE 的**恢复拍** | 视为新 episode：清零重打戳 → **再 1 条** |
| `unretire → retire` | ② 的 unretire 清零 → **重新计轮** |

**轻量端点的归属声明（ADR §1.2 要求二选一）**：`POST /api/v1/agent/heartbeat`
选**共用检测**——调用同一个 `should_alert_retired_heartbeat`，不复制规则；代码注释
写在 `agent_heartbeat` 内。

**逻辑事件键**：告警 context 携带 `event_key = "{host.id}|{retired_at}|HOST_RETIRED_HEARTBEAT"`，
供下游（通知去重 / 审计复盘）做跨重试、跨进程幂等；行内 `retire_alerted_at` 与事件键
互补。未使用任何 `Host.extra` 裸键（主心跳每拍按白名单重建 extra）。

**徽标判据**（前端 ⑥ 消费）：`retired_at IS NOT NULL ∧ status = ONLINE`——与告警去重
**解耦**（徽标常驻显示，告警只响一轮）；「状态震荡」按上表的恢复拍定义处理。

## Alternatives

- **两条心跳各自实现判据**：弃——ADR §1.2 明确要求二选一，重复实现是最容易漂移的
  形态（#1761/#1792 的同族教训：同一判据两份实现必然分叉）；
- **不区分 episode，一个退役周期只响一次（含恢复）**：弃——issue 的四条边沿明确要求
  「超时恢复再 1 条」；恢复拍说明主机被重启/重新上线，值得再次提醒；
- **把告警载体放 `Host.extra`**：ADR 明令禁止（extra 白名单重建会丢键）；
- **心跳返回 409 拒绝**：ADR §3 本轮否决（会给 Agent 制造新失败模式），失败模式升级
  留在 ADR Revisit；
- **新增独立通知事件表 / 去重表**：弃——`retire_alerted_at` 由 ① 落列，ADR D-4 已定
  载体；再引入表属过度设计。

## Verification

- **新增 10 例**（`backend/tests/api/test_host_retired_heartbeat_1806.py`）：
  首拍 1 条 + 痕迹不动 + 心跳字段照常更新；持续 3 拍仍 1 条；离线恢复再 1 条；
  恢复后持续拍不重复；活跃主机不告警；**IP 找回命中退役行**（不新建、不复活、1 条）；
  轻量端点共用去重（1 条）与活跃静默；`unretire→retire` 重新计轮（2 条）；
  设备 re-home 语义不变（退役主机仍可收回设备归属）；
- **反例实证**：
  - 去掉 `retire_alerted_at` 去重 → **3 failed**（持续拍 / 恢复后持续拍 / 轻量端点）；
  - 去掉恢复拍清零 → **2 failed**（离线恢复、恢复后持续拍）；
  恢复后 **10 passed**；
- **回归**：`test_heartbeat.py` + `test_heartbeat_backpressure.py` + ②/⑤ 新文件
  → **57 passed**；
- `ruff` → All checks passed；`check:quick` → **7 gates OK**。

未做：claim/派发过滤（④）、前端徽标（⑥）；心跳失败模式升级（409）按 ADR 留在 Revisit。

## Revisit

- **告警降噪实测**：真实 fleet 中「退役但仍在心跳」的时长分布未知——若恢复拍频繁
  （网络抖动导致 OFFLINE→ONLINE 来回），单周期可能响多条；届时应考虑加最小间隔
  （如恢复拍距上次告警 < N 分钟则抑制），需实测数据支撑；
- **通知事件类型登记**：`HOST_RETIRED_HEARTBEAT` 尚未在告警规则 UI/文档的事件类型
  清单中列出（后端 event_type 自由字符串）；若 ⑥ 需要前端配置告警规则，应补进
  `docs/design` 的事件类型表（或前端下拉常量）；
- **双通道收敛的替代**：本单选「共用检测」；若将来轻量心跳被废弃或与权威心跳合并，
  ADR §1.2 的归属声明需同步更新（当前声明写在 `agent_heartbeat` 注释里）;
- **`prev_status` 的取值面**：恢复判定当前只看「上一次记录的状态」，未区分
  OFFLINE 与 DEGRADED（都算恢复）；若运维认为 DEGRADED 不该重响，可细化为白名单。
