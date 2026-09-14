# ADR-0042 P2（第一片）：scheduler 四 reconciler 批处理旋钮迁移（#737）

Status: implemented
Class: architecture

## Decision

按 [ADR-0042](../../adr/ADR-0042-settings-convergence-and-bare-read-boundary.md) 的 P2 范围，
把调度域**对账/重试批处理**子域的 7 个旋钮并入既有 `SchedulerSettings`（不新建类：
同属 `backend/scheduler/`、判据同源、`_sched()`/getter 复用），迁移四个文件：

| 文件 | 旋钮（env） | 默认 |
|---|---|---|
| `counter_reconciler.py` | `STP_COUNTER_RECONCILE_LOOKBACK_HOURS` / `STP_COUNTER_RECONCILE_BATCH` | 48 / 200 |
| `signal_link_reconciler.py` | `STP_SIGNAL_LINK_RECONCILE_BATCH` | 200 |
| `plan_chain_reconciler.py` | `CHAIN_RECONCILE_BATCH_SIZE` | 100 |
| `precheck_reaper.py` | `MAX_PRECHECK_REENQUEUE_ATTEMPTS` / `MAX_ADMISSION_REQUEUE_ATTEMPTS` / `ADMISSION_REQUEUE_BACKOFF_SECONDS` | 1 / 3 / 60 |

口径与 P1 一致：字段名 = env 名小写（含 `stp_` 前缀保留）、默认值逐一不变、取值惰性；
这四个文件的调用点少（各 1–3 处），直接 `get_scheduler_settings().<field>`（不再各自
维护 `_sched()` 帮助函数——对应 P1 Agent Note 的 Revisit「P2 铺开后考虑抽公共 helper」，
本单的结论是：**调用点少的文件不必包一层**，多调用点的 app_scheduler 保留 `_sched()`）。

测试随迁（2 处）：

- `backend/tests/scheduler/test_precheck_reaper.py`：常量路径 monkeypatch →
  `scheduler_env("MAX_PRECHECK_REENQUEUE_ATTEMPTS", "1")`（P1 引入的 conftest fixture）；
- `backend/tests/services/test_admission_queue_step2.py`：局部导入常量 → 读 Settings 字段；
- `tests/test_settings_scheduler.py` 默认值表扩到 28 项（P1 的 21 + 本单 7）。

## Alternatives

- **为这 7 个旋钮新建 `ReconcilerSettings` 类**：否决——同域同文件树、判据同源，
  拆类只会平添一个 getter/一处 reset 入口，与「分域」本意（按边界而非数量切）不符；
- **保留 `_sched()` 包装逐文件复制**：否决——本单四文件共 7 处调用，包装的收益为负；
- **顺手把 `precheck_reaper` 的其余实现细节（如 `MAX_ADMISSION_REQUEUE_ATTEMPTS` 相关硬编码）
  一并调整**：超范围；本单只做「裸读 → Settings」，行为等价的约束不变。

## Verification

- `pytest tests/test_settings_scheduler.py` → **7 passed**（默认值表 28 项含新 7 项，类型/值逐一对照迁移前）；
- `pytest backend/tests/scheduler/` → **95 passed**；
- `pytest backend/tests/scheduler/test_precheck_reaper.py backend/tests/services/test_admission_queue_step2.py backend/tests/scheduler/test_counter_reconciler_aggregation.py` → **48 passed**；
- `pytest tests/`（根）→ **532 passed**（env_inventory 211 名一致）；
- `run_gates.py check:quick` → **10 gates 全绿**；
- 迁移后 `backend/scheduler/*.py` 的裸 `os.getenv` 归零（本域收口）。

## Revisit

- **scheduler 域收口状态**：本单后该目录已无裸读；后续新增旋钮必须直接进 Settings
  （否则 env_inventory 门禁会要求二选一，但会以「裸读」形态出现，评审时应指回本 ADR）；
- **P2 其余**：agent 侧其余域（watcher/磁盘/注册等）按 D2 判据逐个评估；
- **`_sched()` 形态**：现为「多调用点文件保留包装、少调用点直呼 getter」的混合形态，
  若后续出现更多文件，再评估统一（当前不做）。
