# #2155 展示面收口：前端钉住 digest 判据 + 移除 revision 派生的动作暗示字段

Status: implemented
Class: bug-fix

## Decision

ADR-0040 **v1.1「判据唯一性」**（2026-09-15 裁决，由 #2057 触发）已经由 #2158 把
`resolve_agent_code_sync_status()` 的判据换成 code artifact digest。本单收口两处**残余**：

**1. 前端补两条用例（该口径此前只有后端钉子）**

- `digest 判据：revision 不等不得渲染成 drift`——`agent_code_sync_status='matched'` +
  `agent_code_revision='abc1234'` ≠ `expected_code_revision='def5678'` → 渲染
  **已对齐**、**无**「内容漂移」；
- `digest 缺失（未上报）渲染为 unknown，而不是 drift`——按 v1.1，未上报 digest
  （#1907 前部署 / 新装未心跳）的运维动作是「等一次心跳 / 首次 `--force` 迁移」，
  不得渲染成需更新。

代码面本就是对的（徽章 class 与 label 都取自 `agent_code_sync_status`，
`ExpandableHostTable.tsx:600/610`；revision 只出现在溯源文案 `:604-606`），
但**没有用例**——#2155 第 2、4 项的「前端侧」因此处于零钉子状态。

**2. 移除 `code_revision_stale`（#2057/#2109 期间的临时可观测字段）**

`backend/api/routes/hosts.py` 的 no-op 响应曾回传 `code_revision_stale` 并打
`hot_update_noop_code_revision_stale` WARNING。按 v1.1「面向运维的动作信号**唯一**由
digest 产生；revision、部署时间等溯源信息**不得**被渲染成 drift / 待更新一类动作信号」，
该字段正属被禁的那一类，且**全仓零消费方**（唯一引用是它自己的 Agent Note）。
决定：字段与 WARNING 一并移除；`code_version` 保留为纯溯源值。

## Alternatives

- **保留字段、把 WARNING 降为 `debug`**：v1.1 之后「revision 与 HEAD 不一致」是**设计上的
  常态**（VERSION 记仓库 HEAD），留一条噪声日志只增加误判面；溯源信息在主机详情/`code_version`
  已可得。否决。
- **保留字段但改名不含 action 词（如 `revision_note`）**：零消费方的字段无论叫什么都不该留在
  响应契约里；P2 若确有展示需求，应按 v1.1 重新提需求并注明「纯溯源、非判据」。否决。
- **只补前端用例、不动 `code_revision_stale`**：#2057 的 Note 把该字段写成"临时可观测"，
  留着就是自己造的「文档宣称 > 实现」孤儿结构（#1928 同款）。一并清掉。

## Verification

- 前端：`npx vitest run src/components/network/ExpandableHostTable.test.tsx` → **20 passed**
  （新增 2 例）；
- **反事实验证**：把徽章改回「按 revision 判等」的旧口径（class + label 两处）→ 新增两例
  **FAILED**（2 failed / 18 passed）；恢复后 20 passed；
- 后端：`pytest backend/tests/api/test_hosts.py backend/tests/api/test_hot_update_noop_gate_1907.py
  backend/tests/services/test_agent_version_info.py -q` → **66 passed**；
- `npx tsc --noEmit` 通过、`npx eslint src --max-warnings 0` 通过；
- `python scripts/run_gates.py check:quick` → **[OK] check:quick (10 gates)**。

## Revisit

- `AgentCodeSyncStatus` 仍保留 `pending` 枚举值：v1.1 已明示「digest 判据下不再产生」，
  保留仅为兼容既有前端与历史数据；待前端与历史行都无该值后可删。
- 与 S14（代码注释里的 ADR 版本引用门禁）无关，但相邻：#2249/#2250 指出 S14 的判据
  （只比 ADR 头部版本、5 个 ADR 头部无规范版本位）不稳——属那两个单，不在本单范围。
