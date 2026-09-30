# pytest 默认入口内存硬顶（#3547 / #3516 G1）

Status: proposed
Class: process

## Decision

gate runner 的六个 pytest 调用与 shell wrapper 共用 `scripts/run_pytest.py`。
入口先确认当前 cgroup v2 与祖先的有效硬顶不超过 6 GiB、swap=0；否则建立
systemd user scope，子进程再次确认后以当前解释器 `-m pytest` 执行。
无法确认保护时拒绝启动，不回退裸跑；信号退出按 shell 的 128+signal 表达。

保护入口不加载 env 文件，Agent gate 保留 `env -i`；仅补充 systemd user bus
所需的路由变量。`.env.test` 加载仍只属于原 shell wrapper。API skill、PR 模板、
测试指南与自检 skill 统一指向保护入口。生产诊断凭据来源残留属于独立后续范围。

## Alternatives

- 每条命令手写 systemd 前缀：无法守住新增调用与 Harness 默认路径，已复现漂移。
- 只相信 systemd-run 返回成功：不能证明实际控制器/限制生效，子进程必须查实。
- 无 user bus 时裸跑或只用 timeout：不构成内存硬顶，拒绝。
- 修改全部 CI / 所有构建命令：本单仅保护本地 pytest 默认入口；CI 原有隔离与
  required checks 的执行路径保持原样，不新增通用资源管理器。

## Verification

- 实际入口创建 scope 后读到 memory.max=6442450944、memory.swap.max=0。
- 边界/负向路径、参数与退出码、真实入口、Agent 环境与离线子集测试 31 项通过。
- 恢复 gate 裸跑：1 failed / 19 passed；取消边界核验：10 failed / 10 passed。
  恢复实现后相关 31 项再次通过。缺少 systemd-run 的真实入口退出 2、未启动 pytest。
- 真实入口测试在 CI 没有已验证 cgroup 时明确 skip，不把 mock 单测当作实际 cgroup 验收。
- check:quick 首轮因本文缺少 Status / Class 头失败；补齐后 16 项通过。schema-at-head
  因无 DATABASE_URL 明确跳过，未作数据库验收。
- 真实 Agent gate：2469 collected / 2469 passed（259.11s）；repo-tests 的 CI 离线
  子集：1986 passed / 18 skipped（380.48s）。两者均通过共同入口核实 cgroup。
- 64 MiB 外层 scope 内分配 128 MiB 的小规模探针被 SIGKILL（原始返回 -9），
  证明硬顶执行，不为测试耗尽 6 GiB；生产配置与数据库未参与。

## Revisit

- 非 Linux / 无 systemd user bus 的工作面须在受保护 Linux 环境执行；不增加绕过开关。
- 稳定项目 Python 与 root/worktree/deep cwd 命令导航由 #3516 G3 承接。
- git hooks opt-in 自检、Git 差分 dev 探针、真实 Claude / IDE 验收由后续单元承接；
  此 PR 不代表 G1 或 #3516 全部完成。独立复核前保持 draft。