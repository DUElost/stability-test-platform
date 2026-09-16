# plan_run_abort 的 emit 缝：模块级可 patch + import 期零副作用（#2372）

Status: implemented
Class: bug-fix

## Decision

**取 issue 方案 2（保留模块级再导出，用不触发 import 期副作用的形态）**：
`schedule_emit` / `schedule_agent_control_fanout` 在 `plan_run_abort` 里改为**模块级薄包装**，
真正取 `socketio_server` 的符号发生在**被调用时**：

```python
def schedule_emit(*args, **kwargs):
    from backend.realtime.socketio_server import schedule_emit as _emit
    return _emit(*args, **kwargs)
```

两个约束因此同时成立：

- **clean-env 可 import**：agent collect 路径会 `import backend.services.plan_run_abort`
  （#2270 的 `plan_dispatcher_sync → run_abort_pending`），import 期不得拉起 socketio
  （→ `JWT_SECRET_KEY`）——但函数体内 import 不触发 ✓；
- **patch 目标在场**：11 条控制面用例 patch 的是 `backend.services.plan_run_abort.schedule_emit`
  这个**模块属性**（#2270 → #2350 的惰性 import 修复把符号挪进了函数体，属性随之消失 ✗）。
  包装函数在模块命名空间里 ✓，调用点按名取属性 ✓，patch 生效 ✓。

**成因回路**：#2270（我的）引入 `plan_dispatcher_sync → run_abort_pending` → socketio 被拖进
clean-env → #2350（pr-agent-tests 红）→ 28d7d0f9 改函数内惰性 import → 模块属性消失 →
11 条 `backend/tests/services` 确定性红（**不在 required checks 里，静默进 main**）。

**防复发（issue 建议，已落）**：新增 `tests/test_plan_run_abort_import_contract.py`——子进程
clean-env（去 `JWT_SECRET_KEY`）导入本模块，断言 ① 导入成功、② 两个 emit 缝是模块属性、
③ `socketio_server` 不在 `sys.modules`。只满足一条就会重演这条回路，故三条一起钉。

**顺带修复（独立提交）**：`backend/tests/core/test_storage_root.py` 仍 import 已被 #2324
（dashboard 与 DEVICE_UPDATE 解耦）删除的 `_disk_usage_percent_from_extra` → **整个
`backend/tests` 收集失败**（不只是该文件红）。退役那两条用例并保留退役说明；修后
`backend/tests` 2886 条可收集。同类（改接缝、忘跟用例、且不在 required checks 内）。

## Alternatives

- **方案 1（改 11 处 patch 目标到 socketio 内部）**：否决。会让控制面用例依赖 socketio 的
  内部符号（更深耦合），且 11 处改动本身是「测试跟着实现走」的形态——本次问题的教训正是
  「接缝应当**显式**存在」。
- **维持 28d7d0f9 现状、只改测试**：等同方案 1 ✗。
- **在 `plan_run_abort` 顶层 `import socketio_server`（回到 #2270 之前）**：否决——会把
  `JWT_SECRET_KEY` 重新拖进 clean-env collect（#2350 复发）。
- **把 emit 抽到独立模块（如 `backend/services/plan_run_emit.py`）**：方向对但更重；
  若将来再出现第三个约束（例如 emit 需要独立的测试替身），再抽（见 Revisit）。

## Verification

- **红绿双向**：
  - 还原 `plan_run_abort.py` → 11 条用例
    （`test_plan_run_abort_aggregator_race.py` ×10 +
    `test_plan_dispatcher_device_validation.py` ×1）**全部红**（`AttributeError: … does not
    have the attribute 'schedule_emit'`）；新结构断言同样红；
  - 改后：54 passed（两文件全量）+ 结构断言 1 passed。
- **clean-env 实测**：`env -u JWT_SECRET_KEY`（仅给 DATABASE_URL）下 `import
  backend.services.plan_run_abort` 成功、两个 emit 缝在场、`backend.realtime.socketio_server`
  **未进 `sys.modules`**。
- **CI 同款 collect 门禁**：`env -i PATH="$PATH" PYTHONPATH=. pytest backend/agent/tests/
  --collect-only` → **2104 tests collected**（#2350 那条路径仍干净）。
- 受影响面：`backend/tests -k "abort or aggregator or dispatcher or dispatch or plan_run"` →
  **511 passed**；`backend/tests --collect-only` → **2886 collected**（修复了收集断链）；
  `ruff check backend/` 全绿；`gov-surface --check` 全绿。
- **未跑**：`check:quick` 全档（本单改动面为 backend 服务与测试；由 CI required checks 复核）。

## Revisit

- **一层间接的成本**：每次 emit 多一次函数调用 + `import`（已缓存的模块查找）——热路径上
  可忽略（emit 本身是 `run_coroutine_threadsafe` 投递）。
- **第三个约束出现时抽模块**：目前「模块级可 patch + 零 import 副作用」靠包装函数承载；
  若未来 emit 还需要独立替身/多个消费方，抽 `backend/services/plan_run_emit.py` 更干净。
- **同类风险的体检面**：`backend/tests/services` 不在 required checks 内（#2333 在治），
  本次两条都是「接缝改动用例没跟」✗。若再现第三次，建议把 `backend/tests --collect-only`
  前移到 PR 路径（收集期失败成本很低、收益覆盖全部这类断链）。
