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

**勘误（记录本身，不改写历史）**：`9c6fa686` 的 commit 正文里那段示例被 shell 的双引号转义
污染成了 `f"{item[\"k\"]}"`（带反斜杠）——真实 repro 是 f-string 内层复用**同类型**引号的
`f"{item["k"]}"`（3.13 正常运行、3.11 `SyntaxError: f-string: unmatched '['`）。该 commit 已是
`main` 的祖先，按「`main` 只通过 PR 合入、不直推、不事后改写已发布历史」不做事后修正，
勘误以本节为准。除本 note 外的 7 个交付文件（两份文档、服务、CLI、执行器、两份测试）正文均
未被污染：逐个跑 `git grep -F` 匹配「反斜杠 + 双引号」皆为空；本 note 内该序列出现 3 处，全是
上面为复现 commit 原样（1 处）与引用检索命令（2 处）而刻意保留的字面量。

`python scripts/run_gates.py check:quick` → 10 gates 全过（结果见 PR）。

**补记（同日 20:4x，守卫首跑就暴露的两件事）**：

- 守卫上线后对生产库首跑即 `--guard` 退出码 `1`（`active ∧ 零引用` 由 32 涨到 36——期间
  其他批次注册了 3 个新版本，其中一个把同族旧版顶成「零引用 + 零执行 + 非最新」）。
  判据本身工作正常：这正是要它自动产出的候选，不需要人再盘一遍。
- **执行器 `plan` 子命令在文档给的调用形式下必炸**：`python tools/dev/retire_script_versions.py plan` 直跑时
  `sys.path[0]` 是 `tools/dev`，函数内 `from backend…` 抛 `ModuleNotFoundError`。
  单测当时用 importlib + `PYTHONPATH=.` 加载模块，恰好绕过了这条路径形态——教训：
  **CLI 工具的回归必须包含「按路径直跑」的子进程用例**（已补
  `test_script_runs_when_invoked_by_path`，反证：去掉 bootstrap 该测试红）。
  修法与 #1659 的 `queue_head_telemetry.py` 同款：`__file__` 推导 `REPO_ROOT` 插入 sys.path。

**补记（次日 2026-09-17，合入后首巡暴露的第二类假红）**：

- 按路径形态调用 `backend/scripts/check_unreferenced_script_versions.py --guard` 时，import 期
  `ModuleNotFoundError` 让解释器返回 **1**——与 `--guard` 的「存在应退役版本」同码。判红是退役
  授权依据，这条混淆的方向比漏退役更危险（运维会去 `DELETE` 根本不该动的版本）。
- 两道防线，缺一不可（各有回归用例）：① 与 `retire_script_versions.py` 同款的 `REPO_ROOT`
  bootstrap，使 `-m` 与路径形态等价——import 期崩溃发生在任何 wrapper 之前，只加 wrapper 拦不住；
  ② `main()` 包一层，把非预期异常折成 `GUARD_ERROR_EXIT = 3` 并显式打 `GUARD ERROR`，连接失败、
  schema 漂移等也不再伪装成 1。
- 教训比本工具更一般：**凡给自动化消费的退出码，必须为「工具自己坏了」留一个不属于任何判定
  语义的码**。只定义 0/1/2 的契约，默认含义就是「崩溃算 1 号判定」。同理，反证要做两遍：删
  bootstrap 第一条断言红、删 wrapper 第二条断言红。

**补记（同日，24h 只读审计两项的处置）**：

- **审计 A（`--guard` 无执行者）成立**：`git grep` 非文档命中确实只有模块自身与一行 print
  提示，`.github/workflows/*` 三个 schedule 任务都不涉及它。文档原写「由运维或定时任务跑」
  与现状不符，已改为「当前执行者是人工」并说明为什么不塞进 CI（CI 不得连生产库）。**未**新增
  schedule：接入自动化需要先定「谁在哪个窗口连生产库跑」，那是运维归属问题，不是再写一个
  timer 就能收工的事（见本 note Revisit）。
- **审计 B（回环护栏被 userinfo 绕过）成立且已复现**：`http://127.0.0.1:8000@evil.example`
  在旧正则下 `host` 截成 `127.0.0.1` 被放行，而 `urlsplit().hostname` 是 `evil.example` ⇒
  发出去的是 admin Bearer token + 批量 `DELETE /scripts/{id}`；反向 `http://[::1]:8000` 被截成
  `[` 误拒。改法按建议用 `urlsplit().hostname`，另加 scheme 白名单。三条回归：spoof 必拒、
  IPv6/localhost 必放行、非 HTTP(S) 必拒。
- 与「缺最后一跳」有关的方法论：审计给的是 `file:line` + 可复现输入，**修之前先自己跑一遍
  复现**——本轮两项都是照做后才确认成立（此前也遇到过审计判断不成立的情况）。

**补记（同日正式授权后的退役执行，暴露第四例「最后一跳缺失」）**：

- 授权后对生产库执行了首条判据退役：`flash_preflight@1.0.3 (id=181)` → `is_active=False`，
  `audit_logs` 264933（`action=deactivate`、`username=stp-admin`、`details` 带 name/version）；
  同族 `1.0.1 / 1.0.2 / 1.0.4` 仍 active（族未被清空）、`inactive ∧ referenced = 0`、
  账 `active 74→73`、`active ∧ 零引用 37→36`，复跑 `--guard` → **rc=0（GUARD OK）**。
- **`execute` 此前从未对真实 API 跑通过**：`ControlPlane._login()` 在 `__init__` 里先调
  `_login()`、之后才把 `Authorization` 挂到 session ⇒ 那个「校验身份」的 `/auth/me` 实际是
  **未认证请求**（`/auth/token` 不发 auth cookie，没有 cookie 可兜）⇒ 生产实跑 `401`。
  单测把 `ControlPlane` 整体换成 `FakeClient`，这条真实调用序列一次都没被执行过——与 PR
  #2430「用 importlib + PYTHONPATH 绕过路径形态」是同一个失效模式的两个实例：
  **打桩把被测对象自己测的那条路径替掉了**。修法是 Bearer 头显式挂在 `/auth/me` 那一次请求上，
  并补三条登录面回归（校验请求带凭据 / 缺 `access_token` 显式报错 / 非 admin 拒执行）。
- 审计 A 的处置由「按现状更正文档」升级为「补上执行者」：新增
  `tools/dev/script_guard_probe.py` + `stp-script-guard.{service,timer}`（每日 09:30、
  `Persistent=true`，落 `due/unknown/broken/last_run` 四个 node-exporter 指标）。关键取舍：
  **`due`（有到期项）与 `unknown` 都算任务成功**，只有 `broken` 让 systemd failed——否则 timer
  会因「存在待授权项」天天红，真故障被告警疲劳淹掉；`last_run` 则用来区分「干净」与「静默停摆」。
  probe 只用 stdlib、不 import `backend`（避免 import 期解析 `DATABASE_URL` 的老副作用）、
  不读凭据、永不写库。

**补记（同日，告警面接入——顺带暴露两处契约盲区）**：

- 指标没有告警就等于「跑了一个没人看的定时器」，所以补两条规则：`due>0` 持续 7 天
  （`StabilityScriptGuardRetirementDue`）；三种失能形态合成一条
  （`StabilityScriptGuardUntrusted`：broken／unknown／`time()-last_run > 48h`／`absent`）。
- **盲区 1（指标来源）**：告警结构层只认 `backend/core/metrics.py` 注册表，textfile 指标会被判
  「未知指标」。解法不是开豁免清单，而是让 `tests/metrics_registry.py` 从生产者源码 `ast`
  提取 `_METRIC_HELP` 的键并入索引——生产者改名/删除，告警立刻红。实现时先踩了 `ast.walk`
  把 HELP **文案**也当指标名收进来（4 真 4 假），已改为只取 `ast.Dict` 的 keys 并校验指标名
  字符集，非法名直接 `AssertionError`，不把垃圾名静默入表（那等于把「未知指标」伪装成已知）。
- **盲区 2（解析器）**：`_selectors` 跳过函数名（后随 `(`），但 PromQL 的**集合运算符**
  `or`/`and`/`unless` 既不是函数也不是指标 ⇒ 仓库 17 条规则从没用过 `or`，第一次用就被判成
  「未知指标 or」。修的是解析器（`_SET_OPERATORS` + 解析器自证断言），不是把一条告警拆成三条
  去迁就解析器——后者等于让实现缺陷反向约束表达力。
- 场景层两个坑同样只有真跑 promtool 才暴露：`interval: 1h` 大于 5m staleness ⇒ 求值点上指标
  无值 ⇒ 既凑不满 `for: 7d`（该报的不报）又让 `absent()` 误真（不该报的报）；必须 `interval: 1m`
  且点数覆盖最长 eval_time（12100 点 ≈ 201h）。版本不对称也顺手消掉了：本机 promtool
  2.53.3 与 CI pin 的 3.13.3（`docker run --rm --user $(id -u):$(id -g)
  prom/prometheus:v3.13.3 --entrypoint sh … promtool test rules …`）**两版都实跑 SUCCESS**。
- 工具教训（第三次同类）：`edit()` 用「old ⊂ new」的锚点重复执行会**叠加插入**（本次 doc 里
  同一段被插了 3 次）。以后带锚点重放的补丁一律先查 marker 是否已存在，**且 marker 串必须逐字出现在新增文本里**——我紧接着写第二段补记时 marker 用了「另外踩了一条流程教训」而正文写的是「还有一条流程教训」，guard 当场失效、同一段被插了两遍（靠 `count()==2` 才检出）。

- 还有一条流程教训：#2457 被 FIFO 队列合入**之后**我又往同一分支 push 了告警 commit，
  它留在已合并分支上、不会进 main——`gh pr view` 只说 MERGED，真相要靠
  `git merge-base --is-ancestor <sha> origin/main` 才看得出来。往 OPEN 分支追加内容前
  必须先确认队列还没吃掉它；补救办法是从新 main 开分支 `cherry-pick` 重放，**不是**
  force-push 已合并分支。

## Revisit

- **谁在什么时候跑 `--guard`**：**已于 2026-09-17 闭合**——控制面 `stp-script-guard.timer` 每日
  跑 `tools/dev/script_guard_probe.py`，落四个 textfile 指标并接两条告警（`due>0` 持续 7d、
  四种失能合成一条）。原判断「不要为此新增调度器 job」仍然成立：它是 systemd timer，
  **没有**进后端调度器。剩待观察：`7d`/`48h` 两个阈值是拍的，真实噪声水平要跑两周后复议。
- 冷却期 60 天是 #735 评审追加项的口径，不是实测最优——若 `PLAN_RUN_RETENTION_DAYS` 收紧，
  「窗口内零执行」的含义变化，常量与文档要一起重议（测试会挡住只改一侧）。
- ADR-0039 的「退役 → 冷却 → 删除」第二步若启动，`plan` 产出的 manifest 形状可直接作为
  其输入清单载体（届时需要新增 `--include-retired-days` 之类的过滤，而不是再写一个工具）。
- `KEEP_LATEST_ACTIVE` 豁免的脚本族若长期零引用（如 `noop`、`monkey_test`），说明该族已死；
  本判据不处理「整族退役」，那需要产品侧确认脚本不再提供。
