# ADR-0051：发布单元与内容寻址——不可变性从源码目录移到包

- 状态：**Accepted** v1.3（2026-09-24：**族级 kind 字段**落地，见 v1.3 修订行；v1.2 2026-09-23：**Phase 3 落地**——210 个版本目录删除、每族一棵源码树、scan 改从 manifest+包注册、tarball 排除 `scripts/`、不可变门禁退役；v1.1 2026-09-23：**勘误 Phase 3 依赖**——删目录须在 2b 且 fleet 全部切到包模式之后，原「只依赖 2a」不成立；v1.0 2026-09-22 owner 裁决：§10 五个裁决点**全采推荐项**——D1 包模型 / D3 采 C1 双列 / D6 选 B / D2 例外声明 + 棘轮 / Phase 3 只依赖 2a；§9 四组机械改动随本版同 PR 落地；v0.1 决策材料 PR #3158）
- 优先级：P1（脚本目录 12 天翻倍、控制面部署源与开发工作区同一棵检出已造成事故；多站点交付 ADR-0041 依赖可 digest 校验的发布物）
- 目标里程碑：M7
- 日期：2026-09-22
- 决策者：owner（DUElost，2026-09-22）；起草：平台研发组
- 归属域：semantic-ownership script-version-immutability
- 落地状态：**Phase 5 首项 ✅**（v1.3 族 kind 字段——ghost 保护精确回收）；Phase 0 ✅（#3162）；Phase 4 部分 ✅（控制面摘要面：`release-manifest.json` 新增 `control-plane` component，`backend/**` 除 agent、**不排除 `.env*`**——#2269「不在任何摘要面内」根因封死；build/S0 共用同一量具文件，S0 比对 declared 全键、旧两键 bundle 兼容）；**Phase 3 ✅**（版本目录删除 + 族树 + manifest 注册 + tarball 排除 + 门禁退役）；**Phase 2b ✅ 且 fleet 48/48 已 `strict`**（2026-09-23 实操：发包 → on 灰度 → strict）；**Phase 2a ✅**（`tool_manifest.json` 登记 35 族 210 版本、`script.package_sha256` 列 + scan 回填、`check_script_packages.py` 并入 tool-manifest 门禁、等价证明对生产库 210/210 通过；包尚未发布到站点 `packages/`，属运维推进项）；Phase 1 / 2b / 3 / 4 / 5 待排期
- 归属说明：v1.0 起 `script-version-immutability` 的 owner_anchor 改指本 ADR D1（语义归属表同 PR 改）
- 标签：release-unit, content-addressing, package-store, script-versioning, deploy-source, anti-corruption, #735, #3075, #1987, #2386
- 关联：[#735](https://github.com/DUElost/stability-test-platform/issues/735)（脚本膨胀治理）/ [#3075](https://github.com/DUElost/stability-test-platform/issues/3075)（ADR-0033 Phase B 包存储实现）/ [#1987](https://github.com/DUElost/stability-test-platform/issues/1987)、[#2386](https://github.com/DUElost/stability-test-platform/issues/2386)（部署源与检出双重角色）
  / [ADR-0020](./ADR-0020-plan-step-one-shot-migration.md)（本 ADR 修订其「已发布版本目录不可变」的承载物）
  / [ADR-0023](./ADR-0023-script-traceability.md)（sha 溯源契约，本 ADR 保其语义、换其载体）
  / [ADR-0033](./ADR-0033-tool-kit-ecosystem-integration.md)（本 ADR 修订 D3 一句措辞、复用 D0/D1/D2/D4 与 Phase B 机械面）
  / [ADR-0039](./ADR-0039-script-version-immutability-narrowing.md)（本 ADR **正面回应其 D6**、吸收 D1、**显式继承 D2/D3/D4/D5/D7**）
  / [ADR-0040](./ADR-0040-deployment-artifact-digest-protocol.md)（包身份复用其 digest，不另造）
  / [ADR-0041](./ADR-0041-independent-site-delivery-and-management.md)（多站点交付是本 ADR 的需求来源）
  / [ADR-0042](./ADR-0042-settings-convergence-and-bare-read-boundary.md)（D3「env 名不变」与本 ADR D7 冲突，须先修订）
  / [ADR-0046](./ADR-0046-control-plane-checkout-roles.md)（本 ADR 接管其 D1–D6 裁决点）
  / 可行性研究 [`SCRIPT_VERSION_BLOAT_ENDGAME_FEASIBILITY_2026-09-10.md`](../reviews/SCRIPT_VERSION_BLOAT_ENDGAME_FEASIBILITY_2026-09-10.md)（P2 否决的原始论证）
  / Agent Note [`2026-09-22-adr0051-release-unit-drafting.md`](../notes/architecture/2026-09-22-adr0051-release-unit-drafting.md)

## 修订记录

| 版本 | 日期 | 变更 |
|------|------|------|
| v1.3 | 2026-09-24 | **族级 `kind` 字段（Phase 5 首项）**：`tools[name]` 由 `{versions}` 变 `{kind, versions}`，`kind ∈ {script, tool}` 成为登记面**唯一族归类判据**——取代 Phase 4a 的「`python:null` 即平台族」启发（`--python-absent` 让外部工具族也有 null python，二义实锤：展锐两族被 scan 误注册进 `script` 表、`check_script_packages` 族分类靠树集反推）。落地：`check_tool_manifest` lint 必填 kind + 族级 kind 不可变（v1.3 前 base 无 kind = 迁移补写合法）；`package_tool_asset --kind`（默认 script）；`script_catalog.script_entries` 只注册 kind=script；`check_script_packages` 归类与 ghost 保护（kind=script 无树恢复判红——Phase 4a 移交 PR 评审的那条保护由 kind 精确回收，**无需再议 schema v2**：kind 是族级字段，不触 C5 版本六元组）。存量 38 族显式迁移（35 script + Start-Log-Scan/Scan-Result-GT/Monkey-Log-Scan-GT-SPRD=tool）。生产数据清理（部署清单）：`script` 表两行误建 Unisoc 行走 admin `PUT is_active=false` 软退役。 |
| v1.2 | 2026-09-23 | **Phase 3 落地**（fleet 48/48 已 `strict` 后执行）：`backend/agent/scripts/` 210 个版本目录删除，每族保留最新版本内容为源码树；`tool_manifest.json` 210 条目原样保留（append-only）；`check_script_packages.py` 改为「族树重建 sha == 最新未退役条目」+ 残留 v 目录红；`POST /scripts/scan` 改为 `sync_scripts_from_manifest`（manifest + 站点包源，退役由 `retired:true` 显式驱动，永不因盘上缺失反激活，`allow_deactivate` 退役）；`agent-code` tarball / wrapper / Ansible 三处排除集同源加入 `scripts/`；`check-script-version-immutability.py` 与其 CI 步骤退役；AGENTS.md 条款去过渡句；测试面：版本目录用例重指族树、目录模型 catalog 用例重写为包夹具 |
| v1.1 | 2026-09-23 | **勘误 + Phase 2b 落地**：§5「Phase 3 不必等 2b」**撤销**——热更新对 agent 树 `rsync --delete`，版本目录就在这棵树里（`_TAR_EXCLUDES` 未排除 `scripts/`），删目录会随下一次热更新把主机上的脚本一并删掉；scan 亦以目录为注册输入。Phase 3 前置改为 **2a + 2b + fleet 全部 `STP_SCRIPT_PACKAGES=strict` 且一轮 verify_scripts 全 `package_active`** + scan 注册改读 manifest（Phase 3 自身范围）。D4 落地：`backend/agent/script_packages.py`（DB 权威 `package_sha256` → `tool_cache.ensure_package` → `tools_cache`），三处形态耦合解除，`verify_scripts` 在开关开时按整包核验并预热；开关 `STP_SCRIPT_PACKAGES=off|on|strict` 默认 off（源键 `STP_AGENT_SCRIPT_PACKAGES` 走既有 env 推送链）；`agent-code` tarball **本版不排除** `scripts/`（排除即等于删主机目录，归 Phase 3）。另修 2a 打包器缺陷：tar 权限位按 Git 语义归一化（此前随 umask 分叉，`72250d4b` 只是按 644 环境重登记） |
| v1.0 | 2026-09-22 | **owner 裁决 Accepted**：§10 全采推荐项；§9 四组机械改动落地（`AGENTS.md` 总原则条款改写 + S11 锚同步；ADR-0039 / ADR-0046 转 Superseded；adr/README + M7 + DOC-MAP + 语义归属表改指；ADR-0033 D3 一句措辞改采 C1 → v1.13）；D1 补「Phase 3 前版本目录仍是发布单元」过渡句（否则目录保护在 2a 前被提前撤销） |
| v0.1 | 2026-09-22 | 初稿：用户两轮逐项核对后的方案定稿为 ADR 草案；§2 正面回应 ADR-0039 D6；§3 八条决策；§5 落地顺序按 2a/2b 拆分；§9 Accepted 当日的四组同 PR 机械改动清单 |

---

## 1. 背景与问题定性

### 1.1 同一个病根的三个症状

仓库里 `backend/agent/scripts/<name>/v<version>/` 目录同时承担三个角色：**源码**（开发者修改的对象）、**发布物**（不可变性施加的对象、`script.content_sha256` 冻结的对象）、**部署源**（热更新 tarball 直接 `os.walk` 这棵树）。三个角色的诉求互斥，产生三个独立被治理、但从未收敛的症状：

| 症状 | 现象（2026-09-22 实测，`origin/main`） | 现有处方 | 处方为何不收敛 |
|---|---|---|---|
| **脚本目录膨胀** | 版本目录 08-01 → 09-22：30 → 100 → 111 → 175 → **208**；行数 7.5k → **128.7k**；最近 12 天翻倍。目录中位 554 行；**patch 级发布复制比例 95%+**（如 `monkey_setup` v2.3.9→v2.3.10 差异 18 行 / 目录 1048 行），minor/major 级 60–85%；存在 diff = 0 的纯改名版本（`flash_firmware` v1.0.0→v1.0.1） | ADR-0039（Proposed，退役后允许删零引用目录） | **减存量不减流量**：ADR-0039 等待裁决期间目录 163 → 208（+45），而其 D1 可删上限（零引用且已退役）为 31。即便今日裁决执行完，也只回到 09-15 水平 |
| **部署源 = 开发工作区** | 控制面 unit `WorkingDirectory` = 仓根；`STP_SCRIPT_ROOT` 本机指向工作树（示例值本是 `/opt/...` 非检出路径）；`_AGENT_SOURCE_DIR` 由 `__file__` 推导；三条消费路径（scan / hot-update / 派发补推）都读「检出当下的样子」 | ADR-0046（Proposed）+ `check-deploy-source.sh`（unit 内 `ExecStartPre=-`，失败也继续） | **窗口不可守**：ADR-0046 §3 三条目四事件（#2386 scan 在非 main 检出上跑、#1987 检出被他人未跟踪文件卡死、#735 两条已合入告警规则在生产静默失效）。校验与动作之间永远隔着一个可被别家会话切走的窗口 |
| **外部工具无包形态** | 展锐三族 = 中心存储未打包源码目录 + `STP_UNISOC_*` 路径键（ADR-0033 §5.4 登记的 legacy 例外）；`resources/` 230 MB（flashtool / aimonkey）随 agent 树以 `host-resources` artifact 分发；SP Flash Tool 二进制名硬编码在 22 个 `flash_firmware` 版本目录里 | ADR-0033 D3 包存储（Phase B 第一切片 #3075 已合入：`tool_manifest.json` + 打包器 + 门禁 + Agent `tools_cache`，**仅 1 族、逃生阀默认关、生产解耦为零**） | 包机制只覆盖「不走 `script:<name>` 派发的外部工具」；35 个平台族的执行路径（`ScriptRegistry.resolve` → `entry.nfs_path` 进 argv）**根本不经过它** |

**病根只有一条**：不可变性被施加在源码目录上，所以每次改 50 行必须复制 1500 行；部署源等于检出，所以并发会话切分支就等于改生产；外部工具没有包形态，所以只能靠 NFS 源码目录加 env 路径。

### 1.2 已有基建（本 ADR 不新建、只接通）

- **release bundle 链路**：`tools/release/build_bundle.py` 产出 `release-manifest.json`（`product.version` / `source.revision` + `agent-code` / `host-resources` 两个 ADR-0040 digest）；`tools/site_config/install.py` 版本不符 fail-closed、bundle 内容按 digest 校验；`backend/core/release_manifest.py` 运行时真值优先读清单。**真实缺口只有三块**：控制面自身载荷无摘要面（`_digests()` 只算 `backend/agent`，#2269 自陈构建机 `backend/.env` 曾漏进 bundle 且「不在任何摘要面内」）；tool 包不是清单条目；本机生产控制面仍是 checkout 形态。
- **包拉取与校验**：`backend/agent/tool_cache.py` 按整包 `package_sha256` 校验后才写 `.stp-verified`（`tar.extractall` 前已全量校验成员）；`tools/dev/check_tool_manifest.py` 做 schema lint + append-only。
- **digest 算法**：`backend/agent/artifact_digest.py` 规范化序列 `(relpath, 可执行位, sha256)` → `sha256:<hex>`，双侧镜像实现 + 字节级等价性测试（ADR-0040 D1）。
- **硬阻断先例**：同一 unit 内 `check_alembic_at_head.py` 是**无减号** `ExecStartPre`（#2058）。结构性强门禁在本仓已有先例，缺的是推广。

### 1.3 生产事实基线（2026-09-22 只读实测）

| 指标 | 值 |
|---|---|
| `script` 行 / 活跃 | 210 / 99 |
| `plan_step` 引用 distinct `(name, version)` | 52 |
| `origin/main` 版本目录与 `script.content_sha256` + `support_files_manifest` **逐字节一致** | **208 / 208**（另 2 行无对应目录，为已退役行） |
| 历史 `scan_rebaseline`（admin 逃生阀）执行次数 | **5**（2026-07-31 × 1、08-24 × 1、08-25 × 2、08-31 × 1） |
| 被重锚过的 distinct 版本 | **24**（其中仍被 `plan_step` 引用 3：`check_device@1.0.0` / `clean_env@1.0.0` / `ensure_root@1.0.0`） |

**对等价证明的含义**（§5 Phase 2a 前置）：24 行的 `content_sha256` 记录的是「某次盘上状态」而非首次发布字节，因此**不能**用「git 历史首次发布 commit 的字节 == DB sha」做证明；但**当前 `origin/main` 字节 == DB sha 对全部 208 行成立**，且不可变门禁自 08-31 起持续在场。故等价证明的基准取「当前 `origin/main` 目录字节」，而非「首次发布 commit」。

---

## 2. 对 ADR-0039 D6「共享基础库明确不做」的正面回应

本 ADR 的 D2（每族一棵源码树）按 ADR-0039 与可行性研究的词表**就是 P2**。D6 与可行性研究结论 2 否决 P2 的理由是：

> 它把不可变性从「每版独立可校验」变成「跨版共享可变面」……一次库变更会同时改变所有引用它的历史版本的执行语义，除非每个版本 pin 库 sha（那等于把同一份复杂度挪个位置），且 `_` 前缀辅助文件连 entry sha 都不计，是比改入口更隐蔽的漂移面。

这四条理由**全部以「运行时事实面 = 源码目录」为前提**。本 ADR 改的正是这个前提，逐条回应：

| D6 / 可行性研究的顾虑 | 在包模型下 |
|---|---|
| 「每版独立可校验」被破坏 | **由包承担，不由目录承担。** 运行时事实面是 per-version tarball 与其 `package_sha256`；源码树可变，但已发布的包不可变。任何历史版本的执行语义由包字节唯一决定，与源码树后续变化无关 |
| 「一次库变更改变所有引用版本的语义」 | **不成立。** 库变更只影响此后打出的新包；已发布包内的库字节已冻结 |
| 「逐版本 pin 库 sha = 复杂度转移」 | **pin 是打包的副产物，不是额外机制。** 打包时库已被冻结进包，不需要第二套 sha 契约、不需要扫描器/verifier/self-heal 扩展 |
| 「`_` 前缀辅助文件不计入 entry sha 的盲区」 | **盲区消失。** 目录模型只 hash 入口文件（伴随文件靠 `support_files_manifest` 补），整包 sha 天然覆盖全部文件（`tool_cache.ensure_package` 已如此实现） |
| 「78% 空间成本换语义耦合」 | 空间成本由包仓库承担（NFS，已有 TTL 先例 #1521），git 只保留一棵树；语义耦合不存在（上两行） |

**迁移可自动证明等价**：对每个 `script` 行，从 `origin/main` 当前目录打包，入口文件 sha 必须等于现存 `content_sha256`、伴随文件 sha 必须等于 `support_files_manifest`，不等即迁移失败（§1.3 实测 208/208 成立）。

因此本 ADR **不撤销** D6 的判断（在目录模型下 P2 确实不该做），而是**撤销目录模型本身**。D6 的否决对象随其前提一起失效。

---

## 3. 决策

### D1（核心）：发布单元 = 内容寻址包；不可变性属于包，不属于源码目录

- 平台自研脚本族、外部工具族、控制面代码、Agent 代码、主机资源，**统一**以内容寻址 artifact 为发布单元；身份 = `sha256:<hex>`（复用 ADR-0040 D1 算法与双侧实现，**不另造**）。
- `AGENTS.md` 总原则「已发布 `backend/agent/scripts/<name>/v<version>/` 不可原地修改或删除」改写为「**已发布的包（`packages/{name}/{version}.tar.gz` 及其登记条目）不可原地修改；删除按 ADR-0039 D2/D3 继承条款**」（S11 锚同 PR 改写，见 §9）。
- ~~过渡句（v1.0）：Phase 3 完成前版本目录仍是发布单元~~ **v1.2 已撤销**：目录已删除，`check-script-version-immutability.py` 已退役。
- 硬不变量「已存在脚本版本的 `default_params` 不可原地修改；参数变化通过新版本表达」**不变**——新版本 = 新包，成本从「复制目录」降为「打包」。

### D2：源码——每族一棵源码树，版本目录退役

- `backend/agent/scripts/<name>/` 下只保留**一棵**可演进的源码树（入口 + 伴随文件 + `capabilities.json`）；版本号由 `tool_manifest.json` 条目承载，不再由目录名承载。
- 存量 208 个版本目录在 Phase 3 **一次性删除**（前置：Phase 2a 完成且等价证明全绿）；历史可复现性由 git 历史 + 包仓库承担。
- 迁移期间**不冻结**新增版本目录（09-13→09-22 新增 47 个目录中与前版差异 ≤5 行的为 0，几乎全是真实修复；且 `default_params` 硬不变量逼迫参数改动也必须开新版本）。改为：新增版本目录须在 diff 或提交说明内出现一行 `MIGRATION-EXCEPTION: <原因> #<issue>`（与 `check_new_script_family.py` 的「归类声明」同载体、同判据：只扫 `git diff base...head` 与 `git log base..head`，不读 PR 描述），并计入按周公开上调的棘轮（D8）。

### D3：script 表——DB catalog 仍是唯一运行时权威；采 C1 双列语义（修订 ADR-0033 D3 一句措辞）

- **ADR-0033 D3「DB script catalog 唯一运行时权威；manifest 仅发布格式」不变**。注册流从「扫描目录」改为「从 release manifest / `tool_manifest.json` 注册」；退役改为**显式动作**（接管 ADR-0046 D2：scan/注册只报告，不再单向反激活）。
- **修订 ADR-0033 D3 的一句措辞**：原文「升级工具包 = 新建 script 版本行（`content_sha256 := tarball sha256`）」与 v1.12 实际裁决 C1（整包 `package_sha256` 与 entry-file sha **语义分离**）互斥。本 ADR **采 C1**：`script` 表保留 `content_sha256`（入口文件 sha，ADR-0021/0023 溯源与 `verify_scripts` 双轨期继续使用）+ **新增 `package_sha256` 列**（整包 sha，运行时校验唯一判据）。ADR-0033 D3 该句同 PR 改为「`package_sha256 := tarball sha256`；`content_sha256` 仍为入口 sha」。
- 422 不可变守卫、409 退役守卫（`SCRIPT_STILL_REFERENCED`）原样复用，判据对象从目录改为包条目。

### D4：执行路径——Agent 经 `tools_cache` 拉包；`nfs_path` 语义变更；三处形态耦合解除

现状：`tool_cache.resolve_packaged_scan_tool` **全程不查 script 表**（外部工具不走 `script:<name>`）；35 族走 `ScriptRegistry.resolve(name, version)` → `entry.nfs_path` 直接进 argv。「**DB 权威 → 包身份**」这一环**在现有代码里不存在**，是 Phase 2b 要新建的全部工作量所在。

- `nfs_path` 语义从「控制面推送的固定路径」变为「Agent 本地 `tools_cache/{name}/{version}/<entry>` 路径」，由 Agent 在 `ScriptRegistry.resolve` 后经 `tool_cache.ensure_package(name, version, package_sha256)` 解析；`verify_scripts` RPC 改为校验整包（`.stp-verified` 标记 == 期望 `package_sha256`），双轨期保留入口 sha 校验。
- **三处 `nfs_path` 形态耦合必须同 PR 解除**（`backend/agent/pipeline_engine.py`）：
  1. `:1787` `Path(entry.nfs_path).resolve().parents[3]` 注入 PYTHONPATH——硬编码 `agent/scripts/<name>/<ver>/` 深度；换 cache 布局后算错目录、共享库 import 静默失败 → 改为显式 `entry.package_root`；
  2. `:1816` `cwd=os.path.dirname(entry.nfs_path)`——脚本工作目录形态变更（相对产物、同级 `_adb.py`）→ cwd = 包解压根，包内相对布局与现目录一致；
  3. `:804` `"/flash_firmware/" in path` 决定 8 s 终止宽限（ADR-0043 / #1591）——cache 路径恰好仍含该子串而侥幸不破，但这是「路径形态被当契约」的实证 → 改为按 `name` 判定。
- 双轨（v1.1 落地形态）：**Agent 侧开关** `STP_SCRIPT_PACKAGES`——`off`（默认，逃生阀关：一律 `nfs_path`，不碰包源）/ `on`（优先包，失败回退 `nfs_path` 并记 WARNING）/ `strict`（只走包，失败 = 步骤 exit 2）；叠加按 script 行灰度（`package_sha256` 为空 → 旧路径）。控制面只在 expected 清单多带 `package_sha256`（沿 ADR-0033 v1.1「契约翻译在 Agent 边缘」）。scan 回填一次把 210 行全部置非空，所以行级灰度不足以控制切换节奏，开关是必要的。
- `agent-code` artifact 排除 `scripts/` **推迟到 Phase 3**：Phase 3 前排除等于让 `rsync --delete` 清掉主机目录，而回退路径正是这些目录。

### D5：显式继承 ADR-0039 D2 / D3 / D4 / D5 / D7，作用域从目录改为包

本 ADR **不整篇 supersede ADR-0039**——它吸收 D1（不可变收窄为「直至零引用退役」），并对以下条款**原样继承、只改作用域**。理由：删包与删目录一样不可逆，D2「不可逆 + 自动化会放大误判」的理由在包模型下原样适用，且更严重（包仓库是多站点共享面）。

| ADR-0039 条款 | 本 ADR 的继承形态 |
|---|---|
| D2 删除权不下放给自动流程 | 包的物理删除只允许人工发起的 PR（附 `check_unreferenced_script_versions` 只读证据）执行；周期巡检只报告 |
| D3 冷却期 30 天或一个里程碑取长者；按版本不按族 | `retired` 翻转后至少一个完整里程碑或 30 天（取长者）才可删包；按 `(name, version)` 单条目判定 |
| D4 删除后「不可重新派发」是显式接受的代价 | 包删除后 `ensure_package` 拉取失败 → `script_verify_failed` 类显式失败，不提供归档自动还原 |
| D5 重跑需求的正解是发新版本 | 不变 |
| D7 门禁判据与 DB 引用解耦 | 棘轮与不可变门禁只读 git 侧（`tool_manifest.json` append-only、目录计数），不连生产库 |

ADR-0039 转 Superseded 的时机 = 本 ADR Accepted 当日（§9）。

### D6：控制面部署源——接管 ADR-0046 D1–D6；选型 **B**

| ADR-0046 裁决点 | 本 ADR 裁定 |
|---|---|
| D1 部署源是否与开发工作区物理分离 | **是** |
| D2 「盘上缺失」是否仍等于「已退役」 | **否**。退役改为显式动作（`is_active=false` 由人/流程发起，留发起人审计）；注册只报告不反激活。`allow_deactivate` 开关的删除**排在本条落地之后**，且须先建好显式退役路径，否则退化为「scan 什么都不做、退役无处发起」 |
| D3 三条消费路径是否共用同一「已校验 revision」来源 | **是**。scan / hot-update / 派发补推都解析到 bundle 安装根；`_AGENT_SOURCE_DIR` 随 `__file__` 落进 bundle 内 agent 树，**不新增 env 名**（绕开 #737 清单门禁与 ADR-0042 D3）；`STP_SCRIPT_ROOT` 改指部署根是**纯配置切换**（示例值本就是 `/opt/...`） |
| D4 部署检出的推进权 | 部署动作唯一推进；他人未跟踪/已修改文件与部署根不得共存（B 天然满足） |
| D5 是否需要按 revision 回滚 | **需要**——多站点交付（ADR-0041）要求按 revision 复现某站控制面；A 的检出只能向前，不满足。故选 **B：按 revision 归档只读树**（`<releases>/<digest>/` + `current` 链接；保留策略与 #1521 NFS TTL 同族） |
| D6 可见性与审计底线 | scan 与 hot-update 审计落「本次输入树 digest + 是否等于部署目标」；不一致即拒绝 |

- **落地形态 = 接通已有链路，不是新建基建**：`build_bundle` → `site_config install` → unit `WorkingDirectory` 改指安装根；`check-deploy-source.sh` 去减号，与 `check_alembic_at_head.py` 同级硬阻断。
- **部署根外部物料清单（必须显式登记，否则第 1 步当场失效）**：`tools/ansible/inventory.ini`（`.gitignore:85`，`host_updater.py:46` SSH 凭据回退——切根后回退静默消失、热更新连不上主机）、`frontend/dist-prod`（`.gitignore:20`，`TREE_LAYOUT` 必需、缺则 `bundle_layout` 报错）、`venv/`、`logs/`、站点派生的 `packages_root`。安装器 S0 的 `install_root_taken` / `release_version_mismatch` fail-closed 假设「干净且物料齐备」，这份清单是其前置。
- 手工起进程绕过 unit 三道 `ExecStartPre` 的问题（`deploy/control-plane/README.md:28` 自陈）由 B 闭合：部署根不是可当工作树用的目录。

### D7：外部工具与主机资源归入同一包机制

- ADR-0033 D0 / D1 / D2 / D4 **不变**；Phase B 后续切片（展锐其余两族、控制面侧 `STP_BACKEND_DEDUP_SCAN_*` 切包、env 回退窗口移除）**并入本 ADR Phase 4**，不再单独推进。
- `resources/`（flashtool / aimonkey）从 `host-resources` artifact 改为 `tool_manifest.json` 条目；`flash_firmware` 对 SP Flash Tool 的引用从硬编码二进制名改为包内相对入口。
- **删除 `STP_UNISOC_*` / `STP_AGENT_UNISOC_*` 路径键**与 ADR-0033 §5.4 例外③「不得再新增工具私有 env 键」同向，但**撞 ADR-0042 D3「env 名不变」与 #737 清单门禁**（`.env*.example` + `tests/test_agent_env_selfsufficiency.py`）。前置：先修订 ADR-0042 D3 为「不改既有 env 名；**已登记 legacy 例外的键在其终态出口落地后可删**」，并同步全部 `.env*.example` 与自足性测试。
- release manifest 扩展为四类条目：控制面载荷、`agent-code`、tool 包、（过渡期）`host-resources`。控制面摘要面**需要自己的输入集契约与对照测试**（现 `_digests()` 输入集由 `tests/test_ansible_digest_contract.py` 与三处排除集同源锁定，控制面侧无对应物，否则复现 #2269「不在任何摘要面内」）。

### D8：治理面——先做减法，只加两样

- **减**（各有顺序约束）：`check-script-version-immutability.py`（Phase 3 后无对象）、`check_new_script_family.py`（并入包登记门禁）、`check-deploy-source.sh`（D6 落地后由结构替代）、scan `allow_deactivate` 开关（D6-D2 后）。
- **加**：
  1. **git 侧棘轮**：判据钉在 `git ls-tree` 版本目录计数 + `tool_manifest.json` 条目数，**不连生产库**（继承 ADR-0039 D7）。`check_unreferenced_script_versions.py` 是账本（exit 0；未设 root exit 2），做 gate 须**新写判据**，不能复用其退出码。例外声明格式与 `check_new_script_family` 的归类声明**先统一**，避免两道门禁互咬。
  2. **过渡登记簿**：`AGENTS.md` 要求「止血必须写终态出口」，但 docs 内「过渡 / 止血 / 技术债」448 处、143 文件，无一有到期机制。登记簿字段 = 对象 / owner / 终态出口（指向本 ADR 某 Phase）/ 到期日；超期未撤即红。

---

## 4. 备选方案与权衡

| 方案 | 取舍 |
|---|---|
| 继续目录模型 + ADR-0039 退役删存量 | 弃——减存量不减流量（§1.1 表）；流量由「不可变施加在目录」决定 |
| P2 共享基础库（目录模型内抽 `_lib/`） | 弃——D6 的否决在目录模型下成立（§2）；本 ADR 不是 P2 的变体，而是撤销 P2 赖以被否决的前提 |
| 只做 ADR-0033 Phase B（外部工具包化），平台族不动 | 弃——35 族不经过 `tool_cache`，膨胀主体正是平台族 |
| ADR-0046 方案 A（独立只读检出） | 弃——D5 需要按 revision 回滚（多站点），A 只能向前 |
| ADR-0046 方案 C（现状 + 守卫升级为硬阻断） | 弃——只覆盖 systemd 路径；手工起进程绕过；窗口结构仍在 |
| 包身份另造（manifest 自带版本体系） | 弃——ADR-0033 v1.1 与 ADR-0040 D7-5 已裁「不另造」 |
| 整篇 supersede ADR-0039 | 弃——D2/D3/D4/D5/D7 是独立有效裁决，包模型下原样适用（D5） |

---

## 5. 落地顺序（依赖序；每步独立可回滚）

| Phase | 内容 | 依赖 | 会当场变红的门禁（同 PR 处理） |
|---|---|---|---|
| **0** | 本 ADR 裁决 + §9 四组机械改动 | — | S11 锚 / S12 ⑤ + 索引一致性 / 共享元文件串行领单 |
| **1** | 本机控制面切 bundle 安装形态（D6 选 B）：`build_bundle` → install 到 `<releases>/<digest>/` → unit `WorkingDirectory` 与 `STP_SCRIPT_ROOT` 改指 → `check-deploy-source.sh` 去减号；**附部署根外部物料清单** | Phase 0 | 运行期非门禁（最危险）：inventory.ini / dist-prod / venv 物料缺失 |
| **2a** ✅ | 打包 + 登记 + 等价证明：对 210 行 `script` 从 `origin/main` 当前目录打包 → `tool_manifest.json` 登记 → 新增 `package_sha256` 列并回填 → 证明入口 sha == `content_sha256` 且伴随 sha == `support_files_manifest`（基准 = 当前字节，见 §1.3）。**`nfs_path` 不动**，可回滚。**已落地**：`tools/dev/check_script_packages.py`（`git ls-files` 成员 + 确定性打包 + 登记 + 等价门禁）、`backend/scripts/check_script_package_equivalence.py`（只读证明，生产 212 行 = 210 ok + 2 无目录退役行）、迁移 `ad51c1d3f2a1`、scan 回填（`package_backfilled` / `package_conflicts`）；manifest 条目 `python: null` = Agent 自身解释器 | Phase 0 | `check_tool_manifest` append-only；alembic 迁移门禁 |
| **2b** | 切执行路径（D4）：`ScriptRegistry` → `ensure_package` → 三处耦合解除 → `verify_scripts` 整包校验 → `agent-code` 排除 `scripts/`；按 script 行灰度 | 2a + Phase 1 | Agent 测试面（`backend/agent/tests`）大面积改夹具；ADR-0043 宽限判据测试 |
| **3** ✅ | 一次性删除 210 个版本目录；每族留一棵源码树；scan 注册改读 manifest；`agent-code` 排除 `scripts/` | **2a + 2b + fleet 全部 `strict` 且一轮 verify 全 `package_active`**（v1.1 勘误：删目录随热更新清主机树，回退路径消失）——2026-09-23 满足后执行 | `check-script-version-immutability.py` 已同 PR 退役；不再需要目录棘轮（D8 的棘轮对象随目录消失） |
| **4** | 外部工具与资源入包（D7）：展锐三族 + flashtool + aimonkey；控制面摘要面；删 `STP_UNISOC_*` | Phase 1（清单形态）+ ADR-0042 D3 修订 | #737 清单门禁 + `.env*.example` 奇偶；digest 契约测试 |
| **5** | 治理减法与登记簿（D8） | 各自的顺序约束（D8） | 例外声明格式统一 |

**Phase 3 必须等 2b 与 fleet 切换**（v1.1 勘误）：v1.0 写「删目录只依赖 2a」时漏看了两条运行时事实——①热更新 `agent-code` tarball 含 `scripts/`、主机端 `apply-code` 是 `rsync --delete`，仓库删目录 = 下一次热更新删主机目录；②scan 以目录为注册输入，删目录 = 反激活全部行。回退路径（`nfs_path`）与注册输入都随目录消失，所以 2b 的 `strict` 模式必须先在全 fleet 跑绿。

---

## 6. Verification

- **2a 等价证明**：脚本化，对每个 `script` 行输出 `(name, version, entry_sha_match, support_match, package_sha256)`；任一 `false` 即失败。基线：2026-09-22 实测 208/208 一致（§1.3）。
- **2b 灰度**：先对 `plan_step` 零引用且活跃的版本切包路径，真机跑一轮 `check_device` / `noop`；再切被引用版本。判据：`verify_scripts` ack 全 `ok=true`、`step_trace` 无 `script_verify_failed`、PYTHONPATH 注入后共享库 import 成功（针对耦合 1 写显式用例）。
- **Phase 1**：切根后 `resolve_build_info()` 不再返回 `checkout`；hot-update 单台成功（验证 inventory.ini 物料）；`check-deploy-source.sh` 去减号后故意在脏树上起服务须失败。
- **Phase 3**：删除 PR 合入后 `agent-code` digest 变化、`host-resources` digest 不变；热更新载荷 < 5 MB（ADR-0040 D6 验收）。
- **棘轮**：变异测试——人为新增一个无 `MIGRATION-EXCEPTION` 声明的版本目录，gate 必须红；先看选中数量再看红绿（防 0 用例假绿）。

## 7. Revisit（复议触发条件）

1. **包拉取成为派发瓶颈**（60+ host 并发首拉同一包 → NFS 带宽）→ 引入站点级预热或 P2P，不回退目录模型；
2. **出现「必须用某已删包的字节重跑」且反复出现** → 继承 ADR-0039 §6：重审 D5 继承条款的成本假设；
3. **`refs = 0` 不再等于「不在任何 Plan 中」**（引入模板引用 / 动态解析）→ 继承 ADR-0039 §6 最脆弱假设，D5 继承条款重裁；
4. **多站点交付取消**（ADR-0041 撤销）→ D6 选型 B 的主判据消失，可降为 A；D1–D5 不受影响；
5. **Phase 2b 切换后派发中断事故** → 按 script 行回退灰度（`package_sha256` 置空即回旧路径），不回退 2a。

## 8. 关联实现 / 文档（落地时同步）

- `docs/development/script-versioning.md`「目录与扫描」「已发布版本不可变」「退役与删除」三节改写；
- `docs/operations/2026-08-29-post-review-deploy-runbook.md` §1.1 / §1.4 的「再核一次」随 Phase 1 删除；
- `docs/design/2026-storage-roles-and-aliases.md` 登记 `packages/` 与 `<releases>/` 角色；
- `docs/development/environment-variables.md` 随 Phase 4 删键。

## 9. Accepted 当日的四组同 PR 机械改动（缺一即门禁红）——**v1.0 已随本版落地**

| 组 | 改动 | 触发的门禁 |
|---|---|---|
| ① | `AGENTS.md` 总原则「已发布 `backend/agent/scripts/<name>/v<version>/` 不可原地修改或删除」改写为 D1 措辞 | S11：`tools/dev/check_governance_surface.py` `HARD_INVARIANT_ANCHORS` 正则同 PR 更新（注释自陈「ADR-0039 Accepted 当日须同 PR 改写本锚」，本 ADR 接管该义务） |
| ② | ADR-0039 / ADR-0046 头部状态转 **Superseded**（by ADR-0051） | S12 ⑤「锚目标 ADR 头部必须 Accepted」：入向引用 **40 / 12 文件**（含 `docs/DOC-MAP.md`、`docs/adr/README.md`、tools、backend 注释）全部改指本 ADR 或去锚 |
| ③ | `docs/adr/README.md` 主表 + M7 看板、`docs/DOC-MAP.md` 行、ADR-0033 D3 一句措辞与其 README 摘要 | S12 索引一致性（status 词级）、S14 注释版本引用 |
| ④ | 共享元文件（`AGENTS.md` / `CLAUDE.md` / DOC-MAP / adr README）串行领单 | 执行契约「同一时间只由一个 Execution 修改」 |

## 10. 裁决记录（2026-09-22，owner 全采推荐项）

> v0.1 起草时的五个裁决点与取向原文保留如下；owner 于 2026-09-22 裁定**全部采推荐项**（用户原话「按推荐建议进行下一步」）。

1. **D1**：不可变性的承载物从目录改为包——这是全部其余决策的前提；否决即整篇作废。
2. **D3**：采 C1 双列（推荐）还是按 ADR-0033 D3 原文让 `content_sha256` 承载整包 sha（弃入口 sha 溯源双轨）。
3. **D6 选型 B**（推荐，判据 = ADR-0041 需要按 revision 复现）还是 A（更省，但不可回滚）。
4. **D2 迁移期策略**：例外声明 + 棘轮（推荐）还是硬冻结（会阻塞生产修复，47/47 新增目录均为真实变更）。
5. **Phase 3 时机**：只依赖 2a（推荐）还是等 2b 生产跑通。
