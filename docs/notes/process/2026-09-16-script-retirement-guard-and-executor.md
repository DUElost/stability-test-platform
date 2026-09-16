# 退役判据固化与巡检守卫（#735 后续）

Status: implemented
Class: process

上一轮 #735 A 批（50 条零引用版本退役）的判据只活在一次性 SQL 与
[2026-09-16-script-retire-channel](2026-09-16-script-retire-channel-735.md) 里——「这批怎么算
出来的」没有代码载体，下一批必然重算重漂。本 note 记录把它固化成代码 + 门禁的过程。权威条文在
[`script-versioning.md` §判据与巡检](../../development/script-versioning.md)。

## Decision

1. **判据唯一事实源 = `backend/services/script_retirement.py`（纯函数，不碰 DB）**。
   输入是 `(name, version, is_active, refs, last_used_on)` 的事实投影，输出 `RETIRE` 或带理由的
   保留结论（`KEEP_REFERENCED` / `KEEP_LATEST_ACTIVE` / `KEEP_RECENT_USE` / `KEEP_INACTIVE`）。
   优先级固定为「引用 > 同族最新 active 豁免 > 冷却期 > 无执行事实」。后置不变量
   `assert_no_family_emptied` fail-loud：任何脚本族都不得因一批退役失去全部 active 版本。
2. **诊断工具加 `--guard` 巡检模式**（`check_unreferenced_script_versions`）：`0` 无到期项 /
   `1` 存在应退役未退役 / `2` 执行事实不可得。默认模式仍恒 `0`（诊断工具不是门禁）——两条语义
   分开，既保住 #735 §1.3 修好的原契约，又给自动化一个可判定的退出码。
   `--json` 与 `--guard` 组合时结论只进 payload（追加人类可读行会让 jq/`json.load` 直接失败）。
3. **退役执行器 = `tools/dev/retire_script_versions.py`，两段式**：`plan`（只读，判据出
   manifest）→ `execute --yes`（走控制面 API）；`reactivate` 处置误退役。护栏：缺 `--yes` 只
   dry-run；写前逐条核对 `(name, version)` 未漂移；写后读回复核；无凭据不盲试 API；默认拒绝非
   回环地址；manifest 形状错显式拒绝。**不直连数据库写、不碰版本目录文件**。
4. **CI 与生产的分工是本质边界**：CI 不得连生产库，所以 CI 只锁「判据函数 + CLI 退出码 +
   SQL 在真 PG（testcontainers）上跑得通」；「生产库里现在有超期项」这类事实只能由运维/定时
   任务跑 `--guard` 得到。把后者伪装成前者（在测试里连生产库）是红线。
5. **B-6 退出判据入文档**：同族最新 active 的豁免不是永久身份——出现更新的 active 版本即让位。

涉及文件：`backend/services/script_retirement.py`（新）、
`backend/scripts/check_unreferenced_script_versions.py`、`tools/dev/retire_script_versions.py`（新）、
`backend/tests/test_script_retirement_guard.py`（新）、
`tests/test_retire_script_versions_tool.py`（新）、`docs/development/script-versioning.md`、本文。

## Alternatives

- **把巡检写成连生产库的测试**：直接违反 `docs/development/testing.md` 与 `AGENTS.md` 硬边界
  （本机可能同时是生产库宿主）。放弃，改为「CI 锁判据 / 运维跑 `--guard`」。
- **在平台侧加 SAQ 或 APScheduler 告警 job**：治理动作要的是「运维收到信号后决策并留痕」，
  塞进执行引擎等于把退役判据的第二份实现写进调度器，且 ADR-0039 的冷却/删除口径未定。放弃；
  如果将来要自动告警，接 `--guard` 的退出码即可（Prometheus textfile / cron 皆可）。
- **判据直接写在诊断工具里、执行器再抄一份**：两份豁免规则正是本 note 要消除的东西；
  #735 前期「工具口径 82 个人工口径 50」的落差就是没有单一事实源的代价。放弃。
- **用 Alembic 迁移表达批量退役**：上一 note 已否（丢操作者身份与 `audit_logs`）。
- **把执行器做成通用「按 SQL 结果批量改库」工具**：绕过服务端守卫与审计，风险面远大于收益。
  放弃，坚持只经控制面 API。

## Verification

`PYTHONPATH=. python -m pytest backend/tests/test_script_retirement_guard.py tests/test_retire_script_versions_tool.py -q`
→ **34 passed**（其中判据本体 11 条、真 PG 上 `compute_reference_counts` + `compute_usage_facts`
端到端 1 条、CLI 退出码 4 条、执行器 16 条、文档↔代码互锁 3 条）。

判据固化后与实际执行结果的对账（生产库只读）：

- 以 A 批执行前的快照复算：判据候选 **50 条，与实际执行的 50 条零差异**（多无、少无）——
  固化后的规则就是当时人工判断的那套，且额外修正了一处口径（豁免对象是「最新 **active**」
  而非「最新行」，本批数据下两者重合）；
- `--guard` 对当前生产库 → exit `0`（`active ∧ 零引用` 32 全部落在豁免/冷却期，桶计数
  21 承接面 + 11 冷却期 + 38 仍被引用 + 107 已退役 = 177 行账平）；
- `plan --today 2026-11-15` → 恰为 C 组 **11 条**，id 与上一轮手算的到期清单一致
  （`days_until_cooldown_expiry` = 末次执行 + 60 天，如 `monkey_setup@2.3.3` 2026-10-04）。

测试期间修掉的两个真实缺陷：① `--json --guard` 恒返回 0（退出码被人类可读分支绑走，
`--json` 形态正是自动化要消费的）；② 解释器版本不对称——CI 各 job 统一 **Python 3.11**、
本机 venv 是 **3.13.5**，两个方向都实测到了：`f"{item["name"]}"`（PEP 701 同类引号嵌套）
在 3.13 正常运行、在 3.11 是 `SyntaxError`（本机门禁绿、CI 必红）；另一条含 `{{` 转义与
`{len(items)}` 组合的 f-string 反过来在 3.13 报 `unmatched ']'` 而 3.11 通过。
处理：新代码一律**先取局部变量、不在 f-string 内嵌引号字典键**，并在 `python:3.11-slim`
与本仓 venv 双解释器 `compileall` 通过后提交；该陷阱与自查命令已写入
[`dependencies-and-quality.md`](../../development/dependencies-and-quality.md)。

`python scripts/run_gates.py check:quick` → 10 gates 全过（结果见 PR）。

## Revisit

- **谁在什么时候跑 `--guard`**：现在退出码有了，触发还没有。等 #2055/#2048 系列收窗后，
  接一条定时巡检（cron 或部署后检查）即可；不要为此新增调度器 job。
- 冷却期 60 天是 #735 评审追加项的口径，不是实测最优——若 `PLAN_RUN_RETENTION_DAYS` 收紧，
  「窗口内零执行」的含义变化，常量与文档要一起重议（测试会挡住只改一侧）。
- ADR-0039 的「退役 → 冷却 → 删除」第二步若启动，`plan` 产出的 manifest 形状可直接作为
  其输入清单载体（届时需要新增 `--include-retired-days` 之类的过滤，而不是再写一个工具）。
- `KEEP_LATEST_ACTIVE` 豁免的脚本族若长期零引用（如 `noop`、`monkey_test`），说明该族已死；
  本判据不处理「整族退役」，那需要产品侧确认脚本不再提供。
