#!/usr/bin/env bash
# 部署源守卫：生产部署动作（migration / restart / hot-update）前执行，校验
# 生产工作树确实在 main 且无未提交改动——防止把分支代码推上生产。
#
# 背景：systemd WorkingDirectory 即共享 git 工作树（本机=仓库根），谁切了分支、
# 谁重启，谁就把那个分支推上生产（曾实测生产跑在 ci/serial-automerge-update-branch）。
# runbook §1.1 与 .claude/skills/control-plane-deploy §1 的同步/重启步骤前各插一行；
# 已装 systemd unit 用 `ExecStartPre=-`（减号=失败也继续）做运行时兜底，只留日志不中断。
#
# 用法：./tools/dev/check-deploy-source.sh（可从仓库任意子目录运行）
# 退出码：0=通过；1=未通过（部署动作应停止）。
set -u

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || {
    echo "check-deploy-source: FAIL —— 不在 git 仓库内（找不到 .git）" >&2
    exit 1
}
cd "$REPO_ROOT"

branch="$(git symbolic-ref --short HEAD 2>/dev/null || true)"
if [ -z "$branch" ]; then
    echo "check-deploy-source: FAIL —— HEAD 处于 detached 状态（不在任何分支），拒绝部署" >&2
    echo "  该切回 main：git checkout main && git pull origin main" >&2
    exit 1
fi
if [ "$branch" != "main" ]; then
    echo "check-deploy-source: FAIL —— 生产工作树在分支 '$branch'，不在 main" >&2
    echo "  该切回 main：git checkout main && git pull origin main" >&2
    exit 1
fi

# tracked 改动是硬条件：脏工作树会让 checkout 失败、uvicorn 可能读到半写文件。
dirty="$(git status --porcelain --untracked-files=no)"
if [ -n "$dirty" ]; then
    echo "check-deploy-source: FAIL —— main 上有未提交的 tracked 改动" >&2
    echo "$dirty" | sed 's/^/  /' >&2
    echo "  先处理：git stash（或 git commit）后重跑本检查" >&2
    exit 1
fi

# 未跟踪文件不阻塞（可能是并发会话的临时文件），只提示。
untracked="$(git status --porcelain --untracked-files=normal | grep '^??' || true)"
if [ -n "$untracked" ]; then
    echo "check-deploy-source: WARN —— 存在未跟踪文件（不阻塞，仅提示）：" >&2
    echo "$untracked" | sed 's/^?? /  /' >&2
fi

echo "check-deploy-source: OK —— 工作树在 main，tracked 工作区干净"
