# #2030 第 2 条：三处空洞/重言测试加固（拦不住目标缺陷的断言）

Status: implemented
Class: bug-fix

## Decision

#2030 第 2 条点名的三处测试各自可被特定 mutation 绕过（对目标缺陷「拦不住」），
逐点加固：

1. **无非空守卫**：`backend/tests/api/test_plan_run_aggregation_endpoints.py::
   test_events_search_combines_with_stage_severity`（`1e339766` 新增）两条断言都是
   空集上的 `all(...)`（真空真）——`search`+`severity` 交集退化为 0 行时照样绿，
   而这正是该用例的主题。修：`all(...)` 前加非空守卫（同文件
   `test_events_filter_by_severity` 已有 `any(...)` 先例）。
2. **重言断言**：`tests/test_run_gates_schema_gate.py::test_gate_included_in_full_profile`
   的 `assert GATE not in FULL_EXCLUDE` + 自行重算
   `[g for g in GATES if g not in FULL_EXCLUDE]`——第二行由第一行 + `GATE in GATES`
   （同文件其它用例锚定）蕴含，永不独立失败。修：`scripts/run_gates.py` 提取
   `resolve_gate_names(profile)`（`main()` 与测试共用同一实现），断言改为
   `GATE in mod.resolve_gate_names("check:full")`；解析逻辑改坏时本用例可独立转红。
   该提取为纯函数移动，`--list` 输出与执行顺序不变。
3. **子串断言弱于其 docstring**：`backend/tests/services/test_chain_trigger_offline_filter.py::
   test_chain_filters_offline_async_and_sync`（`2f3a52e4` 重写）对函数源码段做
   `"_select_chain_devices("` 子串匹配——「调用但丢弃返回值、继续用未过滤列表派发」
   同样通过（正是 #1686 要防的形态）；`'"ONLINE"'` 子串也会被函数 docstring 里的
   字样满足。修：AST 级断言 ①调用出现在**解包赋值**的 RHS；②解包出的设备 id 变量
   作为 `prepare_plan_run(device_ids=...)` 实参；ONLINE 判据改为比较节点级
   （`ast.Compare` 中含字符串常量 `"ONLINE"`）。原 `_func_source` helper 随之删除。

**影响面**：三个测试文件 + `scripts/run_gates.py` 一处纯提取（无行为变化）；
无产品行为变更。

## Alternatives

- **三处只删断言**（#2030 对第 2 处给出的备选）：删重言行可接受，但另两处删掉会
  丢意图覆盖（非空守卫 / AST 结果是加固方向）。统一采取「加强」而非删除。
- **chain_trigger 改为动态调用行为测试**（构造 rows 调 `_select_chain_devices`
  断言返回）：测的是 helper 本体行为，对「调用方是否真的用了结果」仍拦不住——
  本文件三例的定位就是**接线守卫**（AST 结构断言），行为由
  `test_plan_chain_trigger*` 系列覆盖。维持 AST 定位。
- **run_gates 测试内联重算表达式**：与实现同式即与实现同步漂移（实现改坏、测试
  抄同一表达式，仍可绿）。否决；提取共享函数才让断言可独立失败。

## Verification

三处逐点 mutation（每处只改一个被测点；恢复后复绿；mutation 后均清相关
`__pycache__` 防旧字节码假绿）：

1. aggregation：把 events 端点的 search 过滤改为 `filtered = []`（交集 0 行）→
   `test_events_search_combines_with_stage_severity` **FAILED**（1 failed, 60
   deselected）；恢复后 1 passed，文件全量 61 passed。
2. run_gates：`resolve_gate_names` 改为 `PROFILES[profile] or []` →
   `test_gate_included_in_full_profile` **FAILED**（1 failed, 3 passed）；恢复后
   4 passed。
3. chain_trigger：async 路径改为 `_select_chain_devices(rows)`（丢弃结果）+
   `device_ids = [int(r[0]) for r in rows]` → 新断言 **FAILED**（1 failed, 2
   passed）；同 mutation 下**旧断言三项子串判定全为 True**（证明旧断言放行该
   缺陷、加固必要）；恢复后 3 passed。
- `python scripts/run_gates.py --list` 冒烟：四 profile 输出与提取前一致。

## Revisit

- 本单只处理 #2030 第 2 条；第 3 条（告警 `by(...)` 校验放宽）、第 4 条（部署身份
  枚举含不部署文件）未动，issue 保持 open。
- `test_chain_records_excluded_devices` / `test_chain_joins_device_for_status` 仍是
  源码子串断言——其 docstring 意图即为「源码里存在该输出名/JOIN」，未列入本单；
  若后续出现「名称在注释里也通过」的实证再加固。
