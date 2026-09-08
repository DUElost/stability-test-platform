# R02-F07：死信重放 / JIRA 取消 / Agent 配置重载补可归责审计（#907）

Status: implemented
Class: bug-fix

## Decision

三个写路径取得操作者却无 `record_audit`（#907）——补齐「谁、从哪里、对哪个
目标、执行了什么、结果如何」：

1. **`replay_log_signal_dead_letter`**（hosts.py）：成功、ack 失败（404）、
   Agent 掉线（503）、RPC 失败（502）四条出口均落 `dead_letter_replay` 审计
   （details 带 host_id + reason）；
2. **`cancel_jira_run`**（dedup.py）：`jira_run_cancel`——run 不存在（404，
   探测/误操作同样可归责）与取消结果（canceled 真值）分别落审计；
3. **`reload_agent_config`**（dedup.py）：`agent_config_reload`——emit 成功
   （status=sent）与 emit 异常（reason=emit_failed:<类型名>，只记类型不记参数
   原文）均落审计后重抛。

签名调整：三端点补 `request: Request` + `db: Session = Depends(get_db)` 依赖，
`_user` 更名为可引用的 `user`/`current_user`。审计提交沿用 #281 纪律
（record_audit 的 savepoint 包裹 + 显式 commit，失败审计不随异常回滚）。

## Alternatives

- **在服务层（RunConsole.cancel 等）埋审计**——放弃：操作者身份只在路由层
  可得，传入审计主体会扩大服务层签名面；既有 `record_audit` 惯例即路由层落；
- **只记成功路径**——放弃：issue 验收明确「成功/失败均可还原」；失败审计
  恰是安全调查（谁试图重放/取消什么）的主要证据；
- **404 不记**——放弃：404 可能是探测或误操作，归责价值高于噪音成本。

## Verification

- 目标 2 文件 **37 passed**：重放 OK/404/503 三路审计断言（操作者+host_id+
  reason）、cancel 404/200 审计断言（resource_id 定位）、reload 成功
  （status=sent）与 emit 失败（emit_failed:RuntimeError，TestClient 异常穿透
  语义下断言传播+审计已落）；
- 全量 `backend/tests/api` **873 passed**；
- `check:quick` 7 门禁全绿。

## Revisit

- `trigger_scan`（手动重跑 scan）也未审计——issue 未列，留待 #907 后续或
  与 #720 审计面统一收口时一并处理；
- 审计失败路径的 reason 文案（agent_rpc_failed 等）未入枚举表——当前
  details 为自由 JSON，若后续需要按 reason 检索再收紧。
