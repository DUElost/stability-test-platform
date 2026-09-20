# 读侧绕词表 + 错名 Note 目录免检（#2872 / #2883）

Status: implemented
Class: bug-fix

## Decision

两单共一个形态：**该被看见的东西不在遍历面里**。一处在 SQL 筛选（历史别名行），
一处在治理门禁（错名目录里的 Note）。

### #2872 · 审计资源类型读侧绕过词表

审计行 append-only（ADR-0015），历史字面量只在读取侧归并（#2778）：词表
`AUDIT_RESOURCE_TYPE_ALIASES` + 展开入口 `expand_resource_type_filter()` 是唯一判据。
按规范值**精确匹配**的读侧查询会静默漏掉 `job`（规范值 `job_instance`）等历史行。改了两处：

1. `backend/services/plan_run_event_feed.py`（issue 点名处）：`resource_type == "plan_run"`
   / `== "job_instance"` 两条腿都改成 `in_(expand_resource_type_filter(...))`；
2. `backend/services/ai_assistant/tools.py::_q_audit_logs`（同批同类形态）：LLM 工具按
   `resource_type` 过滤时同样漏历史行，改用同一展开。

并加**机械守卫** `backend/tests/test_audit_read_side_alias_guard.py`（纯 AST，不连库）：
`AuditLog.resource_type` 出现在**比较/筛选**位置（`==`/`!=`、`.in_/.notin_/.like/...`）
时必须处于 `expand_resource_type_filter(...)` 作用域内。沿属性链上溯以覆盖
`AuditLog.resource_type.in_(...)` 这种「列的方法」写法；把列**本身**交给聚合/工具函数
（facets 列原始值）不算筛选，不在此列。

### #2883 · `docs/notes/bugfix/`（无连字符）不在遍历面

S10 遍历的是白名单 `NOTE_CLASSES`，所以错名目录不是「报错」而是「不存在」：该目录里的
Note 的 Status/Class 与四节契约**永久免检**。两步：

1. 把 git 里现有的 6 份从 `docs/notes/bugfix/` `git mv` 到 `docs/notes/bug-fix/`
   （逐份核对头部，6 份的 `Class:` 头本来就是 `bug-fix`，移动后即纳入校验面）；
2. 新增 `check_note_class_dirs()`（挂在 S10）：`docs/notes/` 下**含 .md 的**未知目录
   一律 `[BLOCK]`；`archived` 显式豁免（有意的归档面，不参与 S10 契约），空目录/仅
   README 的占位目录不构成免检面、不报错。

附带：把自检里两处函数体内 `import tempfile` 提为模块级（第二处会把第一处的名字遮成
局部变量，实测 UnboundLocalError），因此 `check_inner_imports` 的 `_BASELINE` 在本 PR
内按棘轮纪律 599 → 598。

## Alternatives

- **#2872 只改 feed（issue 点名处）**：ai_assistant 的工具是同一漏点（LLM 查
  `job_instance` 时历史行同样缺席），同一 PR 一并改；守卫也把两处都钉住。
- **#2872 守卫只拦 `resource_type == "字面量"`**：feed 原写法是 `==`，但 `in_(ids)`
  同样漏历史行——只拦 `==` 会留下同形缺口。改为「比较/筛选位置必须在 expand 作用域内」。
- **#2872 读侧守卫要求所有 `AuditLog.resource_type` 使用都走 expand**：facets 需要**列
  本身**（列原始值），强拦会误伤；故只判定筛选位置。
- **#2883 把 `bugfix` 加进 NOTE_CLASSES**：等于承认两套目录名并存，且 `Class:` 头会与
  目录名分裂（本窗口就有 5 份草稿写成 `Class: bugfix`）；否决。
- **#2883 只移动文件、不加门禁**：下一个人换个错名目录照样免检——检查才是本质修复。

## Verification

- **反例（先证伪）**：#2872 两处读侧退回 `HEAD` 后——
  - 行为用例红：`test_events_include_historical_alias_audit_rows` FAILED（种下的
    `resource_type="job"` 行不在 events 里）；
  - 守卫红：`test_read_side_resource_type_filters_expand_aliases` FAILED（两处裸筛选中枪）。
  恢复后全绿。守卫自检含正向（走 expand 不违规）与负向（裸 `==`、裸 `in_` 必红、
  列交给聚合不违规）。
- **#2883 实弹**：重建 `docs/notes/bugfix/` 并放一份 .md → `--check` 输出
  `[BLOCK] S10 docs/notes/bugfix: 未知 Note 目录——其中 1 份 .md 不在 S10 校验面内（#2883）`；
  删除该目录后 `[OK] 治理面结构检查通过（S1–S15、S5x）`。门禁自检新增红/绿双向样例
  （规范目录 + `archived` + 空占位目录不拦；错名目录必拦）。
- 本机实际执行：
  - `pytest backend/tests/test_audit_read_side_alias_guard.py -q` → **2 passed**
  - `pytest backend/tests/api/test_plan_run_aggregation_endpoints.py -q -k EventsEndpoint`
    → **13 passed**
  - `pytest tests/test_inner_import_ratchet.py -q` → **6 passed**
  - `python tools/dev/check_governance_surface.py --self-test` → OK；
    `--check` → OK（S1–S15、S5x）
  - `python scripts/run_gates.py check:quick` → **[OK] check:quick (12 gates)**
- Agent Note 四节齐备（本文件）。

## Revisit

- `_q_audit_logs` **没有行为用例**（该文件此前无 audit 工具测试）：本次只做了静态修复 +
  守卫覆盖，端到端行为留待补 fixture 时加——「守卫绿」目前只代表「没绕过词表」。
- 主检出里 `docs/notes/bugfix/` 还残留 4 份**未跟踪**草稿（agent-agent-loop / agent-application
  / agent-application-phases / agent-job-runtime）：它们不在 git 内，本 PR 无法移动；一旦被
  提交，新门禁会当场拦下（且它们头部写的是 `Class: bugfix`，需同步改）。
- 读侧守卫只覆盖 `AuditLog.resource_type` 的比较位置；若将来出现别的审计模型或
  `resource_type` 以关键字参数传入 ORM 构造函数（`filter_by(resource_type=...)`），
  需扩展判定面。
- #2883 的目录检查只看 `docs/notes/` 一级子目录；更深层的错名嵌套不在遍历面。
