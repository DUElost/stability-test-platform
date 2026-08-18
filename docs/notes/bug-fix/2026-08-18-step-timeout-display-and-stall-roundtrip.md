# 步骤墙钟的展示语义纠偏 + 停滞钟往返丢失

- **状态**：已实施
- **类别**：bug-fix
- **日期**：2026-08-18
- **关联**：#115（两层钟）、ADR-0020、前端体检遗留项

---

## 决定了什么

### 1. `step.timeout_seconds` 的三种取值分开展示

契约（`backend/schemas/pipeline_schema.json` §step +
`backend/agent/pipeline_engine.py::_resolve_step_wall_clock`）里这个字段有三种语义：

| 值 | 引擎行为 | 原展示 | 现展示 |
|----|----------|--------|--------|
| `0` | 无上限，`communicate(timeout=None)` 等到子进程退出 | `0s` | `∞` |
| `null` / 缺省 | 回落 `STP_STEP_WALL_CLOCK_SECONDS`，再回落 300s | `∞` | `默认` |
| `n > 0` | n 秒墙钟 | `ns` | `ns` |

**两个方向都反了**：配成「不限」的步骤读起来像「零秒超时」，而没配的步骤读起来
像「永不超时」——后者尤其危险，它 300 秒就会被杀，界面却告诉你它不会。

判定抽到 `components/pipeline/stepTiming.ts`，画布行与 Inspector 共用一份。
Inspector 另加一条提示条：值为 `0` 或缺省时把结论直接写出来，因为光看一个数字
框读不出这两种取值背后差着 300 秒起步的行为。

`0` 的提示额外点明「本编辑器存不下去」：schema 要求 `timeout_seconds == 0` 时
必须同时有 `stall_seconds >= 1`，而编辑器不提供停滞钟输入框。

### 2. 清空超时输入框不再静默写 0

从编辑器存得下去的取值只有 `>= 1` 的整数：`0` 缺停滞钟被条件约束拒，`null` 被
`_assemble_lifecycle_for_validation` 原样传给 schema 的 `type: integer` 拒。而
原逻辑 `raw === '' ? 0 : ...` 把「清空」直接写成 `0`——用户要到保存时才收到一条
原始 jsonschema 报错。

改成用 draft 承接编辑中的原始文本：**空值只是不提交**，失焦回落到最后一次提交的
值。不把空折算成默认值，是因为那样输入框会立刻跳成 `30`，接着输入就拼成
`306` / `3060`。

### 3. 保存不再清掉停滞钟（本次真正紧急的那条）

查生产库时发现的：`buildStepsForApi` 不发 `stall_seconds`、
`rebuildLifecycleFromPlan` 也不读它，而 Plan 保存是**整体替换 PlanStep 行**——
在编辑页打开一个配了停滞钟的 Plan、点一次保存，停滞钟就被静默清成 NULL。

生产库当时有两处中招（只读 SELECT 核实）：

```
plan 2 | Monkey专项-watcher-patrol      | monkey_setup | timeout 600 | stall 120
plan 6 | 验证-短时patrol-自然SUCCESS    | monkey_setup | timeout 600 | stall 120
```

修法是让字段穿过往返即可，不加 UI：`PipelineStep` 类型补 `stall_seconds`，
rebuild 读回、build 发回。**只在有值时写键**——凭空多一个键会让所有旧 Plan
一打开就被 `snapshot()` 判成"已修改"。

### 4. 后端一处过期注释

`_resolve_step_wall_clock` 的 docstring 还写着「schema keeps minimum: 1，
所以 PlanStep 无法表达 0」，但 2026-08-04 step 级已放到 `minimum: 0`（改由
`stall_seconds >= 1` 的条件约束把关）。前端新模块引的正是这段语义，留着自相矛盾。
仅改注释，无行为变更。

## 放弃的备选

- **把 `stall_seconds` 做成 Inspector 的输入框** —— 那是功能，不是修 bug。而且
  后端 `_validate_stall_seconds_capability` 要求脚本版本已接入 PROGRESS 打戳，
  配错会另收一个 422，需要配套的能力提示才好用。本次只止血（不再丢配置）。
- **让编辑器支持保存 `timeout_seconds = 0`** —— 同上，前置条件是先有停滞钟字段。
- **把清空超时框改成写默认值 30** —— 清空后输入框会立刻跳成 `30`，再输入就变成
  `306`、`3060`，打字体验比现在更差。改成提示而不是改写值。
- **顺手纠正 `rebuildLifecycleFromPlan` 里 `timeout_seconds ?? 30` 的 null 归一** ——
  它会把 DB 里的 NULL 在下次保存时固化成 30。但 NULL 目前根本存不进去
  （`_assemble_lifecycle_for_validation` 原样传 `None`，schema `type: integer`
  会拒），所以这条路径当前不可达，留待停滞钟字段一并处理。

## 如何验证

```bash
cd frontend
npx vitest run                     # 78 files / 580 tests passed
npx tsc --noEmit                   # 0
npx eslint src --max-warnings 0    # 0

JWT_SECRET_KEY=test-secret python -m pytest backend/agent/tests/ -q   # 1025 passed
ruff check backend/agent/pipeline_engine.py                          # clean
```

新增 18 个用例：`stepTiming` 的三态格式化与提示文案 6 个、画布行的
`∞ / 默认 / ns` 三态 3 个、Inspector 提示条 3 个 + 停滞钟不丢 1 个、
`planEditUtils` 停滞钟往返 3 个（含"不凭空加键"那条，护的是脏检查）、超时输入框空值不落库 3 个。

生产数据核实用只读 SELECT（`plan_step` 的 timeout / stall 分布），未做任何写操作。

## 何时重议

- **做停滞钟输入框时**：`0` 的提示文案「本编辑器不提供停滞钟字段」要一起改，
  `formatStepTimeout` 的三态不变。
- **`timeout_seconds` 的 NULL 语义定下来时**：目前 DB 允许 NULL、引擎当"回落默认"
  处理、但校验器拒收，三方不一致。`rebuildLifecycleFromPlan` 的 `?? 30` 归一
  要跟着走。
- 生产 plan 2 / 6 的停滞钟若再次变成 NULL，说明还有别的写路径在丢字段
  （本次只堵了 Plan 编辑页这一条）。
