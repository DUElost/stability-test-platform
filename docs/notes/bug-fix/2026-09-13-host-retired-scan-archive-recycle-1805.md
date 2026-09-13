# ADR-0038 ④ 切片三：scan/archive 回收类退役语义（#1805）

Status: implemented
Class: bug-fix

## Decision

ADR-0038 §2 D5「数据回收类（scan/archive/日志尾读）**允许**触达退役主机，但
**仅显式 admin 触发 + 审计 + `skipped_retired` 不虚报完整**」的本切片落地：

1. **证据集合与下发改判分家**（182d4e-R02）：`iter_plan_run_scan_hosts` 返回
   `(host_id, status, is_retired)`——历史证据集合保留退役可见性，是否下发由
   调用方按策略裁决；新增 `classify_recycle_targets(rows, allow_retired=...)`
   统一返回 `(targets, skipped_offline, skipped_retired)`。
2. **手动回收路由 = 唯一 admin 显式通道**：
   - `POST /plan-runs/{id}/archive`（archive_now + scan_now 直发）：`role=="admin"`
     → `allow_retired=True`；否则退役主机入 `skipped_retired`；新增审计
     `plan_run_archive_scan_trigger`（含 triggered / skipped / allow_retired）。
   - `POST /plan-runs/{id}/dedup/scan`（enqueue scan_task）：同上判定与审计
     （`plan_run_scan_trigger`），并把 `allow_retired` 透传进 SAQ。
3. **SAQ 下发链路**：`enqueue_dedup_terminal_(a)sync(..., allow_retired=)` →
   `scan_task(..., allow_retired=)` → `_query_hosts_for_scan` 分类；
   **SAQ 键追加 `:ar` 后缀**（目标集不同，禁止与自动轮次互相去重导致静默漏发）；
   日志增记 `skipped_retired=N`。
4. **AI 助手路径**：`run_trigger_plan_run_archive` 固定 `allow_retired=False`
   （非「显式 admin 触发」语义），退役主机入 `skipped_retired`（审计 + 摘要
   明示）；`describe_archive_preview` 单独列出退役 host 及其原因。

## Alternatives

- **回收类一律拒绝退役主机**：放弃——D-5 分类裁决；否则退役前最后一批设备
  日志无法回收（退役即停机后的唯一回捞窗口）。
- **复用「隐藏退役」过滤器统一收口**：放弃——182d4e-R02 点名反对：控制拒绝
  与历史证据收口共用一个过滤器会让统计/取证面失真。
- **SAQ 键不区分策略**：放弃——自动轮次（allow_retired=False）与 admin 轮次
  目标集不同，同键去重会把 admin 请求静默合并进不含退役目标的轮次。
- **AI 路径也放行退役**：放弃——「仅显式 admin 触发」是 D-5 的硬条件，AI
  工具调用不是该语义；如实报告 `skipped_retired` 供人另行 admin 触发。
- **在 `emit_agent_control` 层做通用白/黑名单**：继续放弃（沿用切片二结论：
  回收类放行 + 集中黑名单易误伤，按调用点收口）。

## Verification

- 目标文件：`test_plan_run_scan_scope.py` + `test_plan_run_archive_endpoint.py`
  + `test_dedup_scan_endpoints.py` + `test_ai_plan_run_ops.py` → **53 passed**；
- **反例实证（逐点 mutation，五红）**：①分类器忽略 `allow_retired` ②archive
  路由审计改名（缺失）③dedup 路由硬编码 `allow_retired=False` ④AI 路径放行
  退役 → 对应 5 个用例全部转红；恢复后清 `__pycache__` 复跑全绿（53 passed）；
- 既有用例适配：2 处 dedup 用例的 `assert_awaited_once_with` 补
  `allow_retired=False`（签名扩展为预期变更）；`iter_plan_run_scan_hosts`
  解包处（scan_scope 测试）同步三元组；
- 测试种子修正：同 Run 内复用同一 device 撞 `uq_job_instance_plan_run_device`
  ——退役 host 使用独立 Device 行；
- `scripts/run_gates.py check:quick`：见 PR（7 门禁）；
- **未覆盖**：`saq_tasks.scan_task` 无独立单测（沿用既有测试面，分类逻辑已由
  `classify_recycle_targets` 与其调用方覆盖）；`logs.py` 远程日志尾读现状
  = 允许且无退役判据（与 D-5 回收类放行一致），审计面如需补齐另列（见 Revisit）。

## Revisit

- `logs.py` 远程日志尾读（Agent-secret 服务端触发）当前无退役判据与审计——
  按 D-5 属回收类「允许」，语义一致；若后续要求「显式 admin + 审计」，按本
  切片的分类器扩展。
- 剩余切片（issue #1805 逐条对照）：claim（并行在途）、182d4e-F7 十场景全量
  mutation。
- `scan:{run}:ar` 键为策略分隔；若未来出现更多回收子策略（如按 host 子集），
  键需再细分，避免跨策略去重。
