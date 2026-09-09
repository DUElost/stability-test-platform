# log_signal 部分接受：好坏混合批次不再连坐（#1048）

Status: implemented
Class: bug-fix

## Decision

#1048（R09-F05）：`POST /agent/log-signals` 对批次里任一不可恢复单条（job 不存在 /
租约 fencing 不匹配 / 契约违规 / detected_at 非法）**整批 404/400/409**；Drainer 对
同批所有记录累计 `attempts`，到 10 次一并转死信 —— 默认 50 条批次混入一条已删除
Job 的旧记录，其余正常信号也会停止自动补报（需管理员重放恢复）。

修复：区分「**批次暂时失败**」（DB 不可用、网络、鉴权 —— 仍整批失败，走原 bump
路径）与「**消息永久失败**」（单条隔离）：

- API 层：逐条校验，坏记录收集进响应 `rejected`（`{job_id, seq_no, reason}`，
  reason 截断 300），好记录照常入库/计数/反向关联/广播。响应新增 `rejected` 键；
  整批全坏时返回 200 + `inserted=0` + 完整清单（不再抛 404/400/409）。
- Agent 层（`OutboxDrainer.tick_once`）：解析响应 `rejected`，命中的记录直接
  `mark_log_signal_dead_letter`（API 已判定永久，重试无意义、不 bump attempts），
  其余正常 ack；`tick_once` 返回值语义修正为「本轮实际 ack 数」（docstring 原本
  就这么承诺，实现此前返回 `len(batch)`）。
- 兼容：响应无 `rejected`（旧后端 / JSON 解析失败）→ 保持原语义全部 ack；
  `isinstance` 收窄使 MagicMock 注入的测试替身天然落到该分支。

## Alternatives

- 死信仍走 attempts 计数（坏记录也重试 10 次）：确定性失败重试 10 次纯属噪声，
  且期间每轮占用 batch 名额拖慢好记录；
- API 对坏记录返回 207 多状态：项目响应统一是 `ApiResponse[T]`，引入第三种成功
  形态没有收益，`rejected` 清单已承载全部信息；
- Drainer 侧按 reason 字符串自行判「永久/暂时」：判据在 API 层（它知道 job 与
  租约事实），Drainer 只信清单，避免两处判据漂移。

## Verification

- `pytest backend/agent/tests/test_outbox_drainer_dead_letter.py`：15 passed，
  新增 2 例 —— 混合批次好记录 ack / 坏记录直接死信且 `failed_total=0`（不 bump
  attempts）/ 新信号不受影响；旧后端无 `rejected` 字段保持原语义；
- `pytest backend/tests/api/test_agent_api_watcher.py backend/tests/services/
  test_job_log_signal.py backend/tests/api/test_agent_dual_write.py`：90 passed，
  新增混合批次部分接受用例（4 条中 2 好 2 坏：好记录入库、坏记录各带 reason、
  count 与反向关联只按好记录结算）；原 409/400 整批拒绝用例改写为隔离语义；
- `pytest backend/agent/tests`：全目录通过；ruff 干净。

## Revisit

- `rejected.reason` 目前是自由文本（供死信审计读）；若运维面板要按 reason 分类
  告警，需升级为结构化 code —— 届时是响应契约变更，走 contract 版本化；
- 死信重放（管理员手动）沿用既有 `replay_log_signal_dead_letter`，被拒记录重放
  时若 Job 仍不存在会再次进死信 —— 语义自洽，不另行阻断；
- `_MAX_ATTEMPTS=10` 的整批重试路径保持不变；若「暂时失败」也有更优策略（如
  指数退避），另开单。
