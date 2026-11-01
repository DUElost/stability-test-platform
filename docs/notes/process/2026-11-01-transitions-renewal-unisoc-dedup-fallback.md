# 过渡登记簿续期：unisoc-env-path-keys / dedup-scan-env-fallback-window

Status: implemented
Class: process

## Decision

队首 PR #3845（dependabot follow-redirects bump）的 required `lint` 因
`check_transitions.py` 红而卡住整条 FIFO 队列。两条 active 过渡 due=2026-10-31
已过期（today=2026-11-01）：

| id | exit | 核查结论 |
|---|---|---|
| `unisoc-env-path-keys` | `adr:ADR-0042#D3` | **续期，不关账**。D3 豁免条款要求终态删键；四键仍在 `AGENT_PATH_ENV_KEYS` 与 `UnisocScanRunner.configure` 的 env 回退路径 |
| `dedup-scan-env-fallback-window` | `issue:#3075` | **续期，不关账**。#3075 仍 OPEN；C3「下一窗口移除」未落地——`scan_runner` / `tool_cache` / `unisoc_scan_runner` 仍保留包面失败→env 路径 |

动作：两条均短续期 `due` → `2026-11-23`（约三周，避免拖到年底掩盖未闭合出口），并在 `what` 写明续期理由与代码锚。不改 `check_transitions`、不跳过 lint、不碰 follow-redirects bump。

## Alternatives

- **假关账（done + 捏造 evidence）**：弃——退出条件在代码面未满足；ADR-0051 落地状态仍写「legacy 路径键/回退退出未完」。
- **另开 PR 只改台账**：弃——队首阻塞必须落在 #3845 分支才能让 auto-merge 继续。
- **续期到 2026-12-31**：弃——与其他 active 项对齐虽方便，但会把「仍未删键/未撤回退」藏到季度末；短窗口迫使下一次到期再对账。

## Verification

- `python tools/dev/check_transitions.py` → 绿（无违例）
- 代码面仍可见回退：`backend/agent/scan_runner.py`（C3 注释）、`backend/agent/tool_cache.py`、`backend/agent/unisoc_scan_runner.py`、`backend/services/agent_env_sync.py` 的 `AGENT_PATH_ENV_KEYS`
- `gh issue view 3075` → state=OPEN；issue 正文「后续切片」仍列 env 回退窗口移除（C3 出口）

## Revisit

- 删四路径键 + 撤 env 回退分支的 PR 合入后，两条同 PR 转 `done` 并写 evidence（PR 号 + 删除点）。
- 若 #3075 拆出独立「移除 C3 回退」单，把 `dedup-scan-env-fallback-window` 的 exit 改指新 issue。
- 2026-11-23 到期前若出口仍未闭合，必须再走 PR 续期并写明进展，不得静默拖过。
