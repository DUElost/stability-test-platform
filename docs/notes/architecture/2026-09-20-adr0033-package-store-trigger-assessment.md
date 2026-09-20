# ADR-0033 §5.4 包存储触发条件评估（2026-09-20）

Status: implemented
Class: architecture

## Decision

**结论：未触发。** 三条 §5.4 触发条件在 2026-09-20 评估基线下均**不成立**；
包存储（`tar.gz` + `sha256` + `tools_cache/` + 注册流 + 周期巡检）**继续不排期**。
Phase 2 选项 A（#2819 / ADR-0033 v1.5）已落地 B5 薄适配器，**明确不做**包存储，与本结论一致。

本评估**不改** D0–D4 决策、不撤销过渡形态标注、不实现包存储或搬迁中心 `tools/`。

配套 Agent Note：[`2026-09-20-adr0033-package-store-trigger-assessment-agent-note.md`](./2026-09-20-adr0033-package-store-trigger-assessment-agent-note.md)。

### 评估基线

| 项 | 值 |
|---|---|
| 仓内 HEAD | `origin/main` @ `9e5ca074`（含 #2819 Merge） |
| ADR | ADR-0033 **Accepted v1.5**（落地状态：部分落地——B5 样板；包存储/Phase 3 未做） |
| 语义前提 | #2546 ownership、日志链 Contract、全局语义已合入；X2 地图与 B2≠B5 已钉死 |
| 中心盘 | 本评估环境**无** `/mnt/stp-aee/tools` 挂载；中心 `tools/` 现态引用仓内权威记述 + `.env.example` 路径约定 |

---

## 1. §5.4 触发条件对照表（有证据）

原文（ADR-0033 §5.4）：在下列条件**任一**出现前不排期包存储——

1. 出现**第二个**需要版本化分发 + 防篡改校验的 Tier 1/2 工具族（现有展锐三族之外的第一个）；或  
2. 出现因多机复制不一致、或源码目录被就地修改而导致的**真实事故**；或  
3. Phase 3 存量归一启动（包存储是该迁移的前置）。

| # | 触发条件 | 现态（2026-09-20） | 证据 | 判定 |
|---|---|---|---|---|
| **1** | 第二个需版本化分发 + 防篡改的 Tier 1/2 族 | 仍仅展锐三族走「中心 `tools/{name}/` 未打包源码 + 路径 env」过渡形态；无新族以该形态或包形态入场 | 例外表仍三族：`Start-Log-Scan` / `Monkey-Log-Scan-GT-SPRD` / `Scan-Result-GT`（ADR-0033 §5.4）；`STP_DEDUP_SCAN_*` / `STP_BACKEND_DEDUP_SCAN_*` 指向同一 `Start-Log-Scan`（B5 选项 A），**不是**第二族；`rg -l 'tools_cache\|tool_manifest' backend frontend/src tools scripts` → **0**；脚本树 34 族 / 184 版本目录仍在 `scripts/`（Tier 3 legacy，非 §5.4 Tier1/2 第二族） | **未触发** |
| **2** | 多机复制不一致 / 源码目录就地修改 → **真实事故** | 无针对中心 `tools/` 源码目录复制漂移或就地篡改的事故闭环记录 | 仓内 `docs/operations/incident-*` 覆盖主机硬挂 / xHCI / **脚本 sha 漂移派发中断**（2026-07-31，对象是 Git `scripts/` + DB catalog，**不是**中心 `tools/` 包）；未检索到 `tools/` 多机不一致导致的生产事故 issue | **未触发** |
| **3** | Phase 3 存量归一启动 | Phase 3 **未启动**；v1.5 / #2819 明示不做包存储与 Phase 3 | ADR-0033 头部「落地状态」；Agent Note `2026-09-19-adr0033-phase2-option-a`「明确不做」；Epic #745 已 Closed 但清单 Phase 3 项未开工（关闭 ≠ Phase 3 启动） | **未触发** |

### 相邻现态（不升格为已触发，但写入纪律面）

| 对象 | 形态 | 与 §5.4 关系 | 处理 |
|---|---|---|---|
| 展锐三族 + `STP_UNISOC_*` / `STP_AGENT_UNISOC_*` | 中心 `tools/{name}/` 源码目录 + 已登记路径键 | **唯一正式 legacy 例外** | 维持；禁止扩散 |
| `STP_*DEDUP_SCAN_*` | 指向 `Start-Log-Scan`（控制面 B5 / Agent scan） | 同属已登记三族之一 | 维持；不得借此引入新族路径键 |
| `STP_JIRA_<VENDOR>_{PYTHON,DIR}` | 厂商工具目录 + 私有 env（代码 `backend/api/routes/dedup.py`；引入于 2026-06 ADR-0025/#25，早于 §5.4） | **非**中心 `tools/{name}/` 登记例外；env 清单仅登记 `STP_JIRA_BASE_URL`/`TOKEN`，**未**登记 vendor DIR 键 | **不**计为触发条件 1；属 §5.6-2「过渡形态扩散」的**相邻风险**——禁止再增 vendor / 新 DIR 键；Phase 3 Jira 迁入时走 Contract+包，不在本评估排期 |
| `STP_FLASH_TOOL_DIR` | Tier 3 刷机可选覆盖（`flash_firmware` 脚本内） | D1 v1.3 已补登记刷机；§5.4 明文 Tier 3 不引入新工具私有路径变量为方向 | 不触发 Tier1/2 包存储条件；禁止把 flashtool 拷进中心 `tools/` 当第二套过渡例外 |

### 中心存储 `tools/` 现态（文档权威；本环境无挂载）

| 面 | 记述 |
|---|---|
| 布局 | `docs/design/2026-storage-roles-and-aliases.md`：现态 = `tools/{name}/` 版本化**源码目录**；终态 = `tools/{name}/{version}.tar.gz` + sha256（条件落地） |
| 族名 | ADR-0033 §5.1 / §5.4：`Start-Log-Scan`、`Monkey-Log-Scan-GT-SPRD`、`Scan-Result-GT` |
| 调用 | Agent / 控制面经路径 env；无 `tools_cache/`、无 tarball 注册流、无周期 sha 巡检代码 |
| 示例路径 | `backend/.env.example`、`backend/agent/.env.example` 指向 `/mnt/stp-aee/tools/...` 或 toolkit 路径 |

---

## 2. 结论与动作

| 项 | 内容 |
|---|---|
| **总判** | **未触发**（非「条件触发」——§5.6-2 复议门槛亦未因「第二个中心 `tools/` + env 族」成立而强制提前落地；Jira DIR 仅记为相邻纪律项） |
| **即刻动作** | 强化过渡纪律（下节）；索引锚指向本文；**不**开包存储实现 PR |
| **若未来触发** | 最小落地切片建议见 §4（另开 PR） |

---

## 3. 未触发时的过渡纪律强化

1. **禁止扩散例外**：不得把新 Tier 1/2 族放入中心 `tools/{name}/` 未打包源码 + 新路径 env；第二套即踩 §5.6-2，须先复议包存储而非默许。  
2. **禁止新增工具私有 env**：不得新增 `STP_*_SCRIPT` / `STP_*_DIR` / `STP_*_PYTHON` 类工具路径键（含新 `STP_JIRA_<VENDOR>_*`）；现有 UNISOC / DEDUP 键以 `environment-variables.md` 为准。  
3. **Phase 2 ≠ 包存储**：B5 `DedupMergeEngine`（选项 A）只收 argv 接缝；**不得**借「已有适配器」偷渡 tar.gz / `tools_cache`。  
4. **站点复制**：多站仍可「每站中心存储各有一份源码目录」过渡运维；**不得**把「人肉 rsync `tools/`」写成终态——终态仍是触发后的包存储（见站点复制拆解层 B）。  
5. **日志链 Contract**：B2（GT）与 B5（`start_log_scan` merge）宿主与工具边界不变；包存储只改**分发载体**，不改行为权威（ADR-0032）。

---

## 4. 若触发时的最小落地切片（本 PR 不实现）

仅当 §5.4 任一条件成立后另开 PR，建议最小切片：

1. 选定**一个**族（优先已登记三族之一，如 `Start-Log-Scan`）做 `tools/{name}/{version}.tar.gz` + `content_sha256` 登记样板；  
2. Agent/控制面拉取 → `tools_cache/{name}/{version}/` + sha256 核验（只读路径切换，保留 env 回退一个版本窗口）；  
3. CI：manifest/schema lint（Git 侧）；存在性/sha 巡检（有 NFS 的注册/健康路径）；  
4. **不做**：全族搬家、Phase 3 Jira 全迁、目录大重构、Web 工具管理面板。

---

## 5. 与多站点复制、日志链 Contract 的关系（一句话）

> 包存储是多站点「工具资产可版本化发布」的终态出口，也是日志链 Tier1/2 外置引擎的分发载体；在 §5.4 未触发前，日志链行为面（Contract / 全局语义 / ADR-0032）与结构过渡例外（三族源码目录）继续分立——**复制站点靠登记纪律，不靠提前实现包存储。**

---

## Alternatives

| 选项 | 为何不选（本轮） |
|---|---|
| 判「已触发」并立刻排期包存储 | 三条原文条件均无证据成立；与 v1.5「不做包存储」冲突 |
| 因 Jira `STP_JIRA_*_DIR` 判「条件触发 / §5.6-2 强制提前」 | Jira 早于 §5.4、非中心 `tools/{name}/` 例外扩散；应登记纪律 + Phase 3 迁入，而非本轮强推包存储 |
| 本 PR 做极小 tar.gz 证明切片 | 用户默认不做代码实现；未触发时证明切片会变成无出口的半成品债 |
| 改写 §5.4 触发条件本身 | 属方向级修订，需独立 ADR/复议；本评估只对账现态 |

## Verification

- 仓内：`git fetch` + `origin/main` @ `#2819`；ADR-0033 头部 v1.5 / 部分落地  
- 代码：`backend/services/dedup/` 存在；`tools_cache`/`tool_manifest` 实现树零命中  
- 路径键：`unisoc_scan_runner` / `agent_env_sync` / `dedup_scan` / `dedup.py` resolve_vendor_tool  
- 文档：`2026-storage-roles-and-aliases` tools 段；`2026-log-chain-global-semantics` §7 过渡例外；Contract 指 `tools/` 角色  
- 门禁：本 PR 范围 `python3 scripts/run_gates.py check:quick` → **OK（12 gates）**
- **未做**：未挂载生产/中心盘枚举 `tools/` 实目录；未跑真机 / backend 全量 pytest

## Revisit

- §5.4 任一条件成立 → 撤销「不排期」、按 §4 最小切片另开 PR  
- 第二个中心 `tools/{name}/` + 新路径 env 族出现 → §5.6-2 先行复议  
- Phase 3 / Jira 全迁启动 → 包存储为前置，本评估自动过期  
- 现场若报告 `tools/` 多机漂移事故 → 补证据后重评条件 2
