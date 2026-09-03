# lock 卫生测试前移到 PR 合入路径

Status: implemented
Class: process

## Decision

`tests/test_requirements_lock.py` 与 `tests/test_requirements_dev_lock.py`
（共 32 例）随 `pr-agent-tests` job 在 PR 上直接跑（`ci.yml` 新增
`Run lock hygiene tests` 步骤），不再只等夜间 backend-test 兜底。

背景：2026-08-30 / 08-31 / 09-01 三次夜间全量 CI 失败中有两次是 lock
卫生失败（「lock 已过期」「抬头无 digest 标记」），根因都是「改了
requirements(.dev).txt 未重生成 lock」。这类失败在 PR 作者手里 2 分钟就能
修，但因为只在夜间跑，main 的全量状态最长红了 14 小时（08-31 22:14 →
09-01 12:20）。见 `PLATFORM_HEALTH_REVIEW_2026-09-03` 与 09-03 晨会审计。

选择挂进 `pr-agent-tests` 而非新开 job：该 job 已装 dev.lock（pytest 现成），
且它本身就是 required check，不需要动 branch protection Settings（新 job
的 required 化只能手工改仓库设置，PR 做不到）。

## Alternatives

- **整个 `tests/` 目录前移**：放弃。目录里其余文件（alembic upgrade、部署脚本
  契约）与 PG/部署环境耦合，盲目前移可能把依赖 DB 的失败搬进合并路径，
  违反 ~2 分钟注意力预算。需要的只是两份 lock 卫生。
- **Dependabot workflow 里 `--stamp` 后再跑一次校验**：放弃。只覆盖
  Dependabot 来源，人工改 manifest 的路径仍然漏（两次事故都是人工 PR）。
- **维持现状（夜间兜底）**：已被 14 小时红 main 证伪。

## Verification

- 本地 `python -m pytest tests/test_requirements_lock.py
  tests/test_requirements_dev_lock.py -q` → 32 passed in 0.10s（纯离线，
  无 DB/网络），PR 路径增量 <1s。
- 纯离线性来自测试实现：digest 比对与集合比对只读仓库文件
  （`tools/dev/requirements_digest.py` 子进程同样离线）。
- 回归防护：未来有人往这两个文件加联网/DB 依赖时，会直接拖慢 PR 路径并
  反映在 pr-agent-tests 耗时上，显性可查。

## Revisit

若 `tests/` 其余 repo-level 测试也证明离线且快（alembic 契约类），可再评估
整目录前移；或当 PR 数量上来、Actions 排队时间成为主要延迟时，重新权衡
~1s 增量的去留。
