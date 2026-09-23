# flash-prereqs 状态端点补审计回放：注册位清位后终态仍可观测

Status: implemented
Class: bug-fix

## Decision

`GET /hosts/{id}/flash-prereqs/status` 在无活动运行时不再裸回 `idle`，而是按
ADR-0044 D4 与 install 通道同口径回放 `ensure_flash_prereqs*` 审计：

- 无请求审计 → `idle`（真·从未跑过）；
- 结果审计不早于请求审计 → 回放终态（`succeeded/failed/canceled`，
  `console_status/exit_code/log_path` 取自审计）；
- 有请求、无更新结果 → `lost`（控制面重启/结果未及落库，前端按 CANCELED 收敛）。

审计对查询下沉为共享原语 `_latest_console_audits(db, host_id, request_action,
outcome_action)`，install 的 `_latest_install_audits` 退化为它的参数化薄壳——
两个通道的「(timestamp, id) 稳定取最新」语义自此只有一份实现。前端零改动：
`waitFlashPrereqsTerminal` 的终态集合本就含 `succeeded/failed/canceled` 与 `lost`，
坏的只是后端读路。

背景（#3180 现场）：48 台批量刷机前置，每台 ansible 实际 4~20 秒 SUCCESS，但
`on_complete` 记完 outcome 立即 `_clear_active`，终态可读窗口只剩毫秒级，前端
2s 轮询几乎必然错过；此后端点恒回 `idle`，批次以每波 900s 的节奏空转
（21:06→02:52，恰好 24 波 × 900s），全部假失败且占死 mapPool 槽。

## Alternatives

- 前端把「trigger 后连续 idle」收敛为终态查询——只治等待循环，回放缺位仍在：
  面板刷新、重启后查结果都没有事实来源；后端回放是 install 已趟平的口径，
  数据面（outcome 审计）一直齐，缺的只是读路。留作后续纵深（见 #3180 建议）。
- 延迟 `_clear_active`（保留注册位到保留期满）——引入第二份「最近运行」状态，
  与 RunKeyBusy/409 语义纠缠，且进程重启即失效；审计回放同时覆盖重启场景。
- RunConsole 终态记录直接反查——`_runs` 有 1h 内存保留，但 flash-prereqs 端点
  按 host→run_id 映射取数，映射清了就无从反查；再造一层缓存不值。

## Verification

- `backend/tests/api/test_flash_prereqs_api.py`：新增回放成功（succeeded+SUCCESS
  +rc+log_path 来自审计）、回放失败（FAILED+rc=2+log_path 回退落盘路径）、
  请求无结果（lost）三例；原 idle 例保留并钉住「从无审计史」语义。
- 回归：`test_flash_prereqs_api.py` 7/7、`test_hosts.py` 60/60（helper 重构不破
  install）；`python scripts/run_gates.py check:quick`。
- 生产侧证据（本机控制面）：`logs/console/con-9355bb47d828.log` PLAY RECAP
  failed=0；`audit_logs` 中 `ensure_flash_prereqs` 结果审计
  `console_status=SUCCESS rc=0`，与请求审计间隔 4.1s。

## Revisit

- 若新增第三条「RunConsole + host 级活动登记」通道，应复用
  `_latest_console_audits` 回放口径，别再裸回 idle。
- 前端 `waitFlashPrereqsTerminal` 对 idle 的无限轮询兜底（连续 idle 收敛为终态
  查询）仍未做；若现场再出现回放路径外的 idle 空转，按 #3180 建议补前端。
