# 文档真值尾账收口：#3099 三项（台账守卫下界 / ADR-0015 索引清单 / ADR-0003 落地清单行）

Status: implemented
Class: bug-fix

关联：[#3099](https://github.com/DUElost/stability-test-platform/issues/3099)（本单）——三处尾账分别出自
[#3039](https://github.com/DUElost/stability-test-platform/pull/3039)（`f5e56608`，六份文档同步）、
[#3023](https://github.com/DUElost/stability-test-platform/pull/3023)（`445944b6`，五处对齐）、
[#3035](https://github.com/DUElost/stability-test-platform/pull/3035)（`17a2be1b`，状态机与 hub 自述）。
上游判据：[#2661](https://github.com/DUElost/stability-test-platform/issues/2661)（台账与守卫）、
[#2694](https://github.com/DUElost/stability-test-platform/issues/2694)（两条索引）、
[#3001](https://github.com/DUElost/stability-test-platform/issues/3001)（ADR-0003 的移除标注）。

## Decision

本轮只补三处「同一修复落地后没跟上的尾账」，不改产品行为：

1. **`tests/test_removed_env_keys.py` 的 `MIN_LEDGER_KEYS` 补 `ENABLE_CRON_SCHEDULER`**：
   §6 已在 `#2999` 登记该键，但下界清单未同步——该行被删不会触发判据，而文档
   `:176` 当时正宣称「删掉一行则守卫红」。同时把注释改为写实：这是**手工维护的独立清单，
   不从表体派生**（派生 ⇒ 下界 ≡ 表体 ⇒ 「删掉整张表」重新成为绕过手段，判据恒真）。
2. **`docs/adr/ADR-0015-audit-log-system.md` §数据库 索引行补全**：`#2694` 的两条索引
   只补进了 §索引优化，§数据库 那行仍只列两条 ⇒ 同一份 ADR 内两处清单不一致；
   改为四条并与 §索引优化同源。
3. **`docs/adr/ADR-0003-…md` 落地清单首行加移除标注**：`:95` 仍把 `device_lock.py` 记为
   「✅ 统一设备锁服务」，而同文件 `:110` 已划线标注「已移除」（`#3001` 落地）⇒ 按该文件
   既有的「⚠️ 此项标记有误」体例（`:102`）在行内点注，不删历史清单条目。

## Alternatives

- **让 env 文档那句「删掉一行则守卫红」为真**：唯一实现方式是让守卫同时保护全部表体行，
  而下界必须独立于表体才有意义，故该句在现机制下**不可能为真**。选择改句子描述真实机制，
  而不是留一个做不到的承诺。
- **加一条「表体每行都必须在下界里」的自检**：看似消掉两处维护，但会让下界恒等于表体，
  破坏本守卫第 2 条（台账不可被清空）的本意，否决。
- **ADR-0003 直接删除 `:95` 那行**：落地清单是「当时做了什么」的审计陈述，删除即抹掉轨迹；
  沿用同文件 `:102` 的加注体例更稳。
- **与在飞 PR 同批改 `deploy/prometheus/alerts-stability-platform.yml` 文件头**：不做——
  `.wt/stp-3077` 当时正持该文件的未提交修改，按撞单纪律让出；`#3098` 项 1 另行收口。

## Verification

- `../../venv/bin/python -m pytest tests/test_removed_env_keys.py -q` → **6 passed**（含该文件自带的判别力自证 4 例）。
- **变异自证**（内存内变异，不改文件——本 worktree 有多处未提交改动，避免用 `git checkout` 还原）：
  基线 `MIN_LEDGER_KEYS - 表体键 = ∅` 通过；删掉 `ENABLE_CRON_SCHEDULER` 行 → 判据红
  （`missing=['ENABLE_CRON_SCHEDULER']`）；删掉既有的 `USE_SESSION_WATCHDOG` 行 → 同样红，
  即新增下界键与既有键同权，不是装饰。
- `scripts/run_gates.py check:quick` → **`[OK] check:quick (14 gates)`**（含 ruff / env-inventory / gov-surface /
  ai-work / god-files；worktree 内首跑时 `eslint` 因缺 `frontend/node_modules` 报红，
  软链主树依赖后复跑即绿——与本次改动无关，本次未触碰 `frontend/**`）。
- 本轮为文档 + 测试下界，未改任何产品代码路径；`#3099` 的三处均已在本 PR 内闭合。

## Revisit

- 若**第三次**出现「§6 补行但下界未同步」，说明两处手工维护已不可依赖：届时把下界改为
  checked-in 快照（由脚本刷新）或让守卫对「新行未入下界」报警，而不是继续手工同步。
- `#3098` 项 1（`deploy/prometheus/alerts-stability-platform.yml` 的文件头）待 `#3077` 合入后收口。
