# Claude scoped 启动入口复用根 hook（#3516）

Status: implemented
Class: bug-fix

Refs: #3516；实现候选，合入/新会话激活与母单验收另行记录。

## Decision

修复 #3516 真实实验中 root PASS、Agent/AEE 直接启动 FAIL 的接线缺口，不修改
`check_destructive_git.py` 的命令判定或失败策略：

1. `backend/agent/.claude/settings.json` 与 `backend/agent/aee/.claude/settings.json`
   是指向根 `.claude/settings.json` 的相对 symlink，根仍是唯一内容来源。
2. hook command 用 `git -C "$CLAUDE_PROJECT_DIR" rev-parse --show-toplevel` 定位
   **该会话所在 checkout** 的检查器，而不是把 scoped 会话目录当作根，也不依赖工具 shell cwd。
3. Git 根/检查器不可用时保持告警 + exit 1；命中禁令仍由原检查器返回 exit 2。
   不在这次接线修复中改变缺失检查器的交互告警协议。

上游 shared settings 按 primary working directory 读取；根 `settings.local.json` 的共享
规则不适用于 shared settings，见 [官方设置契约](https://code.claude.com/docs/en/settings)。
配置可见性与检查器定位是两个独立条件，只补其中一个仍会失效。

本次只覆盖登记的 root / Agent / AEE 三个裸 CLI 启动入口。其它 cwd 直接启动或 `/cd`
转到未登记目录仍不承诺自动继承；保持 partial，不把三格修复扩成任意 cwd 的保证。
已有 permissions.deny 原样保留，无新增 allow；权限路径的会话目录解释也不因 hook 验收
变成完整凭据保护承诺。不写宿主/用户全局配置或本地 settings，不改 Codex。

## Alternatives

- **复制三份 hook JSON**：拒绝，后续规则会漂移。symlink 只提供入口，不形成新权威。
- **只改为 Git-root 定位**：拒绝，深层 CLI 没加载设置时根本不会调用 command。
- **只加 symlink**：拒绝，`CLAUDE_PROJECT_DIR` 仍可能是 scoped cwd，旧路径会找不到检查器。
- **改根 settings.local / 全局设置兜底**：拒绝，本地状态不能成为项目规则的唯一来源，
  也不能覆盖用户已有配置。
- **强制 wrapper 启动**：已有 `claude_with_root.sh` 只负责 print 模式契约注入；改变入口
  不能把已登记的裸 CLI 深层启动格直接改判 PASS，本次不扩它的职责。
- **改 Agent 打包排除集**：不需要。既有 Agent 载荷/digest 枚举跳过 symlink，新增引用
  不进入 Agent 运行载荷的文件集合；没有扩展脚本发布、hot-update 或版本登记工作面。

## Verification

### 离线反例与回归

使用项目 Python 入口和 `scripts/run_pytest.py`，cgroup 6 GiB / swap=0，不访问数据库：

- 先在旧配置运行新增 `tests/test_claude_deep_hook_3516.py`：**30 failed / 14 passed**。
  覆盖缺失的 scoped 引用、深层命令定位与 `.git` 文件等反例，不把只测 checker 算作接线覆盖。
- 修改后新增回归与原 `test_check_destructive_git_3516.py`：**84 passed**。
- 新测试覆盖：root/Agent/AEE，带空格的 checkout 路径，`.git` 目录/独立 `.git` 文件，
  Bash cwd 与会话目录不同，缺 Git 根/检查器的非阻断错误，整份根设置的引用一致性，
  symlink 不改变 Agent payload 枚举。没有运行真实破坏性 Git 操作。
- 扩大到检查器差分与 Git hooks opt-in 的相关回归：**153 passed**；新增测试 Ruff 通过。
- `check:quick`：**16 gates OK**。最初因 adapter 预算和 Note 头部格式失败，修订后重跑通过；
  未配置 `DATABASE_URL` 的 schema 对齐检查明确跳过，不计为数据库验证。
- 根目录完整离线回归：**2264 passed / 18 skipped**（402.59s）。首次为 2263 passed / 18 skipped / 1 failed，
  唯一失败来自新 symlink 尚未加入索引，被载荷根未跟踪文件守卫拦截；正常暂存预定文件后，
  该守卫与新回归 53 passed，再完整重跑通过，未修改或跳过守卫。18 条警告为已有无效转义 SyntaxWarning。

### 真实 CLI 自然触发（不是手动管道调用）

旧版实验见 [#3516 回填](https://github.com/DUElost/stability-test-platform/issues/3516#issuecomment-5948743770)：
root 六例通过，Agent/AEE 的 stash push 与 quoted reset 实际进入无副作用替身。

修复候选在独立临时 Git 仓库复测，未使用共享 worktree、无生产数据：

- 基线 `6b9d15404f778116db1409dc7bd4534fdd2eef67` + 本次未提交配置候选；不是完整 main 的新验收。
- Claude Code **2.1.284 / Linux / print**；本机兼容模型 `gpt-6-astra-cc-format[1m]`。
  结论针对 Claude Code 的 hook 调度，不是原生 Claude 模型能力或其它版本的保证。
- 原检查器 SHA-256：`bebcbae72c55afe790e39591e23f86cb0562e931bd90ba2e7ee376d0fc3e15a5`，未变。
- 候选根配置 SHA-256：`47a326d15b77eb5fc00df695eda5d8b3433dfea4a27477dbc1ec2e17caeb90ae`。
- 真实 `claude -p` 从三个入口分别启动；不通过 `--settings` 注入 hook。
  invocation settings 仅提供封闭测试命令 allow 与关闭实验 auto-memory；工具仅 Bash，
  permission-mode=dontAsk，无权限/信任绕过。每轮 cgroup 2 GiB / swap=0、210 秒上限、预算上限 1 USD。
- Git 替身只记录 argv/cwd，不执行 Git。由 `tool_use → tool_result`、debug 与替身记录对拍，
  不依据模型自报或进程 exit 0 判通过。

| cwd | 放行 | 阻断 | 结果 |
|---|---|---|---|
| root | status、stash list、打印禁令普通文本 | stash push、quoted reset --hard、bash -c 包装 stash push | 6/6 PASS |
| backend/agent | 同上 | 同上 | 6/6 PASS |
| backend/agent/aee | 同上 | 同上 | 6/6 PASS |

三轮均正常结束；**18/18 场景通过**。每轮受限命令出现原 hook 的 BLOCKED/exit 2，
替身只执行 status 与 stash list；printf 只打印数据。深层 debug 的 settings watcher
已包含 scoped 设置引用及其根目标。

本地原始材料：`/tmp/stp3516-claude-fixed-8oaw56ny/` 的 `provenance.json`、`summary.json`、
各 cwd 的 stdout JSONL/debug/替身日志；这是临时取证路径，关键事实固定在本文，不声称永久存储。
2026-10-03 提交前复查，该临时目录已不存在；再次核对候选配置/检查器哈希及两个 symlink
目标仍与上述实测一致。保留历史实测结论，但不声称原始日志仍可下载或本轮重新运行了 CLI。

## Revisit

- 合入后需用包含该改动的检出启动新会话；既有会话、其它机器和母单终态不因候选通过自动更新。
- 新增启动入口、CLI 改变设置发现/`/cd` 行为或平台不保留 symlink 时，重新做真实触发实验。
- 任意 cwd 自动覆盖、交互 TUI/Windows、完整凭据权限路径与缺失检查器的交互告警可见性仍未由本轮验证。
- 若未来要统一任意 cwd 的启动路径，单独裁决 launcher/安装机制，不继续复制目录规则。
