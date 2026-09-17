# 函数体局部 import 棘轮门禁（#738 的门禁半件）

Status: implemented
Class: process

## Decision

#738 把「634 处局部 import 掩盖循环依赖」列为治理对象，并要求新建一条**只降不升**的
门禁。本单落地这条门禁（与今天同族的 #2542 上帝文件封顶、#2589 测试侧清单冻结同形）：

- `tools/dev/check_inner_imports.py`：统计生产面（`backend/ tools/ scripts/`）里处于
  **函数/方法体内**的 `import` / `from … import` 总数，超过 `_BASELINE` 即红。
- **基线实测 596**（2026-09-18，`origin/main` `6f135a43`；issue 里的 634 是 2026-09-03
  且口径未固化）。解耦后在**同一个 PR**里把基线调小。
- 排除面与 `audit_silent_exceptions.py` 同口径：测试、已发布脚本版本（ADR-0020）、
  alembic 历史 revision（#2258）、vendored 第三方（`backend/agent/resources/`）。
- **模块顶层的条件导入不算**（`try: import ujson / except ImportError`）——那是平台
  适配，不是依赖遮掩；**类体**里的 import 也不算（类体在模块加载时执行）。
- 接线：`check:quick` 与 `check:pr` 各一条 gate（`--self-test` 与主检查同 step），
  CI 在 `lint` job 加同名 step，锚点按 S5x 登记（漏登记会被治理面门禁当场抓红）。

**棘轮纪律做成硬判据**：`tests/test_inner_import_ratchet.py` 断言
`实测总数 == _BASELINE`——**低于**基线同样报红，提示「请把基线调到实测值」。
这样「解耦」与「调小基线」不会分家，台账不会慢慢失真。

## Alternatives

- **只做一次性统计、不加门禁**：否决。issue 明确要求阻塞门禁；而且没有门禁时，
  新代码用「函数内 import」绕开循环依赖的成本几乎为零（这正是存量长到 600 的机制）。
- **禁掉所有函数体内 import**：否决。存量 596 处里有一部分是**必要**的（例如
  `alembic/env.py` 在运行时读 app 配置、`cron_scheduler` 的调度体按需 import 服务），
  一刀切要一次改 596 处、且会与「不要为重构而重构」冲突。棘轮先冻结，再逐个解耦。
- **按「模块对」判循环依赖（真正的依赖图分析）**：更本质，但需要 imports 图 +
  可达性分析（SCC 检测），成本高得多；且它与本门禁不冲突——将来若做出来，
  本门禁可作为「新增长出来之前」的临时拦截（已登记 Revisit）。
- **把类体 import 也算进去**：否决。类体在模块加载时执行，不构成「运行期依赖遮掩」；
  算进去只会把判据变成「缩进里的 import」，丢掉语义。

## Verification

- **反例构造（先证伪再采信）**：
  - A 往 `backend/services/dedup_scan.py` 加一个函数体内 `import json`（`596 → 597`）
    → `test_repo_is_within_baseline` 与 `test_baseline_is_never_stale` **双双 FAILED**，
    工具打印超基线失败信息；
  - B 把 `_BASELINE` 改成 597 但不解耦（= 只抬基线）→ `test_baseline_is_never_stale`
    **FAILED**（棘轮纪律生效）。两处恢复后 6 passed。
- 实测命令与结果：
  - `python tools/dev/check_inner_imports.py` → `[OK] 函数体内 import 596 处 ≤ 基线 596（122 个文件）`；
  - `--self-test` → 通过（函数体内/顶层/条件/类体四态）；
  - `TESTING=1 python -m pytest tests/test_inner_import_ratchet.py -q` → **6 passed**；
  - `python tools/dev/check_governance_surface.py --check` → S1–S14、S5x 全绿。

## Revisit

- **#738 的另一半：`pipeline_validator` 双端重复拷贝**。它是 agent 与控制面各持一份
  的纯函数模块（今天刚落地的测试侧清单里，`test_pipeline_validator_parity_738.py`
  被标注为「有意」——它就是为这份拷贝而存在的 parity 测试）。消除拷贝后：
  ① 该 parity 测试与其 allowlist 条目一并删除；② 生产侧的 `_SHARED_ALLOWLIST`
  也会少一条。这是**方向性更强的重构**，值得单独一单。
- **解耦批次**：596 处里前几名是 `backend/tasks/saq_tasks.py`(29)、
  `backend/scheduler/cron_scheduler.py`(29)、`backend/services/dedup_scan.py`(26)。
  与今天静默吞咽的分诊结论同构：**按子系统分批**（同子系统的局部 import 往往同因，
  例如「服务层反向依赖 api.routes」的整族），而不是按计数从大到小。`--list` 可直接
  导出全量命中点。
- **与真正的依赖图分析互补**：本门禁只冻结**数量**，不判断「哪一处真的在掩盖循环」。
  若将来引入 SCC 检测，应把本门禁的基线换成「已识别循环依赖模块对」的白名单。
