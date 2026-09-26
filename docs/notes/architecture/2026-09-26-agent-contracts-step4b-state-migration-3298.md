# ADR-0054 第 4 步（二）：`aee_state_migration` 入契约包——C3 基线清零

Status: implemented
Class: architecture

## Decision

按 ADR-0054 §5 第 4 步的最后一项，把 `backend/agent/aee/state_migration.py` **整体**
搬入契约包并删旧位置（#3298）：

- `backend/agent/aee/state_migration.py` → **`backend/agent/contracts/aee_state_migration.py`**
  （git mv，保留历史）。内容是三类定义 + 一份改写实现：键命名空间
  （`WATCHER_AEE_STATE_PREFIX` / `LEGACY_PATROL_STATE_PREFIX`）、键 schema
  （`_AEE_TYPES` / `_STATE_KINDS`）、合并语义（processed 取并集 / pending 键级合并、旧行保留），
  以及两侧（agent 启动守卫、控制面运维脚本）都要执行的
  `migrate_legacy_aee_state_keys`。
- **调用方改指**：`agent/startup_guards.py`（`from .contracts.aee_state_migration import …`）、
  `agent/aee/reconciler.py`（`from ..contracts.aee_state_migration import WATCHER_AEE_STATE_PREFIX`）、
  `backend/scripts/migrate_watcher_aee_state_keys.py`（控制面运维脚本，绝对导入契约）、
  agent 测试与墓碑测试。
- **顺手收掉字面量漂移面**：`processor.py` 的 `state_key_prefix: str = "watcher:aee"` 与
  `db_history.py` 的 `state_key(..., prefix: str = "watcher:aee")` 都改成指向契约常量——
  key 前缀此前有 3 份字面量（契约常量 + 两处默认值），控制面脚本按同一词表匹配键，
  留三份就是 D1 要治的漂移面。
- **C3 基线 2 → 1 → 0**：删最后一条 `scripts.migrate_watcher_aee_state_keys ->
  agent.aee.state_migration`；`.importlinter` 注释改为终态口径：**第 1–4 步全部落地，
  基线清零——此后控制面新增任何对 `backend.agent`（契约包以外）的直接 import 一律红，
  无豁免**；通配行 `backend.** -> backend.agent.contracts.**` 作为 D4 常驻条款保留。
  ADR-0054 §5 的预期终态「C3 基线 0–2 条」据此取到下界 0。
- **墓碑**：`backend/tests/test_legacy_tombstones.py` 增加
  `test_aee_state_migration_lives_only_in_contracts_package`（契约文件在、旧位置不在）。

### 为什么这个模块算契约（D2 边界说明）

ADR-0054 D2 把「状态迁移」列为非契约（「Agent 的运行逻辑（采集、执行、状态迁移）不属于
契约」）。本模块不属该条排除面：

- 它不是 agent 运行期状态机（job/lease/watch 的状态跃迁都在别处），而是一次性的
  **键命名空间改写**：agent 启动守卫与控制面运维脚本执行的是同一份规则；
- 与 D2 反例（`kernel_usb_faults` 的采集线程：subprocess + 线程 + 节流）不同，本模块
  stdlib-only（sqlite3/json）、**import 期无 I/O**、不 import 任何 agent/控制面模块；
- 与第 3 步 `artifact_digest` 同类：契约包收「载荷/键的规范化定义 + 双方共用的读写工具」，
  I/O 只发生在调用时。

结论与 `.importlinter` 里写下的终态出口一致（「整体搬入后删行」），已完成。

## Alternatives

- **只搬常量与合并纯函数，`migrate_legacy_aee_state_keys` 留 agent**：弃——控制面脚本
  要的就是这份改写实现，留 agent 等于 C3 基线永久保留 1 条；而且拆分后两侧会变成
  「脚本 import agent 的迁移函数 + 函数内部用契约常量」的半搬状态，边界更难解释。
- **保留 `backend/agent/aee/state_migration.py` 作再导出壳**：弃——D5 明令不留壳
  （壳让 patch 目标分叉），4 个调用方同 PR 都能改指。
- **不动 `processor`/`db_history` 的字面量默认值**：弃——同一步里把前缀词表单一化是
  这次搬迁的直接结果（两者本来就在改导入面），留着等于宣布契约常量只是「又一份拷贝」。

## Verification

- `backend/agent/tests/` → **2187 passed**（4m08s，systemd-run 6G 硬顶；含
  `test_aee_state_migration.py`、`test_main.py` 的启动守卫用例）；
- `layering`（`--no-cache`）→ 5 合约全 KEPT，**C3 基线清零**（ignore_imports 只剩通配行；
  44 ignored imports 全是契约包边）；
- 控制面/契约子集：`backend/tests/test_legacy_tombstones.py`（含新墓碑）、
  `test_aee_state_migration.py`、`test_main.py`、`test_local_runtime_736.py` → **23 passed**；
- **运维脚本端到端冒烟**（临时 sqlite）：`python -m backend.scripts.migrate_watcher_aee_state_keys`
  `--dry-run` → 1 processed + 1 pending 待迁；真跑 → `["a","b"] ∪ ["b","c"] = ["a","b","c"]`、
  pending 建键、legacy 行保留（M3 可回滚）✓；
- `python scripts/run_gates.py check:quick` → **[OK] check:quick (16 gates)**；
- **反向验证（临时变异，逐条复原）**：
  - 复活 `backend/agent/aee/state_migration.py` → 墓碑判据红；
  - 运维脚本改 import **真实存在的** agent 内部模块（`backend.agent.startup_guards`）→
    **C3 BROKEN**（基线清零语义实测）；
  - 顺带实测一条 grimp 行为：import 指向**已删除**的模块时该边不进图
    （`find_modules_directly_imported_by(...) == []`）——即「回落已删模块」不会被 C3 抓到，
    但运行期必 ImportError；故上面那条变异必须指向存在的模块才有判别力。

## Revisit

- **ADR-0054 §5 四步全部落地**：契约包 8 个模块（pipeline_validator / legacy_aee /
  aee_metadata / aee_event_dirs / watcher_contracts / artifact_digest / kernel_usb_faults /
  aee_state_migration）；C3 基线 0；`_SHARED_ALLOWLIST` 只剩 `metrics`（§3 非目标）。
  实施单 #3298 可随本 PR 合入后收口（另见单内进展评论）；
- C3 现在是**零基线**合约：新增豁免必须走 ADR（`.importlinter` 头部棘轮规则仍适用：
  PR 描述写明 issue 与终态出口）；
- 契约包若继续增长，回审 ADR-0054 §7 的「规模超出定义范畴」复议条件；D6 的
  「不靠 `__file__` 裸深度定位工件」仍是新契约模块的准入判据。
