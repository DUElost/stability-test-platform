# ADR-0032：展锐与 MTK 并列 dedup 归档流水线（#463 / #73）

- 状态：**Proposed**（原则层可评审；**B1 + D3 键名 + `-side`/`STP_DEDUP_SCAN_TAG` 裁定前不得 Accepted**；D7 倾向须 Accepted 前升为决定；**P1 生产合入另须 D4 终裁**）
- 优先级：P1
- 目标里程碑：M7（与 ADR-0025 方案 C 归档链补齐展锐覆盖面）
- 日期：2026-08-31
- 决策者：平台研发组
- 标签：UNISOC, MTK, dedup, scan, merge, toolkit, #73, #463, #220
- 背景：[`TOOLKIT_INTEGRATION_FEASIBILITY_2026-08-26.md`](../reviews/TOOLKIT_INTEGRATION_FEASIBILITY_2026-08-26.md) §2.4；Agent Note [`2026-08-31-unisoc-toolkit-73-463-alignment.md`](../notes/architecture/2026-08-31-unisoc-toolkit-73-463-alignment.md)

## 修订记录

| 版本 | 日期 | 变更 |
|------|------|------|
| v0.1 | 2026-08-31 | 初稿（Proposed） |
| v0.2 | 2026-08-31 | 评审修订：生产混平台 host、设备数更新、B1/B3 待裁定项、术语与 D4/D5/D6 措辞；B2→D7、B4→D6 已闭合 |
| v0.3 | 2026-08-31 | 复审修订：状态行门禁对齐、B1 产物区分不变量、B3 双 merge 断言、D7 FAILED skip、完备性按 host 去重 |
| v0.4 | 2026-08-31 | B1=(ii) 中心 `merge/` 防覆盖布局登记（引用 `dedup_scan.py:559-582`）；Agent Note / `README`/`CLAUDE` 索引挂靠同步 |
| v0.4.1 | 2026-08-31 | 恢复 `## 背景` 标题；`README` M7 加粗对齐；`CLAUDE` 门禁句补 D4 |

## 背景

1. **覆盖面空白**：生产库（2026-08-31 只读）**279 台 UNISOC 设备**（Z2581 223 + Z2582 56；全量 `device`，非仅 ONLINE）。`STP_WATCHER_AEE_RECONCILE_PLATFORMS=MTK`（#220）下 **展锐设备不进入 Reconciler 采集**（设备级门禁，`job_session.py`；服务可启动，非白名单平台跳过）。PlanRun 终态 dedup 链仅包装 `start_log_scan.py -m 0`（MTK AEE_TNE），展锐机无等价归档。
2. **混平台 host 是常态**（口径：`device.status='ONLINE'` 挂到 `host.status='ONLINE'`）：30 台此类 host 中，**6 台同时挂 MTK + UNISOC 设备**（8 台仅 UNISOC、16 台仅 MTK）。**禁止**用 host 级单一 `platform` 做 merge 分流；混平台 host 须**两套采集/汇总工具并存**，按设备或产物路径路由。
3. **工具已存在**：`/mnt/automation-toolkit/python-tools/` 有展锐**工具链**（与 MTK `stability_Start-Log-Scan` **独立、不可混用**）：
   - **采集阶段**：`stability_Monkey-Log-Scan-GT&SPRD` / `scan_log_gt.py`
   - **汇总阶段**：`stability_Scan-Result-GT` / `scan_result.py`（输出 15 列 `aeeexp`——**列格式**与 MTK merge 同构，**工具**不同）
4. **既有预留不完整**：ADR-0028 `PlatformCollector` + `device.platform` 仅覆盖 **Watcher/DLE 实时层**；`ScanRunner` / `dedup_scan` **无**多平台插件，不得将展锐工具塞进 `STP_DEDUP_SCAN_SCRIPT`。

> **术语**：「采集阶段 / 汇总阶段」= toolkit 流水线位次；「落地批次 P1/P2」= TOOLKIT/#463 工程排期（勿与工具阶段混称）。

## 决策

### D1：两条并列流水线，禁止交叉混用

| 平台 | 采集阶段 | 汇总阶段 | 实时信号（独立） |
|------|----------|----------|------------------|
| MTK | `start_log_scan.py -m 0`（`ScanRunner`） | Agent `run_dedup_org`（可选）+ 控制面 merge | Reconciler + inotifyd |
| UNISOC | `scan_log_gt.py -m sprd` | Agent `scan_result.py`（见 D7） | 本 ADR **不**扩 MTK Reconciler |

**禁止**：展锐机跑 MTK 工具；MTK 机跑 GT 工具；展锐采集目录跳过 `scan_result.py` 进 merge；**未分流的** `*_org.xls` 混进同一 merge 输入集。

**产物命名**：上送 NFS 的 `*_org.xls` **必须**带 `{host_id}_` 前缀（`dedup_scan._HOST_PREFIX_RE` 归因依赖此约定）。原生 `Result_None_None_MonkeyAEE_SPRD_{ts}_org.xls` **可**匹配 `*_org.xls` glob，但无前缀时 host 会被误解析为 `Result`——**禁止**裸名上送。

### D2：归档链与 Watcher 链分层

- **Watcher 层**（`PlatformCollector`、`job_log_signal`、DLE）：继续 ADR-0028；**本 ADR 不自动变更 #220**（见 D6）。
- **归档链**（采集 → 汇总 → upload → merge → jira）：按 **device.platform** 与产物路径分流；新增 Agent/控制面接口，**不**复用单一 `STP_DEDUP_SCAN_*` 切换两平台。

### D3：环境键与 fleet 键（Accepted 前须定稿）

| 角色 | MTK（现状） | UNISOC（新增，键名 Accepted 前定稿） |
|------|-------------|--------------------------------------|
| Agent 采集 | `STP_DEDUP_SCAN_PYTHON` / `_SCRIPT` | `STP_UNISOC_LOG_SCAN_PYTHON` / `_SCRIPT` |
| Agent 汇总 | `run_dedup_org`（同脚本 `-dedup_org`） | `STP_UNISOC_SCAN_RESULT_PYTHON` / `_SCRIPT` → `scan_result.py` |
| 控制面 merge | `STP_BACKEND_DEDUP_SCAN_*` | **同一** `start_log_scan -merge_files_list`；输入文件集按 B1 分流 |
| fleet `-side` | `STP_DEDUP_SCAN_TAG`（`_FLEET_ENV_KEYS` 单值） | **待裁定**：展锐是否共用 tag/shanghai，或需独立键（TOOLKIT §2.4 开放项） |

hot-update：UNISOC 路径键纳入 `STP_AGENT_*` 源键映射 + `AGENT_PATH_ENV_KEYS`（对齐 #295），**不得**写入 MTK 键。

### D4：展锐采集 Agent 化形态（**无终裁不得合 P1**）

| 选项 | 描述 | 倾向 |
|------|------|------|
| (a) 移植进 Watcher | 第三条采集路径 | **否决倾向**——Agent 包体与复杂度显著上升（TOOLKIT 原话）；非「与轮询模型冲突」 |
| (b) catalog patrol 脚本 | 同步快照式包装 | 可行；混平台 host 须 **per-device 路由**；需改造工具后台线程 |
| (c) 独立进程 + 产物收割 | 对齐 Jira/scan 外置工具 | **默认倾向**——混平台 host 可双进程并存 |

### D5：实施顺序

| 维度 | 顺序 |
|------|------|
| **运行时因果** | 始终 **采集阶段 → 汇总阶段 → upload → merge** |
| **工程落地** | TOOLKIT「落地批次 P2 先行」= **离线验证/开发先行**（历史问题包跑 `scan_result.py`）；**生产合入**须采集 Agent 化（落地批次 P1）+ 汇总接线齐备 |
| **合入门禁** | P2 spike 可在 Proposed 下；**P1 生产路径须 Accepted + D4 终裁** |

### D6：与 #220 / TOOLKIT G8 的关系

- 本 ADR **不**将 `STP_WATCHER_AEE_RECONCILE_PLATFORMS` 扩至 UNISOC。
- **归档链**（D1–D3）**不**以重议 #220 为前提（TOOLKIT G8 针对「Watcher 内嵌采集」路径）；#220 仅在 D4=(a) 或另立「展锐实时信号」ADR 时触发。
- 若未来要做展锐 Reconciler 等价物，**另开 ADR**，与 D4 裁定一并评审（不预设绑定 (a)）。

### D7：汇总阶段执行侧（MTK 对照，Accepted 前确认）

**MTK 对照（现状）**：

```text
Agent: start_log_scan -m 0 → *_org.xls → [run_dedup_org] → UploadManager({host_id}_*.xls)
控制面: start_log_scan -merge_files_list（同一脚本，不同子命令）
```

**展锐（本 ADR 倾向）**：

```text
Agent: scan_log_gt → 问题包目录 → scan_result.py → *_org.xls → UploadManager({host_id}_*.xls)
控制面: 同一 merge 工具与命令，仅 merge 输入文件集按 B1 分流
```

即 `scan_result.py` **≡ MTK Agent 侧 `run_dedup_org`**，**不是**控制面另起汇总服务。

**触发语义**（随 D4 终裁一并定稿）：`scan_result.py` 何时运行（随 `scan_now` 派发 / 随采集进程连续跑）取决于 D4 采集形态 (b)/(c)。

**FAILED PlanRun**：展锐 merge 分支同样继承 ADR-0028 D2——`PlanRun FAILED` 时 `merge_skip_failed_plan_run`（`dedup_scan.py:267-280`），不因平台例外。

### B1：混平台 PlanRun / merge 语义（**阻塞 Accepted**）

生产可出现「同一 PlanRun 跨 MTK host + UNISOC host」。须在 Accepted 前**三选一**写入不变量：

| 选项 | 含义 |
|------|------|
| **(i)** | PlanRun 平台同质（准入拒绝混平台设备集） |
| **(ii)** | 一 PlanRun **两次 merge**（MTK / UNISOC 各一输入集，各一 `merge_result_xls` 或等价 artifact） |
| **(iii)** | `dedup/{run_id}/` 路径分区（如 `{platform}/` 子目录）+ 每平台一次 merge |

B1=(iii) 时路径分区须**同时**覆盖 scan 上送（`dedup/{run_id}/{host_id}_*.xls`）与 merge 发布（现 `dedup/{run_id}/merge/`）——输入/输出同一层约定，裁定时一并钉死。

未选则 `saq_tasks` / `count_hosts_with_scan_artifacts` 无法设计。**倾向 (ii) 或 (iii)**——与混平台 host 事实兼容；(i) 与现网设备分布冲突风险高。

**混平台产物区分**（B1 选 (ii)/(iii) 时**必须**写入不变量；仅靠 `{host_id}_` 前缀不足——混平台 host 上 MTK/UNISOC 均上送为 `{host_id}_*.xls`）：

| 手段 | 示例 |
|------|------|
| 路径分区 | `dedup/{run_id}/{platform}/` 或 `dedup/{run_id}/{host_id}/{platform}/` |
| 文件名段 | 强制含 `_SPRD_` / `_AEE_TNE_` 等平台段，merge 输入集按 glob 过滤 |
| DB 元数据 | `plan_run_artifact` 增 `platform`（或等价字段） |

若 B1=(ii)：`find_fresh_merge_output_dir` / `_publish_merge_to_center` 现按**单次 merge 唯一最新输出目录**设计（`dedup_scan.py:501-550`、`:559-582`），须扩展为**两次 merge 产物均注册/发布**，不得只保留 mtime 最大者；中心路径现为单一 `dedup/{run_id}/merge/`（`copytree(..., dirs_exist_ok=True)` 会覆盖），B1 终裁时须约定防覆盖布局（如 `merge/mtk/`、`merge/unisoc/`）或等价 `storage_uri` 区分。

### B3：P2 spike 硬验收（不阻塞 Proposed）

- [ ] 15 列 `aeeexp` 同构
- [ ] `{host_id}_Result_*_org.xls` 可被 `_register_scan_artifacts_from_nfs` glob 消费
- [ ] `build_merge_argv` / `-merge_files_list` 单平台试跑
- [ ] 若 B1=(ii)：双 merge 冒烟；**两个** merge 产物目录均注册/发布（非仅 mtime 最新者）
- [ ] 去重后产物（无 `_org` 后缀）**不**进入 merge glob（TOOLKIT F1）

## 备选方案与权衡

| 方案 | 放弃原因 |
|------|----------|
| 手写 unisoc Reconciler（#73 原文） | 与已验收 toolkit 重复 |
| 共用 `STP_DEDUP_SCAN_SCRIPT` 按平台切换 | G9 参数契约不兼容；运维易混用 |
| 仅做汇总、不做采集（生产） | 无输入；仅适离线 spike |
| `PlatformCollector` stub 兼归档采集 | 协议面向元数据，非采集进程托管 |
| 按 host platform 分流 merge | 生产 6/30 host 混平台，host 级键不可靠 |

## 影响

- **代码**：`ScanRunner` 并列 UNISOC runner；`dedup_scan` / `saq_tasks` 按 B1 分流；`AGENT_PATH_ENV_KEYS` 扩展。完备性计数 `count_hosts_with_scan_artifacts` **按 host 去重**，不得假设每 host 固定 2 个 `*_org.xls`（MTK 可有 `_org` + `_org_dedup_org_*`；展锐路径通常 1 个）。
- **观测**：G10/G11（subtype / ZZ_INTERNAL）属**观测与风险卡增强**，纯归档 MVP **不依赖**。
- **运维**：混平台 host 须配置**双套**工具路径；按 PlanRun 设备集路由，非「整 host 单一平台」。
- **文档**：`AGENTS.md` 增双轨说明；#291 后 merge **无** `-merge_files` 回退（须同步修正 AGENTS 契约节）。

## 落地与后续动作

- [ ] 闭合 B1 + D3 键名 + `STP_DEDUP_SCAN_TAG` 裁定 → **Accepted**
- [ ] D4 终裁（含 D7 触发语义）→ P1 编码
- [ ] P2 spike（Proposed 下，按 B3 清单）
- [ ] P1 Agent 化 + Z258 真机验收
- [ ] #73 / #463 挂本 ADR 关闭路径

## 关联实现 / Issue

- #463；#73
- ADR-0025；ADR-0028（Watcher 层 only）
- `scan_runner.py`；`dedup_scan.py`；`agent_env_sync.py`
- toolkit：`stability_Monkey-Log-Scan-GT&SPRD`、`stability_Scan-Result-GT`
