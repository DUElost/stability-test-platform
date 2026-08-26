# 技术设计：治理面防护两层方案（C-G1 落地）

- **状态**：Living（2026-08-26 初版，随 L1 case 校准演进）
- **日期**：2026-08-26
- **上游**：[`reviews/AI_NATIVE_SDLC_PLAYBOOK_COMPARISON_2026-08-26_synthesis.md`](../reviews/AI_NATIVE_SDLC_PLAYBOOK_COMPARISON_2026-08-26_synthesis.md) C-G1 / D1–D5 + 逐项审计裁决（同日四项用户裁决见 §8）
- **Agent Note**：[`../notes/process/2026-08-26-governance-surface-protection.md`](../notes/process/2026-08-26-governance-surface-protection.md)

---

## 1. 问题定义

治理面 = `CLAUDE.md` / `AGENTS.md` / `.cursor/rules/*.mdc` / AI 门禁 workflow。
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
| **L0 结构门禁** | 确定性文本检查 S1–S5（+S6 信息行） | 阻塞：ci.yml lint job + run_gates `check:quick/pr` | 〔证〕真实事故/实测断链 |
| 本地护栏 | git pre-commit 已发布脚本 M/D 拦截 + Claude settings 凭据写保护 | 提交现场/会话现场秒级反馈 | 〔证〕ef8808e 事故 |
| backstop 机械摘要 | 失败 issue 附红灯 job+step / 日志链接 / compare 区间 | 事件驱动 | 〔证〕现有 body 无定位要素 |
| **L1 行为 evals** | 封闭式问答 ×12，确定性正则判卷 | **按需手跑**（`check:gov`），不进 CI 不进常规 profile | 〔前〕门禁化前提不成立 |

L1 重议触发条件：治理面写者 >1 人，或 auto mode 成为默认工作态。

## 3. L0 规则明细（`tools/dev/check_governance_surface.py`）

| # | 规则 | 判定 | 锚定 |
|---|------|------|------|
| S1 | CLAUDE.md `@import` 独占一行（反引号包裹视为文档转义放行；跳过 ``` 围栏）且目标存在 | BLOCK | @import 行内失效事故 |
| S2 | CLAUDE.md / AGENTS.md / docs/DOC-MAP.md 相对链接目标存在（percent-decode、跳锚点） | BLOCK | DOC-MAP 实测断链（落地首日抓到真断链一条：DOC-MAP→ADR-0030 的 `../adr/` 层级错误） |
| S3 | `.mdc` frontmatter 三字段齐全语义合法（值剥离包裹引号后判空） | BLOCK | 坏 frontmatter=规则静默不加载，与 S1 同故障类 |
| S4 | pr-agent.yml 五锚点（digest pin / fallback_models / disable-auto 步骤 / 门禁命令分离 job / security 判定串） | BLOCK | #399 / #421 事故转化物防误删 |
| S5 | ci.yml/pr-agent.yml 的 PR 门禁 job id 与 AGENTS.md 六项记载互检（CodeQL 无 workflow 文件，只查文档侧） | BLOCK | 五稿评审均人工核对过的事实固化 |
| S6 | CLAUDE.md/AGENTS.md 行数 | 仅输出 | 「约一页」属博客前提，本项目以分层加载补偿 |

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
   **只禁写不禁读**——AGENTS.md 把这些文件定义为只读诊断凭据源，禁读会砍掉
   合法运维工作流；误改风险由 deny + gitignore 双层覆盖。`.gitignore` 由整目录
   忽略改为选择性放行 `settings.json` 与 `skills/`。
3. **skill 试点 `.claude/skills/test-env-self-check/`**：测试与环境自检分步清单
   （解释器/测试库红线/快速短路/WSL ADB）。D4 薄适配约束：只列操作与命令，
   权威理由留 CLAUDE.md/AGENTS.md；局限——仅 Claude Code 会话可见。

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
- **规模**：12 条封闭式契约（表名单数 / 唯一 action / lifecycle 键 / Pydantic v2 /
  python -m pytest / ADB 端口 / 生产库禁区 / 脚本不可变 / default_params /
  Agent Note 四节 / 禁手动 Merge / env 单一源），每条标注〔证〕或〔约〕来源。
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

## 9. 修订记录

| 日期 | 变更 |
|------|------|
| 2026-08-26 | 初版：L0+护栏+backstop 摘要落地；L1 十二条 case 首轮全绿（两轮校准，教训二条记档） |
