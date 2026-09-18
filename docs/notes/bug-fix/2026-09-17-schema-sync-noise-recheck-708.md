# #708 复核：5 项 alembic 比较噪音一项未缩——但「缩了」这件事此前无人会被提醒

Status: implemented
Class: bug-fix

关联：[#708](https://github.com/DUElost/stability-test-platform/issues/708)、
[schema 基线收敛的既有 note](../simplification/2026-08-31-schema-baseline-convergence.md)、
`backend/tests/test_schema_sync_guard.py`（#944 成对白名单语义）。

## Decision

### 1. 复核结论（先给事实，再谈改动）

在**隔离库**上按 CI 的 `pr-migrate-empty-db` 同姿势复跑（testcontainers `postgres:16` +
`alembic upgrade head` + `check_schema_sync`，本机实测解释器版本 **SQLAlchemy 2.0.52 / alembic 1.19.1**）：

```text
compare_metadata diff: 5 项（基线 5，新增 0）
  [ ] modify_type|alert_rules|event_type
  [ ] modify_type|jira_run|issue_keys
  [ ] modify_type|notification_channels|type
  [ ] remove_index|plan_run|idx_plan_run_admission_queue
  [ ] add_index|plan_run|idx_plan_run_admission_queue
```

即 **#708 的两个触发条件都未达成**（alembic/SQLAlchemy 未修掉这三类比较 bug；
`jira_run.issue_keys` 的 JSON↔JSONB 权衡也未解除）。**基线不该动**，本单不 rebaseline。

 alembic 侧的原始判读仍然成立：`VARCHAR(32) → Enum(...)`（模型 Enum vs DB USER-DEFINED）、
`JSON → JSONB`、partial index 的表达式文本差异（`priority DESC` vs `priority`）。

### 2. 复核过程暴露的真问题：缩水没有信号

`--rebaseline` 是**覆盖**语义（注释自己写明「并集会让已修复项永久留在基线里」），
所以缩小基线的唯一途径是有人在某次运行后跑一次它；而 `pr-migrate-empty-db` 的判据是
`diff ⊆ 基线`——**diff 变小照样全绿**。两者叠加的后果不是「基线偏大」这种审美问题，而是：

> 一条已经不可能再出现的噪音，会长期留在豁免面里；将来真的把那个形态改坏
> （例如索引又被静默删掉、列型又退回 VARCHAR），门禁**不会红**。

这类「守卫自己悄悄失去判别力」的形状，本仓已经付过学费（#1258 撤面板、#2286 的恒真豁免、
#2287 把生产者判据从告警面扩到全指标面），所以这次不等它发生。

### 3. 落地形态：只加提示，不加红灯

新增纯函数 `_stale_baseline_keys(keys, baseline)`，输出「基线中本次未命中的项」，
在汇总行里报出 `基线未命中 N` 并打一段 `HINT:`（说清下一步动作是人工确认后 `--rebaseline`）。
**刻意不判红**，两条理由：

1. 缩水不是失败——把它判红等于要求「谁升级了 alembic 谁负责在本次 PR 里顺手收基线」，
   那会把不相干的 PR 一起拦在门外（同 #2250/#2249 那轮「判据过严制造大面积红灯」的教训）；
2. 更重要的是它必须与 #944 的**成对白名单**相容：`add_index`/`remove_index` 同对象成对进基线，
   **对偶只剩一边出现在 diff 里是单边形态**（索引真的丢了就是这一形态），归 `_filter_new_keys` 拦截。
   若把它也算成「收敛」，同一次运行就会一边喊「新增漂移」、一边喊「可以缩基线」，
   诱导人在错误的时刻跑覆盖式 `--rebaseline`——**那恰好把该拦的形态洗进新基线**。
   所以 `_stale_baseline_keys` 显式排除「对偶仍在 observed 里」的缺席项。

## Alternatives

- **顺手 `--rebaseline`**：否决。diff 与基线逐字相同，覆盖写入只制造一个空改动文件。
- **把 `基线未命中 > 0` 判红**：否决，理由见 Decision 3。
- **给基线加 TTL / 到期提醒**：否决。信号应当来自实测（本次运行未命中），不是日历——
  按时间提醒会周期性地喊一个已经响过的空枪。
- **只在 #708 里留言记录复核结论**（不改代码）：不够。结论本身有效，但下一次噪音真被修掉时
  仍然没有任何机制会提醒人，本单的成因仍然存在。
- **改 `--rebaseline` 为交集语义**（自动去掉未命中项）：否决。基线是「人工确认过的豁免面」，
  自动收缩等于让脚本代替人决定哪些漂移可以放行——那正是 #708 一开始要把噪音逐条具名的理由。

## Verification

- `pytest backend/tests/test_schema_sync_guard.py -q` → **11 passed**（本单新增 5 条：
  当前 diff 对真实基线**不得**冒出 stale；某项整条消失要点出来；**单边形态不得冒充收敛**；
  `main()` 提示到位且 **exit 仍为 0**；5 项全命中时不得出现 HINT）。
  其中最后一条是反向对照——常驻噪声会让提示很快被人忽略，那等于没有信号。
- 真实空库复跑（testcontainers，**未触碰生产库**）：`rc=0`、
  `diff: 5 项（基线 5，新增 0，基线未命中 0）` —— 新汇总行在真数据上不误报。
- **红绿自证（变异）**：
  - `_stale_baseline_keys` 直接 `return []`（提示消失）→ **2 failed**；
  - 删掉「对偶在 observed 里就跳过」这段排除逻辑（单边形态冒充收敛）→
    `test_one_sided_pair_is_not_reported_as_converged` **红**；
  - 恢复 → **11 passed**。
- 测试为什么喂**真实 `_diff_key()` 能还原的结构**而不是 stub 掉它：key 规范化本身是这个守卫的
  命门（`_diff_key` 的注释记载了「回退 key 含对象 repr 内存地址 → 永不匹配基线」的事故），
  把它 stub 掉等于把这条路径移出测试覆盖。
- `ruff check` / `check:quick` / CI required checks：见 PR 表格。

**未做**：不改基线文件；不动模型与迁移；不给 `jira_run.issue_keys` 的 JSON↔JSONB 权衡找出口
（那属测试库方案，另案）；不改 `pr-migrate-empty-db` 的判据强度。

## Revisit

- **触发条件仍在**：alembic/SQLAlchemy 升级后若这三类比较 bug 被修，HINT 会自己冒出来，
  届时人工确认 + `--rebaseline` 即可让基线随实缩；#708 的关闭条件建议改成
  「`基线未命中 5` 且已 rebaseline」而不是「等人想起来」。
- 若将来发现有人长期无视 HINT，再讨论要不要升级成 required check（代价见 Alternatives 第 3 条）。
- 单边形态（#944）的拦截强度未动：本单只**减少误报**，不放松任何既有红灯条件。
