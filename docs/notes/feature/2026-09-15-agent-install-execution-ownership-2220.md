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
- **终态落库 = 审计**（`backend/services/agent_installer.py`）：console 的 `on_complete` 回调调用
  `_record_install_outcome()`，写 `install_agent` 审计（`ok`/`rc`/`console_status`/`log_path`/
  `console_run_id`）、维护 `host.extra.agent_installed[_at]`（该键在心跳 keep-list 里）；
  回调自吞异常（审计失败不影响清理与状态上报）。
  **运行态不写 `host.extra`**：现场复跑发现心跳会按 allowlist 重建 `extra`，写进去的
  `install_console_run_id` / `last_install` 在 ~20 秒内被静默抹掉（首版实现即栽在这里）。
- **状态接口**：`/install/status` 三级来源——活动运行 → 最近 `install_agent_request` /
  `install_agent` 审计回放 → `lost`（有请求、无更新的结果）→ `idle`；返回
  `console_status` / `console_found` / `log_path` / `exit_code`，`status` 是 console 派生摘要
  （`idle|running|succeeded|failed|canceled|lost`）。
- **调用方**：S5 `await_install` 保留 #2225 的「CANCELED ≠ FAILED」，并把 `lost` 也归到
  `agent_install_canceled`（控制面重启/记录过期是生命周期边界，不是脚本失败）；
  前端 `useHostOperations.waitInstallTerminal` 同步改为按 `console_status` 与摘要判定，
  `lost` 按取消收尾。
- **文案**：`agent_install_canceled` 不再提「作业窗口」（窗口没了），改为「显式取消 / 运行记录
  随控制面丢失」两种成因；「目标机装大件慢」的正确提示移到 `install_timeout`。
- **取消入口**（#2255，2026-09-16 补）：把 console 自带的 cancel 暴露给操作者——
  `POST /hosts/{id}/install/cancel`（管理员）没有在跑的安装 → 409 `NO_INSTALL_IN_PROGRESS`
  （仍落 `install_agent_cancel` 审计，可归责动作不留白），有则 `RunConsole.cancel(run_id)`，
  如实返回 `canceling`/`not_canceled`（进程组 kill 未发起时不假装受理）。**终态不由此端点
  推断**：由 `on_complete` 落 `install_agent` 审计（status=CANCELED），S5 报
  `agent_install_canceled`。前端：安装操作面板行内「取消」（仅 `pending|running` 的
  install/reinstall 行；热更新不适用），`cancelInstall` 把受理失败原因写回该行 error，
  避免「点了没反应」。

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
  审计回放 / `lost` / `idle`）+ 触发用例改为断言「无 `saq_key`、返回 `log_path`」；
  `backend/tests/services/test_agent_installer.py::TestInstallOutcomeRecording` 3 条
  （SUCCESS 置 `agent_installed` 且审计含 `console_status`；FAILED 不置位但审计如实；
  CANCELED 如实记录）。
  过程中的一个自伤：首版测试复用了回调注入的会话，留下 idle-in-transaction 卡住下一个用例的
  TRUNCATE（13 分钟无进展）——改为「短会话 + 关闭」并写进用例 docstring。
- **工具侧**：`tests/test_site_agents.py` 81 passed —— 取消 / `lost` 都报
  `agent_install_canceled`，console FAILED 仍报 `agent_install_failed`；FakeApi 载荷改为
  ADR-0044 形状。
- **前端**：`npx vitest run src/hooks/useHostOperations.test.ts src/pages/hosts/HostsPage.test.tsx`
  27 passed（`waitInstallTerminal` 按 `console_status` + 摘要收尾，`lost` 按取消）。
- **取消入口**（#2255）：`backend/tests/api/test_hosts.py::TestHostInstallCancelEndpoint` 3 条
  （受理 / 无在跑 409 + 审计 / 不可发起如实回报）；前端
  `src/components/host/HostOperationPanel.test.tsx` 8 条（新增 2 条：仅在跑的
  install/reinstall 行渲染并转发 hostId、无回调时不渲染）+
  `src/hooks/useHostOperations.test.ts` 9 条（新增 3 条：受理返 null、未受理与 409 detail
  如实回传）；`npx tsc --noEmit`、`eslint` 通过。
- 全量 `tests/` + `ruff` + `check:quick`（含 tsc/eslint/knip）+ 内网地址门禁结果见 PR。
- **现场复跑（待做）**：238 上再走一次 Agent 接入，确认慢安装不再被任何窗口打断、状态与实时
  日志一致；顺带复查 #2220 里那条「双 ansible 进程」的观察是否随等待者消失。

## Revisit

- **跨控制面重启续跑**：安装进程仍在 API 进程内，重启即取消（现在会如实报 `lost`/`canceled`）。
  需要续跑时按 ADR-0044 §3 第三项立单（独立 supervisor），触发条件是现场出现「重启打断安装
  且必须续跑」。
- **多实例（ADR-0027）**：console 仍是实例本地；若多实例部署安装，需要按 console registry
  做 owner 路由（ADR-0044 §5）。
- **安装历史**：状态接口只回放「最近一次」（审计里其实保留了全部 `install_agent*` 记录）。
  要展示历史应直接查审计表，别把 `extra` 养成事件流（它也承载不了——心跳会重建）。
- **心跳 keep-list**：`agent_installed[_at]` 依赖 keep-list 存活；若将来清理该清单，需要把
  这两个键的语义挪到显式列（ADR-0040 D2 的方向）。
