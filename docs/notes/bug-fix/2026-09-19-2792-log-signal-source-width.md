# 修复 job_log_signal.source 列宽与 agent 白名单脱节（#2792）

Status: implemented
Class: bug-fix

## Decision

三处同 PR 落地：① 新迁移 `t9u0v1w2x3y4` 把 `job_log_signal.source` 从 VARCHAR(16)
扩到 VARCHAR(32)（PG 元数据操作，无表重写；downgrade 先清超宽行再缩列）；
② 模型列宽与注释同步（白名单全集 `inotifyd | polling | logcat | reconciler |
reconciler_rollback`）；③ 入库前新增**派生式**列宽守卫——宽度从
`JobLogSignal.__table__` 列定义现算而非硬编码字典，超宽行逐条折入既有
`rejected` 清单（复用 #1048 部分接受语义，不整批连坐）。

## Alternatives

- **只扩列宽不加守卫**：修掉现行实例但留同类复发面（契约白名单再加长值时
  静默重演）。否决。
- **守卫宽度硬编码字典**：与 schema 漂移风险高（改列宽必忘同步守卫）。否决，
  改为从 `__table__` 派生。
- **在 agent 契约（validate_log_signal）加长度校验**：agent 侧升级节奏独立于
  控制面，旧 agent 发超长值仍能进来；控制面入库侧兜底才是收敛点。否决。

## Verification

- `venv/bin/python -m pytest backend/tests/services/test_agent_log_signals.py -q`
  → 6 passed（含白名单×列宽**结构断言**——该断言在 #2792 引入的
  reconciler_rollback 形态上会当场爆红，即当年缺的那道守卫）；
- 迁移链：`down_revision = f6a7b8c9d0e1`（现 head，无双头）；
  空库迁移由 required check `pr-migrate-empty-db` 承接；
- `python3 scripts/run_gates.py check:quick`（结果见 PR）。

## Revisit

- `first_lines` 为 Text 无长度上限（超大行会膨胀行宽），非本单面；若出现
  存储压力再立项。
- downgrade 的 `DELETE length>16` 是收缩前置（PG 拒绝缩列含数据），退役本列
  时按 script-versioning 常规处理。
