# ADR-0038 评审轮：会话×模型归属 errata（补 synthesis §6.1）

- 日期：2026-09-13
- 性质：**勘误/附录**——补 `REVIEW_ADR0038_2026-09-13_519e346_synthesis.md` §6.1 缺失的
  「会话×模型」维度；**不改该稿任何 finding / 级别 / 处置 / 源计数**。
- 依据：用户裁决（2026-09-12）——多 harness 评审的独立性记账单位是 **会话 × 模型**：
  同一 harness 可有不同会话与不同模型，不同 harness 可能同一模型；**禁止按 harness 名归并汇总**。
- 关联：[#1557](https://github.com/DUElost/stability-test-platform/issues/1557)（评审请求）、
  PR #1677（synthesis）、9 份评审稿（PR #1573 / #1602 / #1604 / #1607 / #1608 / #1609 /
  #1610 / #1623 / #1628）。

## 1. 结论摘要

1. 9 份评审稿 = **9 个独立会话 · 8 个 harness · 7 种模型 ID · 5 个模型系列**。DeepSeek 系
   占 5 会话（`db232b` / `ec183be3` / `713685` = `deepseek-chat`；`d04f71` =
   `deepseek-v4-flash`；`747cae` = `deepseek-v4.1-flash`），其余为 `gemini-3.8-flash`（`45308c`）、
   `Composer`（`409d16`）、`qwen3.8-flash`（`5ff80e`）、`gpt-6-astra`（`182d4e`）。
2. 九源阻断簇（R1/R2/R3/R5/R6/R13/R17 等）在模型维度**不是 9 个独立族**：DeepSeek 族
   占 5/9（其中 3 会话同为 `deepseek-chat`）；按模型系列计的独立来源上限为 **5**。
3. codex 稿实际模型为 **`gpt-6-astra`**（会话 rollout 实证），与本机 `~/.codex/config.toml`
   默认 `GLM-5.3-Flash` 不符——**配置默认值不得当作模型证据**。
4. 验证性复核（codebuddy 会话 `571d95`，即本 errata 产出方）与 `747cae` 同为
   `deepseek-v4.1-flash` → 只记事实核验，**不计独立佐证**；zcode `6f44a7`（`qwen3.8-flash`）
   自认非独立 → 不计源。

## 2. 逐会话归属表

| # | 稿 | harness（稿内） | 会话 | 模型 | 证据等级与来源 | 独立性 |
|---|---|---|---|---|---|---|
| 1 | `…_45308c.md` | antigravity | `613ec10c-…-45308c` | gemini-3.8-flash（同族另见 `-high`） | 实证：会话库 `~/.gemini/antigravity-cli/conversations/613ec10c-….db` | 独立 |
| 2 | `…_409d16.md` | cursor | `624ef331-…-409d16` | Composer | **自报**（稿内声明；未从本地库取证） | 独立 |
| 3 | `…_db232b.md` | zcode | `sess_b025e76e-…-db232b` | deepseek-chat | 实证：`~/.zcode/cli/rollout/model-io-sess_b025e76e-….jsonl` | 独立 |
| 4 | `…_5ff80e.md` | dsh（二稿） | `session-3d8e30c1-…-5ff80e` | qwen3.8-flash | 实证：projcache `session-3d8e30c1-….json`（会话 id 与稿内完全一致） | 独立（经人工确认另开） |
| 5 | `…_713685.md` | opencode | `ses_f6aeb6159ffenO4upR`（稿内以 PID 末 6 位 `713685` 记名） | deepseek-chat | 实证：`~/.local/share/opencode/opencode.db` → `session.model`（title=「按 #1557 提示词执行」） | 独立 |
| 6 | `…_747cae.md` | codebuddy | `01a09514-…-747cae` | deepseek-v4.1-flash | 实证 + 自报（稿内声明；与会话元数据一致） | 独立 |
| 7 | `…_d04f71.md` | claude-code | `3a2861c9-…-d04f71` | deepseek-v4-flash | 实证：`~/.claude/projects/-home-debian13-stability-test-platform/3a2861c9-….jsonl`（205 处单一模型） | 独立 |
| 8 | `…_ec183be3.md` | dsh（一稿） | `dafd7ba1-…-ec183be3` | deepseek-chat | 实证（载荷绑定）：`~/.dsh/storages/session_projcache/sessions/session-5b6bcf29-….json` 含「落稿 `…_ec183be3.md`」载荷且 `model=deepseek-chat`。注：存储会话 id（`5b6bcf29`）≠ 稿内声明 id（`dafd7ba1`），按载荷绑定 | 独立 |
| 9 | `…_182d4e.md` | codex | `01a09513-…-c89a04182d4e` | gpt-6-astra | 实证：`~/.codex/sessions/2026/09/12/rollout-…-01a09513-….jsonl`（⚠️ config.toml 默认 GLM-5.3-Flash 与实际不符） | 独立 |
| 附 | 6f44a7 issue 评论 | zcode | `sess_1d3a6f2a-…-6f44a7` | qwen3.8-flash | 实证：`~/.zcode/cli/rollout/model-io-sess_1d3a6f2a-….jsonl` | **非独立**（自认；不计源） |
| 附 | `…_519e346_synthesis.md` | zcode | `sess_025c15c3-…-519e346` | **未取证**（本地未见 rollout/模型字段） | — | 汇聚者，非评审源 |
| 附 | 本 errata（验证性复核） | codebuddy | `01a08c67-…-5f571d95` | deepseek-v4.1-flash | 会话元数据 | 不计独立佐证 |

## 3. 佐证加权提示（在 synthesis 源计数之上补模型构成）

| 簇 | synthesis 源数 | 模型构成 | 按模型系列的独立来源 |
|---|---|---|---|
| R1/R2/R3/R5/R6/R13/R17（9 源档 = 全样本） | 9 | DeepSeek 5（`chat`×3 / `v4-flash` / `v4.1-flash`）· Gemini 1 · Composer 1 · Qwen 1 · GPT-astra 1 | **5** |
| R4（5 源档） | 5 | DeepSeek 3（`747cae`/`d04f71`/`db232b`）· Qwen 1（`5ff80e`）· GPT 1（`182d4e`）；`6f44a7` 补充不计 | 3 |
| R7（5 源档） | 5 | DeepSeek 3（`ec183be3`/`747cae`/`d04f71`）· Qwen 1 · GPT 1 | 3 |
| R8/R10/R11/R12（8 源档，synthesis 未逐一列名） | 8 | 构成未逐一枚举；不超过全样本 | ≤5 |

> 口径说明：**同模型族会话不构成独立模型来源**；DeepSeek 族内三档
> （`deepseek-chat` / `v4-flash` / `v4.1-flash`）为不同 checkpoint，可按「同族不同档」理解，
> 但对外主张「多源独立」时应以**模型系列**为上限并注明 DeepSeek 占 5/9。

## 4. 勘误与注意事项

1. **配置 ≠ 模型**：codex `config.toml` 默认 `GLM-5.3-Flash`，实际会话为 `gpt-6-astra`；
   后续取证一律以会话 rollout 为准。
2. **自报项**：cursor「Composer」为稿内自报，未取证；本地 Cursor 库可用时补证。
3. **存储 id ≠ 声明 id**：dsh 一稿的 projcache 存储会话（`5b6bcf29`）与稿内声明会话
   （`dafd7ba1`）不一致，已按「写入载荷含该稿文件名」绑定；建议后续稿内同时记录存储 id。
4. **汇聚会话模型未取证**：`~/.zcode` 未见 `519e346` 的 rollout/模型字段；不影响其作为
   汇聚者（非评审源）的角色。
5. 本 errata **不修改** synthesis §1 的源计数（其按稿数计，与「会话=稿」一致且正确），
   也不修改任何 finding、级别与处置。
6. 本 errata 由 codebuddy 会话 `571d95` 产出（执行 `review-adr0038-attribution-errata`）；
   其与 `747cae` 同模型，故本稿只作**归属登记**，不构成对任何 finding 的新佐证。

## 5. 复现（只读，未导出会话内容）

```bash
# claude-code（d04f71）
grep -o '"model":"[^"]*"' ~/.claude/projects/-home-debian13-stability-test-platform/3a2861c9-*.jsonl | sort | uniq -c
# zcode（db232b / 6f44a7）
grep -o '"model"[^,]*' ~/.zcode/cli/rollout/model-io-sess_b025e76e-*.jsonl | sort | uniq -c
grep -o '"model"[^,]*' ~/.zcode/cli/rollout/model-io-sess_1d3a6f2a-*.jsonl | sort | uniq -c
# opencode（713685）
python - <<'PY'
import sqlite3
db = sqlite3.connect('file:/home/debian13/.local/share/opencode/opencode.db?mode=ro', uri=True)
print(db.execute("select id, title, model from session where id='ses_f6aeb6159ffenO4upR'").fetchone())
PY
# dsh（ec183be3 载荷绑定 / 5ff80e 精确匹配）
grep -o '"model": "[^"]*"' ~/.dsh/storages/session_projcache/sessions/session-5b6bcf29-*.json
grep -o '"model": "[^"]*"' ~/.dsh/storages/session_projcache/sessions/session-3d8e30c1-*.json
# codex（182d4e）
grep -o '"model":"[^"]*"' ~/.codex/sessions/2026/09/12/rollout-*-01a09513-*.jsonl | sort | uniq -c
# antigravity（45308c）
strings ~/.gemini/antigravity-cli/conversations/613ec10c-*.db | grep -aoE 'gemini[ -]?[0-9][a-z0-9.-]*' | sort | uniq -c
```

## 6. Revisit

- 后续分派模板建议要求稿内**显式声明模型**（多数 harness CLI 可打印），并入同类样本表；
  届时本表「自报 / 未取证」项可降级为历史。
- cursor Composer 与 `519e346` 的取证在本地库可用时补齐（不影响现有上限：5 个模型系列）。

Refs: #1557 · PR #1677 · 9 份评审稿（#1573 / #1602 / #1604 / #1607 / #1608 / #1609 / #1610 / #1623 / #1628）
