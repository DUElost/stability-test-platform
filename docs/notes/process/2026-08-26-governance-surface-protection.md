# 治理面防护两层方案（L0 门禁 + L1 按需诊断）

Status: implemented
Class: process

## Decision

给治理面（CLAUDE.md/AGENTS.md/.cursor/rules/AI 门禁 workflow，synthesis 编号
C-G1）补上回归防护，形态为两层而非博客原样的「evals 合并门禁」：

- **L0 结构门禁**（`tools/dev/check_governance_surface.py`，阻塞级）：S1 @import
  独占行、S2 治理文档链接解析、S3 .mdc frontmatter、S4 pr-agent 防绕过锚点、
  S5 required checks 文档↔workflow 互检；S6 行数仅信息行。接入 ci.yml lint 与
  run_gates check:quick/pr。
- **本地护栏**：pre-commit 第 4 项拦截已发布脚本版本目录 M/D（引擎中立，新建
  版本 A 放行）；`.claude/settings.json` deny 凭据文件写入（只禁写不禁读）；
  `.gitignore` 选择性放行 settings.json 与 skills/。
- **L1 行为 evals**（`tools/dev/run_gov_evals.py` + 12 条 case，按需诊断）：
  `claude -p` 无工具答题、确定性正则判卷、workers=2/timeout 300s；挂
  `check:gov` 专项 profile 手跑，**不进 CI 不进常规 profile**。重议条件：治理面
  写者 >1 人或 auto mode 成默认。
- **backstop 失败 issue 附机械摘要**（红灯 job/step、日志链接、compare 区间），
  有意不做 claude -p 自动分诊。
- 影响：新增 tools/dev 两文件与 gov_evals_cases.yaml；改动 ci.yml、
  run_gates.py、.githooks/pre-commit、.gitignore、main-ci-backstop.yml、
  docs/notes/README.md（Decision 节兼作轻量 plan 记录）、hot-update runbook
  （§5 回滚演练记录表）、.claude/settings.json 与首个 skill 试点。

## Alternatives

- **evals 作合并门禁（博客原样）**：放弃。前提是多写者/高频变更/auto mode，
  本项目单人低频且治理面编辑 diff 本身高度可读；唯一真实事故（@import 行内）
  是确定性可检的，L0 秒级零成本覆盖。挂载方式的重议条件写入 Revisit。
- **backstop 加 claude -p 自动分诊**：放弃。失败事件稀有，机械三要素足够单人
  启动排障；LLM 引入 flakiness 与密钥依赖，扩展位注释保留。
- **Claude settings deny 整个 `backend/agent/scripts/**`**：放弃——静态模式无法
  区分「新建版本(合法)」与「改既有版本(违规)」，agent 建 v+1 版本是日常路径
  （flash_firmware v1.3.x 即为例）；区分逻辑只能在 git 层做（diff-filter=MD）。
- **skill 先试点高价值部署 SOP**：缓行。价值最高但编码错误的代价也最高（直接
  作用于生产），先以测试与环境自检验证 skill 形态收益。

## Verification

- L0：`check_governance_surface.py --self-test` 六规则红绿样例双向通过
  （自测阶段真逮到 S3 引号值判空的 bug）；`--check` 全仓绿灯并**实际抓到
  DOC-MAP→ADR-0030 一条真断链**（`../adr/` 相对层级错误，GitHub 上 404）。
- pre-commit：/tmp 沙盒仓库三例反证——修改已发布文件拦（T1）、删除拦（T2）、
  新建版本目录放行（T3）。
- L1：runner `--self-test` 通过；单条冒烟通过；全量 12 条经两轮校准后首轮
  全绿（校准教训两条写入 cases.yaml 抬头：forbid 否定搭配误命中、异常路径
  静默吞原因）。
- workflows：两个改动 yml 经 PyYAML 解析 + notify-failure env 断言通过；
  `run_gates.py --list` 显示新 profile。

## Revisit

- 治理面写者 >1 人或 auto mode 成为默认工作态 → 重议 L1 门禁化（paths 触发
  + ANTHROPIC_API_KEY secret 就绪是前置）。
- 出现一次「agent 为过测而改测试文件」事故 → 重议 C-G7（测试文件保护 hook）
  并把负向决策翻转。
- pr-agent findings 观察期（1–2 个月）满 → 按 findings 处置数据决定修复闭环
  立项与否。
- 连续两次回滚不顺 → runbook §5 演练策略升级为正式排期。
