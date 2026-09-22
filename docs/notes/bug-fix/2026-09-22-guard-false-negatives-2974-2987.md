# 两处守卫的假阴性口：#2974 定序须在取锁之前、#2987 乱序输入必须原样透传

Status: implemented
Class: bug-fix

## Decision

两处独立的「判据存在但够不到它要拦的形态」，都是上轮立单（`#2974` / `#2987`）修完
之后留下的**残余**：修对了主判据，但判据本身留了一个能过红线的例子。分别收紧。

### ① 锁序守卫：定序必须发生在**取锁之前**（`tests/test_lock_order_collection_total_order.py`）

`_iterable_has_total_order` 判「迭代集合有没有全序来源」时，只要在**同一个函数里任何
位置**找到 `rows.sort(...)`（或对同名变量的 `sorted()`/`order_by()` 赋值）就算有全序，
**不看位置**。于是「先取锁、后排」这个真实竞态形态完全隐形：

```python
rows = (await db.execute(select(Job))).scalars().all()
for row in rows:                                   # ← 锁在这里按未定序的顺序取
    await db.execute(select(Job).where(...).with_for_update())
rows.sort(key=lambda r: r.id)                      # ← 排序在后面，判据却因此放行
```

修法：在遍历函数体时跳过 `lineno >= loop.lineno` 的候选（即只认循环**之前**出现的
定序语句）。这同时把「排序写在另一条只对部分行生效的分支里」这类形态也归入未定序 ——
判据的命题回到它自己声明的意思：「锁按什么顺序取」。

### ② 前端调用点断言：补「输入未按 failed 降序」的夹具（`PlanFailedDevicesChart.test.tsx`）

`#2987` 把 `#2848` 的不变量钉到了调用点（断言交给 `BarChart` 的 `data` 真的收到 24 条、
顺序等于输入）。但**顺序那条**用的夹具本身已经是 `failed` 降序，而把
`.sort((a, b) => b.failed - a.failed)` 加回调用点时，稳定排序对「已按该键降序」的输入
是恒等变换 ⇒ 断言照旧通过（假阴性口：它能抓 `slice`，抓不住「稳定单键重排」）。

修法：再加一条夹具，输入**故意不按 `failed` 降序**（`[1,9,5]` 的 failed 序列），断言
透传顺序不变。此时判据只剩「原样透传」一个解释：任何重排（稳定或非稳定、单键或多键）
都会改序。

## Alternatives

- **① 改成「循环体内任意位置都不许出现取锁」**：过宽。合法形态（消费点排序后取锁、
  或锁在循环体内但集合已由查询定序）会被误杀，判据会变成恒红而被忽略。
- **① 只在文档里记这个盲区**：否决。原单的残余恰恰是「盲区已记在 Note 的 Revisit 里，
  但判据仍报绿」——记录不能替代判据（本仓反复出现的形态）。
- **② 删掉原来那条「同 failed 不重排」断言，只留新的**：否决。两条盯的不是同一件事：
  前者钉「同分 tie-break 不被单键打散」（服务端次序语义），后者钉「透传」。新夹具
  不能替代旧断言（旧断言用了真实的服务端次序形状做输入）。
- **② 改成静态断言（SourceGuard `assert_absent('.sort(')`）**：也能堵，但它把「不许重排」
  钉成文本形态，绕不过「换一种写法重排」（如 `toSorted`）。行为断言钉的是不变量本身，
  故取行为断言；代价是它依赖 recharts 在 jsdom 下把 `data` 透传（本文件已用
  `StableResponsiveContainer` 桩绕开零尺寸门，已可用）。

## Verification

- `pytest -q tests/test_lock_order_collection_total_order.py` → **13 passed**（含仓库面
  `test_backend_has_no_unordered_lock_loops`：`#2974` 修过的四处站点都是「排序在循环之前」，
  未被误杀；`test_pending_total_order_entries_are_not_stale` / `_adjudicated_safe_` 不变）。
- ① 的红绿双向：新增 `test_detector_requires_ordering_to_precede_the_locks`，红侧
  （`.sort()` 在循环之后）报违规、绿侧（`.sort()` 在循环之前，即两处真实修复的形态）不报。
- **② 变异自证（实跑，非推理）**：在调用点注入 `buildFailedDeviceRows(data).sort((a, b) => b.failed - a.failed)` 后
  `vitest run src/components/charts/PlanFailedDevicesChart.test.tsx` →
  `Tests 1 failed | 10 passed`，失败者恰为新断言「乱序输入也原样透传」，而旧的
  「同 failed 时不按单一键重排」**仍然通过** —— 直接证明残余真实存在、且新断言补上了它。
  变异已撤回（`git checkout -- PlanFailedDevicesChart.tsx`），复核 11 passed。
- `scripts/run_gates.py check:quick` → OK；`check_governance_surface.py` → OK（S1–S15、S5x）。

## Revisit

- ① 的假阴性口只收紧了**同函数内**的位置关系。跨函数（`_sort_then_lock(rows)` 这类
  helper 定序后传入）仍看不见 —— 与守卫文件顶部自陈的盲区同源，要覆盖需要「调用链上
  的定序证明」，属另一个量级的设计，不在本 PR。
- ② 依赖 recharts 把 `data` 透传给 `BarChart`（本文件用桩绕开容器零尺寸）。若将来换图表库，
  这条断言要跟着搬；`captured.data` 的捕获点在文件顶部 `vi.mock('recharts', …)` 里。
- 两条都属「判据判别力」，不改变任何生产行为；`#3109`（pin/timeout 守卫基线）是同一族的
  另一处，本 PR 不夹带。
