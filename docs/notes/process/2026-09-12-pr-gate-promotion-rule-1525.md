# PR 门禁前移触发规则（#1525 决策记录）

Status: implemented
Class: process

## Decision

**背景**：PR 侧不跑 `backend-test` / `frontend-check` / `docker-build`（书面接受的
注意力预算取舍，`ci.yml` 注释在案），缺陷可先合入 main、最迟次日夜间兜底暴露
（敞口 ≤24h）。

**量化（近 30 次 `main-ci-backstop` 运行 ≈30 天）**：

- 红夜 **9 次（≈30%）**；同期合入 PR **300+** → 约**每 3 天 / 每 33 个 PR 一次**；
- 归因：**`backend-test` 类 5/6**（Run backend tests ×3、Run repo-level tests ×1、
  迁移空库类 ×1）、`frontend-check`/`docker-build` **0/6**；
- 迁移空库类已由 required **`pr-migrate-empty-db`** 承接；repo-level 根测试类由
  **#1569**（在途）承接；09-09/09-11 红夜无告警系 backstop 自身缺陷（#1548，
  2026-09-12 已修）。

**裁决（owner，2026-09-12）**：**维持现状 + 规则制度化**——

> 同类夜间红灯 ≥2 次 → 评估将该类前移为 PR 侧检查（required 或信息性）；
> 单次偶发不动作。

规则写入 `docs/development/repository-workflow.md`「CI 分层」节（含当前 PR 侧
required / PR 排除两份清单与量化依据链接）。

## Alternatives

- **引入 Merge Queue**（issue 选项 2）：驳回——拦截收益 ~1/3 夜 vs 成本 ≈10× 全量
  CI/天（~10 次/天 vs 当前 1 次/夜）；且合并已被 FIFO 队列串行化，Queue 的增量仅为
  「合并前全量」；
- **全量前移 PR（把三个 job 都设为 required）**：驳回——直接打破两分钟注意力窗口的
  既有取舍（`2026-08-14-merge-path-attention-budget.md`）；
- **不制度化、维持临场判断**：驳回——既有 4 次前移（迁移/agent/根测试/…）全是临场
  做的，规则缺失正是本项的本质问题。

## Verification

- `docs/development/repository-workflow.md`「CI 分层」节含：前移触发规则 + 前移先例
  + 当前分层清单 + 量化依据链接；
- `python tools/dev/check_governance_surface.py --check` 全绿；
- `python scripts/run_gates.py check:quick` 通过。

## Revisit

- 「≥2 次」阈值若带来过多前移评估噪音，可上调（如 ≥3 次）；
- #1569 落地后，「repo-level 类」从「在途」改为「已前移」，同步更新规则节的先例清单。
