# ADR-0042 P1 试点（控制面）：调度域 Settings 迁移（#737）

Status: implemented
Class: architecture

## Decision

按 [ADR-0042](../../adr/ADR-0042-settings-convergence-and-bare-read-boundary.md) 的 P1 试点，
把**调度节奏与回收**域的三个文件迁移到分域 Settings：

- 新增 `backend/core/settings/`（`base.DomainSettings` + `scheduler.SchedulerSettings`，
  21 个字段）+ `get_scheduler_settings()` / `reset_scheduler_settings_cache()`；
- 迁移 `backend/scheduler/app_scheduler.py`（13 个旋钮）、`recycler.py`（6 个）、
  `cron_scheduler.py`（4 个，去重了与 app_scheduler 重复定义的 `CRON_POLL_INTERVAL` /
  `AUTO_ARCHIVE_POLL_INTERVAL_SECONDS`）；
- 连带修正跨模块取常量的唯一一处：`backend/services/host_upgrade_gate.py` 由
  `from backend.scheduler.app_scheduler import RECONCILER_INTERVAL` 改为读 Settings
  字段（否则删常量即断）。

**试点边界（本 PR 不做的）**：`counter_reconciler` / `signal_link_reconciler` /
`plan_chain_reconciler` / `precheck_reaper` 四个文件的 7 个「对账/重试批处理」旋钮
留在 P2（各自 1–3 个旋钮，迁移是机械的）；agent 侧 `lease_renewer` 域在下一个 PR
（需先给 `backend/agent/requirements.txt` 补依赖并验证 hot-update reload 钩子）。

### 落地的关键设计点

1. **惰性且不 import-time 固化（D4）**：删除常量后，各文件用模块内 `_sched()` 帮助函数
   逐次取缓存值（`lru_cache` 命中为常数开销）；不再有 import 期读 env 的路径；
2. **测试语义随迁**：原先 `monkeypatch.setattr(模块, "常量", v)` 的 3 处改为
   `scheduler_env(env, v)` fixture（`backend/tests/conftest.py` 新增）——写 env 后
   自动 `reset_scheduler_settings_cache()`，并在测试末再清一次（防缓存泄漏到后续用例）；
3. **等价性验收**（`tests/test_settings_scheduler.py`，7 例）：默认值逐一对照迁移前
   `int/float(os.getenv(..., "<默认>"))`（含类型）、env 覆盖生效、缓存语义（不 reset
   读旧值 / reset 读新值）、非法值 → `ValidationError`、**`.env` 文件单独存在不生效**
   （ADR v1.0 裁决的负向用例）、基类 `env_file=None` 契约。

### 顺带修复的清单盲区（意外收获）

`POST_COMPLETION_MAX_DEFER_SECONDS` 的迁移前读取是**跨行**写法
（`int(os.getenv(\n "POST_COMPLETION_MAX_DEFER_SECONDS", ...))`），行级扫描器一直漏掉它
（清单 210 名里没有它）；迁移到 Settings 字段后被 D6 的 AST 扫描暴露 → 按二选一
补登记进 `backend/.env.example`（清单变 211 名）。即 Settings 化顺手补了一个
「配置黑盒」死角。

## Alternatives

- **保留常量名 + 模块 `__getattr__` 惰性代理**（不改 35 处用法）：少改代码，但隐式
  魔法不易评审，且保留「常量」错觉（仍会被误以为 import-time 定型）——否决，取显式迁移；
- **一次性把四个 reconciler 也迁**：超出「试点」意图（P1 的目标是验证机制+等价性，
  不是收口全量）；P2 再按 D2 判据推进；
- **给 P1 顺带加校验约束（如 `ge=1`）**：迁移前的 `max(int(...), 1)` 类钳制在各调用点，
  统一约束会改变既有边界行为——留作独立裁决（ADR Revisit 已记）。

## Verification

- `pytest tests/test_settings_scheduler.py` → **7 passed**（等价性/覆盖/缓存/非法值/.env 负向/基类契约）；
- `pytest tests/` → **341 passed**（含 env_inventory 211 名一致、parity 门禁）；
- 迁移后 `backend/scheduler/*.py` 仅剩 P2 四文件的 7 处 `os.getenv`（本 PR 边界）；
- 模块 import 冒烟：`app_scheduler` / `recycler` / `cron_scheduler` / `host_upgrade_gate` 全部 OK；
- 受影响的 DB 用例（`test_recycler_patrol_stall_pg_query`、`test_retention_cleanup`、
  `test_recycler -k patrol_stall`、`test_execution_state_signals_step5a`）→ **30 passed**（41 deselected）；
- `run_gates.py check:quick` → **10 gates 全绿**；`ruff` 通过；
- 本地 venv 已按 lock 安装 `pydantic-settings==2.15.0` 以运行上述用例。

## Revisit

- **P2**：四个 reconciler 的 7 个旋钮 + agent `lease_renewer` 域（含 `backend/agent/requirements.txt`
  补依赖与 `reload_config` 钩子）；
- **校验约束**：是否给间隔/批量类字段加 `ge=1` 等边界（迁移前是散点钳制，统一会改边界行为）；
- **`_sched()` 帮助函数的去留**：P2 铺开后若每个文件都有，考虑抽公共 helper；
- 控制面是否引入热更重读（当前不热更；`reset_scheduler_settings_cache()` 已备好入口）。
