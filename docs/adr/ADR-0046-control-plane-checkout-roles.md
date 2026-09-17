# ADR-0046：控制面检出的角色分离——开发工作区 vs 部署源

- 状态：**Proposed** v1.0（2026-09-17 起草，**待 owner 裁决**；本稿不声明 Accepted）
- 优先级：P1（它已经造成过一次静默降级，并且仍在阻塞发布正确性）
- 目标里程碑：M7
- 日期：2026-09-17
- 决策者：待裁决（起草：平台研发组）
- 标签：部署源, 控制面检出, 脚本 catalog, hot-update, #1987, #2386
- 关联：[#1987](https://github.com/DUElost/stability-test-platform/issues/1987)（复选项「检出双重角色」）
  / [#2386](https://github.com/DUElost/stability-test-platform/issues/2386)（scan 单向反激活；其验收第 3 条
  明文「需先确认 #735/ADR-0039 的退役轨道是否依赖当前单向语义」）
  / [ADR-0039](./ADR-0039-script-version-immutability-narrowing.md)（脚本版本不可变与退役轨道）
  / [ADR-0023](./ADR-0023-script-traceability.md)（脚本溯源）
  / [ADR-0042](./ADR-0042-settings-convergence-and-bare-read-boundary.md)（配置读取收敛：新 env 名的准入判据）
  / [`docs/design/2026-storage-roles-and-aliases.md`](../design/2026-storage-roles-and-aliases.md)
  （角色与路径键位权威源；本 ADR 不改它的任何定义）
- 版本记录：v1.0（2026-09-17）首次提出，六个裁决点全部开放

## 1. 背景：一个目录树同时承担三个互不兼容的角色

控制面主机上，仓库根目录那**一个** git 检出同时是：

| 角色 | 谁在用 | 代码事实 |
|---|---|---|
| 开发工作区 | 每一个并发会话 / 多个 Harness | `git worktree list` 实测 51 条工作树共用同一 `.git`；主检出被各会话反复 `checkout` |
| 控制面进程运行目录 | systemd `WorkingDirectory` | 进程读到的代码 = 检出当下所在的 commit |
| **部署源** | ① 脚本 catalog 扫描；② 主机 Agent 热更新打包；③ 派发期脚本补推 | 见 §2 |

前两个角色的诉求（「随时可以切走」）与第三个角色的诉求（「必须停在已验证的 revision 上」）
**直接冲突**。这不是假设风险：§3 列了两起已发生的事故。

## 2. 三条消费路径读的都是「检出当下的样子」

1. **脚本 catalog 扫描**：`backend/api/routes/scripts.py:149` `_script_root()` 取
   `STP_SCRIPT_ROOT`（未设即 503，且**刻意不回落**到中心存储），对「盘上缺失」的已注册版本
   做**单向**反激活（`is_active=False`，目录回来再扫也不复活）——见
   `backend/services/script_catalog.py`（`deactivated_versions` 明细可见性由 #2386 前半落地）。
2. **主机 Agent 热更新**：`backend/services/host_updater.py:33`
   `_AGENT_SOURCE_DIR = Path(__file__).resolve().parent.parent / "agent"` —— 路径**硬编码在
   运行代码的父目录上，没有任何 env 可以改指**；`:107` 直接 `os.walk` 这棵树打 tar 包。
3. **派发期脚本补推**：`backend/services/precheck/sync.py:40` `nfs_path_to_local()` 以同一个
   `_AGENT_SOURCE_DIR` 为根做路径回映射；检出缺该文件 → `local file not found`，把 run 挡在
   `plan_precheck` 的 fail-closed 面（#2386 评论记录的第二个消费方）。

**「发布的是哪个 revision」今天只是描述，不是保证**：`host_updater.py:566`
`get_agent_code_version()` 用 `git -C _AGENT_SOURCE_DIR rev-parse --short HEAD` 取值。它读的是
同一棵树的**当前**状态，既不能证明打包字节与记录一致，更不能证明此后检出没被切走。

## 3. 已发生的事实（不是推测）

- **2026-09-16（#2386）**：同一个 scan 部署窗口内两次 `POST /scripts/scan` 都发生在主检出
  **不在 main** 的时候（runbook §1.4 把 scan 排在 restart 之后，而 §1.1 与 §1.4 之间本身有窗口）。
  两次都靠执行人自行做 `git diff --quiet origin/main -- backend/agent/scripts` 才敢扫；
  当时 `deactivated=0` 是**运气 + 人工核对**，不是防线。
- **2026-09-14（#1987）**：主检出 `git merge --ff-only origin/main` 被「他人未跟踪文件冲突」
  拒绝（`tools/site_config/*`、`tests/test_site_config.py`、多个 docs 路径），控制面代码更新与
  主机 Agent 发布**同时停摆**，而 hot-update 接口仍然返回 `ok: true`。事后由**他人手工推进检出**
  解除阻塞——耦合本身原样保留。
- **守卫的性质**：`tools/dev/check-deploy-source.sh`（HEAD 必须在 main、tracked 必须干净、
  `alembic_version` 与 code head 一致）适用动作已扩到 `migration / restart / hot-update /
  scripts/scan`，runbook 在 §1.1、§1.4、§1.5 三处插桩。但它（a）靠人/会话自觉调用，
  （b）已装 systemd unit 用 `ExecStartPre=-`（**失败也继续**，只留日志），
  （c）本身就在注释里承认窗口：「§1.1 后并发会话可能又动了工作树」。**校验与动作之间永远隔着
  一个可被别家会话切走的窗口**——这是结构问题，不是纪律问题。

## 4. 需要裁决的点（本稿只给判据与取向，不替 owner 决定）

- **D1 部署源是否必须与开发工作区物理分离？**
  若「是」→ 走 §5 方案 A 或 B；若「否」→ 必须同时接受「现状守卫要升级成结构性拦截」
  （systemd `ExecStartPre` 去掉 `-`、失败即不启动，且 scan 端拒绝执行），否则本单等于没裁。
- **D2 终态下「盘上缺失」是否仍等于「已退役」？**
  这是 #2386 验收第 3 条卡住的那一点，也是 ADR-0039 退役轨道的依赖。
  本稿取向：**退役改为显式动作**（`is_active=False` 由人/流程发起），scan 只报告不反激活——
  因为一旦 scan 输入变成「某个已校验 revision 的只读树」（D1），「缺失」的语义就从
  「这台机器退役了它」变成「那个 revision 里没有它」，两者不能混用同一个写库动作。
  判据：退役必须留下可审计的发起人，而不是「某次扫描恰好没看见目录」。
- **D3 三条消费路径是否共用同一个「已校验 revision」来源？**
  现状是三个读取点各自算树。落地要求：scan / hot-update / 补推必须解析到**同一个**根，
  且该根由配置决定而非由 `__file__` 位置决定。注意 `_AGENT_SOURCE_DIR` 目前**无 env 开关**，
  env 化即新增 `STP_*` 名 → 受 #737 清单门禁（8 个 `.env*.example` + 奇偶性测试）与
  ADR-0042 的「惰性访问 + 不改既有 env 名」约束。
- **D4 部署检出的推进权归谁？**
  #1987 的直接症状是「谁都不动、谁都动不了」。需要明确：允许 `--ff-only` 推进的前提是
  `check-deploy-source.sh` 通过；他人未跟踪/已修改文件与部署检出**不得共存**（方案 A/B 天然满足，
  方案 C 必然复发）。
- **D5 Agent 代码是否需要「回滚到任意 revision」的能力？**
  这是 A 与 B 的**主判据**：只需要「发布当下主线」→ A 足够；需要「按 revision 复现/回滚某台
  主机的 Agent 代码」→ 只有 B 天然成立（A 的检出随时向前）。
- **D6 可见性与审计底线（与选型无关，两条路线都必须满足）**
  scan 与 hot-update 必须在审计里落「本次输入树的 revision + 该 revision 是否已校验等于
  部署目标」，且与实际推送字节一致；不一致即拒绝，而不是记录后继续。

## 5. 备选方案

| 维度 | A：独立只读部署检出 | B：按 revision 归档只读树 | C：共享检出 + 前置校验（现状） |
|---|---|---|---|
| 形态 | 第二个检出，只由部署动作 `--ff-only` 推进；`STP_SCRIPT_ROOT` 与 Agent 源根指向它 | 部署动作把 `backend/agent` 树按 revision 落成 `<releases>/<sha>/`；三条路径读归档 | 就是现在这台机器这一个目录 |
| 与「已发布版本不可原地修改」不变量 | 中性 | **天然契合**（归档即不可变） | 无关，靠人不许改 |
| 能否按 revision 回滚 Agent | 否（检出只能向前） | **是** | 否 |
| 新配置面 | 1～2 个 env（脚本根已存在；Agent 源根需 env 化 → 触发 D3/#737） | 1 个 releases 根 env + 归档器 | 无 |
| 残留失败模式 | 归档/同步动作本身失败 → 部署检出落后主线（可观测、可告警） | 磁盘增长需保留策略（与 #1521 的 NFS TTL 同族） | **窗口不可守**（§3 两起事故即此） |
| 一次性成本 | 低（一次 clone + 切 env + systemd 指路径） | 中（归档器 + 保留策略 + 存储） | 零（已在） |

**本稿倾向**（不是裁决）：A 作为 D1=是 的最小落地；若 D5 需要回滚能力则直接 B；
C 只作为**已标注的过渡**存在——它的可见性那一半（`deactivated_versions` 点名 + WARNING +
`check-deploy-source.sh` 覆盖 scan）已在 main，但**过渡若不写明出口就会沉淀成债**，这正是本 ADR
要堵住的东西。

## 6. 影响

- 本 ADR **不改代码**：只定义方向与判据。落地需按 D1–D6 拆实施单（建议拆法见 §7）。
- 若采纳 A/B：`STP_SCRIPT_ROOT` 的语义不变（仍是「控制面脚本根」），但**取值**必须改指部署检出/
  归档树——这是运维动作，需在 `docs/development/environment-variables.md` 与
  `deploy/control-plane/README.md` 同步；`check-deploy-source.sh` 的校验对象随之从「进程工作目录」
  变成「部署检出」，其判据（HEAD==main / tracked 干净）不变但**终于可以被强制**（去掉 `ExecStartPre=-`）。
- 若采纳 D2 的「退役改显式动作」：涉及 ADR-0039 退役轨道的既有流程，必须同步该 ADR 或在其中
  追加修订，不得只改代码。

## 7. 落地与后续动作

1. **先裁 D2**（成本最低、解锁 #2386 验收第 3 条，且决定 A/B 对退役轨道的影响面）；
2. **再裁 D1 + D5**（选型：A / B / 明确接受现状）；
3. 选型确定后拆实施单：①Agent 源根 env 化（含 #737 清单与 parity 测试）；②部署检出/归档器；
   ③systemd 与 runbook 改造 + `ExecStartPre` 由「只记日志」升为「失败即不启动」；
   ④退役动作显式化（D2）；
4. **证据缺口（裁决前应补齐）**：目前只有 09-16 的现场观察，没有一次受控实验——建议做一次
   隔离演练：把主检出切到不含某已注册版本的提交 → 跑 scan → 记录 `deactivated_versions`
   实际发生了什么。这是把「风险描述」变成「可复现实验」的最便宜的一步；
5. 本 ADR 的 Accepted 版本必须写明 D1–D6 的逐条结论与不做什么，不得以「同意」一句带过。

## 8. 复查与重议触发条件

- 再次出现 scan 的 `deactivated_versions` 非空且并非本次真要退役；
- hot-update 记录的 revision 与运维预期不符，或主机侧代码与 main 对不上；
- 主检出再次被未跟踪文件挡住 `--ff-only`（#1987 症状复发）；
- 中心存储/脚本盘发生迁移（角色变化会改变 A 与 B 的成本比较，权威定义见
  `docs/design/2026-storage-roles-and-aliases.md`）。
