# Main CI 墙钟同类问题盘点 + 覆盖完备性

Status: observed
Class: process

## Decision

对「全量 Main CI」（`workflow_dispatch` / `main-ci-backstop` 日频）做与本会话
PR 路径同口径的盘点：**保核验优先**，只标空转/编排浪费与覆盖缺口，本 Note
不落地改 workflow（动手需另开 Execution）。

## Alternatives

（盘点阶段无实现备选。）

## Verification

证据源：

- 成功全量 run `35021293928`（2026-09-15）：`backend-test` **1128s**，
  其中 `Run backend tests` **837s**、`Run agent tests` **182s**、
  `Run repo-level tests` **63s**；`frontend-check` 140s；`docker-build` 80s。
- 成功 run `34897662465`：同形（backend ~1109s，backend tests 835s）。
- 近几日 dispatch：`35172501514` / `35148237108` **failure**（失败步均为
  `frontend-check` → `Run vitest`）；`35193848671` cancelled。说明全量独有的
  前端行为回归近期在拦问题——这是覆盖有价值的证据，不是可删项。

## Findings

### 同类墙钟问题（与 PR 会话同形）

| 现象 | 证据 | 保核验下可压？ |
|---|---|---|
| **agent + tests/ 串行且与 PR 重复** | 全量再跑完整 agent（182s）+ 几乎完整 `tests/`（63s）；PR 已 `env -i` 真跑 + 离线子集 | **可**：并行化；或全量只跑 PR ignore 的 2 个容器文件 + promtool 强制场景，避免 ~96 文件双跑 |
| **三段 `--cov` 拖慢** | 注释写明「只度量、无 fail-under」 | **可**：抽样/单段 cov，或偶发度量；**不删测试** |
| **compileall / tsc 与 PR 重复** | full 与 `pr-compileall` / `pr-typecheck` 同形 | **可**删全量侧重复步（核验已在 required） |
| **backend/tests ~14min 本体** | 837s 主耗时 | **慎**：先 `--durations` 定点空转；禁止为速度砍控制面覆盖 |
| stall 假等待 | PR #2538 已修；全量 agent 步 182s 含 #2538 前基线 | 合入后下一轮 dispatch 应自然下降 |

### 覆盖完备性

**Full 相对 PR 独有且必须保留**：

- `backend/tests/` 全量（~255 文件；PR 仅 ~6 个锁序/指标文件 ≈ 2.4%）
- 容器类根测试 2 文件 + promtool 场景层（`PROMTOOL_REQUIRED=1`）
- vitest + frontend build、docker-build

**合入视角缺口（设计敞口，非偶然遗漏）**：控制面行为回归主要靠 ≤24h 夜间
兜底（`#1525` / repository-workflow「CI 分层」）。近窗 backstop 红灯主因也在
`backend-test`。套件资产厚，**合入闸偏薄**——与注意力预算取舍一致。

**结构性非 CI 盲区**：真机 ADB/NFS/共享存储链、已发布脚本目录（覆盖率 omit）
——不靠再堆单测补完。

## Revisit

若动手压墙钟，优先级建议：

1. 全量 `backend-test` 内 **agent ∥（promtool + tests）** 或去重双跑；
2. cov 从关键路径挪开；
3. 去掉与 PR required 完全同形的 compileall/tsc；
4. **不动** `backend/tests/` 覆盖面；若夜间控制面确定性红频繁，按 `#1525`
   评估 **前移子集**，而不是砍夜间。

**2026-09-17 落地**：见
[`2026-09-17-main-ci-followup-wallclock-1525-vitest.md`](./2026-09-17-main-ci-followup-wallclock-1525-vitest.md)
（①③ 已做；② 评估为不前移）。
