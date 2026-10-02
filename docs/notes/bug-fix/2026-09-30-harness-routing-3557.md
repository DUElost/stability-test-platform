# G2：Codex Stop 路径、质量反馈与 scoped 根继承

Status: proposed
Class: bug-fix
- **Requirement**: [#3557](https://github.com/DUElost/stability-test-platform/issues/3557)，父载体 [#3516](https://github.com/DUElost/stability-test-platform/issues/3516)

## Decision

按 #3516 v1.2 已裁决的 inheritance / failure semantics 实施，不改变执行政策。
两层 scoped 入口无条件先获得 root（AEE 还获得 Agent），再叠加领域细节；这只是协议，
真实 Harness × cwd 的行为验收仍 pending，不能据此断言 IDE 已加载根契约。

Codex 移除 apply_patch 前的旧树 tsc，只在 Stop 做 typecheck / syntax compile。
从 Git root 找当前 worktree 的项目解释器与脚本，用已安装 TypeScript，不自动安装依赖。
2026-10-02 按 #3516 v1.3 的 Codex residual 修正输出协议：保留检查器的
PASS / FAIL / UNVERIFIED 分类，全部用 Stop JSON `systemMessage` 投递。
exit 0 / hook `completed` 表示反馈投递完成，质量结论只看消息前缀；非 PASS 额外明确
`not a quality PASS`，保留具体错误或不可验证原因。不返回续跑/停止控制字段，
不使用 exit 2，不恢复前置编辑 veto。两条 hook command 不变，不修改或绕过信任。
这取代原来的 FAIL / UNVERIFIED 写 stderr、exit 1 方案：真实 runtime 丢弃该诊断，
只显示 `hook exited with code 1`（[原始八场景](https://github.com/DUElost/stability-test-platform/issues/3516#issuecomment-5947416623)）。
compileall 把 pyc 写入临时目录，不在已发布源码版本中制造缓存文件；只编译，不 import 业务。

S8 解析完整 symlink 链，必须命中入口同目录的 AGENTS.md 实体，拒绝外部同名文件、
断链与循环；根层保留既有最小 import 形态，两层 scoped 只接受 symlink 薄壳。
先验证根 symlink 真身再读取内容，不读取被拒绝的外部入口。

## Alternatives

2026-10-02 的真实协议实验（Codex 0.159.2，已信任独立 fixture）比较六种组合：

- stderr + exit 1、JSON + exit 1：均 failed，只有退出码，没有分类与原因。
- `systemMessage` + exit 0：FAIL / UNVERIFIED 均成为可见 warning，turn 正常完成；采用此反馈协议。
- `continue:false` + exit 0：变为 stopped 并附 stop entry，增加停止控制语义；不采用。
- exit 2：blocked 后再次触发 Stop；用一次续跑保护收束实验，不用于质量反馈。

依据 [官方 Hook 契约](https://learn.chatgpt.com/docs/hooks)，反馈投递与质量状态分别表达；
不以 hook status=failed 为目标，也不将投递完成包装成质量通过。

原路径与继承实现的取舍保留：

- 保留前置 tsc：检验旧树，可能妨碍修复，违背已冻结的质量失败语义。
- 每次 PostToolUse 全量检查：纯文档编辑也反复支付成本；本单延续 Stop 的两类质量检查。
- 系统 python3 / npx 临时安装：依赖环境不确定且可能联网；项目依赖未初始化明确 UNVERIFIED。
- symlink 只查 basename：外部同名 AGENTS.md 可冒充根契约，结构全绿仍没有正确传导。

## Verification

2026-10-02 输出协议修复：

- 保护 runner：`env -i PATH="$PATH" PYTHONPATH=. ./scripts/project_python.sh -B scripts/run_pytest.py tests/test_harness_routing_3557.py -q`，29 passed，6GiB / swap0。
  root/AEE 类型红灯、缺 TypeScript 四个原失败场景固化为真实 command 回归；另覆盖 compileall 红灯、超时和 JSON 引号/反斜杠/Unicode 诊断。
- 换回 base `af4edf99` 的旧 checker，选择 `existing_red or missing_compiler or quality_error_details or checker_timeout`：8 failed / 21 deselected；随后恢复候选字节。
- 治理结构 S1–S15/S5x、Ruff、`git diff --check` 通过；quick 16 项通过，未配置 DATABASE_URL 的 schema 探针明确跳过。
  首次 quick 因隔离工作树缺 ESLint 失败；按 lock 执行 `npm ci --ignore-scripts` 后通过。
- [真实协议矩阵与候选八场景原始事件](https://github.com/DUElost/stability-test-platform/issues/3516#issuecomment-5947950344)：
  root/AEE 各 baseline、red、repair、missing-typescript，8 PASS / 0 FAIL（指验收判据通过；red 质量仍 FAIL，缺依赖仍 UNVERIFIED）。
  16 次 handler started/completed、8 个 turn 正常完成，无重复 Stop；两处模型实际 fileChange，随后质量 PASS。
  red 显示 TS2322，缺依赖显示 TypeScript 原因。Codex 0.159.2、never / danger-full-access、当前 command hash trusted。
  独立 fixture 冻结于 0c626a5f，仅 checker 替换为候选 SHA256 `5dd9a8820f68f0c82a3316f2028b3c23fca6c6db70a53b48711fef13e2570598`；
  不冒充完整最新 main 验收，不以子进程测试替代 runtime 验收。

2026-09-30 原路径与继承实现的历史验证（不代替本次验收）：

- 隔离行为回归 23 passed（6GiB / swap0 cgroup）；root、两层 cwd、含空格路径与 linked worktree，
  检查报红后编辑修复、解释器/脚本/源码/TypeScript 缺失、超时、symlink 外部目标/循环均覆盖。
- 从 AEE 深层 cwd 执行本 PR 两条真实 command：实际项目 TypeScript 与 compileall 均 exit 0，
  输出有效 Stop JSON；不把此结果当作真实 Codex 事件触发。
- 以 f6c389d9 的旧 S8 实现替换函数（保持新版调用适配）：4 failed / 19 deselected；新版 23 全绿。
- Ruff、治理 self-test、治理结构检查、quick 16 项通过；未提供隔离 DATABASE_URL 的 schema 探针明确跳过。
- 首轮测试 1 failed / 21 passed，原因是删除 backend 后夹具仍从不存在的 cwd 启动；修正夹具后通过。
  首轮 quick 因 Note 头部不合 S10 失败；修正为规定的 Status/Class 纯文本后通过。
- Windows PowerShell command、Codex 真实已信任 hook、Claude hook 与 CLI/IDE × cwd 验收 pending。
- 不读取个人配置/凭据，不改 hook trust/core.hooksPath/permissions，不连接业务库。

## Revisit

G2 后续修复 harness_probe 三态、Q3 和 root+两层 scoped 矩阵；CLI 不替代 Cursor/Zcode/CodeBuddy IDE。
Codex 项目受信任及 `/hooks` 的当前 hash 信任为实际激活前提；修改配置后需重新审阅。
G3 统一项目执行入口；当前 Stop 的项目 Python 检查不代表 CI Python 版本兼容或 required checks 通过。

2026-10-02 边界：检查器启动前缺 Git root、项目解释器或脚本时，原 command 仍报告 hook 执行失败；
该路径不具备 checker 三态 JSON，本次不宣告其诊断透传已验收。Windows、受限沙箱、其它 CLI
版本仍未验证；runtime 协议或信任条件改变时重跑真实场景。当前改动待独立复核及合入，
不能据此关闭 #3516；其它 Harness 验收沿用父单当前裁决，不沿用本 note 的历史 pending 清单。
