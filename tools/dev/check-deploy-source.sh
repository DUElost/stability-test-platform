#!/usr/bin/env bash
# 部署源守卫：生产部署动作前执行，校验生产工作树确实在 main、tracked 工作区干净、
# 热更新**载荷根**下无未跟踪文件，且 alembic_version 与 code head 一致——防止把分支
# 代码、未评审内容或超前 schema 推上生产。
#
# 适用动作：migration / restart / hot-update / **scripts/scan**。最后一条是 #2386
# 补进来的（ADR-0051 Phase 3 起 scan 的输入已改为 tool_manifest.json + 站点包源，
# 不再读这棵树；保留本项是因为 manifest 本身来自检出）：`POST /scripts/scan` 曾以这棵树（STP_SCRIPT_ROOT）为输入，而它对
# 「盘上缺失」的已注册版本做**单向**反激活（目录回来再扫也不复活）。于是「别的会话
# 把主工作树切到不含该版本的提交」+「窗口内跑了 scan」= 主线活跃版本被静默吃掉，
# 且事后无法靠再扫恢复。scan 与 restart 之间也有这个窗口，所以 runbook §1.4 在 scan
# 行前再执行一次本脚本（不是只在 §1.1 跑过一次就算完）。
#
# 背景：systemd WorkingDirectory 即共享 git 工作树（本机=仓库根），谁切了分支、
# 谁重启，谁就把那个分支推上生产（曾实测生产跑在 ci/serial-automerge-update-branch）。
# runbook §1.1 与 .claude/skills/control-plane-deploy §1 的同步/重启步骤前各插一行；
# 已装 systemd unit 用 `ExecStartPre=-`（减号=失败也继续）做运行时兜底，只留日志不中断。
#
# 附带：#735 起还会比对一次监控/告警资产与仓库渲染结果（只 WARN，见文末）；#3112 起
# 载荷根（`backend/agent/`）下的未跟踪文件与 tracked 脏工作区同级**硬失败**——它是载荷
# 内容（会被推送到全部主机）并改变 desired digest，而仓库根其余位置的未跟踪文件维持
# WARN（并发会话的临时文件不该阻塞部署）。
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

PYTHON="${REPO_ROOT}/venv/bin/python"
if [ ! -x "$PYTHON" ]; then
    PYTHON="${REPO_ROOT}/.venv/bin/python"
fi
if [ ! -x "$PYTHON" ]; then
    PYTHON="python3"
fi

# 载荷根未跟踪文件是硬条件（#3112）：`backend/agent/` 是热更新打包与 desired digest 的
# **同一个枚举根**（同一份 os.walk，ADR-0040 D1），所以那里的未跟踪文件不是「并发会话的
# 临时文件」而是**载荷内容**——随下次热更新下发到全部主机，并改变 desired digest
# （`agent_code_sync_status` 的唯一判据，ADR-0040 v1.1）⇒ 一个临时文件即全 fleet 假 drift，
# 且真漂移与这类噪声在同一枚徽标上无法区分。`.gitignore` 覆盖的路径（resources/、
# __pycache__）git 本就不报未跟踪，不受影响。
if ! "$PYTHON" "$REPO_ROOT/tools/dev/check_payload_root_clean.py" --repo-root "$REPO_ROOT"; then
    echo "check-deploy-source: FAIL —— 热更新载荷根存在未跟踪文件（见上方输出）" >&2
    exit 1
fi

# 载荷根**之外**的未跟踪文件不阻塞（可能是并发会话的临时文件），只提示。
untracked="$(git status --porcelain --untracked-files=normal | grep '^??' || true)"
if [ -n "$untracked" ]; then
    echo "check-deploy-source: WARN —— 存在未跟踪文件（不阻塞，仅提示）：" >&2
    echo "$untracked" | sed 's/^?? /  /' >&2
fi
# #2062：手工部署路径按 runbook 是「pull → 守卫 → alembic upgrade head」——刚 pull 到带
# 新迁移的代码时，库必然**合法地落后**。故这里用 --allow-behind（只拒「库超前/修订未知」），
# 精确相等留给 systemd 的 ExecStartPre（那里硬检查位于 upgrade head 之后）。
if ! "$PYTHON" "$REPO_ROOT/tools/dev/check_alembic_at_head.py" --allow-behind; then
    echo "check-deploy-source: FAIL —— alembic schema 超前于代码或修订未知（见上方输出）" >&2
    exit 1
fi

# 监控/告警资产漂移检测（#735 第三格）：**只 WARN，不改变本次结论**——站点副本落后
# 不代表这次部署有问题；但「改了仓库、没人重跑安装」正是 #2488 查出「生产 10 条 vs 仓库
# 17 条」的成因，而本脚本已经在每次部署前与 systemd ExecStartPre 上运行，是现成的执行者。
# 退出码语义要守住：本脚本 exit 1 会被 runbook 读成「停止部署」，所以这里绝不向上抛。
if drift_out="$("$PYTHON" "$REPO_ROOT/tools/dev/check-monitoring-assets.py" 2>&1)"; then
    echo "check-deploy-source: OK —— 监控/告警资产与仓库渲染结果一致"
else
    echo "check-deploy-source: WARN —— 监控/告警资产与仓库不一致（不阻塞本次部署；按下方「源文件」提示的落地路径处置）：" >&2
    # #2985：判定行后面的「源文件：…」是**补救提示**（哪份源、走哪条落地路径）——只透出
    # 判定行会让操作者看不到该改什么，把「恒报 DRIFT 的常亮灯」换成「恒报 DRIFT 且无从下手」。
    printf '%s\n' "$drift_out" | grep -E '^[[:space:]]+(\[(DRIFT|SKIP |ABSENT|MISS )|源文件：)' | sed 's/^/  /' >&2
fi

echo "check-deploy-source: OK —— 工作树在 main，tracked 工作区干净，载荷根无未跟踪文件，schema 未超前 head"
