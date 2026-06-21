# ADR-0025 去重子阶段集成设计确认（4 项待定项定稿）

> 日期：2026-06-16
> 关联：[ADR-0025](./adr/ADR-0025-phase4-architecture-alignment.md) D4、[方案 C 设计](./design/2026-plan-c-storage-and-access.md)、[主链路设计](./design/01-execution-pipeline.md) §dedup
> 状态：**Accepted（实施中）** —— 4 项落地形态 2026-06-16 定稿；§5 三项运营选择 2026-06-18 追认建议默认值并立项开工（见 §5）。首接厂商：**Transsion + Tinno**（2026-06-16 细化）。架构细化为「薄 CLI 封装」模型，见 §7。
> 外部工具：`F:\automation-toolkit\python-tools\stability_Start-Log-Scan`（扫描去重）、`stability_Jira-Automation`（按厂商批量建单）

---

## 0. 勘察结论（决策依据）

| 事实 | 来源 | 影响 |
|------|------|------|
| Start-Log-Scan **无打包 exe**，从源码跑（Py3.7/3.8 + xlwt/xlrd） | 工具目录无 dist/exe，仅 PyInstaller hook 源码 | 调用形态走 subprocess + 独立解释器 |
| 工具按 place(SH/SZ/CQ)+scan_type(shanghai/factory) 多 config profile，自身也能 submit_jira(→Jenkins) | `config/config_aee_*.json` | place/side 即 profile 选择；走人工闸口须 submit_jira=false |
| Jira-Automation 是**两阶段、按厂商**(Transsion/Tinno/Moto，认证各异)，stage1 消费 `Result_*.xls` | `stability_Jira-Automation/CLAUDE.md` | 平台上传的 .xls 正是 Start-Log-Scan 产物，直接喂 stage1 |
| 平台 jira-draft 是从 report_json 生成的**轻量草稿** | `runs.py:155` `build_jira_draft(report)` | 与 MTK 去重链互补，非竞争 |
| 后端 Host 无 place/site 字段、Plan 仅 watcher_policy、无配置表(SettingsPage 静态) | `models/host.py` / `models/plan.py` | place/side 以部署级 env 落地,不加 schema |
| 平台单局域网单站点、尚未真实落地 | project-vision / ADR-0025 背景 | 避免过早 per-Plan/Host 配置粒度 |

---

## 1. 端到端链路（确认后）

```
[Sprint 2 已落地] Agent LogArchiver → run 日志整包归档 NFS archives/<date>/<job_id>/

[去重子阶段，本设计]
控制平面(Windows)：
  ① 触发(已定)：PlanRun 终态自动 + 详情页「重跑去重」手动按钮
        │  subprocess(工具自带解释器) 调 start_log_scan.py
        │    -d_abs <NFS archives 路径> -m 5 -p <place> -tag <profile> -end
        │    （submit_jira=false：强制走人工闸口，不经工具自身 Jenkins 提单）
        ▼
  ② 产物：Result_*_org.xls(原始) + Result_*.xls(去重) → 存为 PlanRun JobArtifact(NFS)
        │  前端详情页挂下载链(复用 Sprint 3 下载入口)
        ▼
  ③ 人工审核闸口(已定)：运维下载 Result_*.xls 复核(可在工具外手工修订)
        ▼
  ④ 平台「.xls 上传接口」：上传经审核的 .xls
        │  subprocess(厂商工具解释器):
        │    stage1 generate_<vendor>_jira_upload_list.py --add-main-excel <上传.xls>
        │      → JIRA_Upload_List_*.xlsx
        │    stage2 create_<vendor>_jira_batch_from_excel.py(默认 dry-run，确认后建单)
        ▼
  ⑤ 返回建单结果(已建 issue / dry-run 摘要) → 前端展示
```

---

## 2. 四项待定项 — 设计确认

### 项 1：调用形态 —— subprocess + 工具自带解释器（非 import，非 exe）

**决策**：控制平面后端通过 `subprocess` 调用工具脚本，使用**工具自己的 Python 解释器/venv**（Py3.7/3.8 + xlwt/xlrd），而非 import 进后端进程（后端为 Py3.11，依赖与版本均不兼容），也不先做 exe 打包。

**理由**：
- import 不可行：后端 Py3.11 vs 工具 Py3.7/3.8 + xlwt/xlrd，且工具有 modules/ 重结构 + 解压子工具
- subprocess + 独立解释器：工具**零改动**、依赖完全隔离、失败被进程边界隔离
- exe 打包（.spec/hook 已具备）列为**可选后续**：若多机分发解释器管理麻烦再打包

**配置（env）**：
```
STP_DEDUP_SCAN_PYTHON     工具解释器路径（如 D:\tools\py38\python.exe）
STP_DEDUP_SCAN_SCRIPT     start_log_scan.py 绝对路径
STP_JIRA_TOOL_PYTHON      厂商 Jira 工具解释器路径
STP_JIRA_TOOL_DIR         厂商工具目录（含 generate_/create_ 脚本）
```
未配置 → 去重/提单入口在前端 disabled + 后端 409「未配置去重工具」，不影响主链路。

### 项 2：.xls 上传接口契约

**端点（新增，control-plane）**：
| 端点 | 作用 |
|------|------|
| `POST /api/v1/plan-runs/{run_id}/dedup/scan` | 触发扫描去重(终态自动 + 手动重跑共用)；异步起 subprocess，产 Result_*.xls 存 JobArtifact |
| `GET /api/v1/plan-runs/{run_id}/dedup/status` | 扫描进度 + 去重产物列表(下载用) |
| `POST /api/v1/plan-runs/{run_id}/dedup/jira-upload` | **人工审核后**上传经复核的 .xls(multipart)；存 NFS → 触发 Jira-Automation |
| `GET /api/v1/plan-runs/{run_id}/dedup/jira-result` | 建单结果(issues / dry-run 摘要) |

**jira-upload 契约**：
- 入参：multipart `file`(.xls，运维复核/修订后的 Result_*.xls) + `dry_run: bool`(默认 true)
- 后端：存上传 .xls 到 NFS → subprocess stage1(`--add-main-excel <xls>`) → stage2(`create_..._batch`，`dry_run` 控制是否真建单)
- 厂商：由 `STP_JIRA_VENDOR ∈ {transsion,tinno,moto}` 选择工具(认证各异，见 §3)
- 返回：`{stage1_ok, upload_list_uri, stage2: {dry_run, created:[...] | preview:[...]}}`
- **不自动提单**：默认 `dry_run=true`；运维显式 `dry_run=false` 才真建单（人工闸口的第二道）

**前端**：PlanRun 详情页新增「去重报告」区——扫描状态 + Result_*.xls 下载 + 上传组件 + dry-run/建单结果。

### 项 3：place/side 配置来源 —— 部署级 env（不加 schema）

**决策**：当前单站点单厂商，place/side/vendor 走**部署级 env**，**不**新增 Host.place / Plan.scan_config 字段。

```
STP_DEDUP_PLACE       扫描地点标识(SH/SZ/CQ) → start_log_scan -p
STP_DEDUP_SCAN_TAG    config profile / scan_type(shanghai/factory) → -tag
STP_JIRA_VENDOR       transsion | tinno | moto
```

**理由**：Host 无 place 字段、Plan 无 scan 配置、无配置表；平台单局域网单站点、尚未落地——按需最小化，避免过早 per-Plan/Host 粒度（与 ADR-0025「不为不存在的需求建抽象」一致）。

**升级路径（记录，不实现）**：多站点 → Host 增 `place` 列；多厂商/多专项差异化 → Plan 增 `scan_config` JSONB（参照 watcher_policy 模式）。届时 env 退为默认值。

### 项 4：与 runs.py jira-draft 的关系 —— 共存互补，不替换

**决策**：保留 `runs.py` 的 `jira-draft`（`build_jira_draft(report)`）；去重→Jira-Automation 链作为**独立的生产批量提单路径**新增，二者并存。

| 能力 | jira-draft（保留） | 去重→Jira-Automation（新增） |
|------|-------------------|------------------------------|
| 输入 | 平台 report_json（单 Job 聚合） | NFS crash 日志整包（MTK AEE/TNE 原始） |
| 处理 | 轻量草稿字段拼装 | 13 类异常识别 + 90% 相似度去重 |
| 产物 | JSON 草稿（页面展示） | Result_*.xls → 厂商批量建单 |
| 规模 | 单 Job 即时 | PlanRun 级批量 + 人工闸口 |
| 定位 | 快速预览/单点提单参考 | 稳定性专项生产提单主路径 |

**理由**：输入与规模根本不同（report_json vs 原始 crash 日志），jira-draft 是即时轻量预览，去重链是重型生产流程；替换会丢失 jira-draft 的即时性。文档化两者角色即可。

---

## 3. 厂商 Jira 工具差异（影响项 2/3 实现）

| 厂商 | 认证 | stage1 / stage2 脚本 |
|------|------|---------------------|
| Transsion | Cookie / 账号 | `generate_transsion_jira_upload_list.py` / `create_transsion_jira_batch_from_excel.py` |
| Tinno | P12 + Cookie（旧链需 OpenSSL 1.1.1g 降 SECLEVEL） | `generate_tinno_jira_upload_list.py` / `create_tinno_jira_batch_from_excel.py` |
| Moto | Personal Access Token（edart EKLAMUC） | `generate_*` / `create_*`（同构） |

凭据经 env/密钥注入工具，**不入平台库**（遵循 ADR-0024 安全基线）。首版建议先接**一个**主用厂商（待用户指定），其余按同构扩展。

---

## 4. 分阶段实施（2026-06-18 立项，按阶段推进）

| 阶段 | 内容 | 依赖 | 状态（2026-06-18） |
|------|------|------|------|
| D-a | 扫描集成：dedup/scan + dedup/status 端点 + subprocess 调 start_log_scan(submit_jira=false) + Result_*.xls 存 JobArtifact + 终态自动/手动触发 | 工具解释器路径 env | **待补**（见 issue #20） |
| D-b | 提单集成：jira-upload + jira-result 端点 + subprocess 调厂商 stage1/2(dry_run 默认) | 选定厂商 + 凭据 env | **已落地**（`backend/api/routes/dedup.py` + `RunConsole`，对应 §7 收敛后的 jira-run 端点） |
| D-c | 前端：PlanRun 详情页去重报告区(扫描状态 + 下载 + 上传 + dry-run/结果) | D-a/D-b | **部分落地**（`JiraSubmitPanel` + `LiveConsole` 已实现提单面板；扫描状态/下载链待 D-a） |
| D-d | 测试 + 灰度：env 缺失降级 / dry-run / 单厂商端到端 | — | **进行中**（纯函数/组件单测已落地；HTTP 集成测试待补，见 issue #24） |

---

## 5. 风险与已拍板决定

**已拍板（2026-06-18 追认建议默认值，代码已按此落地）**：
1. **首接厂商**：**transsion + tinno**（`dedup.py:30` `_VENDORS = {"transsion", "tinno"}`；moto 同构扩展，后续按需）
2. **stage2 默认**：**`dry_run=true`**（`dedup.py:83` `dry_run: bool = Form(True)`；符合人工闸口第二道）
3. **扫描触发**：**全局 env 开关默认开**（`STP_DEDUP_AUTO_SCAN`，D-a 落地时实现；当前 D-b 提单不涉及）

**风险**：
| 风险 | 缓解 |
|------|------|
| 工具解释器/依赖环境漂移 | env 显式指定解释器；缺失则入口 disabled + 409，不影响主链路 |
| 厂商凭据泄露 | 凭据走 env/密钥，不入库不入日志（ADR-0024 基线） |
| 扫描长耗时阻塞 | dedup/scan 异步 subprocess + status 轮询，不阻塞请求 |
| .xls 格式漂移导致 stage1 失败 | 上传即校验 + stage1 失败回显，不静默；保留原始 Result 供重试 |
| 单站点 env 配置 → 多站点 | 升级路径已记录（Host.place / Plan.scan_config），届时平滑迁移 |

---

## 6. 对 Sprint 2 计划 §5.4 的收口

§5.4 四项「仍待定」→ 本文档全部给出 recommended 决策：调用形态(subprocess+独立解释器)、上传接口契约(4 端点 + dry-run 闸口)、place/side(部署级 env)、与 jira-draft 关系(共存互补)。「首接厂商 / stage2 默认 / 触发范围」3 个运营选择于 2026-06-18 追认建议默认值（见 §5），设计定稿闭环。

---

## 7. 细化：薄 CLI 封装模型（2026-06-16）

用户定位：工具端主体链路（`stability_Jira-Automation`，Transsion/Tinno）**已成熟、单独命令行可用**。平台不重造逻辑，只做薄封装三件事：**① 一个上传接口 · ② CLI 参数 → 可视化菜单 · ③ 平台端实时日志输出**。据此把 §2 的 4 端点收敛为：

```
PlanRun 详情页「Jira 提单」面板
  [可视化菜单] 厂商(Transsion/Tinno) + 运行期参数(input .xls / dry_run / place / reporter…)
  [上传] Result_*.xls(运维复核后)
        │
  POST /plan-runs/{id}/dedup/jira-run  (multipart .xls + 菜单参数)
        │  后端 subprocess(厂商自带 venv，正确 cwd):
        │    stage1 generate_<vendor>_jira_upload_list.py --add-main-excel <上传.xls>
        │    stage2 create_<vendor>_jira_batch_from_excel.py  (dry_run 默认 true)
        │  stdout 逐行 → SocketIO room `dedup:{plan_run_id}`
        ▼
  [实时日志] 前端 XTerminal 订阅该 room 渲染(复用现有 step_log/xterm 设施)
  [结果] 运行结束回填 result/ 下 JSON 摘要(已建 issue / dry-run 预览)
```

### 平台已具备的可复用件
- **实时日志**：`socketio_server.py` 已有 `step_log` → `job:{id}` room 广播 + `log_writer` 落盘；前端 `XTerminal`(xterm.js) 已成熟。新 room `dedup:{id}` 同构复用。
- **上传/下载**：JobArtifact + NFS + 下载端点(Sprint 2/3)已可承载 Result_*.xls 与 JIRA_Upload_List。
- **解释器自带**：Tinno 目录内含 `venv38/` + `890177.p12`；Transsion 有 `requirements.txt`(各自建 venv)。subprocess 直接用各厂商 venv，**项 1 的解释器问题天然解决**。

### 诚实的难点判断（「应该不存在困难吧」→ 总体不难，但有 4 处需预算）

| # | 难点 | 程度 | 说明 |
|---|------|------|------|
| 1 | **控制面 subprocess → 实时日志流** | 中 | 日志设施(xterm+SocketIO+落盘)齐备，但现有是 **Agent 喂** 的；新增「Popen + 独立线程逐行读 stdout → emit room」。Windows 下注意 **stdout 编码(GBK/UTF-8)** + 行缓冲(`-u`/`bufsize=1`) + 运行生命周期(取消/超时) |
| 2 | **Transsion 凭据时效** | 中 | Cookie/账号认证 → **cookie 会过期**。独立使用时人工刷新；平台驱动时新鲜 cookie 从哪来？建议菜单加 cookie 字段(敏感，不落库不落日志，仅本次 subprocess env 注入) |
| 3 | **Tinno P12/旧链** | 低-中 | 自带 `venv38` + `890177.p12` → 在工具 cwd 内用其 venv 运行即可，**相对自洽**；仅需确保平台机器能加载该 P12(旧链 SECLEVEL 问题工具内已处理) |
| 4 | **参数菜单 ↔ 工具 CLI 漂移 + 共享目录并发** | 低 | 菜单只暴露少量运行期参数(input/dry_run/place/reporter)，海量映射表仍留工具 config/；工具更新 CLI 时菜单需跟。工具 log//result/ 是共享目录 → 同厂商**串行执行**(一次一个 run)避免互踩 |

**结论**：方向成立、平台积木齐全，不存在架构级困难。真正要花功夫的是 #1(subprocess 流式日志 + Windows 编码)与 #2(Transsion cookie 新鲜度的 UX/注入)。Tinno 因自带 venv+P12 反而最自洽。这些都是工程细节而非阻塞项。

### 凭据处理原则（沿用 ADR-0024 安全基线）
- cookie / token / P12 口令：菜单输入或 env 注入 subprocess，**不入库、不入日志、不回显**
- P12 文件：留在工具目录(已 gitignore)，平台只引用路径不复制

---

## 8. 难点 #1 落地方案 A：控制面 subprocess → 实时日志流（in-platform）

### 8.1 复用的现成件（桥已存在）
- `socketio_server.py:526 capture_main_loop()`（main.py:111 启动期已调用）+ `:532 schedule_emit(event,data,room)` → `run_coroutine_threadsafe`：**任意线程可安全 emit**
- `/dashboard` room subscribe/unsubscribe + 前端 `useSocketIO`(断线重订阅) + `XTerminal`(xterm.js)
- → 子进程 reader 线程直接 `schedule_emit("dedup_log", {...}, room=f"dedup:{run_id}")`，无需新传输层

### 8.2 架构
```
POST /plan-runs/{id}/dedup/jira-run → DedupRunRegistry.start(同 plan_run 串行,409 互斥)
  → 落上传 .xls 到 NFS → spawn daemon 线程
_run_subprocess():
  Popen([vendor_venv_python, "generate_..._list.py","--add-main-excel",xls],
        cwd=vendor_tool_dir, stdout=PIPE, stderr=STDOUT, text=True, bufsize=1,
        encoding=STP_DEDUP_LOG_ENCODING, errors="replace",
        env={**creds_env,"PYTHONUNBUFFERED":"1","PYTHONIOENCODING":"utf-8"},
        creationflags=CREATE_NEW_PROCESS_GROUP)
  for line in proc.stdout: 落盘 + 批量 schedule_emit("dedup_log",room=dedup:{id})
  proc.wait() → stage2(dry_run) → schedule_emit("dedup_status",...)
```

### 8.3 三个真正的工程点
1. **编码**：MTK 工具 Windows 常 GBK → `encoding=STP_DEDUP_LOG_ENCODING`(默认 utf-8,可 gbk)+`errors="replace"`；子进程 `PYTHONIOENCODING=utf-8`+`PYTHONUNBUFFERED=1`
2. **节流**：`bufsize=1` + reader 批量(≤100ms/≤50 行)合并 emit,避免 SocketIO 洪泛
3. **取消/超时**：注册表持 Popen；`CTRL_BREAK_EVENT`/`psutil` 杀进程树(沿用 Agent 经验)+ 超时兜底

### 8.4 断线补齐（live + 文件 replay 双模，对齐既有 job 日志）
落盘 + `GET .../jira-run/{run_id}/log?from_seq=N`；前端先 GET 回填→再接 SocketIO 增量；`seq` 去重补缺。

### 8.5 端点 + 前端 + 工作量
端点：jira-run(起) / log(replay) / cancel / status + room `dedup:{id}`。前端：菜单+上传+`XTerminal`(复用)。后端 ~270 行、前端 ~200 行，无架构级新增。

> 本方案 A 是「平台自己跑工具」；用户提出的 Jenkins 复用见 §9。

---

## 9. Jenkins 式「web 实时日志」——能力缺口与可复用方案

### 9.0 缺口精确界定（用户诉求）
用户看中 Jenkins 的**「web 端实时打印命令执行日志」**，并指出当前项目缺乏。判断准确，需界定：
- 平台**已有**：Agent 设备脚本执行的实时控制台（`step_log`→`XTerminal`，仅 device job）
- 平台**缺乏**：通用的**「控制面跑任意命令 → web 实时控制台」**（Jenkins 招牌：build params 表单 + progressiveText 控制台）
- 缺口属实，但**积木已齐**（`schedule_emit` + xterm + 落盘 + SAQ），缺的是把它们组装成可复用的控制台运行抽象

### 9.1 勘察：Jenkins 对新工具是遗留路径
- 旧：`Start-Log-Scan` 的 `send_jira_request` → POST `AutoAeeLog/buildWithParameters`（Jenkins 中介）
- 新（用户要接的 `stability_Jira-Automation`）：日志实证直连 `jira.transsion.com/rest/api/2/`，**不经 Jenkins**
- ⇒ 用真 Jenkins 需把新工具**重新包成 Jenkins job**，而非沿用 AutoAeeLog

### 9.2 三方案（聚焦「web 实时日志」诉求）
| 方案 | 实时控制台来源 | 新增外部依赖 | 复用面 | 代价 |
|------|---------------|-------------|--------|------|
| B1 真 Jenkins | progressiveText API（现成） | **是**（平台↔Jenkins + token + 可达 + 维护 job） | 仅 Jenkins job | 新工具需重包 job；多一跳；与 ADR-0025「最小外部基础设施」相悖 |
| **B2/B3 平台内建 `RunConsole`（推荐）** | `schedule_emit`→xterm（已具备）+ SAQ 作业语义 | **否**（全栈内件） | **全平台**控制面命令 | 自建 RunConsole（后端 ~250 + 前端 ~180 行），一次投入复用 |

### 9.3 可复用抽象 `RunConsole`（dedup 只是首个消费者）
```
RunConsole.start(cmd, cwd, env, run_key) → run_id   # SAQ task 或 daemon 线程承载
  Popen(bufsize=1, encoding, errors=replace, NEW_PROCESS_GROUP)
  reader 线程逐行 → 落盘 + 批量 schedule_emit("console_log", room=console:{run_id})
  退出 → schedule_emit("console_status", {exit_code})
cancel/status/log(from_seq) 四通用操作；前端 <LiveConsole runId>(复用 XTerminal)
```
一次建成后，备份/演练/任意控制面命令均可复用同一控制台，平台补齐此前缺失的「Jenkins 式 web 实时日志」通用能力。

### 9.4 推荐 B2/B3（平台内建，不引 Jenkins）
契合 ADR-0025 单控制平面、最小外部基础设施基调；用户要的「web 实时日志」=xterm+行级流（平台已有），只差通用控制面 runner。**仅当**组织已强制以 Jenkins 为执行底座、或合规要求不在控制面跑工具时，才退选 B1（包 Jenkins job + 平台瘦客户端）。

---

## 10. 确定的产品形态（实用优先，2026-06-16 用户拍板「可以」）

实用为主：要「**Jenkins 式 web 实时日志显示**」，执行入口**如最初约定**——文档上传 + 参数菜单 + 一键执行；底层用 §9 平台内建 `RunConsole`（不引 Jenkins）。

### 10.1 用户可见形态（PlanRun 详情页内单面板）
```
┌── Jira 提单（去重报告）────────────────────────────┐
│ ① 上传：[选择 Result_*.xls]  (运维复核后的去重结果)      │
│ ② 参数菜单(映射 CLI)：厂商(Transsion▼|Tinno)           │
│     dry-run[✓]  place(SH▼)  reporter[____]            │
│ ③ [▶ 一键执行]                                         │
│ ④ ┌ 实时日志(Jenkins 式 web 控制台, xterm 滚动)──────┐│
│    │ > stage1 generate_upload_list ...               ││
│    │ > parsed 128 rows, dedup 23 → 105 ...           ││
│    │ > stage2 create_batch (dry-run) ...             ││
│    └────────────── [状态 RUNNING] [■ 取消] ──────────┘│
└──────────────────────────────────────────────────┘
```

### 10.2 三件事（实用边界）
| 要素 | 落地 |
|------|------|
| 文档上传入口 | multipart .xls → 存 NFS（复用 JobArtifact/NFS） |
| 参数化命令菜单 | 前端表单(厂商/dry-run/place/reporter 等少量运行期参数)→ 后端拼 CLI argv；海量映射表留工具 config/ |
| 一键执行 + 实时日志 | `POST → RunConsole.start(vendor_venv_python, generate/create_*.py, argv, creds_env)` → subprocess 行级 → `console_log` room → 前端 `<LiveConsole>`(xterm) 实时滚动 + 状态 + 取消 |

### 10.3 明确不做（最小可用，避免过度工程）
- 不引 Jenkins / 不加外部基础设施（用栈内 SocketIO+xterm+SAQ）
- 不做自动提单复杂判定（人工复核后上传即批准；dry-run 人控）
- 不做多租户/复杂队列（同 plan_run 串行、一键一跑）
- 不在平台重实现去重/提单逻辑（工具成熟，平台只上传+参数+跑+显示日志）

### 10.4 实现工作量（立项后）
- 后端：`RunConsole`(subprocess+行级 emit+落盘+cancel/status/log) ~250 行 + 上传/执行端点 ~120 行
- 前端：`<LiveConsole>`(复用 XTerminal) ~150 行 + 提单面板(上传+菜单+一键) ~180 行
- 无架构级新增；难点收敛为 §8.3 三点（编码/节流/取消）+ Transsion cookie 注入

> 设计基线至此收敛完毕。去重子阶段 2026-06-18 立项开工（状态：Accepted 实施中），D-b 已落地、D-a/D-c 待补、D-d 进行中，按 §4 推进。


