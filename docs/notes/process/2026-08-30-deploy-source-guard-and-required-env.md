# 部署源守卫 + 兼容键缺失阻塞检查

Status: implemented
Class: process

## Decision

生产控制面（本机 127.0.0.1:8000）的 systemd `WorkingDirectory` 就是共享 git 工作树
（仓库根），「谁切了分支、谁重启，谁就把那个分支推上生产」。2026-08-30 实测生产一度
跑在 `ci/serial-automerge-update-branch` 上，且并发会话的工作树被误移。落两道防护：

1. **部署源守卫 `tools/dev/check-deploy-source.sh`**：校验 `HEAD == refs/heads/main`
   （detached HEAD 同样拒绝）且 tracked 工作区干净（`--untracked-files=no`；未跟踪文件
   降级为 WARN）。接入 runbook §1.1（补了缺失的 `git checkout main` + 守卫）与 §1.4
   重启前、skill `control-plane-deploy` §1 step 2/4 前。已装 systemd unit 另加
   `ExecStartPre=-...`（减号 = 失败继续），只留日志不中断。
2. **required-env 阻塞检查**：`tools/dev/check-deploy-readiness.py` 新增
   `_REQUIRED_ENV_KEYS`，断言 ambient env 或 `.env.backend` 中存在（#518：删除无前缀键
   回落后，生产只配旧键 → `resolve_scan_tool()` 返回 None → merge 静默跳过）。该脚本
   已是 runbook §1.3 的强制步骤（退出码须为 0），因此新断言天然阻塞。

关键取舍：

- **不用 ExecStartPre 硬失败**。unit 是 `Restart=always + RestartSec=5` 且无
  `StartLimit*`，硬失败会进重启循环撞 start-limit → failed → 生产中断。守卫是
  「人工步骤 + `-` 兜底」，零中断风险。
- 落点选 `check-deploy-readiness.py` 而非 `preflight_control_plane.py`：后者在
  runbook §1.6 是「可选但推荐」（非阻塞），且其 audit 链默认 env 文件是
  `backend/.env`（dev 覆盖文件，生产机上存在但不含生产键）——查会查错文件。

涉及文件：`tools/dev/check-deploy-source.sh`（新）、`tools/dev/check-deploy-readiness.py`、
`docs/operations/2026-08-29-post-review-deploy-runbook.md`、
`.claude/skills/control-plane-deploy/SKILL.md`、已装
`/etc/systemd/system/stability-backend.service`（仓库外）。

## Alternatives

- **ExecStartPre 硬失败守卫**：拒绝。无 StartLimit 配置 + `Restart=always` →
  守卫一失败即重启循环 → start-limit → failed → 生产中断，用防事故的守卫制造事故。
  已装 unit 现有 `py_compile` ExecStartPre 是同类暴露的历史遗留，不新增。
- **守卫放 preflight_control_plane.py**：拒绝，理由见上（非阻塞 + 默认查错文件）。

## Verification

- `tools/dev/check-deploy-source.sh`：非 main 分支 → 退出 1 并提示 `git checkout main`；
  main + 干净 → 退出 0；detached HEAD → 退出 1；untracked 文件 → WARN 不阻塞。
- `check-deploy-readiness.py --expect-revision ...`：生产 `.env.backend` 已配两键 →
  `[env] required keys present`；删除任一键后复跑 → 列出缺失并退出 1。
- 测试：`backend/tests/services/test_dedup_scan_merge.py` 新增
  `test_run_merge_sync_skips_when_tool_not_configured` /
  `test_run_merge_sync_skips_no_org_files`。

## Revisit

- 守卫只覆盖部署时刻，不阻止并发会话在工作树切分支——「共享工作树常驻 main」的
  治理（如 worktree 化开发）另行议。
- `_REQUIRED_ENV_KEYS` 初版只含 `STP_BACKEND_DEDUP_SCAN_*` 两键；后续每次「删除兼容
  回落」的 PR 必须同 PR 追加，否则清单会过期（与 Dependabot lock 再生成同款纪律）。
