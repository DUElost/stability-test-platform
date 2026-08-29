# 覆盖率：先度量，不设阈值

Status: implemented
Class: testing

## Decision

仓库此前**完全没有覆盖率数据**：后端无 `pytest-cov`，前端有 `test:coverage`
脚本却没装 `@vitest/coverage-v8`（直接跑会失败），`ci.yml` 里 `--cov` /
`--cov-fail-under` / codecov 均为 0 处。

本次只把度量接上，**不设任何阈值**：

| 位置 | 改动 |
|------|------|
| `backend/requirements-dev.txt` | 加 `pytest-cov>=6.0,<8.0` |
| `.coveragerc` | **新增**，定义 omit 清单（见下） |
| `.github/workflows/ci.yml` | `backend-test` 三段 pytest 加 `--cov=backend`（后两段 `--cov-append`，末段出 `term-missing`）；`frontend-check` 的 vitest 加 `--coverage` |
| `frontend/package.json` | 加 `@vitest/coverage-v8 ^4.1.11` |

不写进 `pytest.ini` 的 `addopts`：本地日常单测不该为覆盖率付开销，只有 CI 跑。

### omit 清单（`.coveragerc`）是这份数字能否用的前提

`backend/agent/scripts/` 有 **11 个脚本版本 × 全量重复 ≈ 3.1 万行**
（backend 总量约 17.5 万行），它们靠真机执行验证而非单测（ADR-0020 还规定
目录内容不可变）。计入分母会凭空压低约 18 个百分点。
`alembic/versions/` 同理 —— 由 `alembic upgrade head` 执行，不在 pytest
进程内，永远统计为 0%。

**换 omit 口径会让数字不可比**，所以此后任何改动 omit 的 PR 都必须重测并
更新本 note 的水位记录。

## Alternatives

**A. 度量 + 立即设阈值**（放弃）
需要先知道当前水位才能定底线。一上来设（比如拍脑袋 70%）要么立刻把门禁
变红、要么形同虚设。而且首次数字里含口径噪音，据此定的阈值会在修好口径
后失效。

**B. 度量 + 上传 artifact / 接 codecov**（暂缓）
信息最全，但多一个外部服务依赖，且单人项目里「历史趋势」的价值低于
「我现在能不能看到这个 PR 有没有让覆盖率掉」。留待确实需要趋势时再做。

**C. 只度量不设阈值**（采纳）
最低成本拿到水位，等数字稳定后按「略低于当前水位」收口。

## Verification

实测水位（2026-08-29，本 note 对应的 commit）：

| 范围 | 结果 |
|------|------|
| 前端（85 文件 / 621 测试全绿） | 语句 **70.94%** · 分支 66.55% · 函数 63.45% · 行 **73.38%** |
| 后端（**仅 agent tests**，1237 测试全绿） | 语句 **39%**（29744 语句 / 18064 未覆盖） |

⚠️ 后端 39% **不是完整水位**：本地只跑了 `backend/agent/tests/`（不需要 PG），
`backend/tests/`（135 个控制面文件，需 PostgreSQL）与根 `tests/` 没跑。
CI 上三段 `--cov-append` 累加后的真实数字会明显更高。首轮 nightly 跑完后
应回来把这一行补正。

前端已能看出的薄弱点：`utils/api/` 整体 37.83%，其中 `planRuns.ts` 2.7%、
`management.ts` 6.15%、`projects.ts` 8.33% —— API 层几乎是裸的。

## Revisit

- **首轮 nightly backstop 跑完后**：用 CI 日志里的后端总覆盖率补正本 note
  的水位表，然后决定是否设 `--cov-fail-under`（建议设在补正值下方 3–5 个点）。
- 若 `--cov` 让 nightly job 明显变慢到影响排队，改为只在其中一个 job 度量。
- 前端若要设阈值，用 vitest 的 `coverage.thresholds`；注意它按 glob 生效，
  与后端 `--cov-fail-under` 的全局口径不同，两边数字不可直接比较。
