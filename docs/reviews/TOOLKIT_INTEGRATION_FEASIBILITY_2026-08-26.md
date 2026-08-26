# automation-toolkit 合入与平台优化七方向可行性审查

- 日期：2026-08-26
- 性质：**可行性审查（背景研究，非 ADR、非设计定稿）**。回答「七个优化方向的可行性与重要性」，供后续各项 PRD / ADR / design 引用；进行中跟踪落 GitHub Projects 看板，**不在仓库内维护 roadmap**。
- 研究对象：外部仓库 `DUElost/automation-toolkit`（私有，gh REST 可读）四个工具族 × 平台现状（脚本管理 / Jira / 展锐 AEE / API 与 SOP）。
- 相关文档：[AGENTS.md](../../AGENTS.md)（scan/upload/merge 跨进程契约）、[aee/CLAUDE.md](../../backend/agent/aee/CLAUDE.md)（#220 白名单禁令）、[ADR-0028](../adr/ADR-0028-device-log-event-and-continuous-upload.md)、[ADR-0030](../adr/ADR-0030-multi-case-suite-management.md)、[MTBF_MULTI_CASE_RESEARCH_2026-08-19](./MTBF_MULTI_CASE_RESEARCH_2026-08-19.md)、[DOC-MAP](../DOC-MAP.md)。

## 0. 结论摘要（TL;DR）

1. **建议优先级**：方向 7（SOP/API）→ 方向 6（Jira 闭环）≈ 方向 3（脚本管理补全）→ 方向 5（android-tools 合入）→ 方向 4（展锐合入，**必须拆两步**）→ 方向 2 ≈ 方向 1（UI 风格，横切并行）。依据见 §5。
2. **三个关键发现**改变了各项的成本估算：
   - 展锐第二阶段工具（stability_Scan-Result-GT）产出 **MTK 格式 xls（表名 `aeeexp`、15 列）**，去重前 `*_org.xls` 与控制面 merge 链的输入 glob 及列格式**同构**；但去重后产物命名不含 `_org`，与「每 host 2 个 `*_org*.xls`」的构成不同，接入需按可配文件名模板对齐（F1，非零改造）。
   - Jira 提单管道**不是从零建**：控制面已有 `jira_run` 表 + `/api/v1/jira` 路由组（config-gated 子进程调用仓库外 Transsion/Tinno 工具）+ `{root}/jira/{run_id}/` 产物约定 + S/A/B 风险评级（F2）。
   - 脚本管理后端大半就绪（PUT/DELETE/versions API 均在），缺口集中在前端 UI 与内容上传下载通道（F3）。
3. **ADR 触发面收敛到一项确定项**：唯一确定要开的新 ADR 是方向 4-P1（展锐采集 Agent 化：架构形态选择 + 重议 #220 白名单禁令）；另有方向 6 两项**条件触发**的轻量 ADR（自动闭环策略、凭据托管，见 §6）。其余各项走 prd/design/Agent Note 常规流程或并入既有文档。
4. 访问性限制：用户提供的 Projects 看板链接不可达（404，私有看板），抽屉交互参考暂缺原始素材；技术侧无阻塞（前端已是 Radix + Tailwind 栈，Sheet/Drawer 是标准组件形态）。

---

## 1. 调研范围与方法

| 来源 | 可达性 | 用途 |
|------|--------|------|
| GitHub Projects 看板 `users/DUElost/projects/1/views/1` | **不可达**（网页 404；GraphQL API EOF） | 方向 1 的风格参考源缺失，待用户提供截图/邀请协作者 |
| `DUElost/automation-toolkit`（私有仓库） | `gh` REST 凭据可读（GraphQL 仍 EOF）；初稿基于本地克隆，复审时克隆已不在，全部主张已对照远端原文复核 | 方向 4/5/6 的工具族全量摸底（git tree + README + CLAUDE.md） |
| openrouter.ai | 公网可读 | 方向 2 的风格参考（深浅色双主题、卡片化、密度克制、导航分层） |
| 本仓库代码 | 只读探查 | 方向 3/6/7 的现状基线（file:line 证据见 §2 与附录 A） |

toolkit 仓库与本平台的关系：它是稳定性流水线工具的**上游孵化地**（README 自述「自动化测试工具集」），其中 `stability_Start-Log-Scan`（MTK 扫描工具）已被控制面经 `STP_BACKEND_DEDUP_SCAN_*` 以仓库外厂商工具形态调用；本次评估的是其余四个工具族的合入路径。

## 2. 逐项评估

### 2.1 方向 1：学习看板「抽屉风格」

| 维度 | 结论 |
|------|------|
| 可行性 | 技术上**高**；参考源**阻塞** |
| 重要性 | 低-中（纯 UX 改进） |

- 前端栈就绪：Radix UI 全家桶（`@radix-ui/react-dialog` ^1.1.23）+ tailwindcss 4 + cva，shadcn 式 Sheet/Drawer 属标准组件形态，`frontend/src/components/ui/` 下新增一个 `drawer.tsx`/`sheet.tsx` 即可，不影响既有 dialog 使用点。
- 参考素材缺失：看板 404 且 gh GraphQL 不可达。**解除条件**：拿到看板截图或协作者权限后再定交互细节；在此之前不立项。

### 2.2 方向 2：学习 OpenRouter 整体界面风格

| 维度 | 结论 |
|------|------|
| 可行性 | 中-高 |
| 重要性 | 中 |

- OpenRouter 参考点：双主题（已有 `ThemeToggle.tsx`）、卡片网格 + 密度克制、导航按 Product/Company/Developer 分层、状态徽标体系。
- 工程定性：整站翻新属中大型工程。建议**渐进式**：先沉淀全站 design tokens（色彩/圆角/密度/间距），再布局框架，最后逐页迁移——避免一次性大改造成 vitest 快照与视觉回归的集中爆炸。
- 现状缺口：全站无统一视觉基线，仅有 `design/mockups/plan-execute-v2/` 一个局部先例（G24）。

### 2.3 方向 3：STP 脚本管理优化完善

| 维度 | 结论 |
|------|------|
| 可行性 | **高** |
| 重要性 | **高**（唯一 action 类型 `script:<name>` 的地基；sha 漂移事故高发区；方向 5 的前置） |

现状是「NFS 目录扫描为主 + 手动 CRUD 兜底」双轨模型，后端完成度高：

| 能力 | 状态 | 位置 |
|------|------|------|
| 目录扫描 + 对账 + conflicts 检测 | ✅ | `services/script_catalog.py:163-311`（`scan_script_root`） |
| 新建版本（default_params 必填、版本内不可变） | ✅ | `api/routes/scripts.py:544`、L476-482 |
| 编辑元数据 / 软停用（被 PlanStep 引用时拦截）/ force_rebaseline 逃生阀 | ✅ 后端 | `routes/scripts.py:446` / L620 / L313-328 |
| capabilities 门禁（progress_stamps） | ✅ | `models/script.py:23-25` |
| 版本不可变校验（immutability gate） | ✅ | `tools/dev/check-script-version-immutability.py`（事故防线，incident-2026-07-31） |
| 内容上传/下载 API | ❌ | `nfs_path` 指向文件靠运维 NFS 手工放置（G1） |
| category 保持策略 | ❌ | scan 把 category 重置回硬编码 `device`（`script_catalog.py:58`），手动分类被覆盖（G2） |
| 前端新建/编辑/停用入口 | ❌ | `ScriptManagementPage.tsx` 仅列表+搜索+扫描+新建版本对话框；后端 PUT/DELETE 无 UI 入口（G4） |
| scan 冲突处置下钻 | ❌ | 前端仅 toast 计数（G5）；布局仅两层平铺/单入口/单 category（G3） |

**建议切分**：第一批纯前端（G4 + G5 下钻展示，后端零改动）；第二批 G1 上传下载 API（需审计 + sha 校验 + 与 immutability gate 协同——上传即改内容，必须走「新版本目录」而非覆盖，这是 ADR-0020 契约的延伸）；第三批 G2/G3（scan 语义调整，影响面大单独评审）。

### 2.4 方向 4：展锐平台日志监测与汇总去重合入

| 维度 | 结论 |
|------|------|
| 可行性 | 第二阶段工具（汇总去重 → 落地为 P2）**高** / 第一阶段工具（采集 → 落地为 P1）**中**（需 ADR） |
| 重要性 | **高**（展锐设备目前崩溃采集为零，是覆盖面空白；#73 延期项的实质推进） |

两个工具构成流水线上游两阶段，与平台链路天然同构：

```
Monkey-Log-Scan-GT&SPRD（采集：uniview 主信号 + dropbox 14 类辅 + 平台源增量）
        ↓ 每问题一文件夹（uniview 聚合包 tar.gz 不解包直存）
Scan-Result-GT（汇总去重：MTK 格式 xls，去重前/后两份）
        ↓ Result_*_MonkeyAEE_SPRD_{ts}.xls / *_org.xls
Jira 自动化提单 ←→ 平台现有 scan → upload → merge → jira 链路
```

> 术语约定：「第一/第二阶段**工具**」指工具在上图流水线中的位次（采集 → 汇总去重）；「P1/P2」指本方向的**落地批次**，执行顺序 **P2 先于 P1**（P2 = 第二阶段工具的服务化，P1 = 第一阶段工具的 Agent 化）。

**对齐资产（F1）**：

- Scan-Result-GT 输出 15 列 MTK 格式（Id/Path/Version/ExpTime/ExpClass/ExpType/CurProcess/Package/Detail/CausedBy/extraTag/Count/Activity/DeviceCount/Rom_Ram），表名 `aeeexp`；去重前产物 `*_org.xls` 落入 merge 输入 glob（`*_org.xls` / `*_org_*.xls`），列格式同构。注意平台「每台 host 上送 2 个 `*_org*.xls`」的构成是 `_org.xls` + `_org_dedup_org_*.xls`（现有工具 `-dedup_org` 模式产物，`dedup_scan.py:157`），GT 去重后产物命名不含 `_org`、不匹配 glob——文件名模板虽可配，仍是接入时要验证的对齐点，**非零改造**；
- 去重规则对齐 MTK `get_str_similar`（清洗后 difflib 比对）+ NE pc 指纹硬匹配，跨设备归并含 DeviceCount/Rom_Ram 多配置拼接——语义上就是控制面 merge 期待的输入；
- 45 个 pytest 全过，真机 Z2581/MyOS16 单设备/多设备/多版本验收通过（2026-08）；
- 采集侧取舍规则明确（有现场才留：uniview 聚合包/tombstone/detail/prelogs；空壳事件包舍弃）。

**平台侧缺口**：

| 编号 | 缺口 | 位置 |
|------|------|------|
| G6 | unisoc collector 为 stub（detect 恒 False、parse_metadata 抛错） | `backend/agent/aee/collectors/unisoc.py` |
| G7 | AEE 监测目录硬编码 MTK（`/data/aee_exp` + `/data/vendor/aee_exp`），展锐需换 uniview/dropbox/ylog 源 | `backend/agent/aee/CLAUDE.md`「监测目录」节 |
| G8 | 平台白名单 `STP_WATCHER_AEE_RECONCILE_PLATFORMS` 默认 MTK；#220 明令禁止扩白名单直至真实采集实现——**P1（采集 Agent 化）的启动前提就是重议此决策** | `backend/agent/job_session.py:292-326` |
| G9 | scan 工具链 `-m 0` 为 AEE_TNE 模式识别 MTK 产物；GT&SPRD 工具是独立采集路径（`-m auto/sprd/qcom/mtk/none`），不能直接套用现有 `STP_DEDUP_SCAN_SCRIPT` 参数契约 | 仓库外厂商工具 |
| G10 | 风险评级规则表 `_RISK_RATING_RULES` 按 `event_subtype` 定级，无展锐 subtype 映射（uniview/dropbox 类型段 → S/A/B 规则需新增） | `services/report_service.py` |
| G11 | DLE/extract 解析链 ZZ_INTERNAL 权威（ADR-0028）为 MTK 特化 | `device_log_event` 链路 |

**合入形态候选**（P1 的 ADR 要裁决的核心问题）：第一阶段工具是「PC 侧 adb 轮询常驻进程」（Python 3.9+ + adb，180s 补齐式轮询、后台线程按需导出、多设备 max_workers≤4），与 Linux Agent 服务化架构存在三种对接方式：

- **(a) 移植进 Agent Watcher**：在 inotifyd/Reconciler 双路径外为 UNISOC 增加第三条采集路径（轮询式）。最彻底，工作量最大，Agent 包体与复杂度上升；
- **(b) 包装为 catalog 脚本走 patrol step**：180s 轮询天然契合 `patrol_interval_seconds` 循环模型，stdout JSON 摘要可直接进 step_trace。代价：工具内部的后台导出线程模型与 patrol 同步执行语义冲突，需改造为单轮快照式；
- **(c) 独立进程 systemd 托管**，平台仅做触发与产物收割（对齐现有「仓库外厂商工具」模式，如 start_log_scan）。最快但脱离 Agent 生命周期管理（升级/健康/日志流均要另做）。

**拆步建议**：

- **P2 先行（不开 ADR）**：把 Scan-Result-GT 的解析/去重逻辑服务化——纯 Python、无设备依赖、自带测试，可作为控制面 merge 链的展锐分支或独立「汇总任务」接入；先用历史导出的问题包离线验证输出格式被 merge/jira 链正确消费。涉及 G10（subtype 规则）与 merge 的 `-side` 语义确认（`STP_DEDUP_SCAN_TAG` 是否需要展锐取值）。
- **P1 立项（必开 ADR）**：第一阶段采集的 Agent 化，裁决上述 (a)/(b)/(c) 并正式重议 #220（G8）。前置条件：P2 已验证 + 攒够真机问题包样本。

### 2.5 方向 5：稳定性测试工具合入（android-tools）

| 维度 | 结论 |
|------|------|
| 可行性 | 中 |
| 重要性 | 中-高（GPU/Sleep/PowerCycle 直接扩充专项矩阵，呼应方向 7 的「建立新的专项」SOP 需求） |

内容盘点：`stability_MTBF-Test`（离线老化执行包，runtask.xml 130 testpoint/137 testcase）、`stability_PowerCycle-Test`、`stability_Sleep-Test`、`stability_GPU-Test`（Antutu v10/v10_lite 按 RAM 分版）、`apps/`（OfflineScriptManager/PowerCycleManager/SleepTestManager APK 源码与构建脚本）、`vendor/`（apktool/jadx/签名工具）。执行包结构统一（deploy/run/stop 的 .bat/.ps1 + `lib.ps1` 共享库 + test-config.properties）。

| 编号 | 缺口/约束 | 说明 |
|------|-----------|------|
| G12 | Windows 执行环境 vs Linux Agent fleet | .bat/.ps1 + adb 编排需移植为 Python（平台脚本契约：env 注入 + stdout JSON）；移植本身机械，但 GPU 包依赖 Antutu APK 安装器等 Windows 侧假设需逐项核对 |
| G13 | suites XML ↔ test_suite/test_case 映射 | ADR-0030 P1 已实施 test_suite/test_case 实体与 import/export；PowerCycle/Sleep 的 task XML 可走同一导入通道，属**顺路验证**而非新建设计 |
| G14 | APK 类资产分发 | `support_files_manifest` 字段已有（`models/script.py:22`），但放置仍手工——与 G1 同根，方向 3 第二批做完此项自动解锁 |
| G15 | 与 MTBF P0/P1 设计重叠 | mtbf-p0-runner-design / p1-suite-management 已覆盖 MTBF 专项；合入前必须先对齐，避免「android-tools 版 MTBF」与「平台 MTBF runner」两套概念并存 |

**建议路径**：GPU/Sleep/PowerCycle 三个非 MTBF 执行包作为「新专项模板」的首批用户（同时喂给方向 7 的 runbook 写作），MTBF 执行包本身不合入（平台已有等价物）。

### 2.6 方向 6：Jira 自动化提单建设

| 维度 | 结论 |
|------|------|
| 可行性 | **高**（管道已在，性价比最高的一项） |
| 重要性 | **高**（稳定性测试的价值闭环终点：发现 → 汇总 → 提单） |

现状基线（F2）：

| 能力 | 状态 | 位置 |
|------|------|------|
| `jira_run` 表 + 迁移 | ✅ | `models/jira_run.py:32-58`、`alembic/versions/a6b7c8d9e0f1_add_jira_run.py` |
| 路由组（创建/列表/record/log 流/cancel） | ✅ | `api/routes/dedup.py:37` 起（prefix `/api/v1/jira`） |
| config-gated 调用仓库外 vendor 工具（子进程 + RunConsole 日志流） | ✅ Transsion/Tinno | `resolve_vendor_tool` L44-56（`STP_JIRA_<VENDOR>_PYTHON/_DIR` 未配则 503） |
| `{root}/jira/{run_id}/` 产物约定 | ✅ | SAQ `extract_task` + 手动端点（`dedup.py:504-545`） |
| 每 run JIRA draft（独立于提单） | ✅ | `api/routes/runs.py:154-183`（jira-draft 端点）；`build_jira_draft` 定义在 `services/report_service.py:545` |
| 项目映射字段 | ⚠️ 透传未填齐 | `models/project.py:52` `jira_project_key`（G17） |
| S/A/B 风险评级 | ✅ | `report_service.aggregate_risk_summary_from_signals`（观测层聚合） |

| 编号 | 缺口 | 说明 |
|------|------|------|
| G16 | Moto vendor 未接 | 三套工具中 Moto 用 PAT 认证（最简单），Transsion Cookie / Tinno P12 已在用；扩 vendor = 加一组 env 配置 + argv 组装适配 |
| G17 | `jira_project_key` 仅透传 | 项目登记簿（ADR-0029 P2.5）里补齐映射即闭环 |
| G18 | extract → 提单人工复核，无自动闭环 | 候选策略：风险评级 S/A 的 PlanRun 完结时自动生成 dry-run 提单草稿（复用 `build_jira_draft` + jira_run 表记 dry-run 态），人工确认后转正式——**自动直接建单不建议作为第一形态** |
| G19 | 回归验证能力未暴露平台 | 工具侧 regression executor/store/matcher/report 成熟（含测试），平台只暴露运行入口即可 |
| G20 | 凭据托管边界未决策 | 现状凭据全部工具侧自理（Cookie/P12/PAT 文件）；平台是否接管凭据（Secret 管理）需单独立项，短期维持工具侧自理并在 runbook 写明凭据存放位置约束 |

### 2.7 方向 7：平台可维护性（API 暴露 + SOP）

| 维度 | 结论 |
|------|------|
| 可行性 | **高**（成本最小） |
| 重要性 | **最高杠杆**（是方向 3/4/5/6 的操作载体；20 台生产机环境下 SOP 缺失是实际运维风险） |

现状：`/docs` `/redoc` `/openapi.json` 全开（`main.py:219`，限流豁免 `limiter.py:232`）；人机双鉴权通道齐备（`/auth/token` bearer 专为脚本客户端设计 `auth.py:326-376` + `X-Agent-Secret`）；外部管理面「复用 8000 端口 REST 不新增端口」在 MTBF 研究 §0.4 已有结论背书。

| 编号 | 缺口 | 说明 |
|------|------|------|
| G21 | 「新建专项/适配新项目」无 step-by-step runbook | 材料散在 DOC-MAP 阅读顺序 / project-taxonomy 设计 / mtbf-p0-p1 / honor-flash 各处；操作者视角的一份 runbook 不存在。骨架建议：项目登记（ADR-0029 登记簿）→ 脚本入库（catalog 两轨）→ 建 Plan（init/patrol/teardown）→ 试运行验证 → 专项上线检查单（对齐 `production-minimum-deployment-checklist.md` 风格） |
| G22 | `/docs` 公网可达且无关闭开关 | 控制面若对公网开放需 Nginx 层遮蔽，或 FastAPI 条件化 `docs_url=None`（env 开关）；二选一需小决策，倾向后者（自包含、不依赖部署侧配置纪律） |
| G23 | CI 未调 `run_gates.py` | 本地门禁矩阵与 CI required checks 仍是两套清单（`scripts/run_gates.py` 头注自述）；接入属 CI 工作项，与七方向无强耦合，可顺手排 |

## 3. 缺口汇总

G1-G5 脚本管理 · G6-G11 展锐 · G12-G15 android-tools · G16-G20 Jira · G21-G23 SOP/API · G24 UI 基线（明细见 §2 各表）。

对齐资产（toolkit 侧可直接复用，降低缺口成本）：F1 Scan-Result-GT 去重前输出与 merge 链输入同构（45 测试 + 真机验收；命名对齐注意点见 §2.4）；F2 Jira 路由组/vendor 工具/风险评级均已存在；F3 脚本管理 PUT/DELETE/versions 后端 API 就绪；F4 android-tools 四执行包结构统一（lib.ps1 模式），移植模式可复制；F5 Jira 三 vendor 工具自带回归验证与测试。

## 4. （预留）交叉分析

供后续多 agent 并行评审追加；当前单源调研无交叉结论。

## 5. 建议落地顺序

```
阶段 0（立即可做，成本最小杠杆最大）
  ├─ G21 新建专项 runbook 草案（operations/）
  ├─ G22 /docs 开关小决策 + 实施
  └─ 看板录入七方向（跟踪载体在 Projects，不入仓库）
阶段 1（价值闭环）
  ├─ 方向 6：G17 project_key 补齐 → G18 dry-run 自动草稿 → G16 Moto vendor → G19 回归入口
  └─ 方向 3 第一批：G4 前端 CRUD 入口 + G5 冲突下钻（后端零改动）
阶段 2（供给能力）
  ├─ 方向 3 第二批：G1 上传下载（走新版本目录，衔接 immutability gate）
  └─ 方向 5：GPU/Sleep/PowerCycle 三执行包移植（依赖 G1/G14；先做 G15 对齐确认）
阶段 3（覆盖面空白）
  ├─ 方向 4-P2：Scan-Result-GT 服务化 + G10 subtype 规则（不开 ADR）
  └─ 方向 4-P1：采集 Agent 化立项 → 开 ADR 重议 G8/#220
横切：G24 design tokens → 方向 2 逐页迁移 → 方向 1（等看板参考素材）
```

依赖关系要点：方向 5 依赖 G1（APK/support files 分发）；方向 4-P1 依赖 4-P2 的格式验证；方向 1 依赖外部素材到位；其余可并行。

## 6. 后续决策点（何时开什么文档）

| 项 | 决策触发条件 | 文档类型 |
|----|--------------|----------|
| 4-P1 展锐采集 Agent 化 | P2 合入且真机问题包样本积累充分 | **新 ADR**（形态 a/b/c 裁决 + 重议 #220）+ design |
| 4-P2 去重服务化 | 排期即做 | design + Agent Note |
| 5 android-tools 合入 | G1 解锁后 | 更新 mtbf-p1 design（suite import 通道）+ 各专项 prd→design 常规流程 |
| 6 自动提单闭环 | dry-run 试运行数据支持放开自动策略 | 轻量 ADR 或本文档增补章节 |
| 6 凭据托管（G20） | 出现第二个使用方或审计要求 | 独立小 ADR |
| 7 /docs 暴露（G22） | 控制面对公网开放前 | operations 说明 + env 开关（实施型，无需 ADR） |
| 3 scan 语义调整（G2/G3） | 第一批前端落地后 | design 评审（影响 scan 对账不变量） |
| 1/2 UI 风格 | tokens 定稿 | mockup + Agent Note（沿用 plan-execute-v2 先例） |

## 附录 A：证据索引（file:line）

| 主题 | 位置 |
|------|------|
| 脚本模型（manifest/capabilities/唯一约束） | `backend/models/script.py:9-42` |
| scripts 路由（双认证/scan/CRUD/versions/停用拦截/force_rebaseline/503） | `backend/api/routes/scripts.py:148-160,230-247,264-278,298,313-328,374,435,446,544,620` |
| 目录扫描（category 硬编码/两层平铺/单入口/capabilities.json） | `backend/services/script_catalog.py:58,61-78,81-90,108-135,163-311` |
| 前端脚本页（列表+搜索+扫描，无编辑） | `frontend/src/pages/scripts/ScriptManagementPage.tsx:30-43`、`router/index.tsx:35,88` |
| 平台识别（UNISOC 前缀族） | `backend/agent/device_platform.py:24,32-43,65`、`backend/api/routes/heartbeat.py:390-394`（上报平台落库；勿与仅 91 行的 `backend/agent/heartbeat.py` 混淆） |
| AEE 平台门禁白名单 | `backend/agent/job_session.py:292-326`（跳过事件 `aee_reconciler_skipped_platform`） |
| unisoc stub | `backend/agent/aee/collectors/unisoc.py`（全文 20 行）、注册表 `backend/agent/aee/collector.py:39-59` |
| Jira 路由组/config-gated/argv | `backend/api/routes/dedup.py:37,44-56,59-82,149,269,287,306,314,332` |
| jira_run 模型/迁移 | `backend/models/jira_run.py:32-58`、`alembic/versions/a6b7c8d9e0f1_add_jira_run.py` |
| jira 产物/draft/project_key | `tasks/saq_tasks.py`（extract_task）、`api/routes/runs.py:154-183`、`services/report_service.py:545`（`build_jira_draft`）、`models/job.py:32`、`models/project.py:52` |
| 风险评级 | `backend/services/report_service.py`（`aggregate_risk_summary_from_signals` + `_RISK_RATING_RULES`） |
| docs 开关缺失/限流豁免/auth token | `backend/main.py:219,238-250`、`core/limiter.py:232`、`auth.py:326-376`、`core/agent_secret.py` |
| 本地门禁矩阵 CI 未接 | `scripts/run_gates.py` 头注 |
| toolkit：采集工具（uniview 主信号/180s 补齐式/-m auto） | `DUElost/automation-toolkit:python-tools/stability_Monkey-Log-Scan-GT&SPRD/README.md`（gh api contents 复核） |
| toolkit：汇总去重（MTK 格式 15 列/NE pc 指纹/跨设备归并/45 测试） | `DUElost/automation-toolkit:python-tools/stability_Scan-Result-GT/README.md`（gh api contents 复核） |
| toolkit：Jira 三 vendor（Cookie/P12/PAT + 两阶段流程 + 回归验证） | `DUElost/automation-toolkit:python-tools/stability_Jira-Automation/CLAUDE.md`（gh api contents 复核） |
| toolkit：android-tools 执行包结构 | `DUElost/automation-toolkit:android-tools/{stability_MTBF-Test,stability_PowerCycle-Test,stability_Sleep-Test,stability_GPU-Test}/`（gh api contents 复核） |

## 附录 B：访问性限制记录

- Projects 看板 `https://github.com/users/DUElost/projects/1/views/1`：HTTP 404（私有）；`gh api graphql` 对该 projectV2 查询返回 EOF。解除方式：截图 / 邀请协作者 / 临时公开。
- `automation-toolkit` 为私有仓库，公网匿名访问一律 404。初稿依据本地克隆 `/mnt/automation-toolkit`（main @ 2026-08-26 推送）；同日复审发现该目录已空（克隆不在、非挂载点），全部 toolkit 主张改经 `gh api`（REST，凭据可读）对照远端原文复核一致——GraphQL 仍 EOF，REST 正常。后续引用以远端仓库为准。
