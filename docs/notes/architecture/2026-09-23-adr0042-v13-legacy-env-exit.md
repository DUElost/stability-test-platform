# ADR-0042 v1.3——D3 加 legacy 例外键的终态出口豁免（2026-09-23）

Status: implemented
Class: architecture

## Decision

按 ADR-0051 D7 的前置裁决，给 ADR-0042 **D3「环境变量名不变」**加一条有界豁免：

- 禁的是「改名 / 新增第二键」造成的漂移，不是把已登记键永久钉死；
- 豁免面**只有** ADR-0033 §5.4 显式登记的 legacy 例外路径键（`STP_UNISOC_*` /
  `STP_AGENT_UNISOC_*`），且仅在其**终态出口**（工具入包：`tool_manifest.json` 条目 +
  站点 `packages/` + Agent `tools_cache` 解析，即 ADR-0051 Phase 4）落地时可删；
- 删除动作的同步义务：`.env*.example`、`environment-variables.md`、`env_inventory --write`、
  退役台账 `tests/test_removed_env_keys.py`（「台账键必须有出处」）。
- 其余全部 env 名继续受 D3 原语义约束。

同步面：ADR 头部 v1.2→v1.3 + 版本记录行；`docs/adr/README.md` 0042 行行首 v1.3 token；
`docs/DOC-MAP.md` 0042 行**行尾** v1.3 token（S12 实测取行内最后一个 vX.Y——第一版插行首被拦）。

## Alternatives

- **在 ADR-0051 里直接宣布删键，不动 ADR-0042**：弃——ADR-0051 D7 自己写明「撞 ADR-0042 D3，
  须先修订」，绕过被撞方 = 再造双权威（正是这轮治理在消灭的东西）。
- **把 D3 整体放宽为「有终态出口即可删」**：弃——豁免必须有登记面背书（§5.4 是显式清单），
  否则「先写个例外注释」就能绕过 #737 的收敛成果。

## Verification

- `tools/dev/check_governance_surface.py` → S1–S15 OK（首跑两条 S12 红：DOC-MAP 版本 token 位置 +
  0049 行误插，均已修正——0049 行恢复原文后复跑）；
- 本单为纯文档，无代码/测试面变化。

## Revisit

- Phase 4 删键 PR 必须引用本豁免并同 PR 完成四个同步面；若 §5.4 例外登记面扩大，豁免面随登记走，
  不逐条改本 ADR。
