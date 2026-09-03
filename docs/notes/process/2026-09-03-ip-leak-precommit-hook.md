# ip-leak 门禁的 pre-commit 本地预检

Status: implemented
Class: process

## Decision

`.githooks/pre-commit` 新增检查 5：对暂存的新增/修改/重命名文件
（`--diff-filter=ACMR`，删除的文件不扫）调用
`tools/dev/check-internal-ip-leak.py -q`，命中即 `[BLOCK]` 拦提交。
与 CI lint job 的阻塞门禁同规则同工具，白名单与后缀过滤由工具内部
统一生效，本地/CI 不存在两套判定。

动机（2026-09-01 审计）：08-31 与 09-01 两次 PR lint 失败均是把真实
内网地址 / 设备序列号写进代码或 bug-fix note，push 后 CI 才红，各损失
一轮往返。两次都是**提交时随手写**的形态，提交现场拦截收益最高。

细节取舍：

- 解释器取 `python3` 回退 `python`，都没有则 `[WARN]` 跳过——本地预检是
  best-effort，CI 全仓扫描仍是权威；缺解释器不该堵死提交。
- 只扫暂存文件而非全仓：pre-commit 在提交热路径上，全仓扫描即使秒级
  也是每次提交的固定税；增量漏检由 CI 兜底。
- 未改 AGENTS.md：钩子头部注释与拦截文案自说明，常驻文档不重复可推导
  内容。

## Alternatives

- **全仓扫描**：放弃，见上；且工具默认走 `git ls-files`，成本随仓库线性涨。
- **不动 CI、只加钩子**：本就是单向增强，CI 门禁保持不变（钩子未启用的
  开发者仍受 CI 保护）。
- **把 ip-leak 提示写进 AGENTS.md**：放弃，见上；拦截文案已含三步处理指引
  （泛化 / CIDR / 白名单）。

## Verification

- `bash -n` 通过。
- 植入 `172.21.8.202` + `A2WENX6628000035` 的暂存文件 → 钩子 exit 1，
  两条规则（ipv4-dot / device-serial）均命中并给出处理指引。
- 脱敏写法（`172.21.x.x`、CIDR、`172.17.0.1`、掩码序列号）→ exit 0 静默。
- 无暂存改动 → exit 0。
- 测试用 `git config core.hooksPath` 写入共享 `.git/config` 后已还原 unset
  （worktree 共享主配置，勿残留指向临时目录的路径）。

## Revisit

若 hooksPath 一次性启用率长期偏低（本地预检覆盖不足），可考虑把启用动作
写进 onboarding 文档或 dev 容器入口；或当 `pre-commit` 框架入仓时合并迁移。
