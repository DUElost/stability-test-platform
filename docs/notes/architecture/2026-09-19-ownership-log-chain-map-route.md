# ownership 表登记 log-chain-map 路由行（#2546 P1）

Status: proposed
Class: architecture

## Decision

在 [`2026-semantic-ownership.md`](../../design/2026-semantic-ownership.md)
Ownership 表新增一行 `log-chain-map`：日志域全链**地图/路由入口**，
`owner_anchor` 指向
[`2026-device-log-chain-contract.md`](../../design/2026-device-log-chain-contract.md)
§3。明确 **Ownership Authority only / 非第五内容权威**——内容仍归
ADR-0018/0025/0028/0032 与 scan-upload-merge；Contract 只串地图。

X2 单行口径同步提及该路由行；修订记录补一条。变更追加在未合入的 #2811
同分支，避免叠第二份 PR 与 Contract 双权威。

## Alternatives

| 方案 | 未选原因 |
|------|----------|
| 新开 stacked draft PR | #2811 未合入且已含 Contract；同分支追加更干净 |
| 把四层合并成单行指向 Contract | 违反 X2「四层都对」 |
| 不做表行、只靠 X2 散文链接 | P1 目标是表内显式可机读登记 |

## Verification

- S15：`python3 tools/dev/check_governance_surface.py`（`log-chain-map` 锚命中恰 1）
- #2811 仍 Ready；不开 auto-merge、不自行 merge
- 无代码 / 无目录迁移 / 不拍 Phase2 A/B/C

## Revisit

- Contract 合入并稳定后，若 ADR 头部写 `归属域：` 指向本 key，按 §4.2③ 同 PR 出现
- 下一刀仍是用户下令后再拍 ADR-0033 Phase2 A/B/C
