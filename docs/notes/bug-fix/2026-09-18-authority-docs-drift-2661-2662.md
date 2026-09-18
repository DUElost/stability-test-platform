# 权威文档漂移批二：ADR-0042 状态回填 + 已删开关引用面（#2661）· 站点样例字段完整性（#2662）

Status: implemented
Class: bug-fix

- 日期：2026-09-18（两单立案日 2026-09-17）
- 关联：`#2661`（ADR-0042 记录停在「P2 待启动」；ADR-0002/0003/0004 仍把已删的
  `USE_SESSION_WATCHDOG` 当生效开关）、`#2662`（`agents[].ssh_port` 不在「唯一权威样例」
  与多站点契约表里）；上游：`#737`（删键的那一批）、`#2283`（`ansible_port` 被丢弃）、
  `#2197`（站点 `monitoring` 段）、`#1520`（`_get_backpressure` 下沉）；同线批一：
  `#2655`/`#2656`/`#2657`/`#2660`（PR #2668）

## Decision

两单的**共同形态**不是「字写错了」，而是**文档对读者许了一个没有判据背书的承诺**：
#2662 的样例自称「完整字段以此为准」而无人检查，#2661 的 ADR 自称「P2 待启动」而 P2 已落四片、
且三篇旧 ADR 教的开关早在 #737 就没了读取点。所以每条都不只改文字，**同时补一条机器判据**
——否则这次修完，下次以同样的形态再漂移一次。

### #2661-1：ADR-0042 就地升 v1.2（不新起 ADR、不写成「P2 完成」）

- 头部规范位 v1.1 → **v1.2**，版本记录顶部新增 v1.2 条，把四个已落地片逐个登记并附
  commit：`759526d7`（控制面 scheduler 四 reconciler 批处理旋钮）、`90377dbe`
  （安全与会话域 `core/security` + `cors` → `AuthSessionSettings`）、`b8ebf843`
  （agent 侧磁盘与日志归档域 `DiskArchiveSettings`）、`d58093dd`
  （`HeartbeatSettings` + `RegistrationSettings`）。四个 commit 均用
  `git merge-base --is-ancestor <c> HEAD` 确认在本线内，未凭 Note 标题推断。
- **措辞只到「已落地四片」**：issue 证据只证到这里，余域仍是「按 D2 逐个评估」、P3 收口未启动。
  写成「P2 完成」就是用一个漂移换一个漂移。
- §P2 范围清单同步回填，并显式写下「**清单不是执行序**」：v1.1 的粗粒度枚举不含安全/会话域，
  而两片落地时是「按 D2 候选清单第 1/2 优先项」做的（见对应 architecture Note）——
  漂移在**记录面**，不在执行面，故回填而非追加裁决。
- 派生面 `docs/adr/README.md:95` 与 `docs/DOC-MAP.md:105` 同步 v1.2（S12 强制，
  `check_governance_surface.py --check` 全绿）。ADR-0042 不在 M7 看板，该面无需动。

### #2661-2：三篇旧 ADR 就地行内标注（不删历史陈述）

issue 明确留了一个未决裁决点（「历史陈述失真」还是「历史留档」）。本单按**既有实践**处置，
不是按偏好：仓内已有 ADR 用 `~~原文~~ + 已移除` 做就地勘误（`ADR-0003:27` 的 `PENDING_TOOL`、
`ADR-0019:97/117` 的 `max_concurrent_jobs`），且现行 ADR 就地升版本受 S12 保护（ADR-0024 v1.2 先例）。
于是四处（`ADR-0002:22`、`ADR-0003:63`、`ADR-0004:24,29`）保留原句、加划线、**同一行内**补
〔#2661 标注〕该键已移除（#737）+ 现状（watchdog 常驻无开关、`heartbeat_monitor` 已不在
`backend/tasks/`）+ 指向台账。`docs/archive/**` 与 `docs/notes/**` 属留档，**不追改**。

### #2661-3：「已移除的键」台账 + 守卫（把「删掉的键」变成有机登记面）

- `docs/development/environment-variables.md` §6 新增台账（§1–§5 与附录编号不动）：
  `USE_SESSION_WATCHDOG` + 三个 `BACKPRESSURE_*` 键，键名取自
  `git show 5dc2d030` 的删除行（不凭记忆），每行写「状态 / 真实读取点 / 现状落点」。
- 新守卫 `tests/test_removed_env_keys.py` 四条判据：① 台账键在全仓引用处
  **同一行**必须带 `已移除|已删除|无读取点|removed`（扫 `docs/**` 除 archive/notes、
  `backend/**`、`deploy/**`、`tools/**`、`scripts/**`、`.github/workflows/*.yml`）；
  ② 台账下界键集不得缺席（防「删表即绕过」）；③ 台账键不得再有 `os.getenv` /
  `os.environ` 读取点（标记不是复活许可，这条是精确判据）；④ 扫描面必须覆盖
  #2661 真实漂移的那几个文件（防「把目录挪出扫描面」这种静默绕过）。
- **本单踩到的假绿（值得留痕）**：初版标注里链接写作
  `[环境变量文档 §6 已移除键台账](…#6-已移除的键)`，「已移除」出现在**锚点 URL** 里，
  于是删掉正句的标记后该行仍被同行规则放行（变异 M1 首跑 GREEN）。修法不是换措辞，而是
  结构性收口：判定标记前先剥 `[text](url)` 的 url 部分，并补一条判别力用例钉住这个形态
  （`smuggled.md`）。同批 M7 变异（把剥离逻辑退回）确认该用例自身会红。

### #2662：样例字段完整性守卫（先修判据，再补字段）

- `tests/test_site_config.py::test_site_example_documents_every_model_field`：把
  `SiteConfig` 模型树展开全部键路径，要求「唯一权威样例」里都有对应键；
  反向不重复钉（`extra="forbid"` 已让多余键失败）。
- **发现过程本身就是该单影响的复现**：#2662 只登记了 `agents[].ssh_port` 一处，判据上线后
  同一棵树又暴露 3 处（`storage.export_to_agents`、`monitoring` 整段——含 `enabled` 与
  `prometheus_port` 两键，此前样例根本没有 `monitoring:` 段）。66 个模型路径 vs 59 个样例键。
- 豁免面只留一条：**父键被显式写成 `null` 才豁免其子键**（`storage.os` 在
  `provisioning=local_mount` 下必须为空，否则 `local_mount_fields_conflict`）。
  复核时发现初版 helper 把「每个存在的键」都塞进豁免集合，等价于「`storage:` 在 ⇒
  `storage.*` 任意缺键都放行」——正是会吞掉本单要修的那类漂移；收紧后变异 N2
  （删 `export_to_agents`）才真正变红，N5（把 `storage.os` 写成半截 mapping）也在红侧。
- 文档面：`docs/design/2026-09-multi-site-installation.md:83` 的「完整字段见样例」改成
  **可证伪的表述**（点明由该守卫检查，并区分「值待填」≠「字段没登记」）；§3 契约表补
  `agents[].ssh_port` 行；`docs/operations/installation.md` 的 `ansible_port` 一句补上其
  site.yaml 来源与 #2283 历史。

### 批内新发现（顺手但有据，属同一漂移面）

`backend/.env.example:27` 让读者「见 `agent_api._get_backpressure`」，而该函数已随 #1520
下沉到 `backend/services/agent_host_heartbeat.py`（`agent_api` 内已无此名）。这是台账
「现状/落点」列要写的内容，指针不改就等于台账与示例互相打脸，故一并改为
`services/agent_host_heartbeat.get_backpressure`（一行注释，不动语义）。

### 同线认领与刻意不并入

#2661 与 #2662 都是「权威文档与既有代码/样例失同步、且无判据」的同一面，合批处理可共用
「补判据」这一套动作。**#2663 未并入**：它的落点含
`tests/test_prometheus_alerts_contract.py`，该文件被在窗 Execution 占用
（cursor「promtool 逐条漂移：场景过滤加速」，CODING/LIVE/NO_PR，worktree
`.wt/stp-agent-test-clock`），同文件双开必致队列冲突——留待其出窗后单独做。

## Alternatives

- **给 ADR-0042 另起一篇「P2 落地记录」**：被否。状态漂移的代价正是「读者按索引读」，
  新开一篇而索引仍写「P2 待启动」等于把漂移复制成两份；S12 已把「就地升版本 + 派生面同步」
  变成被门禁保护的既有实践。
- **改写/删除三篇旧 ADR 里 `USE_SESSION_WATCHDOG` 的句子**：被否。ADR 的决策叙述是历史证据，
  删掉会让「当初为什么这么设计」失去依据；`~~划线~~ + 已移除 + 现状` 既止血又不毁史，且与
  ADR-0003/0019 的既有勘误形态一致。
- **只补 `ssh_port: 22` 一行，不加样例完整性守卫**：被否（这也是本单最有信息量的一处）。
  只补一行是过渡止血，且**当场就能证伪**：加判据后同一面又掉出 3 处。若确要止血，
  终态出口是判据本身，故直接落判据。
- **守卫只做「无读取点」精确断言，不做行级标记**：被否为「不足」。零读取点只能证明代码里没有，
  证不了文档没在教读者调它——而 #2661 的实际症状正是后者。两条都做，且明确精确判据（③）与
  启发式判据（①）的分工。
- **`BACKPRESSURE_*` 以通配形态进台账**：被否。守卫用精确 token 正则
  （`(?<![A-Za-z0-9_])KEY(?![A-Za-z0-9_])`），通配不产生判据；子串匹配又会把
  `BATCH_SIZE`/`TASK_TIMEOUT` 这类大面积假阳性引进来，所以台账按精确键名登记、
  示例文件里的 `BACKPRESSURE_*` 通配注释天然不命中。

## Verification

只列实跑过的命令与结果（未跑的一律标 pending）。

- `env -u DATABASE_URL …python -m pytest tests/test_site_config.py tests/test_removed_env_keys.py -q`
  → **211 passed**（其中新增守卫 6 passed；收紧豁免面后 `test_site_config.py` 仍 205 passed）。
- 守卫判别力（`tests/test_removed_env_keys.py`，7 条变异全部变红、还原后基线 6 green）：
  M1 删 ADR-0002 同行标记（首轮**曾假绿** → 促成剥 URL 的结构性修法）、M2 删 ADR-0004:29 整条标注、
  M3 现行契约表新增裸引用、M4 台账删一行、M5 复活 `os.getenv("USE_SESSION_WATCHDOG")`（同行带
  「已移除」仍红）、M6 把 `docs` 移出 `SCAN_ROOTS`、M7 把「剥链接 URL」退回。
- 样例完整性判据变异（5 条全部变红、还原后基线 green）：N1 删 `agents[].ssh_port`、
  N2 删 `storage.export_to_agents`（**修复豁免面前不可拦**）、N3 删 `monitoring.prometheus_port`、
  N4 模型新增字段而样例不登记、N5 `storage.os` 写成半截 mapping。
- 豁免面实测：模型 66 路径 / 样例 64 键路径 / 显式 `null` 22 个；紧判据后
  `MISSING after 豁免 = []`，被豁免的只有 `$.storage.os.distribution` 与 `$.storage.os.version`。
- `python tools/dev/check_governance_surface.py --check` → `[OK] 治理面结构检查通过（S1–S14、S5x）`
  （纯文档改动也单独复跑，S12 是这批的强制面）。
- `git merge-base --is-ancestor` × 4 → 四个 P2 落地 commit 均在 `HEAD` 可达（回填依据）。
- 键名出处 `git show 5dc2d030` → 台账四条键逐字取自删除行，未凭记忆书写。
- `python scripts/run_gates.py check:quick` / `check:pr`：见 PR 描述（提交后在干净树上跑）。
- pending：#2661 的 ADR 面与 #2662 的样例面均为文档/守卫变更，无运行时行为改动，故不需
  dev 栈渲染级复验；`environment-variables.md` 附录仍由 `env_inventory.py --check` 负责
  （本单未改其生成块，门禁在 `check:*` 内覆盖）。

## Revisit

- **行级标记是启发式，不是证明**：判据①只能要求「同一行（链接外）有 已移除 类字样」，
  理论上仍可写出「带标记但语义仍在教人用它」的句子。更硬的形态是把台账升级为
  「键 ∈ 台账 ⇒ 该键必须无读取点且不得出现在任何 `.env*.example` 的非注释位置」；
  目前 `.env*.example` 的注释态条目是本判据的有意例外（它们是「旧名没了」的指路注释）。
- **排除面 `docs/notes/**` 是有争议的取舍**：一次性 Note 是追加式历史，若纳入扫描，
  改一笔就要追改旧 Note。代价是新 Note 里的裸引用无人拦——若日后发现 Note 面成了
  主要误导源，改成「Note 只扫最近 90 天」比全量排除更合身。
- **扫描面未含 `frontend/**`**：已移除的后端键目前不在前端出现，且前端不读这些键。
  若出现跨栈配置键（前端 `import.meta.env` 之类），`SCAN_ROOTS` 需要按语言扩展判据③的形态。
- **ADR 就地标注的裁决仍归 owner**：本单按既有实践（就地勘误 + 不删原文）落地，
  若裁定「旧 ADR 的历史陈述一字不动、失真只允许在台账侧记录」，则需回退三篇 ADR 的四处标注
  （台账与守卫可保留，二者无耦合），判据①的扫描范围随之缩到代码/示例面。
- **样例完整性判据的粒度**：现按「键路径在场」判定，不校验样例值是否等于模型默认值。
  后者是另一类漂移（默认值变了但样例仍写旧值），若要做需先决定样例是「模板」还是「快照」。
