# Git 破坏性操作防线：成文纪律 + PreToolUse 拦截 + stash 观测

Status: implemented
Class: feature

## Decision

#930 落地三层（成文禁令此前不存在——全库 grep 零命中，纪律只在会话级实践）：

1. **成文纪律**：`repository-workflow.md` §Git 破坏性操作纪律——禁令 +
   风险分级表（`reset --hard`/`stash drop|clear` 不可逆；stash 创建可恢复
   但污染全局 refs/stash 栈；读侧 list/show/pop/apply/branch 放行）；
2. **Claude 会话拦截**：`.claude/settings.json` 增 PreToolUse/Bash hook →
   `tools/dev/check_destructive_git.py`：按 shell 段（`&&`/`|`/`;`/换行）
   解析命令，段首为 git 且命中禁止项 exit 2 阻断；env 前缀跳过；段首
   判定天然放行 grep/echo 等含禁令字样的误报面；
3. **git 级观测**：`.githooks/reference-transaction` 对 `refs/stash` 更新
   留痕告警（非阻塞；随既有 `core.hooksPath` opt-in 生效）——`reset --hard`
   的 worktree 破坏无 git 级拦截点，硬拦截只在 Claude 层。

覆盖图定位：把「git 破坏性操作」从 context-only（纯模型自觉）升到
结构防线（Claude 会话内强制 + git 级观测），是 #855 覆盖图方法论的首个
非 lint 类收缩实例。

## Alternatives

- **git 级硬拦截 stash**（reference-transaction prepared 阶段 exit 非零）：
  技术可行但会中止 ref 事务，且只覆盖 stash 不覆盖 reset --hard——观测
  先行，硬拦截留待独立裁决；
- **拦截扩到 restore ./clean -f/checkout --**：同族但常用合法场景多
  （丢弃自己单文件改动），先纪律成文，事故驱动入清单（棘轮）；
- **用 alias/wrapper 替代 PreToolUse**：alias 不影响脚本内调用、wrapper
  只覆盖显式走 wrapper 的路径——PreToolUse 是 Claude 会话内唯一可靠的
  强制点；
- **fail-closed 脚本异常也阻断**：否决——hook 自身 bug 会瘫痪所有 Bash
  调用，fail-open（exit 1 显示错误不阻断）+ 显式命中才 fail-closed。

## Verification

- `venv/bin/python tools/dev/check_destructive_git.py --self-test` 全绿
  （18 样例：8 阻断红 + 10 放行绿，含复合命令/env 前缀/分号串联/误报面）；
- 真实 hook JSON 冒烟：`{"tool_input":{"command":"cd /x && git reset --hard"}}`
  → exit 2 + [BLOCKED] 指引；`git stash list` → exit 0；
- `ruff check` 通过；`check_governance_surface.py --check` 全绿（S1–S12+S5x）；
- `check:quick` 全绿。

## Revisit

- 同族破坏性命令按事故棘轮入清单；
- reference-transaction 硬拦截（prepared 中止事务）如出现 stash 绕过
  PreToolUse 的真实事件再裁决；
- 其他 harness（Codex hooks.json 等）的等价拦截：当前仅 Claude 会话有
  hooks 机制覆盖，跨 harness 扩展待各 harness 能力核实。
