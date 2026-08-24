# 派发链路补写 project/build 快照（ADR-0029 挂起保留段，#401）

Status: implemented
Class: bug-fix

## Decision

`_prepare_queued_plan_run`（`backend/services/plan_dispatcher_sync.py:538` 起）创建
PlanRun 时补写两个登记/报表维度字段——ADR-0029 D5 挂起时明确保留的「快照仅留
project_id 与 build_version」，此前实现丢失（四份独立评审一致定位的最高优先级缺口）：

1. **`project_id = plan.project_id`**：prepare 时从活表冻结。Plan 事后改归属不影响
   历史 Run 的归因（模型注释 :87 的快照语义自此才真正成立）。
2. **`build_version`**：prepare 时查 `Device.build_display_id`（心跳上报值）。
   **仅在全部目标设备同版本时写列**；分歧或缺失留 NULL，逐台明细冻结进
   `run_context.device_builds`（键为 `str(device_id)`——run_context 是 JSONB，
   int 键经往返会变字符串，写入端先规范化保证内存态与落库态一致）。

「全体同版本才写列」的理由：一次 Run 覆盖 N 台设备、各有 build，单值列表达不了
分歧；硬塞首台值会把「混合版本 Run」伪装成单一版本，报表层无法察觉。NULL + 明细
是唯一不撒谎的表示。

## 放弃的备选

- **build_version 取 plan_snapshot 内嵌**：拒绝——列已存在（迁移 M-a 建的），不用
  白不用；且 results/列表按列过滤比解 JSON 便宜。
- **PRECHECK 准入时再补 build**（评审 4a7c2d91 建议）：放弃——prepare 与准入之间
  设备可能掉线重连，心跳值漂移；冻结点越早越接近「操作员按下按钮那一刻」的事实。
- **混合版本时取众数/首台值**：见上，伪装成真比缺失更糟。

## 如何验证

- `backend/tests/services/test_plan_dispatcher.py::TestDispatchProjectSnapshot`
  四例：project 冻结 + 快照语义（改归属后 run 不变）、同版本写列 + 明细、分歧留
  NULL + 明细、未归属 Plan 兼容 NULL。
- 相邻回归：admission_queue_step2 / chain_trigger / device_validation /
  dispatch_retry 共 79 例通过；ruff 干净。

## 边界与何时重议

- 存量数据不动：M-b 已回填的历史行保持原值；本次修复只保证新派发不再产生 NULL。
- 「迁移不变量」（生产库任何新 run 不得 NULL project_id）由本修复的结构保证 +
  测试锁定；若未来新增 PlanRun 创建路径（如批量重跑工具），必须复用
  `_prepare_queued_plan_run`，绕行者需先复议本 note。
- build_version 列的语义若将来需要「逐台展示」，消费端读
  `run_context.device_builds`，不再改列。
