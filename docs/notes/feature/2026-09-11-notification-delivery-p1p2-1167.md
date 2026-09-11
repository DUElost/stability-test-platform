# 通知投递：DeliveryResult 归一化 + 三态分类与重试策略（#1167 P1/P2）

Status: implemented
Class: feature

## Decision

#1167 台账（ADR-0036 Accepted v1.0）的 **P1 + P2** 落地——契约 → 代码的第一批
（#1166 定稿后即可推进；P3/P4/P5 不同批）：

**P1（D1/D9）**：新增 `backend/services/notification_delivery.py`：

- `DeliveryOutcome` 四分类（D2）与 `DeliveryResult(outcome, detail, channel_type)`
  ——`ACCEPTED` 语义 = 渠道明确接受请求；`accepted` / `retryable` 属性与
  `record()`（投递事实字典形态）；
- 适配器归一化：webhook / DingTalk / SMTP 全部返回 `DeliveryResult`，**不再用
  异常表达投递失败**；`send_to_channel` 同改（未知渠道 → PERMANENT）；
- 协议映射（adapter contract，ADR 正文有意不写）：HTTP <400 → ACCEPTED；
  429/5xx → TRANSIENT；其余 4xx → PERMANENT；DingTalk errcode≠0 → PERMANENT；
  requests.Timeout / SMTPServerDisconnected → **UNKNOWN**（请求可能已成功）；
  requests.ConnectionError / 通用 SMTP/OSError → TRANSIENT；
  配置类 ValueError/TypeError/KeyError → PERMANENT；未识别异常 → UNKNOWN
  （保守，计入上限）；#1214 的凭据脱敏保留（进 `detail`）。

**P2（D2/D5）**：失败三态分类 + `RetryPolicy`（max_attempts / 指数退避 /
上限封顶；UNKNOWN 与 TRANSIENT 同策略计数）；`dispatch_notification` 改为
**只有可重试失败才抛 `NotificationDeliveryError`**（永久拒绝如实落投递事实、
不再让 SAQ 白重试）；`failed` 明细与 `channel_delivery` 记录新增
`outcome` / `retryable`。

**调用点同步**：管理员测试端点与 AI 助手工具改为按 `result.accepted` 判定
（不再把「未抛异常」当成功——D1 口径统一）。

## Alternatives

- 让适配器保持 raise、另建包装层返回结果：两套语义并存，调用点还会各自
  try/except，回到「各自发明成功」；ADR D9 要求单点归一化；
- 永久拒绝也抛（让 SAQ 重试到上限）：违反 D5「再试无意义不重试」，且浪费
  重试预算、延迟告警；
- ConnectionError 归 UNKNOWN：requests 不区分「连接未建立」（明确未送达）与
  「发送中断」，取常见情形 TRANSIENT；重复投递由 at-least-once 语义承接
  （ADR D7 已知代价），代码注释已说明。

## Verification

- `pytest backend/tests/services/test_notification_delivery.py`（新）+
  `test_notification_service.py`：38 passed——HTTP/异常分类逐项、策略
  上限与退避封顶、UNKNOWN 计入上限、record 形态、永久拒绝不抛/可重试抛出；
- 跨模块回归：`test_saq_tasks.py` + `test_ai_assistant_endpoints.py` +
  `test_plan_run_aggregation_shared.py` 等 148 passed；
- `pytest backend/tests` 全量：见 PR 验证节；
- ruff 全绿。

## Revisit

- **P3**（retry owner 收敛 SAQ + 投递级幂等）与 **P4**（投递事实落库形态：
  扩 `notification_logs` vs 新建单数表）不在本批；当前事实仍存
  `NotificationLog.context.channel_delivery`（JSONB，1:N 以 channel_id 键表达）；
- DingTalk errcode 的细分映射（限流类 → TRANSIENT）待实测数据，adapter
  contract 内可调，不影响 ADR；
- `RetryPolicy` 目前是语义对象（缺省 3 次/5s/120s）：接 SAQ 重试参数属 P3；
- 同步测试路径（D8）不写投递事实表——已在 ADR v1.0 澄清条目锁定。
