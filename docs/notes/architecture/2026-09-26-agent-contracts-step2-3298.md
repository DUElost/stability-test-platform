# ADR-0054 第 2 步落地：`aee_metadata` / `aee_event_dirs` / `watcher_contracts` 入契约包

Status: implemented
Class: architecture

## Decision

按 ADR-0054 §5 第 2 步，把三个「控制面伸手进 agent」的模块搬入 `backend/agent/contracts/`
（#3298），旧位置与 `core` 再导出壳全部删除：

| 旧位置 | 新位置 | 控制面消费方 |
|---|---|---|
| `backend/agent/aee/metadata.py` | `backend/agent/contracts/aee_metadata.py` | `services/plan_run_watcher_summary.py`（经 `core/aee_metadata.py` 壳，壳已删） |
| `backend/agent/aee/event_dirs.py` | `backend/agent/contracts/aee_event_dirs.py` | `services/dedup_extract.py` |
| `backend/agent/watcher/contracts.py` | `backend/agent/contracts/watcher_contracts.py` | `services/agent_log_signals.py` |

命名：契约包内用**族前缀**去歧义（`aee_metadata` 沿用旧 `core/aee_metadata.py` 的熟名；
`watcher_contracts` 避免 `contracts/contracts.py` 式重复命名）。三者均为 stdlib-only
（`re` / `pathlib` / `typing`），import 期无 I/O——C6 纯度判据自动覆盖（无需登记第三方）。

具体改动：

- **D5 完成定义**：`backend/agent/aee/metadata.py`、`backend/agent/aee/event_dirs.py`、
  `backend/agent/watcher/contracts.py`、`backend/core/aee_metadata.py` 全部删除（不留再导出壳）；
  `.importlinter` C3 删 3 条基线行（`core.aee_metadata → agent.aee.metadata`、
  `services.agent_log_signals → agent.watcher.contracts`、`services.dedup_extract → agent.aee.event_dirs`）
  → **C3 基线 5 → 2**（剩 `host_health_probe → kernel_usb_faults`、
  `migrate_watcher_aee_state_keys → aee.state_migration`，属第 4 步）。
- **agent 侧改相对导入**：`aee/processor.py`、`aee/collectors/mtk.py`、`aee/db_history.py`、
  `aee/reconciler.py`、`aee/unisoc_reconciler.py`、`watcher/emitter.py`、`watcher/device_watcher.py`、
  `job_session.py`。其中 `device_watcher` 的两处 **`try: backend.agent… / except ImportError:
  agent…` 双形态兜底顺手塌成单条相对导入**（契约在包内，两种布局天然同一路径；这是 ADR §3
  非目标里的形态，但既然本 PR 必须改这两行，留下兜底反而误导）。
- **控制面侧**：3 个消费方改指 `backend.agent.contracts.*`（C3 通配行放行）；同时回扫
  **运行期消息与文案载体**——`watcher_contracts.py` 的 `ContractViolation` 提示串、
  `emitter` / `agent_log_signals` / `agent_api` / `agent_completion` / `models/job` 的
  路径引用全部改指新位置（口径修订不留旧路径指向已删文件）。
- **测试**：6 个测试文件的 import 改指；`backend/tests/core/test_aee_metadata.py` 由
  「agent 实现 vs core 再导出」参数化对拍改为**单实现测试 + 旧位置墓碑断言**；
  `backend/agent/tests/test_aee_processor.py` 的子进程探针补断言
  `agent.contracts.aee_metadata in sys.modules`（证明主机布局走的是契约，且旧 core 壳不再被需要）。
- **文档**：`backend/agent/DEPLOY.md` 的 `contracts/` 清单补齐 5 个契约模块；
  `docs/reviews/DEVICE_LOG_FLOW_REVIEW_2026-08-09.md` 的 `find_event_dir_under_root` 链接改指
  新路径（gov-surface S2 断链门禁实测抓红）。
- **棘轮**：`device_watcher` 双形态塌缩让函数体内 import **619 → 617**
  （`tools/dev/check_inner_imports.py`，门禁提示要求同 PR 下调）。

**刻意不动**：`backend/alembic/versions/t9u0v1w2x3y4_*.py` 注释里的旧路径——迁移文件受
`alembic-immutability` 门禁保护（相对 base 的既有 revision 不可改），属冻结历史载体，
不随本次搬迁回扫。

## Alternatives

- **旧位置留薄再导出壳**（`aee/metadata.py` 里 `from ..contracts.aee_metadata import *`）：
  弃——D5 明令不留壳（壳让 patch 目标分叉、产生假绿），且 agent 侧 5 个调用方同 PR 都能改指。
- **契约模块用无前缀名**（`contracts/metadata.py` / `event_dirs.py` / `contracts.py`）：
  弃——`metadata`/`contracts` 在契约包里语义太泛，检索与 code review 都容易误读。
- **保留 `device_watcher` 的双形态兜底**（ADR §3 把这类兜底列为非目标）：弃——本次搬迁
  必须改这两行的目标路径，兜底分支的两个分支会指向不同模块名（`backend.agent.contracts` /
  `agent.contracts`），保留等于同时维护两条等价路径；改单条相对导入是净减法。
- **把 `aee_event_dirs` 放控制面**（其唯一生产消费方是 `services/dedup_extract`）：
  弃——目录命名规则是双方匹配键（DLE 上送标记 / scan xls Path 列 / watcher 落地路径），
  ADR-0054 D1 已裁定契约归 agent 包；且放控制面会在主机侧产生「agent 缺规则」的漂移面。

## Verification

- `.importlinter`（`--no-cache`）→ 5 条合约全 KEPT，**C3 基线由 5 行降为 2 行**（34 ignored
  imports 为通配行 + 剩余基线；`unmatched_ignore_imports_alerting=error` 实证被删的 3 行已无匹配）；
- `backend/agent/tests/` → **2160 passed**（4m06s，systemd-run 6G 硬顶；`--collect-only` 2160
  与搬迁前同数，无收集缺失——首轮曾因漏改 `device_watcher.py` 的 `from .contracts import`
  出现 21 个收集错误，提醒「`from .contracts` 这类兄弟相对形态不在 `watcher.contracts` 字面检索里」）；
- 控制面相关（testcontainers）：`backend/tests/core/test_aee_metadata.py`、
  `services/test_dedup_extract.py`、`services/test_agent_log_signals.py`、
  `api/test_agent_api_watcher.py`、`api/test_watcher_summary_uniview_1956.py`、
  `api/test_plan_run_dedup_key_2285.py` → **87 passed**；
- 根 `tests/`（import 边界 C6 纯度现覆盖 6 个契约模块 + 测试棘轮 + Ansible digest + 安装产物）
  → **33 passed**；
- 反向验证：契约模块注入非 stdlib 依赖/绝对导入 → C6 红（既有判据，两次搬迁共用一套）；
  首轮漏改的兄弟相对导入由 agent 收集面当场抓红（真实用例，见上）；
- `python scripts/run_gates.py check:quick` → **[OK] check:quick (17 gates)**（含 `layering`
  5 合约全 KEPT、`inner-imports` 617/617、`gov-surface` 链接检查）。

## Revisit

- ADR-0054 §5 第 3 步（`artifact_digest` 规范化算法）与第 4 步（`kernel_usb_faults` 解析 +
  签名词表、`state_migration`）未做；C3 剩余 2 条基线是第 4 步的出口；
- `watcher_contracts.py` 的 `WatcherSummaryPayload` 等 shape 与后端 model 的同步义务仍在
  （文件头「不一致必须同步改两边」条款），后续如引入代码生成再议；
- `agent/contracts/` 已 6 个模块；若继续增长到出现「目录分片」感，回审 D2 判据
  （是否把解析逻辑误当契约收进来）。
