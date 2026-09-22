# 单例清单的第三份副本红在 main 上：测试字面量改为按注册站点判据（#3060）

Status: implemented
Class: bug-fix

## Decision

**红的位置不是根因，副本才是。** `main` 全量 CI 的 `backend-test` 确定性红
（重跑仍红 → 非 flake，#2333 归因链已判定）落在
`test_singleton_schedule_ids_cover_p3_3_jobs`：#2958 新增 `script_presence_sweep`
时把 **prod 常量 `SINGLETON_SCHEDULE_IDS`** 和 **注册站点 `_instrumented(..., singleton=True)`**
都改了，唯独漏了测试里手抄的 `expected` 字面量。

同一清单存在三处时，"改了实现也要记得改测试"就是全部的防线——而它会漏。所以：

- 测试不再自带清单副本，改为从**真实来源**取数：解析 `app_scheduler` 的 AST，
  收集所有 `_instrumented(name, fn, singleton=True)` 的 `name`；
- 判据双向：`站点 singleton=True 但常量未登记` 与 `常量登记但站点未包 leadership`
  各自点名，报错即结论（不再需要人来差分哪一侧）；
- 保留 `INTERNAL_LEADERSHIP_IDS` 这条**政策**断言（ADR-0027：`admission_pump` 等
  四个作业已有 internal leadership，重复包 = 不同 DB session 上的嵌套 advisory
  lock 会把 tick 打死）。政策清单留在测试里是对的——它表达决定，不跟实现同变；
- 取数器本身加反空断言：AST 形状变了导致取到 0 个站点时**判红**，不得把
  "取数失效"读成"清单一致"（这条比断言本身更容易被忽略）。

## Alternatives

- **只给 `expected` 补一行 `script_presence_sweep`**：弃。这是 1 行止血，能绿，但三处
  副本原地保留，下次加单例作业还会红在同一个地方。按 AGENTS.md「临时止血必须写明
  终态出口」——本方案就是那个终态，且成本只多 20 行测试代码、零生产风险（不改 prod）。
- **把 `singleton=True` 从注册站点删掉、统一由 `_instrumented` 查常量决定**：弃（本批）。
  这确实把三处收成一处，但代价是站点不再显式表达"这个作业是单例"，非单例作业会变成
  "没进常量"的默认态——把一个需要人拍板的决定降级为隐式默认，且要动 11 处生产代码。
  留作 Revisit 的候选，不搭 main 红的车。
- **做成 lint 门禁**：弃。判据只对这个模块有意义，塞进 `run_gates` 会让门禁清单为
  单点长尾；就近放在测试文件里，改 AST 形状时同一屏就能看到。

## Verification

- `python -m pytest backend/tests/realtime/test_p3_3_multi_instance.py` → **15 passed**
  （含原失败用例）；
- 变异自证 4 处，各自按预期变红后恢复（生产文件用 `cp` 备份回滚，未用 `git checkout`）：
  1. 常量删一项、站点仍 `singleton=True` → 红，点名 `not_declared`；
  2. 常量加一项 `ghost_job`、站点无 → 红，点名 `not_wrapped`；
  3. 把 `admission_pump` 塞进常量 → 红，点名"重复包了一层 leadership"；
  4. 取数器改成不收集（恒空）→ 红，命中"取数失效"守卫；
  5. 追加：站点漏写 `singleton=True`（`auto_archive_sweep`）→ 红（这才是未来真会犯的错）；
- `python3 scripts/run_gates.py check:quick` → 见 PR；
- 本批只动测试文件，无 API/契约/前端面变化。

## Revisit

- main 红修完后，`SINGLETON_SCHEDULE_IDS` 与注册站点仍是两处；若再加第三个单例作业时
  感到摩擦，就启用上面的「站点不写 `singleton=True`、由常量决定」形态（需单独一批，
  含 ADR-0027 注记）；
- 同类「测试自带清单副本」的形态值得扫一遍：`tools/dev/check_god_files_ceiling.py`
  的基线、`SECURITY_ACTION` 登记（#3017）、观测资产副本（#2984/#2985）都是同族问题，
  但各自已有机制，不在本批顺手改。
