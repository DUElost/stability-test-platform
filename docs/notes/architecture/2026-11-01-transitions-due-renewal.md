# 过渡登记簿到期续期：unisoc-env-path-keys / dedup-scan-env-fallback-window

Status: implemented
Class: architecture

## Decision

`docs/governance/transitions.json` 两条 `active` 条目 due=2026-10-31，在
2026-11-01 触发 ADR-0051 D8 到期执法红灯。核查代码后两者**过渡物仍在途**，
走合法短续期（due → 2026-11-23），并在 `what` 写明理由：

- `unisoc-env-path-keys`：四键仍在 `AGENT_PATH_ENV_KEYS` 与
  `unisoc_scan_runner` env 回退读取面；Phase 4b 删键下发（exit=`adr:ADR-0042#D3`）
  未落地。
- `dedup-scan-env-fallback-window`：`scan_runner` / `unisoc_scan_runner` 仍保留
  「包面失败→env 路径」C3 回退；#3075 仍 OPEN，后续切片「env 回退窗口移除」未合入。

仅改台账；不删键、不改 runner、不削弱门禁。挂在已开的 dependabot PR #3845 上，
使 required `lint` 的「过渡登记簿检查(ADR-0051)」恢复绿，不另开 PR。

**2026-11-24 二次短续期（due → 2026-12-15）**：2026-11-23 窗口到期触发全仓 `lint`
「过渡登记簿检查(ADR-0051)」红灯。重新核查代码与 issue：

- `unisoc-env-path-keys`：`backend/services/agent_env_sync.py:99-118` 仍保留
  `STP_UNISOC_{LOG_SCAN,SCAN_RESULT}_{PYTHON,SCRIPT}` 映射与 `AGENT_PATH_ENV_KEYS`，
  `backend/agent/unisoc_scan_runner.py:70-73` 仍读取该四键作 env 回退；
- `dedup-scan-env-fallback-window`：`backend/agent/scan_runner.py:556` 与
  `backend/agent/unisoc_scan_runner.py:64` 仍保留 C3 包面失败回退，#3075 仍 OPEN。

两项过渡物仍在途，经 Owner 确认另开独立 PR 续期三周至 `2026-12-15`（仍不拉至
`2026-12-31`），并在 `what` 追加复核依据。

选用约三周短窗口而非对齐年终 due（2026-12-31）：出口条件仍开放，短续期强迫
在出口落地前再次当着评审露面，避免把「忘了」拖到季度末。

## Alternatives

- **撤账（done + evidence）**：弃——代码与 #3075 后续切片仍保留回退，假撤会掩盖在途债。
- **删除登记条目 / 改 status 绕过**：弃——违反 ADR-0051 D8 台账纪律。
- **续期至 2026-12-31（与台账其他在途项对齐）**：弃——窗口偏长；短续期更贴合
  「当着评审改日期」的执法意图。

## Verification

- `python3 tools/dev/check_transitions.py --self-test`
- `python3 tools/dev/check_transitions.py`（及 `--today 2026-11-01`）应绿
- 代码在场抽查：`backend/agent/unisoc_scan_runner.py` / `scan_runner.py` 仍含 env 回退

## Revisit

- Phase 4b 删 `STP_UNISOC_*` 路径键合入后：`unisoc-env-path-keys` → done + evidence
- #3075「env 回退窗口移除」合入后：`dedup-scan-env-fallback-window` → done + evidence
- 若 2026-12-15 前仍未撤：必须再走 PR 续期（改 due + what 理由），不得静默留账
