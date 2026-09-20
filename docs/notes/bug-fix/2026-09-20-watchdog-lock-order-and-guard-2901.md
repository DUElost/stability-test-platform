# session_watchdog 两处查询补 id 全序 + 集合内全序的机读判据（#2901）

Status: implemented
Class: bug-fix

- 日期：2026-09-20
- 关联：`#2901`（本单）、锁序家族 `#2635` / `#2787` / `#2796` / `#2871`（第三处，修复在队未合入）、
  `docs/notes/bug-fix/2026-09-19-lock-order-two-halves-2787-2796.md`

## Decision

### 1. 本处修法：两条查询各补 `order_by`

`backend/tasks/session_watchdog.py` 的两条查询各自补上 id 全序（`Host.id` / `JobInstance.id`）：

- `dead_hosts`：循环体里 `host.status = OFFLINE` 的 ORM UPDATE 落在同一事务内 ⇒ host 行锁的
  取锁顺序 = 该查询的返回序；
- `running_jobs`：循环体逐条 `SELECT … FOR UPDATE` ⇒ job 行锁顺序 = 该查询的返回序（对侧
  `agent_lease_extend.py` 是 `ORDER BY JobInstance.id`，无保证的返回序与之交错即可成环）。

### 2. 系统性半边：把「集合内全序」变成**未知即红**的机读判据

家族已出现四次，且**第四处是靠人工重扫全仓才发现的**（#2901 正文自陈）——继续靠审计等于
把判据押在下次有人记得扫上。故新增 `tests/test_lock_order_collection_total_order.py`：

- **判据**（AST）：循环**自身**体内出现 `with_for_update()` ⇒ 其迭代集合必须有确定的全序来源；
- **认三种形态**：① `sorted(...)`（含 `enumerate(sorted(...))`）；② 迭代对象是名字，且其**同一
  函数内**的赋值源文段含 `order_by(` 或 `sorted(`；③ 内联调用自带 `order_by(`；
- **欠账显式**：`_PENDING_TOTAL_ORDER` 登记尚未修的行锁循环（当前仅 `agent_recovery.py` 的
  `payload.active_jobs` ⇒ #2871，其修复 PR **在队未合入**），并有**陈旧即红**的对照用例
  （修复合入后不删登记同样红）；
- **补一条针对性直证**：`session_watchdog` 的 **host 行锁是 ORM UPDATE**、不进
  `with_for_update` 分支 ⇒ 通用判据看不到它，故单列一条钉住该文件两条查询的 `order_by`
  （去掉任一即红，见 Verification 的变异表）。

### 3. 明确不做（issue 自己划的边界）

`事务边界是否下沉为「一主机一提交」`**不在本单**——issue 写明「取决于该 pass 对部分成功的
语义要求，**需 owner 定**」（与 #2871 的开放半边同题）。本单只做**取锁顺序**这一半，不代替
裁决语义。

## Alternatives

- **只改代码、不加判据**：家族第四处的发现方式就是反例（人工重扫）——第五处会同样出现。
  否。
- **把判据扩到「循环内的一切 ORM 写」**：那会把大量与行锁无关的循环写判红（例如循环里累积
  计数后再 UPDATE 同一行），噪音压过信号；`with_for_update` 是「显式取行锁」的机械标记，
  拿它当锚才可维护。否（host 行那一处改用针对性直证）。
- **顺手把 #2871 的 agent_recovery 也修了**：那是**别人的在队 Execution**（其 PR 在队），
  跨 PR 改同一批文件既撞 scope 又抢它的判据。只登记欠账，不代修。否。
- **判据只做注册表（人工维护清单，不检测）**：那样新出现的无序站点要等人先想起来登记——
  检测 + 欠账登记的组合才是「机器负责发现、人负责解释」。否。

## Verification

- `python -m pytest tests/test_lock_order_collection_total_order.py -q` → **6 passed**
  （判据自证 3 条 + 仓库面 1 条 + 欠账陈旧 1 条 + watchdog 直证 1 条）；
- **4 处定向变异逐条回退即红**：撤掉 watchdog 的 job 全序 → **2 failed**（通用判据 + 直证）；
  撤掉 host 全序 → **1 failed**（直证；通用判据**看不到** ORM UPDATE 形态，这正是它存在的理由）；
  判据不再认 `sorted(` 赋值形态 → **2 failed**；登记一条并不存在的欠账 → **1 failed**（陈旧即红）；
- `python scripts/run_gates.py check:quick` → **12 gates 绿**。

**过程中一条自纠**：判据第一版只认「迭代对象直接是 `sorted(...)`」，而真实树里的既有正例是
`ordered = sorted(...)` / `stale = …order_by(...)` **先赋值再迭代** ⇒ 第一版把
`device_lease_reconciler.py:115` 误判成违规。补上赋值形态后才与「家族已合入的三处」对齐
——**新判据必须先在既有正例上跑绿**，否则它测的是判据作者的想象。

## Revisit

- **欠账登记 `agent_recovery.py / payload.active_jobs`**：#2871 的修复 PR 合入后必须删掉该
  登记（陈旧即红会强制这一条）。若 #2871 最终选择了别的修法（非 sorted/order_by 形态），
  判据要跟着扩形态，而不是把登记留成永久豁免。
- **判据的已知盲区**（写下来免得下次当它全能）：① 循环内的 ORM UPDATE 行锁（本单的 host 行
  即此形态，靠针对性直证覆盖）；② 动态拼装的查询（`select(...)` 由变量拼出）；③ 在**另一个
  函数**里定序后再传入的集合；④ 非 `with_for_update` 的显式锁（如 advisory lock）。
  再出现这个家族的第五处而判据没响，先看它落在哪个盲区、扩判据或补直证——不要只修那一处。
- **事务边界的开放半边**（本单 + #2871 同题）：一主机/一候选一提交 vs 整趟一次提交，取决于
  部分成功的语义要求；owner 裁决前，判据只管顺序、不管边界。
