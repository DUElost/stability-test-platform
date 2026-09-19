# 起草设备日志链路 Living Contract（#2546）

Status: proposed
Class: architecture

## Decision

新增 Living Design / Contract
[`docs/design/2026-device-log-chain-contract.md`](../../design/2026-device-log-chain-contract.md)，
作为设备日志域的**系统级地图 + 逻辑坐标**入口，**不是**新 ADR、不取代
ADR-0018 / 0025 / 0028 / 0032 或 `2026-scan-upload-merge-contract` 的内容权威。

本轮只做文档定型（P0）：完整链路图、四层 owner 路由、Log Event 坐标、
Central Storage 逻辑 namespace↔现态物理映射、MTK/UNISOC 分叉点、raw vs derived
（含 HddSpill）。**不**改物理目录、**不**改 merge 位置、**不**落适配器代码。

与 #2546 X2：Contract = ownership 表的 **chain map 路由入口**；四层仍是
signal / DLE / storage / platform-merge。`2026-log-chain-global-semantics`
标为被 Contract 引用的**细节展开**（B2≠B5 等），避免双权威。

DOC-MAP 与 `2026-semantic-ownership` 仅加最小交叉链接。

## Alternatives

| 方案 | 为何未选 |
|------|----------|
| 新立「日志架构 ADR」 | 现有链路基本合理；缺的是地图不是新决策 |
| 立刻收敛 `devices/`/`dedup/`/`jira/` 物理目录 | 路径叠语义的根因未钉死前迁移风险高；分享结论明确先逻辑坐标 |
| 只靠 scan-upload-merge contract | 只覆盖后半段，不能替代 Device→Watcher→DLE 全链 |
| 把全局语义文升为地图权威 | 语义文宜做展开；地图与坐标由 Contract 单点承担 |

## Verification

- 文档：Contract 六件事齐全；标明非 ADR / 不搬目录 / B2≠B5 指向展开文
- 交叉链接：DOC-MAP 登记行 + ownership X2 / 域矩阵指向 Contract
- `python3 tools/dev/ai_work.py status`：本 Execution 已 declare（#2546）
- 无代码变更；`test_impact=none`
- 不开 auto-merge

## Revisit

- P1：ADR / scan-upload-merge / 展开文合入后统一「指向 Contract」措辞
- P2：是否物理收敛目录（默认可逻辑收敛 + 物理兼容不动）
- P3：UI/API 按 Event 坐标导航
- Contract 定型并评审通过前：#2546 X2 / ADR-0033 Phase 2 **不**推动目录迁移或 merge 位置改造
