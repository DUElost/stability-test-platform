# Agent 安装改由 RunConsole 自持（ADR-0044 落地，#2220）

Status: implemented
Class: feature

## Decision

按 [ADR-0044](../../adr/ADR-0044-agent-install-execution-ownership.md) 把 Agent 安装从
「SAQ 作业持有」改成「RunConsole 自持」：

- **触发路径**（`backend/api/routes/hosts.py`）：`POST /hosts/{id}/install` = 校验 → 起 console →
  写 `install_agent_request` 审计 + `host.extra.install_console_run_id`（同一事务）→ 返回
  `{console_run_id, room, log_path, status}`。**不再入队 `install_agent_task`**（该作业连同
  `wait_install_agent_runconsole` / `run_install_agent_sync` 一并删除——它们只做等待）。
  同主机并发仍由 console 的 `run_key` 拒绝（409 + 现有 id）。
- **终态落库**（`backend/services/agent_installer.py`）：console 的 `on_complete` 回调调用
  `_record_install_outcome()`，写 `install_agent` 审计、维护 `host.extra.agent_installed[_at]`，
  并落 `host.extra.last_install`（run_id/status/ok/ended_at）；回调内**先落库再清理**活动登记，
  所以调用方在安装结束后查状态一定能读到结果。回调自吞异常（审计失败不影响清理与状态上报）。
- **状态接口**：`/install/status` 改三级来源——活动运行 → `host.extra.last_install` 回放 →
  `lost`（跑过但从未落结果）→ `idle`；返回 `console_status` / `console_found` / `log_path`
  （由 run_id 推导）/ `exit_code`，`status` 是 console 派生摘要
  （`idle|running|succeeded|failed|canceled|lost`）。
- **调用方**：S5 `await_install` 保留 #2225 的「CANCELED ≠ FAILED」，并把 `lost` 也归到
  `agent_install_canceled`（控制面重启/记录过期是生命周期边界，不是脚本失败）；
  前端 `useHostOperations.waitInstallTerminal` 同步改为按 `console_status` 与摘要判定，
  `lost` 按取消收尾。
- **文案**：`agent_install_canceled` 不再提「作业窗口」（窗口没了），改为「显式取消 / 运行记录
  随控制面丢失」两种成因；「目标机装大件慢」的正确提示移到 `install_timeout`。

## Alternatives

- **只把 SAQ 作业窗口调大或变成可配**：慢目标机只会在更大的窗口外再失败，且更久占住 worker
  槽（并发 10 里占 1 个 15 分钟）；治症状不治归属。
- **作业只做 starter（起 console 后立即返回）**：保留队列记录但引入双启动路径与 busy 竞态，
  且 `status=complete` 会立刻表示「已启动」，语义误导。安装历史由审计承载即可。
- **把安装搬到 API 进程外（systemd-run / 独立 supervisor）**：能让安装跨控制面重启存活，但引入
  第二套进程/日志/状态与清理语义，还要回答多实例归属（ADR-0027）——列为终态候选，未立单。
- **状态只读内存 console 快照**：实施中发现 console 的「活动运行」登记在 `on_complete` 里清空，
  安装一结束 `/install/status` 就会读不到结果（调用方要等超时）——故必须落 DB（`last_install`）。

## Verification

- **后端**：`backend/tests/api/test_hosts.py::TestHostInstallStatusEndpoint` 4 条（活动运行 /
  DB 回放 / `lost` / `idle`）+ 触发用例改为断言「无 `saq_key`、返回 `log_path`、
  DB 落 `install_console_run_id`」；`backend/tests/services/test_agent_installer.py::
  TestInstallOutcomeRecording` 3 条（SUCCESS 置 `agent_installed` 且审计 ok；FAILED 不置位但记
  `last_install`；CANCELED 如实记录）。
  过程中的一个自伤：首版测试复用了回调注入的会话，留下 idle-in-transaction 卡住下一个用例的
  TRUNCATE（13 分钟无进展）——改为「短会话 + 关闭」并写进用例 docstring。
- **工具侧**：`tests/test_site_agents.py` 81 passed —— 取消 / `lost` 都报
  `agent_install_canceled`，console FAILED 仍报 `agent_install_failed`；FakeApi 载荷改为
  ADR-0044 形状。
- **前端**：`npx vitest run src/hooks/useHostOperations.test.ts src/pages/hosts/HostsPage.test.tsx`
  27 passed（`waitInstallTerminal` 按 `console_status` + 摘要收尾，`lost` 按取消）。
- 全量 `tests/` + `ruff` + `check:quick`（含 tsc/eslint/knip）+ 内网地址门禁结果见 PR。
- **现场复跑（待做）**：238 上再走一次 Agent 接入，确认慢安装不再被任何窗口打断、状态与实时
  日志一致；顺带复查 #2220 里那条「双 ansible 进程」的观察是否随等待者消失。

## Revisit

- **跨控制面重启续跑**：安装进程仍在 API 进程内，重启即取消（现在会如实报 `lost`/`canceled`）。
  需要续跑时按 ADR-0044 §3 第三项立单（独立 supervisor），触发条件是现场出现「重启打断安装
  且必须续跑」。
- **多实例（ADR-0027）**：console 仍是实例本地；若多实例部署安装，需要按 console registry
  做 owner 路由（ADR-0044 §5）。
- **`host.extra.last_install` 的保留**：目前只留最近一次（覆盖式）。若将来需要安装历史，应走
  审计表（`install_agent` 已有），不要把 `extra` 养成事件流。
