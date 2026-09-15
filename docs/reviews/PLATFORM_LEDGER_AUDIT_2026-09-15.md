# 平台台账审计报告（2026-09-15）

- **类型**：只读审计（ledger audit）——复核「台账声明」与「代码/issue 事实」的一致性
- **审计基线**：`origin/main` @ 2026-09-15
- **审计范围**：本仓 `📌 [总表]` 类审查台账与风险台账，共 **8 份台账 + 1 项 issue 验收复核**
- **产出方**：dsh（DeepSeek Harness）会话
- **性质**：**只读**。本报告不改变任何代码、台账或 issue 状态；唯一入库改动是
  DOC-MAP 计数更正（PR [#2102](https://github.com/DUElost/stability-test-platform/pull/2102) /
  [#2117](https://github.com/DUElost/stability-test-platform/pull/2117)）
- **留痕说明**：审计过程评论留在各 issue；本报告是按
  [`shared-row-lock-table.md`](../notes/architecture/2026-09-14-shared-row-lock-table.md)
  已确立的纪律（「只写在 issue 评论里 = 放弃；issue 不进仓库检索面，下一轮无法在本地增量核对」）
  所做的**入库固化**

---

## 1. 执行摘要

| # | 结论 | 证据强度 |
|---|---|---|
| 1 | **逐项挂 issue 号的台账：8 份、零假闭环** | 逐项状态核验（8/8）+ 源码级抽验（3 份） |
| 2 | **仅给汇总计数的风险表：出现 2 处登记过期**（#1515 R-01/R-02） | 源码级确认「已修但未回写」 |
| 3 | **`FINISHED × MERGED` ≠ issue 验收满足** | #1737 反例：四片 PR 全合、19 测试绿，但验收清单 0 勾选、单未关 |
| 4 | **不能默认 issue 的修复建议正确** | #1522 反例：一条建议被正确拒绝，机械照验会得相反结论 |
| 5 | **#1515 清单标注「已修」的 11 项，11/11 属实** | 逐项源码级复核 |
| 6 | **#1035 自杀条款的「批次」未定义 → 无法机械判定** | 条款文本 + 实测空白期数据 |

**一句话**：**台账的准确性取决于「是否逐项挂单并可追溯到 PR」，而非编写者的认真程度。**

---

## 2. 8 份台账的结构与核验结果

### 2.1 结构一致性：R03–R10 均采用「逐项挂 issue + 关单 PR 列」

| 台账 | 批次 | 条目数 | 唯一 issue 引用 | 关单 PR 列 | 核验方式 |
|---|---|:--:|:--:|:--:|---|
| [#1086](https://github.com/DUElost/stability-test-platform/issues/1086) R10 扫描上传存储 | 09-08 | 17 | 25 | ✅ | **源码级抽验 2 例** |
| [#1015](https://github.com/DUElost/stability-test-platform/issues/1015) R07 Agent 执行引擎 | 09-08 | 15 | 21 | ✅ | **源码级抽验 1 例** |
| [#1055](https://github.com/DUElost/stability-test-platform/issues/1055) R09 设备日志采集 | 09-08 | 10 | 22 | ✅ | 结构 + 逐项状态 |
| [#1031](https://github.com/DUElost/stability-test-platform/issues/1031) R08 脚本库/版本 | 09-08 | 13 | 20 | ✅ | 结构 + 逐项状态 |
| [#996](https://github.com/DUElost/stability-test-platform/issues/996) R06 调度派发 | 09-08 | 10 | 16 | ✅ | 结构 + 逐项状态 |
| [#979](https://github.com/DUElost/stability-test-platform/issues/979) R05 Plan/Suite | 09-07 | 14 | 19 | ✅ | 结构 + 逐项状态 |
| [#961](https://github.com/DUElost/stability-test-platform/issues/961) R04 项目主机设备 | 09-07 | 19 | 22 | ✅ | 结构 + 逐项状态 |
| [#945](https://github.com/DUElost/stability-test-platform/issues/945) R03 数据模型迁移 | 09-07 | 12 | 17 | ✅ | 结构 + 逐项状态 |

> 唯一 issue 引用合计 **162**（唯一个数，非匹配次数）。

### 2.2 横向核验：116 个引用中仍 OPEN 者，**全部正当**

对 6 份未做源码级抽验的台账（#1055/#1031/#996/#979/#961/#945，合计 116 个唯一 issue 引用）
逐项查证状态，仍 OPEN 的仅 3 类，**无一为遗留缺陷**：

| 被引用 issue | 性质 | 是否遗留缺陷 |
|---|---|---|
| [#716](https://github.com/DUElost/stability-test-platform/issues/716) | `[Epic] 展锐 UNISOC 平台全链路闭环`——**Epic 容器** | ❌ 否 |
| [#708](https://github.com/DUElost/stability-test-platform/issues/708) | `schema-sync 基线噪音收敛**触发条件**`——条件触发项 | ❌ 否 |
| [#910](https://github.com/DUElost/stability-test-platform/issues/910) | **R02 台账本身**（正当 open，另见 §4） | ❌ 否 |

---

## 3. 源码级复核明细

### 3.1 #1515 四维度审查台账（12 项，复核 11 项）

清单标注「已修」的 11 项，**逐项回源码复核，11/11 属实**：

| 项 | issue | 结论 | 关键证据 |
|---|---|---|---|
| CRITICAL-1 | #1516 | ✅ 已修 | `database.py:164 get_sync_engine_kwargs` 调用 `_pool_capacity_kwargs()`；docstring 完整记录原风险（15 连接 × 12 调度任务 + SAQ 并发 10 + 84 处 `SessionLocal`） |
| CRITICAL-2 | #1517 | ✅ **两条修复方向均落地** | ①**登记**：ADR-0027 **v1.2**（09-11）补 RunConsole 单实例约束；清单第 6 条现为**双模态契约** ②**治本**：v1.4–v1.7（#1737 P1–P4）注册表外置（`console_registry.py`，`SET NX PX` + Lua CAS）。**实测 19 passed** |
| HIGH-1 | #1518 | ✅ **完整闭环** | 原 4 处独立解析点（120 vs 300×3）**全部**改为 `from backend.core.job_timeout_config import HOST_HEARTBEAT_TIMEOUT_SECONDS`；全仓字面量仅剩定义处 + `__all__` 再导出，**无任何处自行 `os.getenv`** |
| HIGH-2 | #1519 | ✅ **完整闭环（正例）** | 4 个私有符号全部下沉（`services/plan_wifi.py`、`services/plan_run_queries.py`）；**门禁** `tools/dev/check_layering.py` 已接入 `run_gates:71-73`，**带 `--self-test` 红绿双向** |
| HIGH-4 | #1521 | ✅ **完整闭环** | `purge_run_storage_dirs`（`cron_scheduler.py:230`）：两轨同源 `plan_run_retention_days` cutoff；覆盖 `("devices","dedup","jira")`（`jira` 系 #1698 补救）；**行删除前**调用、失败剔除可重试 |
| HIGH-5 | #1522 | ✅ 已修（**issue 一条建议被正确拒绝**） | 净速率 **4 → 40 目录/分钟**（`_SPILL_CATCHUP_INTERVAL` 默认 30s）；水位驱动提前停。**详见 §3.3** |
| HIGH-6 | #1527 | ✅ 已修 | 全平台失败由原静默 `return` 改为 `raise RuntimeError`（`saq_tasks.py`） |
| HIGH-7 | #1523 | ✅ 已修 | 编号冲突以 `ADR-0031-A-appendix-…` 后缀消解 |
| HIGH-7B | #1524 | ✅ 已修 **且门禁化** | 43 个 ADR **零**状态行缺失；S12 源码**直接引用 #1524**（「此前键位粗体/表格形态会让取行…」）+ `--self-test` 14 规则全绿 |
| HIGH-8 | #1525 | ✅ 取舍仍成立 | PR 门禁未扩、未引 Merge Queue；量化代价见 §3.4 |
| HIGH-9 | #1526 | ✅ **完整闭环** | `h4i5j6k7l8m9` 的 **`upgrade()`** 含 `drop_table`（非仅 downgrade），在 head 祖先链上（140 revision）；ORM 模型与代码引用已清零。**顺带验明 #1890 两项修复**（#1909 forward drop + #1910 `check_orphan_models.py` 门禁） |

**#1520（HIGH-3 God-module）为清单中唯一未勾选项**，本轮**未复核**（清单自身标注为「分期」，
未声称已修）。故本节结论限于「**标注已修的 11 项属实**」，非「12/12 均已修」。

### 3.2 R10 / R07 源码级抽验

**R10-F02（#1071）双平台 scan 屏障**——两条验收标准均满足：

- ① 完成条件由「按 host 计数」升级为 **`(host, platform)` 单元**：
  `units_expected = sum(len(set(platforms)) for platforms in expected.values())`，
  `return self.units_satisfied >= self.units_expected`（`dedup_scan.py:225,158`）；
- ② `_any_scan_runner_configured()`（`scan_runner.py:233`）使**仅配置 UNISOC** 也可执行，
  `_execute_job` 内 UNISOC 分支独立于 MTK。

**R10-F05（#1074）中心发布失败不得静默 ok**——`except OSError` 由 `return None`
改为 **`raise RuntimeError`**（`dedup_scan.py:821-823`）；调用方的 None-回退**仅保留给
「未配置中心存储」**（`:813-814`），与「发布失败」分离。

**R07-F02（#1003）进程组收敛**——`pipeline_engine.py:718` 保存 `proc._stp_pgid`，
`:786-793` POSIX 走 `killpg(SIGTERM → 等整组收敛 → SIGKILL)`；父进程退出后**仍继续收敛**。
回归测试 `test_terminate_posix_escalates_when_parent_exited_but_group_alive` 等两条
（正是 issue 的两条验收标准），**实测 14 passed**。

**R10 关单 PR 映射抽验（3 例全对）**：

| 台账声称 | PR | 标题 |
|---|---|---|
| #1070 ← #1126 | MERGED | `fix(extract): require completion marker before treating jira…` |
| #1071 ← #1127 | MERGED | `fix(#1071): dual-platform scan barrier and UNISOC-only runne…` |
| #1074 ← #1131 | MERGED | `fix(#1074): fail merge when center publish raises OSError` |

### 3.3 反例：#1522 的 issue 建议「不可全采纳」

issue 建议两条：①「跌破 target 才停」②「`_spill_enqueued_ids` 跨轮保留」。

**② 被拒绝，且拒绝理由经本审计独立验证成立**：该集合语义是「已成功 enqueue」，
跨轮保留会让**上传失败仍为 LOCAL** 的事件被永久跳过（饿死）。
验证：`EventUploader.enqueue_local_event` 在 enqueue 成功时返回 `True`，
但事件要等控制面 `upload_task` 标记后才经 `_recover_pending` 实际上送——**失败则仍为 LOCAL**。

**判据修正**：应验「**现象是否消除**」（净速率封顶是否破除），而非「**建议是否被逐条采纳**」。
若机械照建议 ② 验收，会得出**相反且错误**的结论。

### 3.4 #1525 取舍的量化代价

PR 门禁不跑 `backend-test`/`frontend-check`/`docker-build`（`if: github.event_name != 'pull_request'`）
未变。其依赖的夜间兜底近 **9 次中 3 次 `backend-test` 失败（33%）**：

| 夜间 run | backend-test | 根因 | 状态 |
|---|---|---|---|
| 2026-09-09 20:15 | ❌ | 测试漂移 | ✅ 已修 |
| 2026-09-11 20:16 | ❌ | `UnsafeTestDatabaseUrl`（#1547） | ✅ 已修（#1566 + 守卫 #1664） |
| 2026-09-13 20:04 | ❌ | 三处测试漂移（#1935） | ✅ 已修 |
| 其余 6 次 | ✅ | — | — |

**结构性观察**：三次失败涉及的文件均位于 `backend/tests/{migration,services}/` 与前端 vitest
——**全部在 PR 门禁覆盖之外**，只能等夜间暴露（main 带红最长约 1 天）。
**但自愈链路有效**：失败 → 自动开单 [#1935](https://github.com/DUElost/stability-test-platform/issues/1935)
→ 修复 → 连续 2 次 success。
**三次根因无一是生产代码缺陷**——可作 #1525「取舍可接受」的实测支撑。

---

## 4. #910（R02 认证台账）复核：准确，无假闭环

11 项逐条核对：7 个确定缺陷**全部真实闭环**，2 项风险正确标注仍 open（#906 / #91）。

关键发现：**F02/F03/F04 不是三处补丁，而是统一设计**——PR #1020 以 **`token_version` 会话纪元**
一次性覆盖（迁移 `n4o5p6q7r8s9` + 模型列 + `services/auth_session.py:35` 唯一校验点），
印证台账「会话身份与撤销宜统一设计」的建议被实际采纳。

**审计自查留痕**：初查时我在 `security.py`/`auth.py`/`deps.py` grep `token_version`，
**只见签发未见校验**，一度疑似「只签发不校验」的假闭环；全仓搜索后确认校验在
`services/auth_session.py:35`——**是我的 grep 范围过窄，非台账问题**。
（方法论：**grep 命中集合 ≠ 实现真值**。）

---

## 5. #1737 复核：工作已完成，issue 未关闭

`feat-1737-console-readlog-cross-instance` 为 **`FINISHED × MERGED`**（P4 最后一片），
且其 Agent Note 的 Revisit 明写关闭条件：「P1–P4 已全部落地…按 ADR-0027 v1.7 清单第 6 条
复核即可关闭本单」。五项验收标准逐条复核**全部满足**：

| # | 验收标准 | 证据 |
|---|---|---|
| 1 | 双实例可订阅/查 status/cancel | `test_cross_instance_status_reads_snapshot` / `..._cancel_forwards_and_waits_ack` |
| 2 | 同 `run_key` 仅一方成功 | `test_second_instance_same_key_rejected`（真实两个 RunConsole 实例） |
| 3 | Owner 失联有测试且 `run_key` 可恢复 | `test_renew_lost_triggers_self_abort` 等 4 例 |
| 4 | ADR-0027 清单第 6 条修订 | v1.2→v1.7 逐版记录 |
| 5 | Agent Note（四节） | 四份 note 齐备 |

**实测**：`pytest backend/tests/services/test_run_console_registry.py -q` → **19 passed**。
**建议**：勾选验收项并关闭（属 owner 决定，本审计未代为执行）。

---

## 6. #1035（ADR-0034 Evidence 台账）观察

### 6.1 当场组补记：一次实测 M4（查重拦截）

2026-09-15 拟承接 #1962，`declare --issue 1962` 返回：

```
[REFUSED] issue #1962 已被在窗 Execution 'fix 1962 终态 PlanRun 仪表盘窗口丢弃延后落库的本轮事件'
          （codebuddy，CODING）引用
```

**处置**：放弃认领（未用 `--force`），改做只读审计。
**归因（L2）**：认领前检查显示「在窗=0」——**我的检索方式漏了**（对方 requirement 名含中文）；
若无 §3.4 查重闸，将与对方同改 `plan_runs.py` **撞车**。事后核验：对方两条 Execution
均为 **`FINISHED × MERGED`**（已完整交付）。

### 6.2 审计疑问：自杀条款的「批次」未定义

条款：「**连续两个批次**『当场组』空白（明知有 hint/dedup 事件却无记录）→ 提前退役」。

实测：当场组最后记录停在 **09-12**，审计日 **09-15**（空白 3 天）；该窗口 registry 新增
**217 条 declare**。即条款的**客观部分（有事件）已具备**，但**「批次」边界在台账中未定义**
→ 条款**无法机械判定**，只能主观判断，与台账「宁可没有精细数据，不要僵尸仪器」的自我要求有张力。
**建议**：10-15 裁决时一并明确「批次」定义。

---

## 7. 方法论沉淀（6 条，均来自本次实证）

1. **`FINISHED × MERGED` ≠ issue 验收满足**——#1737：四片 PR 全合、19 测试绿，
   但验收清单 **0 勾选**、单未关。判据必须**回 issue 正文的验收标准逐条对源码**。
2. **风险陈述类台账易过期**——#1515 风险表 R-01/R-02 均「已修未回写」，因它**只给汇总计数**；
   而**逐项挂 issue 号的台账准确**（8 份零假闭环），因 issue 状态即事实源。
3. **不能默认 issue 的修复建议正确**——#1522 建议 ② 被正确拒绝；判据应是
   「**现象是否消除**」而非「**建议是否被逐条采纳**」。
4. **验证「迁移已删除 X」须确认语句在 `upgrade()` 而非 `downgrade()`**——
   `grep drop_table` 会同时命中回滚路径；仅凭命中即判「已完成」会产生**假证据**（#1890 实例）。
5. **grep 命中集合 ≠ 实现真值**——判「未校验/未实现」前必须**全仓搜索消费点**
   （本审计在 #910 上亲身踩过一次）。
6. **区分「机制被使用」与「机制发生拦截」**——同 issue 多条 Execution 只说明**多 Execution 存在**，
   不说明查重闸拦住了谁。判 M4 必须找**被拒的 declare**（exit 2 / REFUSED 原文），
   而非数同 issue 记录数。（本审计曾误以「31 个同 issue 多 Execution」为 M4 量级依据，已自纠。）

### 建议：把「逐项挂 issue + 关单 PR 列」定为台账关闭的模板要求

**同文档内的自然实验**（证据强度高于跨文档比较）：#1515 **同一份 issue** 中，
**批次清单**（逐项挂单）**11/11 准确**，而**风险台账复核表**（汇总计数）**2 处过期**。

成本极低（关闭时多写一列），收益是**关闭后的台账仍可复核**——本审计即靠该列快速抽验了
R10 三例、R07 一例。

---

## 8. 审计过程的自查更正（3 次，均留痕）

| # | 错误 | 更正 |
|---|---|---|
| 1 | DOC-MAP 计数先写 `1/1/7`（只审了 R-01） | 续审发现 R-02 也早已修复 → **PR #2117 改为 `2/1/6`** |
| 2 | #1890 时序写反（称「续查 → 立单」） | 实际 **立单 14:48 早于续查 15:00** → 发评论更正 |
| 3 | 以「31 个同 issue 多 Execution」支撑「M4 素材密集」 | 复核发现多为**正当切片**（#1737 五条系 P1–P4 顺序交付，非撞车）→ **撤回量级依据** |

**留痕理由**：计数、时序、推理范围**都是事实**；写错会误导后续追溯。宁可自纠，不留错档。

---

## 9. 边界与未覆盖面（如实标注）

- **只读**：未改代码、未改 ADR、未改任何台账或 issue 状态；唯一入库改动为 DOC-MAP 计数更正
  （PR #2102 / #2117）。
- **核验深度分层**：8 份台账中，**逐项状态核验覆盖 8 份**，**源码级抽验仅 3 份**
  （#1515 的 11 项、#1086 的 2 例、#1015 的 1 例）。**不宣称**「162 个引用均已源码级验证」。
- **#1515 的 #1520 未复核**（清单自身标为「分期」，未声称已修）。
- **#1035 未裁决**：自杀条款是否触发属 owner 决定，本报告只登记疑问与数据。
- **#1737 未关闭**：关闭属状态变更，本报告只提供复核依据。

---

## 10. 关联

| 载体 | 关系 |
|---|---|
| [#1515](https://github.com/DUElost/stability-test-platform/issues/1515) | 四维度审查台账（本报告 §3.1 复核对象，7 条审计评论） |
| [#910](https://github.com/DUElost/stability-test-platform/issues/910) | R02 认证台账（§4） |
| [#1086](https://github.com/DUElost/stability-test-platform/issues/1086) / [#1015](https://github.com/DUElost/stability-test-platform/issues/1015) | R10 / R07 台账（§3.2） |
| [#1737](https://github.com/DUElost/stability-test-platform/issues/1737) | RunConsole 多实例（§5） |
| [#1035](https://github.com/DUElost/stability-test-platform/issues/1035) | ADR-0034 Evidence 台账（§6） |
| `docs/notes/architecture/2026-09-14-shared-row-lock-table.md` | **入库纪律来源**（「只写在 issue 评论里：放弃」） |
| `docs/reviews/PLATFORM_AUDIT_2026-09-11.md` | 本报告复核的主要台账来源 |
