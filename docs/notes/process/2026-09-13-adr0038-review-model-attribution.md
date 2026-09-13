# ADR-0038 评审轮：会话×模型归属 errata（571d95）

Status: implemented
Class: process

## Decision

- 依据用户 2026-09-12 裁决（多 harness 评审的独立性记账单位 = **会话 × 模型**；禁止按
  harness 名归并汇总），对已完成评审轮补 errata 稿
  [`REVIEW_ADR0038_2026-09-13_571d95-attribution.md`](../../reviews/REVIEW_ADR0038_2026-09-13_571d95-attribution.md)，
  补 `REVIEW_ADR0038_2026-09-13_519e346_synthesis.md` §6.1 缺失的模型维度；
  **不代改 synthesis 正文**，不改任何 finding / 级别 / 处置 / 源计数。
- 取证分级：实证（会话存储直读 / 写入载荷绑定）· 自报（稿内声明）· 未取证；
  **配置默认值不作为证据**（codex 反例：config `GLM-5.3-Flash` ≠ 实际 `gpt-6-astra`）。
- 关键结论：9 稿 = 9 会话 · 8 harness · 7 模型 ID · **5 个模型系列**（DeepSeek 系占 5 会话）；
  九源阻断簇按模型系列的独立来源上限为 5；验证性复核（本会话）与 `747cae` 同模型，
  不计独立佐证。
- Registry：`review-adr0038-attribution-errata`（role=implementation，scope
  `docs/reviews`+`docs/notes/process`，issue #1557，test_impact=none；Mode C 下以
  `--force` 显式越过 issue 级查重）。

## Alternatives

- **直接修改 synthesis §6.1**：放弃——他源产物不代改（「新稿不覆盖」纪律）；
  补稿 + PR 引用等价且可追溯。
- **仅在会话内 / issue 评论口头登记**：放弃——模型构成直接影响佐证权重，须入库并可从
  稿件链接复核。
- **借机重算各簇源计数**：放弃——synthesis 源计数按稿数计本身正确（会话=稿），
  本 errata 只补模型维度，不做重判。

## Verification

- 9 稿 + 1 补充评论 + 汇聚稿逐会话取证；harness≠model 双向反例均实证：
  claude-code=`deepseek-v4-flash`、zcode 两会话=`deepseek-chat` vs `qwen3.8-flash`、
  dsh 两会话=`deepseek-chat` vs `qwen3.8-flash`、codex=`gpt-6-astra`（config 默认不符）。
- dsh 一稿按「projcache 载荷含落稿文件名」绑定模型（存储 id `5b6bcf29` ≠ 稿内声明
  id `dafd7ba1`，已在 errata 注明）。
- 复现命令见 errata §5（只读，未导出会话内容、未触生产）；`check:quick` 见 PR。

## Revisit

- 后续分派模板要求稿内**显式声明模型**，并入同类样本表；届时「自报 / 未取证」项
  可降级为历史。
- cursor Composer 与汇聚会话 `519e346` 待本地库可取证时补齐（不影响现有上限：5 系列）。
