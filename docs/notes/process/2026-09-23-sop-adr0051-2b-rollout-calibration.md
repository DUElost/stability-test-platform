# control-plane-deploy SOP 校准：ADR-0051 Phase 2a/2b 上线实跑（2026-09-23）

Status: implemented
Class: process

## Decision

按 SOP「实跑→改文→同 PR 追加 §7 行」的约定，把 2026-09-23 的 Phase 2a/2b 生产上线实操
回写进 `.claude/skills/control-plane-deploy/SKILL.md`：

- §2 补 scan 的新响应键 `package_backfilled` / `package_conflicts`（来源仓根 `tool_manifest.json`）、
  只读等价证明命令，以及新增版本目录后的 `check_script_packages.py --register` / 合入后
  `--publish --packages-root /mnt/stp-aee/packages` 两步；
- §3 补三条：①「哪些 host 忙」要经 `device.host_id` join（`job_instance.host_id` 在生产上常为空，
  按它查得 0 台忙，随后 hot-update API 回 `HOST_HAS_ACTIVE_JOBS`）；批量脚本默认跳过、run 结束后
  重跑补齐；②脚本包开关 `STP_AGENT_SCRIPT_PACKAGES` 的推法：改 `.env.backend` → **重启后端**
  → hot-update，`env_keys_synced` 含 `STP_SCRIPT_PACKAGES`；digest 相等时 env-only 变更是
  `nothing-to-converge`，改值必须 `--force`；③验证三件套：presence refresh（走 verify_scripts 整包核验
  并预热）+ 主机 `tools_cache` 的 `.stp-verified` 计数 + 日志 `script_packages_fallback_tree` 计数；
- §7 追加校准行。

## Alternatives

- **不回写 SOP，只留 Agent Note**：弃——SOP 自述「校准有保质期」，且这次踩到的两个坑
  （忙碌判定列、env-only 不下发）下次上线必再踩。
- **把「忙碌判定」写成改代码（让 `job_instance.host_id` 有值）**：未做——本单是文档校准；
  是否修列属独立议题，先把现场判据写对。

## Verification

- 现场（2026-09-23）：生产检出 pull 到 `28e24185`；`alembic upgrade head` → `ad51c1d3f2a1`；
  `check_script_packages.py --publish` 发布 210 包（1.8 MB），`/mnt/stp-aee/packages/manifest.json`
  与 Git 源 `cmp` 一致；后端重启后 `/health` healthy；scan 响应
  `{"created":0,"skipped":210,"conflicts":[],"package_backfilled":210,"package_conflicts":[]}`；
  等价证明 `EQUIVALENCE OK rows=212 ok=210 backfilled=210 failing=0`；
- canary `172-21-x-x`：hot-update `ok=true reason=deployed code_version=28e24185`，
  `env_keys_synced` 含 `STP_SCRIPT_PACKAGES`；presence refresh 52 行 0 missing / 0 mismatch；
  主机 `tools_cache` 52 个 `.stp-verified`，fallback 0，错误 0；
- 批量 `--direct`：`SUMMARY ok=11 converged=11 fail=0 skipped=37`（37 台被 plan_run 518 活跃 job 跳过）；
  11 台逐台 ansible 只读核对：`v=28e24185 sw=STP_SCRIPT_PACKAGES=on cache=52 fallback=0 err=0`；
- `tools/dev/check_governance_surface.py` → S1–S15 OK（S7 skill frontmatter 未动）。

## Revisit

- plan_run 518 结束后重跑批量命令补齐 37 台；全 fleet `fallback=0` 后改 `strict`（需 `--force`）；
  之后才可进入 ADR-0051 Phase 3。
- `job_instance.host_id` 为空的成因与是否回填，另立单。

