# 技术设计：治理面防护两层方案（C-G1 落地）

- **状态**：Living（2026-08-26 初版，随 L1 case 校准演进）
- **日期**：2026-08-26
- **上游**：[`reviews/AI_NATIVE_SDLC_PLAYBOOK_COMPARISON_2026-08-26_synthesis.md`](../reviews/AI_NATIVE_SDLC_PLAYBOOK_COMPARISON_2026-08-26_synthesis.md) C-G1 / D1–D5 + 逐项审计裁决（同日四项用户裁决见 §8）
- **Agent Note**：[`../notes/process/2026-08-26-governance-surface-protection.md`](../notes/process/2026-08-26-governance-surface-protection.md)

---

## 1. 问题定义

治理面 = `CLAUDE.md` / `AGENTS.md` / Harness 适配说明 /
`.cursor/rules/*.mdc` / AI 门禁 workflow。
它是所有 AI 会话行为的上游事实源，却是唯一无回归防护的层：代码侧有六项
required checks，治理面改动靠「下次 agent 犯蠢」暴露。已有实付事故：
CLAUDE.md `@import` 写在中文行内静默失效（人肉 `/context` 才发现）。

`approvals=0` 后机器门禁是合入唯一事实源（synthesis D1），其行为契约全部编码
在治理面——治理面回归即门禁语义漂移，因此本方案是「守 gate 的 gate」。

## 2. 分层裁决（借鉴而非照搬 playbook）

博客「continuous evals 作合并门禁」的隐含前提——多写者、变更高频、auto mode
默认工作态——本项目一条不满足；唯一真实事故样本（@import 行内）是**确定性可
检**的。故：

| 层 | 形态 | 挂载 | 依据类型 |
|----|------|------|----------|
| **L0 结构门禁** | 确定性文本检查 S1–S10 | 阻塞：ci.yml lint job + run_gates `check:quick/pr` | 〔证〕真实事故/实测断链/常驻上下文回膨胀 |
| 本地护栏 | git pre-commit 已发布脚本 M/D 拦截 + Claude settings 凭据写保护 | 提交现场/会话现场秒级反馈 | 〔证〕ef8808e 事故 |
| backstop 机械摘要 | 失败 issue 附红灯 job+step / 日志链接 / compare 区间 | 事件驱动 | 〔证〕现有 body 无定位要素 |
| **L1 行为 evals** | 封闭式问答 ×12，确定性正则判卷 | **按需手跑**（`check:gov`），不进 CI 不进常规 profile | 〔前〕门禁化前提不成立 |

L1 重议触发条件：治理面写者 >1 人，或 auto mode 成为默认工作态。

## 3. L0 规则明细（`tools/dev/check_governance_surface.py`）

| # | 规则 | 判定 | 锚定 |
|---|------|------|------|
| S1 | CLAUDE.md `@import` 独占一行（反引号包裹视为文档转义放行；跳过 ``` 围栏）且目标存在 | BLOCK | @import 行内失效事故 |
| S2 | 根治理文档、文档地图、scoped CLAUDE 与 Harness 按需入口的相对链接目标存在（percent-decode、跳锚点） | BLOCK | DOC-MAP 实测断链（落地首日抓到真断链一条：DOC-MAP→ADR-0030 的 `../adr/` 层级错误） |
| S3 | `.mdc` frontmatter 三字段齐全语义合法（值剥离包裹引号后判空） | BLOCK | 坏 frontmatter=规则静默不加载，与 S1 同故障类 |
| S4 | pr-agent.yml 五锚点（digest pin / fallback_models / disable-auto 步骤 / 门禁命令分离 job / security 判定串） | BLOCK | #399 / #421 事故转化物防误删 |
| S5 | ci.yml/pr-agent.yml 的 PR 门禁 job id 与 AGENTS.md 六项记载互检（CodeQL 无 workflow 文件，只查文档侧） | BLOCK | 五稿评审均人工核对过的事实固化 |
| S6 | AGENTS ≤80 行/8KB、CLAUDE ≤60 行/6KB、每个 Cursor rule ≤30 行/3KB；Harness 总索引与 scoped CLAUDE 各有独立预算 | BLOCK | Requirement 无关细节曾让常驻链超过 50KB；超预算必须迁往按需文档 |
| S7 | `.claude/skills/*/SKILL.md` frontmatter 的 name 与目录一致且 description 非空 | BLOCK | 错误 frontmatter 会让技能静默不可见 |
| S8 | CLAUDE.md 只能 `@import` 最小 `AGENTS.md` | BLOCK | 导入 DOC-MAP 会把完整索引无条件带入每次会话 |
| S9 | AGENTS/CLAUDE 只允许固定启动级二级章节，禁止三级章节 | BLOCK | 体量预算只能限制总量，章节白名单进一步阻止领域知识重新常驻 |
| S10 | 2026-09-05 起新增 Agent Note 的 Status/Class 头部与 class 目录一致 | BLOCK | 197 份存量中 78 份格式不统一；新门禁只阻止继续新增，不批量改写历史 |

检查器自身由 `--self-test` 守护：每条规则一红一绿样例双向验证
（verify-before-asserting；自测曾真逮到 S3 引号值误判空的非空 bug）。

## 4. 接线

- `scripts/run_gates.py`：`gov-surface` 入 `check:quick` / `check:pr`；
  新增专项 `check:gov = [gov-surface, gov-evals]`（后者按需手跑）。
- `ci.yml` lint job：脚本不可变检查之后追加「治理面结构检查(C-G1 L0)」步骤。

## 5. 本地护栏

1. **git pre-commit 第 4 项**：暂存集中命中 `backend/agent/scripts/*/v*/*`
   且 diff-filter=MD（修改/删除 HEAD 中已存在文件）即 BLOCK；新建版本目录(A)
   放行。git 层对全部引擎中立，补上 ruff exclude 只护 lint 工具、CI 分钟级才兜
   底的现场空白。
2. **`.claude/settings.json`**（首次入库）：permissions.deny 凭据文件
   Write/Edit（`.env.backend`、`**/.env`、`**/*.pem`、`//home/debian13/hosts.ini`）。
   **只禁写不禁读**——`docs/operations/production-diagnostics.md` 定义经授权的只读
   诊断边界，禁读会砍掉合法运维工作流；误改风险由 deny + gitignore 双层覆盖。`.gitignore` 由整目录
   忽略改为选择性放行 `settings.json` 与 `skills/`。
3. **skill 试点 `.claude/skills/test-env-self-check/`**：测试与环境自检分步清单
   （解释器/测试库红线/快速短路/WSL ADB）。D4 薄适配约束：只列操作与命令，
   权威理由留在按需开发文档；局限——仅 Claude Code 会话可见。

## 6. backstop 机械摘要

`main-ci-backstop.yml`：透传 `ci_run_id`/`main_sha` 输出；notify-failure 追加
三要素——① 红灯 job 及失败步骤名清单；② CI run 日志链接；③
`compare/<parent>...<main>` 疑似变更区间。纯 GH API，**有意不含 LLM 分诊**
（失败事件稀有 + 单人读得懂机械要素；扩展位留在 job 注释）。

## 7. L1 行为 evals（`tools/dev/run_gov_evals.py` + `gov_evals_cases.yaml`）

- **答题人**：`claude -p` 无工具会话在仓库根运行——测端到端摄取
  （自动加载 + import 解析），非文本包含性检查；不给工具是因为要测
  「治理面→会话知识」传导而非检索能力。引擎留 `--engine` 扩展位（api 填充式
  只在需要 CI 冒烟时实现）。
- **判卷人**：cases.yaml 出题即定死的 expect_any/forbid 正则；永不使用 LLM judge。
- **规模**：10 条封闭式启动契约（表名单数 / 唯一 action / lifecycle 键 /
  Pydantic v2 / python -m pytest / 生产库禁区 / 脚本不可变 / default_params /
  Agent Note 四节 / 禁手动 Merge），每条标注〔证〕或〔约〕来源。ADB 端口与生产
  env 文件等 Requirement 特定知识改由按需文档承载，不再要求无工具启动会话记忆。
- **运行参数**（实测校准）：workers=2 + 单问 timeout 300s——3 并发曾使部分调用
  连续撞超时；重载机器上单问可达数分钟。
- **首日校准教训两条**（已写入 cases.yaml 抬头注释）：
  1. forbid 词避开否定搭配：「不可用」命中裸 `可用` 正则（sole-action-type 首跑翻车；
     能被 expect_any 承载的不重复设 forbid）；
  2. 调用异常路径必须显式 graded 标记落报告——v1 用 `"pass" not in result` 判断，
     初值已含该键导致超时被静默吞成"无原因 FAIL"。
- **验收演示**：`run_gov_evals.py --self-test` 判卷逻辑双向自证；实际注入攻击
  （改坏治理面）由 L0 在 CI 级拦截，L1 定位语义级传导损伤。

## 8. 同日用户裁决记录（审计收口）

| 待决点 | 裁决 |
|--------|------|
| skill 建设 | 先建 1 个低风险试点（测试与环境自检）；高价值部署 SOP 缓行 |
| pr-agent findings 修复闭环 | 降为观察项：收集 1–2 月 findings 与处置数据再议 |
| 回滚演练 | 下次真实回滚时补记录（runbook §5 新增表格）；连续两次不顺才升级排期 |
| DORA 近似采集 | 暂不建；需要数据时按 synthesis 查询口径现查 |
| skills 防空洞机制（2026-08-27 补充裁决） | L0 增 **S7**：SKILL.md frontmatter name==目录名 + description 非空（写坏=对 agent 静默不存在）；新增 `tools/dev/skill_usage_report.py` 扫本机会话转录统计真实调用——判洞只看「是否为零」（零值可靠，正数为启发式上界）；**HOLLOW 判据=存在 ≥14 天零调用**，`check:gov` 以 `--strict` 把洞变红灯 | pilot（test-env-self-check）上线次日实测已非空（25 次）|

## 9. 修订记录

| 日期 | 变更 |
|------|------|
| 2026-08-26 | 初版：L0+护栏+backstop 摘要落地；L1 十二条 case 首轮全绿（两轮校准，教训二条记档） |
| 2026-08-27 | S7 skill frontmatter 校验入 L0（自测 7 条规则全绿）+ skill_usage_report 用量探针上线（HOLLOW=≥14 天零调用，strict 进 check:gov）；常驻瘦身 A/B1 依 RESIDENT_CONTEXT_AUDIT 执行完毕（−31.7%）另行留档 |
| 2026-09-05 | S6 从观测升级为常驻入口行数/字节阻塞预算，新增 S8/S9 限制 CLAUDE import 与根章节，S10 守住新 Agent Note 头部；L1 收敛为 10 条启动契约，领域知识改走按需文档 |
