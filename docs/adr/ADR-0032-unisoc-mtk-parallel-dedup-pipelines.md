# ADR-0032：展锐与 MTK 并列日志链路（Watcher 实时 + 归档 dedup）（#463 / #73）

- 状态：**Accepted**（v0.8：B3 spike 已执行 2026-09-15，D3 转已验证；P1 编码已合入 main 2026-08-31）
- 优先级：P1
- 目标里程碑：M7（方案 C 下补齐展锐 **实时信号 + 终态 dedup** 双覆盖面）
- 日期：2026-08-31
- 决策者：平台研发组
- 标签：UNISOC, MTK, dedup, scan, merge, watcher, toolkit, #73, #463, #220
- 背景：[`TOOLKIT_INTEGRATION_FEASIBILITY_2026-08-26.md`](../reviews/TOOLKIT_INTEGRATION_FEASIBILITY_2026-08-26.md) §2.4；Agent Note [`2026-08-31-unisoc-toolkit-73-463-alignment.md`](../notes/architecture/2026-08-31-unisoc-toolkit-73-463-alignment.md)

## 修订记录

| 版本 | 日期 | 变更 |
|------|------|------|
| v0.1–v0.4.1 | 2026-08-31 | 评审迭代（见 git 历史） |
| v0.5 | 2026-08-31 | 范围扩展：Watcher 实时层 + 统一路由；重议 #220 |
| v0.6 | 2026-08-31 | **Accepted**：B1 路径分区 + 双 merge；D3/D4/D7/D8/B5 终裁 |
| v0.7 | 2026-09-04 | P1 编码已合入 main（`922049d2`/`2368228f`，2026-08-31）；剩余 Z258 真机验收与 B3 spike 不阻塞落地记录 |
| v0.8 | 2026-09-15 | **B3 spike 已执行**（§B3）：五项验收实测通过，D3「UNISOC 复用 MTK merge 工具」由断言转为**已验证**；第 1 项精确化为「列数同构、两列命名有差异且被工具归一」；未覆盖平台侧发布路径记入 Revisit |

## 背景

1. **覆盖面空白**：生产 **279 台 UNISOC**（Z2581 223 + Z2582 56）。#220 下展锐不进 MTK Reconciler；终态 dedup 仅 MTK `start_log_scan -m 0`。Watcher 与归档两层均缺展锐等价能力。
2. **混平台 host**：30 台 ONLINE host 中 **6 台 MTK+UNISOC 共存**；路由键 **`device.platform`**，per-device 双轨并存。
3. **工具链**（`/mnt/automation-toolkit/python-tools/`，与 MTK **不可混用**）：`scan_log_gt.py`（uniview 主信号）→ `scan_result.py`（15 列 `aeeexp`）。
4. **平台缺口**：`UnisocPlatformCollector` 为 stub；`ScanRunner`/`dedup_scan` 无 UNISOC 分支。

> **术语**：「采集阶段/汇总阶段」= toolkit 位次；「落地批次 P1/P2」= 工程排期。

## 决策

### D0：统一平台路由（Watcher + 归档）

PlanRun 活跃期间按 **`device.platform`** 路由；两层 **共享路由键、不共享工具**。混平台 host **per-device** 双轨。**P1 验收**：Watcher + 归档 **一并交付**。

### D1：两条并列流水线，禁止交叉混用

| 平台 | 归档·采集 | 归档·汇总 | Watcher·信号源 | Watcher·入口 |
|------|-----------|-----------|----------------|--------------|
| MTK | `start_log_scan -m 0` | `run_dedup_org` + merge | `/data/aee_exp`、ZZ_INTERNAL | AEE Reconciler + inotifyd |
| UNISOC | `scan_log_gt -m sprd` | `scan_result.py` + merge | uniview + dropbox/ylog | `UnisocPlatformCollector` + UNISOC Reconciler（D8） |

**禁止**：跨平台混工具；未分流 `*_org.xls` 进同一 merge 输入集；展锐走 MTK AEE Reconciler。

**上送命名**：`{host_id}_*_org.xls` 必填；禁止裸 `Result_*_MonkeyAEE_SPRD_*` 上送。

### D2：两层职责（一并交付）

| 层 | MTK | UNISOC |
|----|-----|--------|
| Watcher | ADR-0028 Reconciler 链 | D8 + B5 |
| 归档 | `ScanRunner` + `dedup_scan` | D4 并列 runner + B1 分流 merge |

### B1：混平台 PlanRun / merge（**已裁定**）

**原则**：不同平台 **互不干预**——各自输入集、各自 merge、各自产物；产品面 **一张总表**（UI 合并展示，后端分平台 artifact）。

**裁定**：**路径分区 (iii) + 每平台一次 merge (ii)**，组合不变量：

| 对象 | 路径 |
|------|------|
| scan 上送 | `dedup/{run_id}/mtk/{host_id}_*.xls`；`dedup/{run_id}/unisoc/{host_id}_*.xls` |
| merge 发布 | `dedup/{run_id}/merge/mtk/`；`dedup/{run_id}/merge/unisoc/` |
| 产物区分手段 | **路径分区**（主）；`{host_id}_` 前缀（归因，保留） |

**实现约束**：

- `build_merge_argv` / `_load_org_files_for_merge` 按 `mtk`/`unisoc` 子目录分别收集输入。
- `find_fresh_merge_output_dir` / `_publish_merge_to_center` 扩展为 **每平台各注册/发布一次**，不得只保留 mtime 最大者。
- `count_hosts_with_scan_artifacts`：按 **host 去重**，MTK/UNISOC **分区各自完备性判定** 后分别触发 merge。
- 前端：watcher-summary / 终态报表 **一张总表**，数据按 platform 分桶聚合（见 B5）。

### D3：环境键与 fleet 键（**已裁定**）

| 角色 | MTK | UNISOC |
|------|-----|--------|
| Agent 归档采集 | `STP_DEDUP_SCAN_PYTHON` / `_SCRIPT` | `STP_UNISOC_LOG_SCAN_PYTHON` / `_SCRIPT` |
| Agent 归档汇总 | `run_dedup_org`（同脚本） | `STP_UNISOC_SCAN_RESULT_PYTHON` / `_SCRIPT` |
| Agent Watcher | 内置于 Agent | **(w1) 内置于 Agent**，**不**另设外置 Watcher 脚本 env 键 |
| 控制面 merge | `STP_BACKEND_DEDUP_SCAN_*` | 同一 merge 工具；输入按 B1 子目录 |
| fleet `-side` | `STP_DEDUP_SCAN_TAG` | **裁定 A：与 MTK 共用** `STP_DEDUP_SCAN_TAG`（factory/shanghai 逻辑不变） |

hot-update：`STP_AGENT_UNISOC_*` 源键 → Agent 无前缀键；纳入 `AGENT_PATH_ENV_KEYS`；**不得**写入 MTK 键。

### D4：展锐归档采集 Agent 化（**已裁定：c**）

**裁定 (c)**：独立进程 + 产物收割（对齐现有 `ScanRunner`/外置工具模式），与 MTK **触发链同构**——仅脚本与信号源不同：

```text
scan_now（控制面）→ Agent 按 platform 路由
  MTK:    ScanRunner → start_log_scan → run_dedup_org → upload → dedup/{run}/mtk/
  UNISOC: UnisocScanRunner → scan_log_gt → scan_result.py → upload → dedup/{run}/unisoc/
```

- **与 MTK 一致**：终态（或 `scan_now`）触发采集 → 汇总 → `UploadManager` 上送；`scan_result.py` **紧接采集成功后**调用（≡ `run_dedup_org`）。
- **与 MTK 差异**：展锐用 GT 工具链；错误类型/subtype 不同；底层触发为 uniview 轮询而非 AEE db_history。
- **否决 (a)(b)** 作为归档主路径（(a) 与 Watcher 职责混淆；(b) 与 toolkit 后台线程模型冲突）。

### D5：实施顺序

| 维度 | 顺序 |
|------|------|
| Watcher（运行中） | 平台 Reconciler 轮询 → 事件落盘 → `job_log_signal` |
| 归档（终态） | 采集 → 汇总 → upload → **分平台 merge** |
| 工程 | P2 spike（B3）可先行；**P1 = Watcher + 归档** |

### D6：与 #220 的关系（**已裁定：按 platform 分支**）

- **supersede #220「仅 MTK」语义**：`job_session` 按 **`device.platform`** 选择 Reconciler 实现（MTK → `AeeDbHistoryReconciler`；UNISOC → D8），**不再**仅靠 `STP_WATCHER_AEE_RECONCILE_PLATFORMS` 单白名单名承载展锐。
- MTK 路径不变；展锐 **禁止**进入 MTK AEE 监测目录逻辑。

### D7：归档汇总（**已裁定**）

`scan_result.py` ≡ MTK `run_dedup_org`；控制面同一 `-merge_files_list`，输入集按 B1 子目录分流。`FAILED PlanRun` → `merge_skip_failed_plan_run`（ADR-0028 D2），无平台例外。

### D8：Watcher 实时层（**已裁定：w1**）

| 项 | 裁定 |
|----|------|
| 形态 | **(w1)** 展锐专用 Reconciler 等价物（轮询 uniview/dropbox/ylog 增量，对齐 toolkit 检测逻辑；**非**整进程 `scan_log_gt` 塞入 Watcher） |
| 路由 | **`device.platform` 分支**（D6） |
| env | **内置**，不暴露独立 Watcher 外置脚本键（D3） |
| 与归档目录 | **串行、分树**：Watcher 运行期落盘至 **Watcher 事件目录**（参照 MTK `STP_AEE_LOCAL_ROOT` 角色）；`scan_now` 归档链 **另起** `scan_log_gt` 工作区，**禁止**两路径并发写同一目录。归档可读 Watcher 已落盘材料，但须在 Reconciler 轮次与 `scan_now` 之间 **串行化**（Job RUNNING 仅 Watcher；终态 scan 仅归档 runner）。 |

### B5：`job_log_signal` 与 G10（**已裁定**）

**`extra` 契约（展锐）**：

| 字段 | 值 |
|------|-----|
| `event_type` | 固定 **`UNIVIEW`**（uniview 主路径；dropbox 辅信号若上报另议扩展，P1 以 uniview 为准） |
| `event_subtype` | toolkit **`event_name`**（`unievent_info.json`） |
| `package_name` | 由采集/Reconciler 脚本解析填入（对齐 toolkit 字段） |
| 时间戳 | 由采集/Reconciler 脚本解析填入（对齐 toolkit；字段名与 MTK 对齐为 `aee_ts` 或文档化别名） |
| `nfs_path` | **参照 MTK**：Agent HDD 事件目录路径，供后续 DLE/上送 |

**G10**：展锐 **原始 subtype 字符串直接**进入 `_RISK_RATING_RULES` 匹配；未命中 → **默认 B**（`_DEFAULT_RISK_LEVEL`）。

**UI**：`watcher-summary` **按 `platform` 分桶**展示；PlanRun 终态风险/日志 **一张总表**（B1 产品语义）。

### B3：P2 spike 验收（**已执行** 2026-09-15）

**结论：D3「UNISOC 复用 MTK merge 工具」✅ 成立**——五项验收实测通过；但第 1 项
「15 列 `aeeexp` 同构」应精确表述为「**列数同构、两列命名有差异且被工具归一**」（见下）。

| # | 验收项 | 结论 | 实测证据 |
|---|---|---|---|
| 1 | 15 列 `aeeexp` 同构 | ⚠️ **列数同构，命名有差异** | 两平台均 **15 列**，但**两列命名不同**：UNISOC `ExpType` / `DeviceCount` vs MTK `ExpType␠`（**尾随空格**）/ `DeviceId`。抽样 **40 UNISOC + 40 MTK**（盘上计 853/320）**各自表头 100% 一致**——差异稳定，非偶发 |
| 2 | `{host_id}_Result_*_org.xls` 在 `dedup/{run}/unisoc/` 可注册 | ✅ | 盘上 `dedup/342/unisoc/` 等含 `172-21-15-*.…_SPRD_*_org.xls`，命名形态与 MTK 侧同构 |
| 3 | 分平台 merge 试跑（`mtk`/`unisoc`） | ✅ | 两平台各自经 `-merge_files_list` 实跑成功（见 #4） |
| 4 | 双 merge 产物均发布 | ✅ | 工具产出 `Result_MergeFiles_org.xls`（UNISOC **285 行**）+ `Result_MergeFiles.xls`（去重后 **4 行**）；MTK 侧同形（5 行 / 2 行）——**两平台产物结构一致** |
| 5 | 无 `_org` 后缀产物不进 merge glob | ✅ | 本 spike 以 `*_org.xls` 显式构造 listfile（`build_merge_argv` 的 `-merge_files_list`），非 glob 展开 |

**第 1 项的关键实测**：merge 工具**能消费** 15 列 UNISOC 输入，且**输出表头统一为
MTK 形态**（`ExpType␠` / `DeviceId`）——即工具内部已做列名归一，**「同一工具」成立**；
但原文「同构」若被读作「列名逐一相同」，则该表述**不准确**。

**观测口径与边界**：以盘上真实产物执行（输入 `dedup/{342/unisoc, 408/mtk}` 的
`*_org.xls`；工具经 `.env.backend` 的 `STP_BACKEND_DEDUP_SCAN_{PYTHON,SCRIPT}` 解析，
控制面同一套键、未引入平台分支）。**未**经控制面 HTTP 全链路——即验证了**工具能力**，
未验证 `merge/unisoc/` 在**平台侧**的发布路径，该差异记入 Revisit。


## 备选方案（已否决）

| 方案 | 原因 |
|------|------|
| PlanRun 拒绝混平台 (B1-i) | 与现网 6/30 混平台 host 冲突 |
| 单 merge 混收两平台 | 违反 D1 |
| 展锐独立 `-side` TAG (D3-B) | 裁定共用 TAG |
| Watcher (w2) 常驻 scan_log_gt | 与归档职责重叠；裁定 w1 |
| 仅归档不做 Watcher | v0.5 已否决 |

## 影响

- **代码**：`UnisocReconciler`（w1）；`UnisocScanRunner`（D4c）；`dedup_scan` 分平台子目录 + 双 merge；`job_session` platform 分支；`report_service` G10 扩展展锐 subtype；`upload_manager` B1 路径。
- **存储**：`dedup/{run_id}/{mtk,unisoc}/` + `merge/{mtk,unisoc}/`。
- **运维**：混平台 host 配双套 **归档** 工具路径；Watcher 无额外外置键。
- **文档**：`aee/CLAUDE.md` 增展锐监测源；`AGENTS.md` 双轨 + 分平台 merge。

## 落地与后续动作

- [x] B1 + D3 + D4 + D7 + D8 + B5 → **Accepted**（v0.6）
- [x] P1 编码：Watcher (w1) + 归档 (D4c) + 控制面双 merge（2026-08-31 合入：`922049d2` / `2368228f`；落地记录见 `docs/notes/feature/2026-08-31-adr0032-p1-unisoc-pipelines.md`）
- [x] P2 spike（B3）——2026-09-15 执行完毕，五项验收见 §B3
- [ ] Z258 真机：Watcher 冒烟 + 归档端到端
- [ ] #73 / #463 关闭路径

## 关联实现 / Issue

- #463；#73；#220（supersede，D6）
- ADR-0025；ADR-0028
- `job_session.py`；`aee/reconciler.py`；`collectors/unisoc.py`；`scan_runner.py`；`dedup_scan.py`；`agent_env_sync.py`
- toolkit：`stability_Monkey-Log-Scan-GT&SPRD`、`stability_Scan-Result-GT`

## Revisit

- **平台侧发布路径未验证**：B3 spike 验证的是**工具能力**（`-merge_files_list` 消费 15 列
  UNISOC 输入并产出归一表头），**未**经控制面 HTTP 全链路——即 `merge/unisoc/` 在
  **平台侧**（`run_merge_sync` → `PlanRunArtifact` 登记 → 发布）的路径仍待端到端确认。
  触发条件：首次真实 UNISOC run 走完归档链时按 `run_context.merge_platforms` 核对。
- **列名差异的长期风险**：工具当前对 `ExpType`/`DeviceCount` → `ExpType␠`/`DeviceId` 做了
  归一，但该归一**未见于文档**、属工具内部行为。若上游工具改版或引入侧参数差异，
  可能出现「列数仍 15、语义已错位」的静默失败。触发条件：工具升级或新增平台时，
  以「输出表头比对」为验收项重跑本 spike。
- **Z258 真机验收**（B3 之外）：Watcher 冒烟 + 归档端到端仍未执行，保持原状。
