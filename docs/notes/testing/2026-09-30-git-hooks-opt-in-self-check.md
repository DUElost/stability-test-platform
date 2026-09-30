# Git hooks 的 opt-in 状态与隔离行为自检（#3555 / #3516 G1）

Status: proposed
Class: testing

## Decision

按 Owner 已裁决的 opt-in 实施，不自动配置宿主。`check_git_hooks.py` 默认只读：
由 Git 解析 hooks 目录，核对本仓库真实 `.githooks` 路径、文件与执行位。
DISABLED 是查询成功，不是防线通过；CONFIGURED 仅为结构证据。
其他路径中已有可执行 hooks 为 UNVERIFIED，不冒充“未启用”，也不读取/执行外部脚本。

显式 --self-test 要求已配置，否则 UNVERIFIED / 非零。行为验收复制当前 hook
到临时隔离 Git 仓库，实际 commit 验证污染阻断、update-ref refs/stash 验证
prepared/committed 告警而不阻断。不执行 stash/reset，不写调用者 config/index/refs。
临时仓库禁用个人/系统 Git 配置、使用空模板与干净环境，不继承外部 Git 目录或索引。
能力/工具异常为 UNVERIFIED，不当 PASS；实际不匹配为 FAIL。linked worktree 单独检查。

workflow 同步已合入解析器的简单命令/包装器/替换/heredoc 语义、保守回退与
可见的检查器故障；文档和 hook 注释统一 opt-in。不改禁止集合、permissions、
AI hook 接线或 CI gate。Git 路径与执行位依据见 [官方 githooks](https://git-scm.com/docs/githooks)。

## Alternatives

- 默认安装/启用宿主 hooks：违背 Owner 裁决，也会更改同克隆其他 worktree 行为。
- 只看文件存在或直接喂 stdin：不能证明 Git 触发；保留原 stdin 回归，新增真实 Git 自检。
- 在当前共享仓库制造 stash/提交：会影响其他会话；全部写入独立临时仓库。
- 泛化所有 hooks/加 CI gate：本轮无必要，仅核实现有两项 opt-in 辅助。

## Verification

- 基线 origin/main@f6c389d9；#3548/#3552/#3554 已合入且 Registry 核销，开放 PR 无目标文件重叠。
- 状态/行为回归覆盖未启用、相对/绝对路径、深 cwd、linked worktree、缺失/执行位、
  原仓库 config/index/refs 保持、外部索引隔离、CLI 与工具超时。
- fixture 将 reference-transaction 改回旧读取顺序或让 pre-commit 放行污染，
  自检分别 FAIL；原逻辑从真实 Git 同时验证提交阻断与 ref 告警。
- 首次运行 1 failed / 18 passed：真实 Git 将 hook 输出转到 stderr，自检只查 stdout。
  修正为同时检查两者；不把初次失败当通过。最终相关测试 22 passed，quick gates
  16 项通过；schema-at-head 未配置而跳过。Ruff 与最终 diff 治理检查通过。

## Revisit

- CONFIGURED/隔离 PASS 不代表真实 Claude/IDE hook 触发；该验收仍归 #3516。
- 当前宿主与本实现 worktree 的 opt-in 未启用；没有为取 PASS 改宿主 core.hooksPath。
- G3 提供稳定项目 Python/深 cwd 命令导航；此工具按当前项目解释器运行。
- 手动 hooks 可绕过，不替代 CI；未来新事故按现行棘轮复议，不扩大本轮禁止集合。
