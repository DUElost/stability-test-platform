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
[官方 hooks 契约](https://learn.chatgpt.com/docs/hooks) 要求 Stop 成功时输出 JSON，
因此成功用 systemMessage 报 PASS；质量红灯 exit 1 报 FAIL，缺环境/超时等报 UNVERIFIED，
不使用 exit 2 或 decision:block 触发续跑循环，不阻止修复编辑。
compileall 把 pyc 写入临时目录，不在已发布源码版本中制造缓存文件；只编译，不 import 业务。

S8 解析完整 symlink 链，必须命中入口同目录的 AGENTS.md 实体，拒绝外部同名文件、
断链与循环；根层保留既有最小 import 形态，两层 scoped 只接受 symlink 薄壳。
先验证根 symlink 真身再读取内容，不读取被拒绝的外部入口。

## Alternatives

- 保留前置 tsc：检验旧树，可能妨碍修复，违背已冻结的质量失败语义。
- 每次 PostToolUse 全量检查：纯文档编辑也反复支付成本；本单延续 Stop 的两类质量检查。
- 系统 python3 / npx 临时安装：依赖环境不确定且可能联网；项目依赖未初始化明确 UNVERIFIED。
- symlink 只查 basename：外部同名 AGENTS.md 可冒充根契约，结构全绿仍没有正确传导。

## Verification

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
