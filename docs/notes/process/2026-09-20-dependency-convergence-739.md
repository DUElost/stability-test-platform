# 依赖收敛规划：xlwt → openpyxl 与 apscheduler 稳定版跟踪（#739 面③）

Status: proposed
Class: process

> 阶段 0（使用面冻结门禁）已随本单实现；阶段 1–3 为**待触发**的规划，命中触发条件后执行。

## Decision

#739 面③的第二个复选框（「规划替换 `xlwt`（评估全面切换至 `openpyxl`）并跟踪
`apscheduler` 的稳定版演进」）落成本文。结论：

1. **xlwt 的替换不是「换库」，而是「产物格式迁移」**——`openpyxl` 只能读写 OOXML
   （`.xlsx`/`.xlsm`），**不能写也不能读 `.xls`**（OLE/BIFF）。因此「全面切换」的前提是
   `Result_*.xls` 这一族产物改成 `.xlsx`，而这需要跨方（Toolkit / JIRA 上传链 / 归档面）
   一起改；本单不实施，交付**分阶段路径 + 触发条件 + 出口判据**，并先把使用面冻结成门禁。
2. **apscheduler 维持现状 pin，记跟踪判据**（4.x 仍是预发布；`<5.0` 上限防越级）。

### 一、xlwt / xlrd：使用面与消费方契约（2026-09-20 实测）

| 位置 | 用途 |
|---|---|
| `backend/services/dedup_scan.py:1176-1201`（`_rewrite_merge_report_paths_to_center`） | `.xls` **读 + 写回**：xlrd 打开 `Result_MergeFiles*.xls`，xlwt 重建同 sheet 并把 `Path` 列改写为中心可达路径（其余列原值保留；本就是「重建」，不保格式/公式） |
| `backend/services/dedup_extract.py:84-92` | `.xls` **读**：抽取链读 merge 报告 |
| `backend/requirements.txt:48-49` | `xlrd>=2.0.2,<3.0` + `xlwt==1.3.0`（均为直接依赖） |
| `backend/tests/services/test_dedup_scan_merge.py` | 用 xlwt 造测试夹具（不属生产使用面） |

**为什么不能只换写侧**：读侧同样受格式绑定——`openpyxl` 读不了 `.xls`；只要上游还产出
`.xls`，`xlrd` 就下不掉。**为什么上游难动**：Toolkit 侧与 `.xls` 绑定（`backend/agent/.env.example:178`
记：UNISOC archive toolchain 的 `scan_result` 需系统包 `python3-xlwt`）；JIRA 上传链按
`--add-main-excel <Result_*.xls>` 取数（`backend/api/routes/dedup.py:85` 的 stage 说明）；
`Result_*.xls` 同时是 `plan_run_artifact` 归档与下载面的既有契约。

**风险与现状边界**：`xlwt` 自 2017 年起无维护（1.3.0）；`.xls` 写回是重建式、列宽/公式
本就不保；迁移到 `.xlsx` 会改变下载文件名与消费脚本的参数（`--add-main-excel` 的路径后缀）。

### 二、迁移路径（分阶段；阶段 0 已随本单交付）

| 阶段 | 内容 | 前置/出口 |
|---|---|---|
| **0（本单）** | 冻结生产使用面：`tests/test_excel_dependency_inventory.py` 断言 `xlrd`/`xlwt` 的生产 import 集合 == 本台账；写死触发条件 | 已完成 |
| 1（读面先行） | `dedup_extract` / `dedup_scan` 的读取改「优先 `openpyxl`（`.xlsx`）→ 回落 `xlrd`（`.xls`）」，产出格式不变 | 不改消费方；可独立上线 |
| 2（产出面） | 与 Toolkit 侧约定新产物为 `.xlsx`；`dedup_scan` 写回改 `openpyxl`；JIRA 上传链与下载/归档面同步（文件名后缀、`--add-main-excel` 参数） | 需跨方确认；同一份 Result 做通路对拍（列/行/Path 改写数一致） |
| 3（退役） | 确认无 `.xls` 消费者后，从 `requirements.txt` 移除 `xlrd`/`xlwt`；inventory 门禁改为「禁止 `xlwt`」单向断言 | 出口判据：全链 `.xls` 引用 = 0 |

**触发条件（命中任一即启动阶段 2 评估）**：① Toolkit 发布支持 `.xlsx` 的版本；② 产线决定
不再消费 `.xls`；③ `xlwt`/`xlrd` 在目标 Python 版本上失效或出现未修复缺陷。

### 三、apscheduler：跟踪判据（不新增门禁）

- **现状**：`backend/requirements.txt:42` = `apscheduler>=4.0.0a6,<5.0`；lock 固定 `4.0.0a6`。
  用途集中在控制面调度：`backend/scheduler/app_scheduler.py`（`create_scheduler` /
  执行器路由 `_job_executor_for` / `_instrumented` singleton 包装），ADR-0018 生命周期。
- **风险**：4.x 仍是**预发布**（alpha），无稳定性/安全支持承诺；`<5.0` 上限防越级大版本。
- **跟踪判据（命中任一即评估升级）**：① 4.0 首个**非预发布**版本发布；② 使用面（threadpool/
  async 执行器路由、动态增删作业、进程内调度生命周期）出现已知缺陷或 CVE；③ 3.11.x 分支
  停止维护且被迫二选一。
- **升级动作**：升到首个非预发布版本 → 跑 `backend/tests/scheduler/`、`backend/tests/api/test_heartbeat*.py`、
  `backend/tests/realtime/test_p3_3_multi_instance.py`（singleton 包装）→ 夜间 `backend-test` 兜底。
- **回落预案**：若 4.x 长期不转正，评估回 3.x LTS——我方使用面窄（三个 API 面），迁移成本可控，
  但需重做「执行器路由 / async 调度」的等价性验证。

## Alternatives

- **本单直接切 openpyxl**：弃——`openpyxl` 不能读写 `.xls`，直接切等于把产线 `.xls` 消费者
  （Toolkit / JIRA 上传链 / 归档下载）一次性打断；且至少需要一个跨方约定窗口。
- **只换写侧（xlwt→openpyxl）但那意味着产出 `.xlsx`**：弃——读侧（`xlrd`）与下游消费方仍绑 `.xls`，
  会造出「读写两套格式」的中间态，复杂度高于收益。
- **为 xlwt 建定期升级任务/告警**：弃——`xlwt` 无新版本（1.3.0 定格），跟踪无对象；
  要盯的是**消费方格式契约**，已写进触发条件。
- **把 inventory 做成「只减不增」棘轮并立即减项**：弃——当前无减项可做（阶段 2 未启动），
  门禁先承担「新增耦合必须更新台账」的职责，等阶段 2/3 再按进度收紧。
- **apscheduler 上加版本告警门禁**：弃——pin 与 lock 已锁定；跟踪项是外部发布节奏，
  机器判据（依赖上游 PyPI 状态）引入不必要的外部耦合。

## Verification

- 墓碑清理：`tools/archive/`（含旧实现 `ci_check_migrations.py`）与 `tools/ci_check_migrations.py`
  删除前全仓引用扫描 = 仅互为引用 + `tests/test_removed_env_keys.py` 一处历史注释（已同步措辞）；
- `tests/test_excel_dependency_inventory.py`：生产使用面 == 台账（新增/删除即红）；
- `python -m pytest tests/test_removed_env_keys.py tests/test_excel_dependency_inventory.py -q` → **8 passed**；
- `python scripts/run_gates.py check:quick` → **[OK] 12 gates**；`check:pr` → **[OK] 21 gates**。

## Revisit

- **阶段 2 启动时**：本文的消费方清单（Toolkit / JIRA 上传链 / 归档下载）需按当时的
  实际调用点复核——`--add-main-excel` 等参数名可能已演进。
- **inventory 门禁的生命周期**：阶段 3 完成后改为「禁止 `xlwt`」单向断言；`xlrd` 若因
  外部 `.xls` 输入长期保留，则台账保留读侧一条并注明理由。
- **apscheduler**：任一跟踪判据命中时，先在本文件记录判据命中事实与评估结论，再动 pin。
