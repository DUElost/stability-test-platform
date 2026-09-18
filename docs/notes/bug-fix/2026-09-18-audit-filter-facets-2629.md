# 审计筛选候选与写入词表同源：facets 端点驱动选项，杜绝「选中即 0 条」的假阴性（#2629）

Status: implemented
Class: bug-fix

## Decision

`/audit` 的两个筛选维度原先是**两侧各写各的**：后端 `_apply_audit_filters` 对裸 `str` 做精确
等值（`backend/api/schemas/audit.py` 里 `resource_type`/`action` 都没有枚举），前端硬编码
「9 个资源 + 6 个操作」。逐值核对写入侧字面量后：操作的 `dispatch`/`start`/`cancel` 与资源的
`tool`/`tool_category`/`template` **代码零写入点**——选中必然「共 0 条」；而真实存在的 `session`
(160) / `plan_run` (85) / `job_instance` 等 18 种资源类型没有任何入口（dev 269 行里 98.5% 筛不出来）。

**本质不是少了几个 option，是没有机制保证「写入词表」与「筛选选项」同源**，所以删掉这 6 个
也只是把下一次漂移推迟。终态做法：

1. 新增 `GET /api/v1/audit-logs/facets`（admin-only）——两个维度的候选值由 `audit_logs` 里
   **实际写入过的 distinct 值 + 条数**给出，按条数倒序。**能选出来的一定筛得出东西**，
   这是结构上的，不靠任何人维护清单；
2. 口径是**全表**，不随时间或其它筛选收窄：否则「选项随选择消失」会让已选值在下拉里找不到，
   比原来的假阴性更难解释；
3. 前端资源维度改成数据驱动下拉；**操作维度改成「精确匹配 + datalist」**——`action` 有 86 种
   字面量，下拉列不全，而「列不全的下拉」正是死选项的产生方式。这退到与 #628 用户名/IP 同一
   范式（issue 明确认可该范式），代价是操作筛选从「点一下」变成「选一下或打一下」；
4. 中文标签降级为**纯展示名**：`ACTION_LABELS` 保留（表格里渲染 `create → 创建` 仍要有），
   另加 `RESOURCE_LABELS`；两者都不再充当选项来源，**没有映射的值按原字面量展示**。
   宁可看到 `job_instance`，也不要一个筛不出东西的中文选项——「硬编码中文标签直连精确等值
   自由词表」这个连接本身就是病灶。
5. `audit_logs` 表不存在的兜底判定从列表路由里抽成 `_is_missing_audit_table()`，两条路由共用
   （否则「老库没建表」时一边能用、一边 500）。facets 同样返回空而不是 500。

顺带被自己的两道闸门夹住两次，都是**真实生效**的记录，不是补写：

- 新端点是 admin-only GET，`#2642` 刚修好的 ast 扫描器当场把它拦下来要求登记
  （`audit.py:/facets`，依据 = `/audit` 在 `AdminRoute` 下）。跑完 `M1b` 之后立刻在真单上应验；
- 新增「后端 Pydantic + 前端 interface」等于新增一处双声明，因此登记进
  `tests/test_api_response_shape_contract.py` 的轴线 C（28 → **30 对**），由该门禁双向对拍。

## Alternatives

- **只删 6 个死选项**（issue 的止血项）：作为终态否决。它把词表清单从「含错值」改成「不含错值」，
  下一次写入侧改名（历史上 `tool`→`script`、`template`→`task` 就改过）仍然零报警，且 98.5%
  无入口的问题一分未动。止血项被终态**包含**（硬编码选项整份消失），不需要单独做。
- **issue 的方案 (b)：后端定义 `AuditResourceType`/`AuditAction` 常量集，写入点只能引用常量**：
  否决。86 个 action 字面量散落在各写入点，收常量集是一次跨全仓的重排（且既有历史行不会跟着改），
  收益不及「数据本身就是词表」；更关键的是它仍要回答「下拉放不放得下 86 项」。
- **「取消」映射成 `abort_plan_run`/`job_batch_terminalized`/… 的 `IN` 白名单**（issue 提的另一路）：
  暂不做。它需要产品口径「哪些 action 算取消」，那是**第二套词表**——把漂移从「选项 vs 写入」
  挪到「类别 vs 成员」，除非确有按类别筛的需求，否则不如精确匹配诚实。触发条件见 Revisit。
- **facets 随当前筛选/时间窗收窄**：否决，理由见 Decision 第 2 点。
- **前端缓存词表到 localStorage / 构建期生成**：否决。任何静态副本都会重新引入漂移面。

## Verification

环境：本机隔离库（`env -u DATABASE_URL`，testcontainers PG；`PYTHONDONTWRITEBYTECODE=1`），
生产控制面与生产库零写入。

- 后端：`backend/tests/api/test_audit.py` → **14 passed**（原 9 → 14，新增 5 条 facets 判据）。
  其中核心一条是**自洽判据** `test_facets_are_all_selectable`：遍历 facets 返回的每个值，
  用它去请求列表，断言 `total >= 1`——它不依赖任何词表清单，写入侧将来改名/新增都不需要改它。
- 后端变异 3 条全部 on-target（跑完还原、结尾复跑基线）：
  `M1` facets **造出一个从未写入的值** → **2 红**（自洽判据 + 值域判据）；
  `M2` 排序改成条数升序 → **1 红**；
  `M3` 把「表不存在」判定变成万能兜底 → **1 红**（纯函数参数化：`users does not exist` /
  `permission denied for table audit_logs` / `syntax error at audit_logs` 都不得被吞）。
- 前端：`src/pages/audit/` → **10 passed**（新增 5 条，含「facets 失败时降级为只剩『全部资源』
  + 自由输入，而不是回落到硬编码词表」）；`npm run type-check`（`tsc --noEmit` ×2）通过；
  `npx eslint src/pages/audit src/utils/api/management.ts src/utils/api/types.ts` 无输出。
- 前端变异 `M4`：把资源下拉改回硬编码（含 `tool`） → **4 红**，基线复跑 **10 passed**。
- 棘轮与契约：`tests/test_admin_only_read_surface_register.py` **7 passed**（含新登记的
  `audit.py:/facets`；未登记前实测红一次）；`tests/test_api_response_shape_contract.py`
  **15 passed**（新登记的 2 对通过双向对拍）。
- 门禁：见本 PR 后续评论 / CI。

## Revisit

- **facets 是全表 `GROUP BY`**，且 `action` 列没有索引 ⇒ 现在是两次顺序扫描。dev（269 行）无感；
  生产 `audit_logs` 上到几十万行、且 admin 频繁开页时会有读数成本。出口按代价从小到大：
  前端已有 `staleTime: 60_000`（每管理员每分钟至多一次）→ 给 `(action)` 建索引 → 把词表物化成
  低频任务（注意 Redis 只承载队列，不作业务事实存储，物化目标应是表或进程内缓存）。
  触发信号：审计页首屏 P95 因 facets 明显变慢，或 `audit_logs` 行数量级变化。
- **若确实需要「按类别筛操作」**（例如「所有取消类动作」），再做 issue 的 `IN` 白名单方案；
  届时类别必须成为**登记在单一来源的词表**（后端常量 + 前端由 facets/枚举取得），
  不得再在 `AuditLogPage.tsx` 里写第二份清单——那正是本单消灭的东西。
- **86 种 action 里没有中文名的会直接显示英文原词**。这是有意选择（宁缺毋假），但如果 admin
  反馈读不动，正确做法是给 facets 响应加一个后端维护的 `label` 字段（词表与标签同源），
  而不是在前端补一张映射表。
- 本单是 #2546（一个概念唯一 owner）的又一实例，与 #2494/#2360/#2418 同族：
  展示面自己维护一份词表 ⇒ 必然漂移。facets 这种「由数据反推值域」的做法能否推广到别的
  裸 `str` 筛选维度，等 #2546 的裁决；不在这里预先推广。
