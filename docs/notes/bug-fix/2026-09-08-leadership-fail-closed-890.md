# scheduler leadership 失败策略 fail-open → 分级 fail-closed（#890，R01-F10）

Status: implemented
Class: bug-fix

## Decision

`hold_scheduler_leadership` 在 session 工厂 / 取锁异常后 `yield True`
（fail-open）：多实例部署下 DB 抖动会让**全部副本同时自认 leader**，singleton
job（admission pump、counter reconcile）并发双跑。采纳 issue 建议 1（按部署
形态分级，非逐任务证明）：

1. **Postgres + 选举启用（默认）→ fail-closed**：失败即 `yield False` 跳过
   本轮 tick（日志 `scheduler_leadership_fail_closed ... reason=...`）。
   论证：singleton job 本就依赖同一 DB——DB 不可用期间跳过无可
   用性损失，DB 恢复后 tick 自动恢复；跳过成本不对称地小于双跑。
2. **fail-open 仅保留两条文档化豁免路径**：`STP_SCHEDULER_LEADER_ELECTION=0`
   （显式退出协调 = 遗留单进程，单进程即无双跑——「任务清单+幂等证明」在
   单进程语义下结构性满足，无需逐任务列举）；SQLite / `TESTING=1` / 非 PG
   （非多实例部署形态）。
3. ADR-0027 升 v1.1：P3-1 节写入策略与论证，修订记录留痕。

## Alternatives

- **保留 fail-open + 逐任务幂等/行锁安全证明**（issue 建议 2）——放弃：
  需审计全部 singleton job 及其下游写路径且每次新增 job 重做证明，维护成本
  随任务数增长；fail-closed 一条策略覆盖全部现在与未来任务；
- **只修 lock_acquire 保留 session_factory fail-open**——放弃：两个失败点
  的后果相同（无协调保证），策略应一致；
- **新增显式多实例开关再分级**——放弃：`STP_SCHEDULER_LEADER_ELECTION`
  本身已是「参与协调」的声明位，再造平行开关只会让组合语义更难推断。

## Verification

- 新增 `tests/test_leader_election.py` **7 passed**（故障注入 2：session
  工厂失败 / 取锁失败均 `yield False`；happy path 2：抢到锁 True + 未抢到
  False；豁免路径 3：禁用选举 / TESTING=1 / SQLite 均 True 且断言不触达
  DB）；
- `check:quick` 7 门禁全绿。

## Revisit

- 单实例生产部署在 DB 抖动窗口内 pump tick 会跳过（此前会执行然后失败）——
  外部可观察差异是日志从「pump 失败」变为「leadership 跳过」，无功能损失；
- 若未来出现「不依赖 DB 的 singleton job」，需重新评估该 job 的失败策略
  （当前策略假设 singleton job 均依赖同一 DB）。
