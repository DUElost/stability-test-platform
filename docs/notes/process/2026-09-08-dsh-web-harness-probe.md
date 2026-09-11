# dsh web 加载行为实测——ADR-0034 附录 A v1.10 输入

Status: implemented
Class: process

## 背景

用户问「ADR-0034 是否支持 dsh web 这个 harness」。v1.9 的附录 A 矩阵没有 dsh 条目
（dsh=DeepSeek Harness，`2026-09-05-deepseek-harness-convention-study.md` 的研究
对象，此前从未作为执行载体实测）。本 note 记录按附录 A 协议对 dsh 0.1.1-rc.2 的
加载行为实测；结论已写入 ADR-0034 v1.10 附录 A。

## 实测

- **样本**：本机 `dsh` 0.1.1-rc.2（`~/.dsh`，DeepSeek 官方发行；web/headless 两个
  profile 共享 dsh-base 插件栈，含 `@deepseek-ai/dsh-agent-instructions`）。
- **headless 阳性对照**：/tmp 一次性 worktree + 嵌套 `backend/agent/AGENTS.md`
  （探针串 DSH-PROBE-20260908-ZQ7K），cwd=嵌套目录非交互单问：Q1（根
  「## 总原则/## 提交前」）=yes、Q2（探针串，来自嵌套文件）=yes——引擎层
  root→cwd 基线链全通，探针方法有效。
- **web 实测**（`dsh web --no-open --port 8737`，浏览器自动化驱动；工作区=
  本仓库，会话 cwd=工作区根）：
  - Q1 根标题=yes（注入段落 `Instructions from: AGENTS.md`，UI 打「上下文注入
    AGENTS.md」标签）；
  - Q2 嵌套 scoped 内容=no——基线不含子目录文件：会话 cwd=工作区根，附录 A
    「cwd 深度」验收形态对 web 不适用；
  - Q3 探针串=no（阴性对照干净，无幻觉）；
  - **动态注入=yes**：允许工具后 read `backend/agent/AGENTS.md`，上下文随即出现
    `Additional instructions from: backend/agent/AGENTS.md`（UI 同步打注入标签）。
- **机制铁证**：会话持久化记录 typed source `kind=agent-instructions`、
  `baseline: true`，baselineIdentity 与插件 README 语义一致（candidates
  AGENTS.md/CLAUDE.md、maxBytes 65536）——web 与 headless 同一插件在生效。
- **坑（记录在案）**：静态面（`dsh --profile web --dump-config` 与 dsh-web-app
  的 cordis.patch.yml 均显示 `agent-instructions` `disabled: true`）与运行时行为
  **矛盾**——运行时该插件实际生效（re-enable 机制未深挖）。加载判定只认行为
  探针，静态读数不作准绳；与 Antigravity「声明式配置与官方文档矛盾」同类。
- **调用前提**：工作区经**原生目录选择器**注册（持久化于
  `~/.dsh/storages/workspace.json`），GUI 无脚本通道，浏览器自动化不可驱动；
  headless profile 无此问题（启动 cwd 即工作区）。另：web/headless 实例共享
  `~/.dsh` 全局状态（工作区注册表与会话列表跨实例可见），探针会话会出现在
  用户自己的 dsh 侧边栏。
- **未验证**：Registry CLI 经 dsh web 的 Shell 工具理论可用，未 dogfood；完整
  Execution 周期以首个真实单为准。

## Decision

dsh web **可作为 ADR-0034 harness 使用**——加载行为与 headless 同源（root→cwd 基线链
经行为探针证实）；静态 `disabled: true` 与运行时矛盾，判定只认行为探针。调用前提
与已知限制（原生目录选择器注册、GUI 无脚本通道、共享 `~/.dsh` 全局状态）随结论一并
写入 ADR-0034 v1.10 附录 A；本 note 为实测证据母本。

## Alternatives

- 以静态配置（`--dump-config` / cordis.patch.yml）判定插件禁用 → 放弃：与运行时
  行为矛盾（见「实测 · 坑」），只认行为探针；
- 经 dsh web GUI 做自动化探针 → 放弃：工作区注册仅原生目录选择器、无脚本通道，
  浏览器自动化不可驱动（headless profile 即替代通道）；
- 等待上游澄清 re-enable 机制后再实测 → 放弃：结论不依赖机制细节（行为已证），
  机制留 Revisit。

## Verification

- headless/web 双通道探针问答与 UI 状态留档于本会话记录；探针 worktree 已
  `git worktree remove --force` 清理，8737 探针实例已停止，探针浏览器标签已关闭。
- 会话持久化证据：`~/.dsh/sessions/--home-debian13-stability-test-platform--/
  session-15842fd7-637d-4af3-8034-7b10f8d57b53/session.jsonl.zstd`（baseline
  typed source 与动态注入记录）。
- 探针会话（web UI 内「指令可见性三题检查」）保留在 dsh 侧边栏未归档，由用户
  自行处置。

## Revisit

- dsh web 首个真实 Requirement dogfood（Registry CLI 全周期）后回填附录 A
  「Registry CLI 可用」结论。
- 静态 dump 与运行时不一致的 re-enable 机制待上游源码/版本确认；上游升级后按
  harness-adapters.md「对应版本实测」纪律复测。
